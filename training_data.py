"""Local-first stock/range resolution, independent of the GUI."""
import os
import random
import re

import pandas as pd

from data_loader import load_market_data_file
from online_data import download_kline, fetch_stock_universe
from app_paths import application_dir

PROJECT_DIR = str(application_dir())
SKIP_DIRS = {'.git', '.venv', '.venv-release', '.build-tools', 'venv', '__pycache__',
            'node_modules', 'tests', 'gui', '.idea', 'build', 'dist', 'release', '_internal', 'licenses'}
AGGREGATION = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}


class LocalDataMissing(ValueError):
    """No local match; fetching is a separate, user-authorized action."""


def iter_market_files(roots, paths=(), cancelled=lambda: False):
    seen = set()
    for path in paths:
        if cancelled():
            raise InterruptedError('已取消数据检索')
        absolute = os.path.abspath(path)
        if os.path.isfile(absolute) and absolute not in seen:
            seen.add(absolute)
            yield absolute
    for folder in dict.fromkeys(os.path.abspath(r) for r in roots if r):
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
            if cancelled():
                raise InterruptedError('已取消数据检索')
            for filename in files:
                path = os.path.join(root, filename)
                if path not in seen and os.path.splitext(filename)[1].lower() in ('.csv', '.tsv', '.txt', '.day'):
                    seen.add(path)
                    yield path


def convert_period(frame, source, target):
    if target == 'auto' or source == target:
        return frame, source
    if source.endswith('min'):
        minutes = int(source[:-3])
        if target.endswith('min'):
            if int(target[:-3]) % minutes:
                raise ValueError('本地周期无法无损合成目标周期')
            return frame.resample(target, closed='right', label='right', origin='start_day',
                                  offset='30min').agg(AGGREGATION).dropna(subset=['Open']), target
    elif source != 'D' or target.endswith('min') or target == 'D':
        raise ValueError('本地数据不能还原为更细周期')
    rule = {'D': 'D', 'W': 'W-FRI', 'M': pd.offsets.MonthEnd()}[target]
    return frame.resample(rule).agg(AGGREGATION).dropna(subset=['Open']), target


def range_covered(frame, start, end, period):
    """Conservative boundary check; weekends are not treated as missing bars.

    This is not an exchange-calendar or missing-bar certification. Holidays and
    suspended stocks may need the user to select their actual available dates.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if frame is None or len(frame) < 3 or end < start:
        return False
    dates = pd.bdate_range(start.normalize(), end.normalize())
    if len(dates) == 0:
        return False
    if period.endswith('min'):
        step = pd.Timedelta(minutes=int(period[:-3]))
        left = max(start, dates[0] + pd.Timedelta(hours=9, minutes=30) + step)
        left = min(left, dates[0] + pd.Timedelta(hours=15))
        right = min(end, dates[-1] + pd.Timedelta(hours=15))
        right = max(right, dates[-1] + pd.Timedelta(hours=9, minutes=30) + step)
        if dates[-1] + pd.Timedelta(hours=11, minutes=30) < right < dates[-1] + pd.Timedelta(hours=13):
            right = dates[-1] + pd.Timedelta(hours=11, minutes=30)
        left = (left.normalize() + pd.Timedelta(hours=9, minutes=30)
                + (left - left.normalize() - pd.Timedelta(hours=9, minutes=30)).ceil(step))
        # Only completed bars up to the requested end are available for replay.
        right = (right.normalize() + pd.Timedelta(hours=9, minutes=30)
                 + (right - right.normalize() - pd.Timedelta(hours=9, minutes=30)).floor(step))
        return left <= right and frame.index[0] <= left and frame.index[-1] >= right
    if period == 'D':
        return frame.index[0].normalize() <= dates[0] and frame.index[-1].normalize() >= dates[-1]
    labels = dates.to_period('W-FRI' if period == 'W' else 'M').end_time.normalize()
    return frame.index[0].normalize() <= labels[0] and frame.index[-1].normalize() >= labels[-1]


def resolve_training_data(request, roots, paths=(), memory=None,
                          progress=lambda message: None, cancelled=lambda: False, allow_online=False):
    start, end = pd.Timestamp(request['start']), pd.Timestamp(request['end'])
    period = request['period']
    requested_code = request.get('code')
    random_mode = request['random']
    candidates = list(iter_market_files(roots, paths, cancelled))
    if random_mode:
        random.shuffle(candidates)
    known = {}
    available_ranges = []

    def consider(raw, code, name, source_period, origin):
        if cancelled():
            raise InterruptedError('已取消数据检索')
        if not code or not re.fullmatch(r'\d{6}', str(code)) or (not random_mode and code != requested_code):
            return None
        known[code] = name or code
        try:
            frame, actual_period = convert_period(raw, source_period, period)
        except (ValueError, KeyError):
            return None
        available_ranges.append(f'{code} {actual_period}: {frame.index[0]} ~ {frame.index[-1]}')
        if not range_covered(frame, start, end, actual_period):
            return None
        limit = end if actual_period.endswith('min') else end.normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        # Preserve prior history for indicators, but remove all bars after end.
        frame = frame.loc[frame.index <= limit].copy()
        if len(frame) < 3 or not (frame.index >= start.normalize() if actual_period == 'D' else frame.index >= start).any():
            return None
        return frame, {'code': code, 'name': name or code, 'period': actual_period,
                       'source_period': source_period, 'raw': raw.loc[raw.index <= limit].copy(), 'origin': origin}

    progress('正在检索本地行情和下载缓存…')
    # In random mode a scanned directory/cache is a pool, not overridden by the
    # last imported stock. A lone imported in-memory frame remains usable.
    if memory is not None and (not random_mode or not candidates):
        result = consider(*memory)
        if result is not None:
            return result
    for number, path in enumerate(candidates):
        if cancelled():
            raise InterruptedError('已取消数据检索')
        match = re.search(r'(\d{6})', os.path.basename(path))
        if not random_mode and match and match.group(1) != requested_code:
            continue
        if number % 50 == 0:
            progress(f'正在检查本地文件 {number + 1}/{len(candidates)}…')
        try:
            raw, code, name, source_period = load_market_data_file(path)
            # Cache filenames are authoritative for weekly/monthly short samples.
            cache_period = re.search(r'^\d{6}_(1min|5min|15min|30min|60min|D|W|M)(?:_|\.)', os.path.basename(path))
            if cache_period:
                source_period = cache_period.group(1)
            result = consider(raw, code, name, source_period, path)
            if result is not None:
                return result
        except (ValueError, OSError, UnicodeError, KeyError, IndexError):
            continue
    if memory is not None and random_mode and candidates:
        result = consider(*memory)
        if result is not None:
            return result
    if not allow_online:
        selection = '随机股票池' if random_mode else f'股票 {requested_code}'
        details = '\n'.join(available_ranges[-3:])
        raise LocalDataMissing(f'本地没有覆盖所选时间和周期的 {selection} 数据。\n'
            f'时间：{start} ~ {end}\n周期：{period if period != "auto" else "自动"}\n'
            + (f'已找到的范围：\n{details}\n' if details else '')
            + '请选择本地数据文件夹，或允许从网上获取。')
    if random_mode:
        if not known:
            progress('本地没有股票池，正在获取在线 A 股列表…')
            known = fetch_stock_universe(start.strftime('%Y-%m-%d'))
        if not known:
            raise ValueError('没有可用股票池，请导入股票数据或稍后重试在线列表')
        codes = random.sample(list(known), min(3, len(known)))
    else:
        codes = [requested_code]
    target = '5min' if period == 'auto' else period
    # Fetch warm-up context before the start, not just the first replay candle.
    lookback = 365 if target in ('D', 'W', 'M') else 30
    beginning = (start - pd.Timedelta(days=lookback)).strftime('%Y-%m-%d')
    errors = []
    for code in codes:
        if cancelled():
            raise InterruptedError('已取消数据检索')
        progress(f'本地缺少所选区间，正在下载 {code} · {target}…')
        try:
            raw, name, source = download_kline(code, target, beginning, end.strftime('%Y-%m-%d'),
                                               required_range=(start, end))
            result = consider(raw, code, name or known.get(code, code), target, f'在线 · {source}')
            if result is not None:
                return result
            errors.append(f'{code}：返回行情未覆盖所选区间，实际 {raw.index[0]} ~ {raw.index[-1]}')
        except Exception as error:
            errors.append(f'{code}：{error}')
    details = '\n'.join(errors + available_ranges[-3:])
    raise ValueError(f'无法取得覆盖 {start} ~ {end} 的 {target} 行情。\n{details}\n'
                     '免费分钟线可能只有近期数据；节假日/停牌边界请按实际可用时间调整。')
