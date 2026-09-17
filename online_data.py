# -*- coding: utf-8 -*-
"""在线免费数据源模块：BaoStock/东方财富主源，新浪/腾讯自动兜底。

输出格式与软件数据契约一致：DataFrame 以 DatetimeIndex 为索引，
列为 Open/High/Low/Close/Volume，可直接交给周期合成与指标计算线程。
"""
import json
import os
import time
import multiprocessing
from typing import Tuple

import pandas as pd
import requests

from utils import logger
from app_paths import download_dir

# 周期键 → 东财 klt / 腾讯周期代码
PERIOD_KLT_MAP = {
    '1min': 1, '5min': 5, '15min': 15, '30min': 30, '60min': 60,
    'D': 101, 'W': 102, 'M': 103,
}
PERIOD_TENCENT_MAP = {
    '1min': 'm1', '5min': 'm5', '15min': 'm15', '30min': 'm30', '60min': 'm60',
    'D': 'day', 'W': 'week', 'M': 'month',
}
# 新浪 scale 映射（分钟 1/5/15/30/60，日 240，周 1200，月 7200；最多约1023根）
PERIOD_SINA_MAP = {
    '1min': 1, '5min': 5, '15min': 15, '30min': 30, '60min': 60,
    'D': 240, 'W': 1200, 'M': 7200,
}
# 分钟级周期（1分钟免费接口仅约近5个交易日）
MINUTE_PERIODS = ('1min', '5min', '15min', '30min', '60min')
BAOSTOCK_PERIODS = ('5min', '15min', '30min', '60min', 'D', 'W', 'M')

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}


def code_to_secid(code: str) -> str:
    """6位股票代码 → 东财 secid（沪市 1.，深市/北交所 0.）"""
    code = str(code).strip()
    if code.startswith('92'):
        return f'0.{code}'
    if code.startswith('6') or code.startswith('9'):
        return f'1.{code}'
    return f'0.{code}'


def code_to_tencent_symbol(code: str) -> str:
    """6位股票代码 → 腾讯/新浪代码（sh600000 / sz000001 / bj920000）"""
    code = str(code).strip()
    if code.startswith(('4', '8')) or code.startswith('92'):
        return f'bj{code}'
    if code.startswith('6') or code.startswith('9'):
        return f'sh{code}'
    return f'sz{code}'


def code_to_baostock_symbol(code: str) -> str:
    """6位股票代码 → BaoStock 代码。"""
    code = str(code).strip()
    if code.startswith(('4', '8', '92')):
        return f'bj.{code}'
    if code.startswith(('6', '9')):
        return f'sh.{code}'
    return f'sz.{code}'


def _request_with_retry(url: str, attempts: int = 4, timeout: int = 10,
                        alternate_direct: bool = True) -> requests.Response:
    """带重试的 GET 请求。

    指数退避；alternate_direct=True 时奇偶次尝试交替使用系统代理/直连，
    应对本机代理间歇性抽风（东财接口实测时好时坏）。
    """
    last_err = None
    for i in range(attempts):
        kwargs = {'headers': _HEADERS, 'timeout': timeout}
        try:
            # requests 的 proxies=None 仍可能继承环境代理；显式关闭 trust_env
            # 才能真正完成“系统代理/直连”交替尝试。
            if alternate_direct and i % 2 == 1:
                with requests.Session() as session:
                    session.trust_env = False
                    resp = session.get(url, **kwargs)
            else:
                resp = requests.get(url, **kwargs)
            if resp.status_code == 200:
                return resp
            last_err = RuntimeError(f'HTTP状态码 {resp.status_code}')
        except Exception as e:
            last_err = e
        if i < attempts - 1:
            time.sleep(2 ** min(i, 3))
    raise ConnectionError(f'请求失败（已重试{attempts}次）：{last_err}\nURL: {url}')


# 东财历史行情主机轮换表：push2his 在部分网络已长期不可达（连接被服务端直接掐断），
# push2 为当前主力但请求过密会触发WAF限流。单主机最多2次尝试，失败快速换下一主机。
_EM_KLINE_HOSTS = ('push2.eastmoney.com', 'push2his.eastmoney.com')


def _baostock_job(connection, kind, args):
    """Run the blocking SDK in an owned process so a stuck socket is bounded."""
    try:
        if kind == 'kline':
            result = _fetch_kline_baostock_direct(*args)
        else:
            import baostock as bs
            login = bs.login()
            if login.error_code != '0':
                raise ConnectionError(login.error_msg)
            result = {}
            try:
                for offset in range(7):
                    day = (pd.Timestamp(args[0]) - pd.Timedelta(days=offset)).strftime('%Y-%m-%d')
                    query = bs.query_all_stock(day=day)
                    if query.error_code != '0':
                        raise ConnectionError(query.error_msg)
                    while query.next():
                        row = dict(zip(query.fields, query.get_row_data()))
                        symbol = row.get('code', '')
                        if (symbol.startswith(('sh.6', 'sz.0', 'sz.3', 'bj.4', 'bj.8', 'bj.92'))
                                and row.get('tradeStatus', '1') == '1'):
                            result[symbol.split('.')[-1]] = row.get('code_name', symbol)
                    if result:
                        break
            finally:
                bs.logout()
        connection.send((True, result))
    except Exception as error:
        connection.send((False, str(error)))
    finally:
        connection.close()


def _bounded_baostock(kind, args, timeout=35):
    import importlib.util
    if importlib.util.find_spec('baostock') is None:
        raise RuntimeError('未安装 baostock')
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_baostock_job, args=(writer, kind, args), daemon=True)
    try:
        process.start()
        writer.close()
        if not reader.poll(timeout):
            raise TimeoutError(f'BaoStock 超过 {timeout} 秒未返回')
        success, value = reader.recv()
        if not success:
            raise ConnectionError(value)
        return value
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            process.join(.2)
            if process.is_alive():
                process.terminate()
                process.join(2)


def fetch_stock_universe(day):
    """Prefer historical candidates; use the current EM list as a fallback.

    EM parameters follow AKShare's stock_zh_a_spot_em upstream implementation:
    https://github.com/akfamily/akshare/blob/master/akshare/stock_feature/stock_hist_em.py
    Current-list fallback cannot restore delisted historical candidates.
    """
    try:
        stocks = _bounded_baostock('universe', (day,))
        if stocks:
            return stocks
    except Exception as error:
        logger.warning(f'BaoStock 股票列表不可用，尝试东方财富：{error}')
    stocks = {}
    page = 1
    while page <= 100:
        params = {'pn': page, 'pz': 100, 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2, 'fid': 'f12',
                  'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                  'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048', 'fields': 'f12,f14'}
        url = 'https://82.push2.eastmoney.com/api/qt/clist/get?' + requests.compat.urlencode(params)
        data = _request_with_retry(url, attempts=2, timeout=8).json().get('data') or {}
        rows = data.get('diff') or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not rows:
            raise ValueError('东方财富股票列表为空或分页不完整')
        previous_count = len(stocks)
        for row in rows:
            code = str(row.get('f12', ''))
            if len(code) == 6 and code.isdigit():
                stocks[code] = row.get('f14') or code
        if len(stocks) >= int(data.get('total', len(stocks))):
            return stocks
        if len(stocks) == previous_count:
            raise ValueError('东方财富股票列表重复分页，无法完成检索')
        page += 1
    raise ValueError('股票列表超过分页上限')


def fetch_kline_baostock(code, period_key, beg, end, fqt=1):
    return _bounded_baostock('kline', (code, period_key, beg, end, fqt))


def _fetch_kline_baostock_direct(code: str, period_key: str, beg: str, end: str,
                         fqt: int = 1) -> Tuple[pd.DataFrame, str]:
    """通过可选 BaoStock 包获取长区间历史行情。"""
    if period_key not in BAOSTOCK_PERIODS:
        raise ValueError(f'BaoStock 不支持周期 {period_key}')
    try:
        import baostock as bs
    except ImportError as e:
        raise RuntimeError('未安装 baostock') from e

    frequency = {'5min': '5', '15min': '15', '30min': '30', '60min': '60',
                 'D': 'd', 'W': 'w', 'M': 'm'}[period_key]
    adjustflag = {0: '3', 1: '2', 2: '1'}.get(fqt, '2')
    fields = ('date,time,code,open,high,low,close,volume' if period_key in MINUTE_PERIODS
              else 'date,code,open,high,low,close,volume')
    login = bs.login()
    if login.error_code != '0':
        raise ConnectionError(f'BaoStock 登录失败：{login.error_msg}')
    try:
        stock_name = ''
        try:
            basic = bs.query_stock_basic(code=code_to_baostock_symbol(code))
            if basic.error_code == '0' and basic.next():
                basic_row = basic.get_row_data()
                stock_name = basic_row[1] if len(basic_row) > 1 else ''
        except Exception:
            logger.debug('BaoStock 股票名称查询失败', exc_info=True)
        result = bs.query_history_k_data_plus(
            code_to_baostock_symbol(code), fields,
            start_date=pd.Timestamp(beg).strftime('%Y-%m-%d'),
            end_date=pd.Timestamp(end).strftime('%Y-%m-%d'),
            frequency=frequency, adjustflag=adjustflag)
        if result.error_code != '0':
            raise ValueError(f'BaoStock 查询失败：{result.error_msg}')
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        if not rows:
            raise ValueError(f'BaoStock 无数据（{code}，{period_key}，{beg} ~ {end}）')
        raw = pd.DataFrame(rows, columns=result.fields)
        if period_key in MINUTE_PERIODS:
            raw['datetime'] = pd.to_datetime(
                raw['time'].astype(str).str.slice(0, 14), format='%Y%m%d%H%M%S', errors='coerce')
        else:
            raw['datetime'] = pd.to_datetime(raw['date'], errors='coerce')
        raw = raw.set_index('datetime')
        df = pd.DataFrame(index=raw.index)
        for source, target in (('open', 'Open'), ('high', 'High'), ('low', 'Low'),
                               ('close', 'Close'), ('volume', 'Volume')):
            df[target] = pd.to_numeric(raw[source], errors='coerce')
        df = df.dropna(subset=['Open', 'High', 'Low', 'Close']).sort_index()
        return df, stock_name
    finally:
        bs.logout()


def _parse_eastmoney_payload(data: dict, code: str, period_key: str,
                             beg: str, end: str) -> Tuple[pd.DataFrame, str]:
    """解析东财K线返回体（不同主机共用）。返回 (df, 股票名称)。"""
    name = data.get('name', '')
    klines = data.get('klines') or []
    if not klines:
        raise ValueError(f'东财接口无数据（代码 {code}，周期 {period_key}，'
                         f'区间 {beg} ~ {end}）。1分钟数据仅保留近5个交易日。')

    if len(klines) >= 1000000:
        logger.warning('返回条数达到上限，数据可能被截断')

    # 每根K线: datetime, open, close, high, low, volume, amount, pct
    rows = [k.split(',') for k in klines]
    df = pd.DataFrame(rows, columns=['datetime', 'open', 'close', 'high',
                                     'low', 'volume', 'amount', 'pct'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime')
    for col in ['open', 'close', 'high', 'low']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
    df = df.rename(columns={
        'open': 'Open', 'close': 'Close', 'high': 'High',
        'low': 'Low', 'volume': 'Volume',
    })
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
    return df, name


def fetch_kline_eastmoney(code: str, period_key: str, beg: str, end: str,
                          fqt: int = 1) -> Tuple[pd.DataFrame, str]:
    """从东方财富下载K线（多主机轮换）。

    返回 (df, 股票名称)。df 列为 Open/High/Low/Close/Volume，DatetimeIndex 索引。
    """
    klt = PERIOD_KLT_MAP[period_key]
    beg_ymd = pd.Timestamp(beg).strftime('%Y%m%d')
    end_ymd = pd.Timestamp(end).strftime('%Y%m%d')
    query = (
        f'secid={code_to_secid(code)}&klt={klt}&fqt={fqt}'
        f'&beg={beg_ymd}&end={end_ymd}'
        '&fields1=f1,f2,f3,f4,f5,f6'
        '&fields2=f51,f52,f53,f54,f55,f56,f57,f58'
        '&lmt=1000000'
    )
    last_err = None
    for host in _EM_KLINE_HOSTS:
        url = f'https://{host}/api/qt/stock/kline/get?{query}'
        try:
            resp = _request_with_retry(url, attempts=2)
            payload = resp.json()
        except Exception as e:
            logger.warning(f'东财主机 {host} 请求失败：{e}')
            last_err = e
        else:
            if payload.get('rc') != 0 or payload.get('data') is None:
                last_err = ValueError(f'东财接口返回异常（{host}）：{payload}')
                logger.warning(str(last_err))
            else:
                try:
                    return _parse_eastmoney_payload(
                        payload['data'], code, period_key, beg, end)
                except ValueError as e:
                    last_err = e
                    logger.warning(f'东财主机 {host} 返回无数据：{e}')
        # 快速换下一主机，避免在同一主机上连续重试触发WAF封禁
        time.sleep(1)
    raise ValueError(f'东财全部主机均失败（{", ".join(_EM_KLINE_HOSTS)}）：{last_err}')


def fetch_kline_tencent(code: str, period_key: str) -> Tuple[pd.DataFrame, str]:
    """从腾讯接口下载K线（兜底源，仅返回最近约320根，1分钟约1.3个交易日）。"""
    symbol = code_to_tencent_symbol(code)
    tperiod = PERIOD_TENCENT_MAP[period_key]
    url = f'https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},{tperiod},,320'
    resp = _request_with_retry(url)
    payload = resp.json()
    if payload.get('code') != 0:
        raise ValueError(f'腾讯接口返回异常：{payload}')

    data = payload.get('data', {})
    symbol_data = data.get(symbol) or {}
    bars = symbol_data.get(tperiod) or symbol_data.get('qfq' + tperiod) or []
    if not bars:
        raise ValueError(f'腾讯接口无数据（代码 {code}，周期 {period_key}）')

    qt = (symbol_data.get('qt') or {}).get(symbol) or []
    name = qt[1] if isinstance(qt, list) and len(qt) > 1 else ''
    # 每根K线: yyyyMMddHHmm, open, close, high, low, volume, {}, pct
    rows = []
    for b in bars:
        try:
            rows.append({
                'datetime': pd.to_datetime(str(b[0]), format='%Y%m%d%H%M'),
                'Open': float(b[1]), 'Close': float(b[2]),
                'High': float(b[3]), 'Low': float(b[4]),
                'Volume': float(b[5]),
            })
        except (ValueError, IndexError, TypeError):
            continue
    if not rows:
        raise ValueError(f'腾讯接口数据解析失败（代码 {code}）')

    df = pd.DataFrame(rows).set_index('datetime')
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
    return df, name


def fetch_kline_sina(code: str, period_key: str) -> Tuple[pd.DataFrame, str]:
    """从新浪接口下载K线（兜底源，最多约1023根：
    5分钟约21个交易日，60分钟约1年，日线约4年）。"""
    symbol = code_to_tencent_symbol(code)
    scale = PERIOD_SINA_MAP[period_key]
    url = (f'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/'
           f'CN_MarketDataService.getKLineData?symbol={symbol}'
           f'&scale={scale}&ma=no&datalen=1023')
    resp = _request_with_retry(url)
    text = resp.text
    start, end = text.find('('), text.rfind(')')
    if start < 0 or end <= start:
        raise ValueError(f'新浪接口响应格式异常（代码 {code}）')
    try:
        bars = json.loads(text[start + 1:end])
    except json.JSONDecodeError as e:
        raise ValueError(f'新浪接口JSON解析失败（代码 {code}）：{e}')
    if not bars:
        raise ValueError(f'新浪接口无数据（代码 {code}，周期 {period_key}）')

    rows = []
    for b in bars:
        try:
            rows.append({
                'datetime': pd.to_datetime(b['day']),
                'Open': float(b['open']), 'High': float(b['high']),
                'Low': float(b['low']), 'Close': float(b['close']),
                'Volume': float(b['volume']),
            })
        except (ValueError, KeyError, TypeError):
            continue
    if not rows:
        raise ValueError(f'新浪接口数据解析失败（代码 {code}）')

    df = pd.DataFrame(rows).set_index('datetime')
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
    # 新浪无股票名，留空由调用方处理
    return df, ''


def download_kline(code: str, period_key: str, beg: str, end: str,
                   fqt: int = 1, required_range=None) -> Tuple[pd.DataFrame, str, str]:
    """统一下载入口：东财主源，失败依次走新浪、腾讯兜底。

    返回 (df, 股票名称, 数据来源)。成功后保存 CSV 到 下载数据/ 目录供离线复用。

    注：各源成交量单位可能不同（手/股），但不影响使用——均价线
    计算 cumsum(close*volume)/cumsum(volume) 对常数因子不敏感。
    """
    if period_key not in PERIOD_KLT_MAP:
        raise ValueError(f'不支持的周期：{period_key}')

    errors = []
    df = name = source = None
    providers = []
    if period_key in BAOSTOCK_PERIODS:
        providers.append(('BaoStock', lambda: fetch_kline_baostock(code, period_key, beg, end, fqt)))
    providers.extend([
        ('东方财富', lambda: fetch_kline_eastmoney(code, period_key, beg, end, fqt)),
        ('新浪（兜底）', lambda: fetch_kline_sina(code, period_key)),
        ('腾讯（兜底）', lambda: fetch_kline_tencent(code, period_key))])
    for provider, fetch in providers:
        try:
            candidate, candidate_name = fetch()
            if candidate is None or candidate.empty:
                raise ValueError('返回行情为空')
            if required_range is not None:
                from training_data import range_covered
                if not range_covered(candidate, *required_range, period_key):
                    raise ValueError(f'未覆盖所选区间，实际 {candidate.index[0]} ~ {candidate.index[-1]}')
            df, name, source = candidate, candidate_name, provider
            break
        except Exception as e:
            errors.append(f'{provider}: {e}')
            logger.warning(f'{provider} 不可用或范围不足，尝试下一源：{e}')
    if df is None:
        raise ConnectionError('所有免费数据源均不可用或无法覆盖所选区间：\n' + '\n'.join(errors))

    # 部分兜底接口只返回最近 N 根，仍严格按用户区间过滤，避免界面显示错误范围。
    begin_ts = pd.Timestamp(beg).normalize()
    end_ts = pd.Timestamp(end).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    df = df[(df.index >= begin_ts) & (df.index <= end_ts)]
    if df.empty:
        detail = '；'.join(errors) if errors else '数据源返回范围与所选日期不相交'
        raise ValueError(f'所选区间没有可用行情（{beg} ~ {end}）。{detail}')
    if source in ('新浪（兜底）', '腾讯（兜底）') and period_key in MINUTE_PERIODS:
        source += '，历史条数有限'

    if df is None or df.empty:
        raise ValueError(f'下载结果为空（代码 {code}，周期 {period_key}）')

    # Range-specific caches preserve older downloads instead of overwriting
    # longer history with a short request. Do not merge different adjustment or
    # provider conventions blindly.
    try:
        out_dir = str(download_dir())
        os.makedirs(out_dir, exist_ok=True)
        begin_label = pd.Timestamp(beg).strftime('%Y%m%d')
        end_label = pd.Timestamp(end).strftime('%Y%m%d')
        provider_label = ('bs' if source == 'BaoStock' else 'em' if source == '东方财富'
                          else 'sina' if source.startswith('新浪') else 'tencent')
        out_file = os.path.join(out_dir, f'{code}_{period_key}_{begin_label}_{end_label}_fqt{fqt}_{provider_label}.csv')
        if os.path.exists(out_file):
            out_file = os.path.splitext(out_file)[0] + f'_v{time.time_ns()}.csv'
        df.to_csv(out_file, encoding='utf-8-sig')
        logger.info(f'已保存 {out_file}（{len(df)} 条）')
    except Exception as e:
        logger.warning(f'保存CSV失败（不影响使用）：{e}')

    return df, name, source
