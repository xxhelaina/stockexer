# -*- coding: utf-8 -*-
"""行情数据加载模块。

统一处理软件自存 CSV、通达信/东方财富等常见文本导出和通达信 ``.day``
文件，所有入口最终都返回同一份 OHLCV 数据契约。
"""
import os
import re
import struct
from typing import Optional, Dict, List, Tuple
import pandas as pd
from datetime import datetime
import logging
from utils import logger, parse_date_robust
import config

logger = logging.getLogger(__name__)

OHLC_COLUMNS = ['Open', 'High', 'Low', 'Close', 'Volume']

_COLUMN_ALIASES = {
    'date': ('datetime', 'date', '日期时间', '交易时间', '交易日期', '日期', '时间戳',
             'trade_date', 'trade_time', 'timestamp'),
    'time': ('time', '时刻', '成交时间', '时间'),
    'open': ('open', '开盘价', '开盘', '今开'),
    'high': ('high', '最高价', '最高'),
    'low': ('low', '最低价', '最低'),
    'close': ('close', '收盘价', '收盘', '现价'),
    'volume': ('volume', '成交量', '总手', 'vol'),
}


def _normalise_column_name(value) -> str:
    """移除 BOM、空白和常见分隔符，便于匹配中英文表头。"""
    return re.sub(r'[\s_\-()（）/]+', '', str(value).replace('\ufeff', '').strip()).lower()


def _detect_text_encoding(file_path: str) -> str:
    """严格读取一段样本来检测编码，避免首行纯 ASCII 时误判为 UTF-8。"""
    with open(file_path, 'rb') as f:
        sample = f.read(512 * 1024)
    if sample.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    for enc in ('utf-8', 'gb18030', 'gbk', 'gb2312'):
        try:
            sample.decode(enc)
            return enc
        except UnicodeError:
            continue
    raise ValueError(f'无法识别文件编码：{os.path.basename(file_path)}')


def _pick_column(columns, alias_key: str, exclude=None):
    excluded = set(exclude or [])
    aliases = {_normalise_column_name(v) for v in _COLUMN_ALIASES[alias_key]}
    for col in columns:
        if col in excluded:
            continue
        normalised = _normalise_column_name(col)
        if normalised in aliases:
            return col
    return None


def _normalise_time_series(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    return values.str.replace(r'^(\d{1,2})(\d{2})(\d{2})$', r'\1:\2:\3', regex=True) \
                 .str.replace(r'^(\d{1,2})(\d{2})$', r'\1:\2', regex=True)


def _build_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """把任意常见表头 DataFrame 规范化为 DatetimeIndex + OHLCV。"""
    raw = raw.copy()
    raw.columns = [str(c).replace('\ufeff', '').strip() for c in raw.columns]
    columns = list(raw.columns)

    date_col = _pick_column(columns, 'date')
    if date_col is None and columns and _normalise_column_name(columns[0]).startswith('unnamed'):
        date_col = columns[0]
    time_col = _pick_column(columns, 'time', exclude=[date_col] if date_col else [])
    mapped = {key: _pick_column(columns, key) for key in ('open', 'high', 'low', 'close', 'volume')}

    missing = [key for key in ('open', 'high', 'low', 'close') if mapped[key] is None]
    if date_col is None or missing:
        shown = '、'.join(map(str, columns[:12]))
        need = '日期/时间列' if date_col is None else '、'.join(missing).upper()
        raise ValueError(f'无法识别 {need}；检测到的列：{shown}')

    date_values = raw[date_col].astype(str).str.strip()
    if time_col is not None:
        time_values = raw[time_col].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        long_timestamp = time_values.str.fullmatch(r'\d{12,17}').mean() > 0.8
        if long_timestamp:
            # BaoStock time: YYYYMMDDHHmmssSSS（17位，末3位毫秒）。
            dt = pd.to_datetime(time_values.str.slice(0, 14),
                                errors='coerce', format='%Y%m%d%H%M%S')
        else:
            dt = pd.to_datetime(date_values + ' ' + _normalise_time_series(time_values),
                                errors='coerce', format='mixed')
        if dt.isna().all():
            dt = pd.to_datetime(date_values, errors='coerce', format='mixed')
    else:
        dt = pd.to_datetime(date_values, errors='coerce', format='mixed')

    result = pd.DataFrame(index=raw.index)
    for key, target in (('open', 'Open'), ('high', 'High'), ('low', 'Low'), ('close', 'Close')):
        result[target] = pd.to_numeric(raw[mapped[key]].astype(str).str.replace(',', ''), errors='coerce')
    if mapped['volume'] is not None:
        result['Volume'] = pd.to_numeric(
            raw[mapped['volume']].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    else:
        result['Volume'] = 0.0
    result['Date'] = dt
    result = result.dropna(subset=['Date', 'Open', 'High', 'Low', 'Close'])
    result = result[(result[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1)]
    result = result.set_index('Date').sort_index()
    result = result[~result.index.duplicated(keep='last')]
    if result.empty:
        raise ValueError('文件中没有可解析的有效 K 线数据')
    invalid = (result['High'] < result[['Open', 'Close', 'Low']].max(axis=1)) | \
              (result['Low'] > result[['Open', 'Close', 'High']].min(axis=1))
    if invalid.any():
        raise ValueError(f'检测到 {int(invalid.sum())} 行 OHLC 价格关系异常，请检查列映射')
    return result[OHLC_COLUMNS].astype(float)


def infer_period_key(df: pd.DataFrame) -> str:
    """根据时间索引推断周期；非标准分钟周期仍按分钟数据处理。"""
    if len(df) < 2:
        return 'D'
    if not pd.Series(df.index.date).duplicated().any():
        return 'D'
    diffs = pd.Series(df.index).diff().dropna()
    diffs = diffs[diffs < pd.Timedelta(hours=3)]
    minutes = int(round(diffs.median().total_seconds() / 60)) if not diffs.empty else 1
    return f'{max(1, minutes)}min'


def _metadata_from_file(file_path: str, header_text: str = '') -> Tuple[str, str]:
    base = os.path.basename(file_path)
    match = re.search(r'(?<!\d)(\d{6})(?!\d)', f'{base} {header_text}')
    code = match.group(1) if match else '000000'
    name = os.path.splitext(base)[0]
    name = re.sub(r'(?i)(sh|sz|bj)?[.#_-]?\d{6}', '', name)
    name = re.sub(r'(?i)[_-]?(1|5|15|20|30|60)?min|[_-]?[dwm]$', '', name).strip(' _-#')
    name = re.sub(r'(?<!\d)20\d{2}(?!\d)', '', name).strip(' _-#')
    return code, name or '本地导入'


def is_valid_kline_file(file_path: str) -> bool:
    """快速判断文件是否可能包含K线数据"""
    try:
        encoding = _detect_text_encoding(file_path)
        with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
            lines = []
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                lines.append(line.strip())
        keywords = ['日期', '时间', '开盘', '最高', '最低', '收盘', '成交量',
                    'date', 'open', 'high', 'low', 'close', 'volume']
        for line in lines:
            line_lower = line.lower()
            match_count = sum(1 for kw in keywords if kw in line_lower)
            if match_count >= 2:
                return True
            if re.search(r'\d', line) and re.search(r'[,\t]', line):
                return True
        return False
    except Exception as e:
        logger.debug(f"检查文件有效性失败 {file_path}: {e}")
        return False


def load_stock_names(tdx_root: str) -> Dict[str, str]:
    """从文件夹中的 .tnf 和 .txt 文件加载股票名称映射"""
    stock_names = {}
    tnf_count = txt_count = parsed_txt = 0

    for root, dirs, files in os.walk(tdx_root):
        for file in files:
            file_lower = file.lower()
            filepath = os.path.join(root, file)

            if file_lower.endswith('.tnf'):
                tnf_count += 1
                try:
                    with open(filepath, 'r', encoding='gb18030', errors='replace') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            parts = re.split(r'[,\t\s]+', line)
                            if len(parts) >= 2:
                                code, name = parts[0].strip(), parts[1].strip()
                                if code and name and code not in stock_names:
                                    stock_names[code] = name
                except Exception as e:
                    logger.error(f"读取 .tnf 文件失败 {filepath}: {e}")

            elif file_lower.endswith('.txt'):
                txt_count += 1
                try:
                    # 尝试读取第一行提取代码和名称
                    with open(filepath, 'r', encoding='gbk', errors='ignore') as f:
                        first_line = f.readline().strip()
                    if not first_line:
                        continue

                    code = name = None
                    match = re.search(r'(.+?)\((\d{6})\)', first_line)
                    if match:
                        name, code = match.group(1).strip(), match.group(2)
                    else:
                        parts = re.split(r'[,\t\s]+', first_line)
                        if len(parts) >= 2:
                            if parts[0].isdigit() and len(parts[0]) == 6:
                                code, name = parts[0], ' '.join(parts[1:]).strip()
                            elif parts[1].isdigit() and len(parts[1]) == 6:
                                name, code = parts[0], parts[1]

                    if code and name and code not in stock_names:
                        stock_names[code] = name
                        parsed_txt += 1
                except Exception as e:
                    logger.debug(f"解析 .txt 文件失败 {filepath}: {e}")

    logger.info(f"找到 {tnf_count} 个 .tnf, {txt_count} 个 .txt, 解析出 {parsed_txt} 个名称")
    return stock_names


def parse_tdx_day_file(file_path: str) -> Optional[pd.DataFrame]:
    """解析通达信 .day 二进制文件"""
    try:
        records = []
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(32)
                if len(chunk) < 32:
                    break
                date_int, open_int, high_int, low_int, close_int, amount_int, volume_int, _ = \
                    struct.unpack('IIIIIIII', chunk)
                date_str = str(date_int)[:8]
                try:
                    date_obj = datetime.strptime(date_str, '%Y%m%d')
                except:
                    date_obj = datetime.strptime(date_str, '%Y%m%d')  # 重新尝试
                records.append({
                    'date': date_obj,
                    'open': open_int / 1000.0,
                    'high': high_int / 1000.0,
                    'low': low_int / 1000.0,
                    'close': close_int / 1000.0,
                    'volume': volume_int,
                    'amount': amount_int / 10.0
                })
        if not records:
            return None
        df = pd.DataFrame(records)
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
        df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'volume': 'Volume'
        }, inplace=True)
        return df
    except Exception as e:
        logger.error(f"解析 .day 文件失败 {file_path}: {e}", exc_info=True)
        return None


def load_tdx_text_file(file_path: str) -> Tuple[Optional[pd.DataFrame], str, str]:
    """加载通达信导出的文本文件，返回 (DataFrame, 股票代码, 股票名称)"""
    stock_code = "TDX001"
    stock_name = "通达信导入数据"

    # 尝试不同编码
    content = None
    used_enc = None
    for enc in config.FILE_ENCODINGS:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.readlines()
            used_enc = enc
            break
        except UnicodeError:
            continue
    if not content:
        raise ValueError(f"无法识别的文件编码: {file_path}")

    lines = [line.strip() for line in content if line.strip() and not line.startswith('#')]
    if len(lines) < 2:
        raise ValueError("文件内容过少")

    # 查找表头行
    header_idx = None
    header_line = None
    common_keywords = ['日期', '时间', 'date', '开盘', 'open', '最高', 'high',
                       '最低', 'low', '收盘', 'close', '成交量', 'volume']
    for i, line in enumerate(lines):
        for sep in ['\t', ',', ';', '|']:
            parts = line.split(sep)
            if len(parts) >= 5 and any(any(kw in p.lower() for kw in common_keywords) for p in parts):
                header_idx, header_line = i, line
                break
        if header_idx is not None:
            break
    if header_idx is None:
        # 尝试按空格分割
        for i, line in enumerate(lines):
            parts = re.split(r'\s+', line)
            if len(parts) >= 5 and any(any(kw in p.lower() for kw in common_keywords) for p in parts):
                header_idx, header_line = i, line
                break
    if header_idx is None:
        raise ValueError("无法找到表头行")

    # 提取股票代码和名称（从表头之前的行）
    for i in range(header_idx):
        line = lines[i]
        code_match = re.search(r'(\d{6})', line)
        if code_match:
            stock_code = code_match.group(1)
            name_part = line.replace(code_match.group(0), '').strip()
            if name_part:
                # 去除常见后缀
                name_part = re.sub(r'(\d*分钟线?|日线|周线|月线|季线|年线|前复权|后复权|不复权)',
                                   '', name_part, flags=re.IGNORECASE)
                name_part = re.sub(r'\s+', ' ', name_part).strip()
                stock_name = name_part
            break

    # 解析数据
    data_lines = lines[header_idx+1:]
    # 检测分隔符
    sep = None
    for s in ['\t', ',', ';', '|']:
        if s in header_line:
            parts = header_line.split(s)
            if len(parts) >= 5:
                # 检查第一行数据是否也有相同数量
                if data_lines and len(data_lines[0].split(s)) == len(parts):
                    sep = s
                    break
    if sep is None:
        # 尝试空格
        sep = 'regex'

    if sep == 'regex':
        header = re.split(r'\s+', header_line)
        data_rows = [re.split(r'\s+', line) for line in data_lines]
    else:
        header = [col.strip() for col in header_line.split(sep)]
        data_rows = [line.split(sep) for line in data_lines]

    # 对齐列数
    max_cols = len(header)
    aligned_rows = []
    for row in data_rows:
        if len(row) >= max_cols:
            aligned_rows.append(row[:max_cols])
        else:
            # 补全缺失列
            aligned_rows.append(row + [''] * (max_cols - len(row)))

    df_raw = pd.DataFrame(aligned_rows, columns=header)

    # 映射标准列名
    col_map = {}
    col_lower = {col: str(col).lower() for col in df_raw.columns}
    for std_col, candidates in {
        'date': ['date', '日期', '时间', 'datetime', 'trade_date'],
        'open': ['open', '开盘', '开盘价'],
        'high': ['high', '最高', '最高价'],
        'low': ['low', '最低', '最低价'],
        'close': ['close', '收盘', '收盘价'],
        'volume': ['volume', '成交量', 'vol']
    }.items():
        for col, low in col_lower.items():
            if any(cand in low for cand in candidates):
                col_map[std_col] = col
                break

    required = {'date', 'open', 'high', 'low', 'close'}
    if not required.issubset(col_map.keys()):
        # 尝试按顺序映射前5列
        cols = list(df_raw.columns)
        if len(cols) >= 5:
            col_map = dict(zip(['date', 'open', 'high', 'low', 'close'], cols[:5]))
            if 'volume' in cols and len(cols) > 5:
                col_map['volume'] = cols[5]
        else:
            raise ValueError(f"无法映射所需列，表头: {list(df_raw.columns)}")

    # 构建最终DataFrame
    df = pd.DataFrame()
    df['Open'] = pd.to_numeric(df_raw[col_map['open']], errors='coerce')
    df['High'] = pd.to_numeric(df_raw[col_map['high']], errors='coerce')
    df['Low'] = pd.to_numeric(df_raw[col_map['low']], errors='coerce')
    df['Close'] = pd.to_numeric(df_raw[col_map['close']], errors='coerce')
    df['Volume'] = pd.to_numeric(df_raw[col_map.get('volume', df_raw.columns[0])], errors='coerce').fillna(0)
    # 解析日期；若存在独立的「时间」列（通达信1分钟导出格式），合并为分钟级时间戳
    date_col = col_map['date']
    time_col = None
    for col in df_raw.columns:
        if col == date_col:
            continue
        low = str(col).lower()
        if ('时间' in low or 'time' in low) and 'trade' not in low:
            time_col = col
            break
    date_series = df_raw[date_col].astype(str).str.strip()
    if time_col is not None:
        time_series = df_raw[time_col].astype(str).str.strip()
        # 通达信时间为 4 位数字（如 0931），补冒号以便解析
        time_norm = time_series.str.replace(r'^(\d{2})(\d{2})$', r'\1:\2', regex=True)
        combined = date_series + ' ' + time_norm
        df['Date'] = pd.to_datetime(combined, errors='coerce')
        if df['Date'].isna().all():
            # 合并解析失败则退回只用日期列
            df['Date'] = pd.to_datetime(date_series, errors='coerce')
    else:
        df['Date'] = pd.to_datetime(date_series, errors='coerce')
    df = df.dropna(subset=['Date', 'Open', 'High', 'Low', 'Close'])
    df.set_index('Date', inplace=True)
    df.sort_index(inplace=True)
    df = df.astype(float)

    logger.info(f"成功加载文本文件 {file_path}, 共 {len(df)} 条记录")
    return df, stock_code, stock_name


def load_minute_csv(file_path: str) -> Optional[pd.DataFrame]:
    """加载分钟K线CSV，兼容两种格式：
    1. 软件自存的下载数据（utf-8-sig，列名 datetime,Open,High,Low,Close,Volume）
    2. 东财/Choice 等导出的中文表头文件（GBK，列名 日期,时间,开盘,最高,最低,收盘,成交量）

    返回 DataFrame：DatetimeIndex 索引，列为 Open/High/Low/Close/Volume。
    """
    return load_delimited_file(file_path)


def load_delimited_file(file_path: str) -> pd.DataFrame:
    """读取 CSV/TSV/分号分隔文件，并自动识别编码、分隔符和表头所在行。"""
    encoding = _detect_text_encoding(file_path)
    with open(file_path, 'r', encoding=encoding) as f:
        lines = [f.readline() for _ in range(30)]
    aliases = set(sum(_COLUMN_ALIASES.values(), ()))
    header_idx = 0
    for i, line in enumerate(lines):
        normal = _normalise_column_name(line)
        if sum(_normalise_column_name(alias) in normal for alias in aliases) >= 4:
            header_idx = i
            break
    try:
        raw = pd.read_csv(file_path, encoding=encoding, skiprows=header_idx,
                          sep=None, engine='python', on_bad_lines='skip')
        return _build_ohlcv(raw)
    except Exception as first_error:
        try:
            raw = pd.read_csv(file_path, encoding=encoding, skiprows=header_idx,
                              sep=r'\s+', engine='python', on_bad_lines='skip')
            return _build_ohlcv(raw)
        except Exception:
            raise ValueError(f'无法解析行情文件：{first_error}') from first_error


def load_market_data_file(file_path: str) -> Tuple[pd.DataFrame, str, str, str]:
    """自动识别本地行情文件，返回 ``(df, code, name, period_key)``。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.day':
        df = parse_tdx_day_file(file_path)
        if df is None or df.empty:
            raise ValueError('通达信 .day 文件中没有有效记录')
    elif ext in ('.csv', '.txt', '.tsv'):
        try:
            df = load_delimited_file(file_path)
        except ValueError:
            df, _, _ = load_tdx_text_file(file_path)
            if df is None or df.empty:
                raise ValueError('文本文件中没有有效记录')
    else:
        raise ValueError(f'不支持的文件类型：{ext or "无扩展名"}')

    try:
        enc = _detect_text_encoding(file_path) if ext != '.day' else ''
        with open(file_path, 'r', encoding=enc) as f:
            header = ''.join(f.readline() for _ in range(3))
    except (OSError, UnicodeError):
        header = ''
    code, name = _metadata_from_file(file_path, header)
    return df, code, name, infer_period_key(df)


def load_stock_data_file(file_path: str, file_type: str) -> Tuple[Optional[pd.DataFrame], str, str]:
    """通用加载函数，返回 (df, code, name)"""
    if file_type == 'txt':
        return load_tdx_text_file(file_path)
    elif file_type == 'day':
        df = parse_tdx_day_file(file_path)
        if df is None:
            return None, "", ""
        # 从文件名提取代码
        base = os.path.basename(file_path)
        code_match = re.search(r'(\d{6})', base)
        code = code_match.group(1) if code_match else "未知代码"
        name = ""  # 需要从名称映射中获取
        return df, code, name
    else:
        raise ValueError(f"不支持的文件类型: {file_type}")
