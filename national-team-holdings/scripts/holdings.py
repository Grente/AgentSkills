#!/usr/bin/env python3
"""国家队持仓代理指标（非官方）。

代理占比 = Σ(四只沪深300 ETF 份额 × 单位净值) / 沪深300流通市值 × 100%

这是可复用的命令行实现，不是看板服务。公开数据失败时会标明数据源与原因，
不会编造数字。
"""
from __future__ import annotations

import argparse
import csv
import html as html_lib
import io
import json
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

DISCLAIMER = "代理指标，非官方国家队/汇金持仓。"
TZ = ZoneInfo("Asia/Shanghai")
SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = SKILL_ROOT / "data_cache"

ETF_LIST = (
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "market": "SH"},
    {"code": "510330", "name": "华夏沪深300ETF", "market": "SH"},
    {"code": "159919", "name": "嘉实沪深300ETF", "market": "SZ"},
    {"code": "510310", "name": "易方达沪深300ETF", "market": "SH"},
)
ETF_CODES = [e["code"] for e in ETF_LIST]
SH_CODES = [e["code"] for e in ETF_LIST if e["market"] == "SH"]
SZ_CODES = [e["code"] for e in ETF_LIST if e["market"] == "SZ"]

SSE_SQL_IDS = (
    "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
    "COMMON_SSE_ZQPZ_ETFGM_SEARCH_L_NEW",
    "COMMON_SSE_ZQPZ_ETFGM_SEARCH_L",
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

PERIOD_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 365}
PERIOD_LABELS = {
    "1m": "最近 1 个月",
    "3m": "最近 3 个月",
    "6m": "最近半年",
    "1y": "最近 1 年",
    "ytd": "年初至今",
    "custom": "自定义起止日",
}
LOOKBACK_DAYS = 21  # 窗口起点之前预留份额，供前向填充后再裁剪


class DataSourceError(RuntimeError):
    """某个公开数据源失败。"""

    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"[{source}] {message}")


# --------------- 纯计算（可供单测，不访问网络） ---------------
def sse_tot_vol_to_shares(tot_vol_wan: float) -> float:
    """上交所 TOT_VOL 为万份 → 份。"""
    return float(tot_vol_wan) * 10_000.0


def etf_market_value(shares: float, unit_nav: float) -> float:
    """市值（元）= 份额(份) × 单位净值(元/份)。不用累计净值。"""
    return float(shares) * float(unit_nav)


def proxy_ratio(sum_mv: float, circ_mv: float) -> float:
    if circ_mv <= 0:
        raise ValueError("沪深300流通市值必须为正")
    return float(sum_mv) / float(circ_mv) * 100.0


def scale_circ_mv(circ_latest: float, close_t: float, close_latest: float) -> float:
    """历史流通市值按点位缩放的近似。"""
    if close_latest <= 0:
        raise ValueError("最新收盘价必须为正")
    return float(circ_latest) * (float(close_t) / float(close_latest))


def today_sh() -> date:
    return datetime.now(TZ).date()


def parse_ymd(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def resolve_window(
    period: str | None,
    start: str | None,
    end: str | None,
) -> tuple[date, date, str]:
    """返回 (start, end, preset)。传入 --start 视为自定义窗口。"""
    end_d = parse_ymd(end) if end else today_sh()
    if end_d > today_sh():
        end_d = today_sh()
    if start:
        start_d = parse_ymd(start)
        preset = "custom"
    elif period == "ytd":
        start_d = date(end_d.year, 1, 1)
        preset = "ytd"
    else:
        preset = period or "1m"
        days = PERIOD_DAYS.get(preset, 31)
        start_d = end_d - timedelta(days=days)
    if start_d > end_d:
        raise ValueError(f"起始日 {start_d} 晚于结束日 {end_d}")
    return start_d, end_d, preset


def crop_series(series: list[dict], start: date | str, end: date | str) -> list[dict]:
    """按所选时间窗口裁剪序列（含端点）。摘要高低、CSV/JSON、可选网页都只用窗口内的点。"""
    s = start.isoformat() if isinstance(start, date) else str(start)
    e = end.isoformat() if isinstance(end, date) else str(end)
    return [p for p in series if s <= p["date"] <= e]


# --------------- HTTP ---------------
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def http_get(
    url: str,
    *,
    source: str,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: int = 18,
    retries: int = 3,
) -> requests.Response:
    last: Exception | None = None
    sess = _session()
    for i in range(retries):
        try:
            r = sess.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(0.6 * (i + 1) + random.random() * 0.3)
    raise DataSourceError(source, f"请求失败 {url}: {last}")


def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def _read_json_cache(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _cache_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < max_age_hours * 3600


# --------------- 净值 ---------------
def fetch_nav_history(code: str, pages: int, cache: Path, quiet: bool) -> list[dict]:
    rows: list[dict] = []
    headers = {"Referer": "https://fund.eastmoney.com/", "User-Agent": UA}
    for page in range(1, pages + 1):
        r = http_get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            source=f"东财净值/{code}",
            headers=headers,
            params={"fundCode": code, "pageIndex": page, "pageSize": 20},
            timeout=20,
        )
        try:
            payload = r.json()
        except Exception as e:
            raise DataSourceError(f"东财净值/{code}", f"JSON 解析失败: {e}") from e
        lst = (payload.get("Data") or {}).get("LSJZList") or []
        if not lst:
            break
        for x in lst:
            dwjz = x.get("DWJZ")
            ljjz = x.get("LJJZ")
            rows.append(
                {
                    "date": x["FSRQ"],
                    "dwjz": float(dwjz) if dwjz not in (None, "") else None,
                    "ljjz": float(ljjz) if ljjz not in (None, "") else None,
                }
            )
        time.sleep(0.15)
    if not rows:
        raise DataSourceError(f"东财净值/{code}", "未返回任何净值记录")
    rows.sort(key=lambda x: x["date"])
    dedup: dict[str, dict] = {}
    for row in rows:
        dedup[row["date"]] = row
    out = [dedup[k] for k in sorted(dedup)]
    _write_json_cache(cache / f"nav_{code}.json", out)
    log(f"[NAV] {code} {len(out)} 条", quiet)
    return out


def load_nav(code: str, pages: int, cache: Path, max_age_hours: float, quiet: bool) -> list[dict]:
    path = cache / f"nav_{code}.json"
    if _cache_fresh(path, max_age_hours):
        data = _read_json_cache(path)
        if data:
            log(f"[NAV] {code} 使用缓存 {len(data)} 条", quiet)
            return data
    return fetch_nav_history(code, pages, cache, quiet)


def nav_asof(rows: list[dict], day: str) -> dict | None:
    last = None
    for row in rows:
        if row["date"] <= day and row.get("dwjz") not in (None,):
            last = row
        elif row["date"] > day:
            break
    return last


# --------------- 上交所份额 ---------------
def _parse_sse_row(row: dict) -> tuple[str, float] | None:
    code = str(row.get("SEC_CODE") or row.get("FUND_CODE") or "").strip()
    if code not in SH_CODES:
        return None
    raw = row.get("TOT_VOL") if row.get("TOT_VOL") not in (None, "") else row.get("TOTVOL")
    if raw in (None, ""):
        return None
    tot = float(str(raw).replace(",", ""))
    return code, sse_tot_vol_to_shares(tot)


def fetch_sse_shares_one_day(date_str: str) -> dict[str, float]:
    url = "https://query.sse.com.cn/commonQuery.do"
    headers = {"Referer": "https://www.sse.com.cn/", "User-Agent": UA}
    last_err: Exception | None = None
    for sql_id in SSE_SQL_IDS:
        params = {
            "isPagination": "true",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": sql_id,
            "STAT_DATE": date_str,
        }
        try:
            r = http_get(url, source="上交所份额", headers=headers, params=params, timeout=20, retries=2)
            data = r.json().get("result") or []
        except Exception as e:
            last_err = e
            continue
        out: dict[str, float] = {}
        for row in data:
            parsed = _parse_sse_row(row)
            if parsed:
                out[parsed[0]] = parsed[1]
        if out:
            return out
    if last_err:
        raise DataSourceError("上交所份额", f"{date_str} 失败: {last_err}")
    return {}


def fetch_sse_shares_range(
    dates: list[str], cache: Path, quiet: bool
) -> tuple[dict[str, dict[str, float]], int, int]:
    cache_file = cache / "sse_shares.json"
    cached: dict = _read_json_cache(cache_file) or {}
    result: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    for d in dates:
        if d in cached and cached[d]:
            result[d] = cached[d]
        else:
            missing.append(d)

    failed = 0
    lock = threading.Lock()

    def _save() -> None:
        _write_json_cache(cache_file, cached)

    def _one(d: str) -> tuple[str, dict[str, float] | None, Exception | None]:
        try:
            return d, fetch_sse_shares_one_day(d), None
        except Exception as e:
            return d, None, e

    if missing:
        log(f"[SSE] 拉取 {len(missing)} 个缺失日期…", quiet)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(_one, d) for d in missing]
            for fut in as_completed(futs):
                d, day, err = fut.result()
                if day:
                    with lock:
                        result[d] = day
                        cached[d] = day
                        _save()
                    log(f"[SSE] {d} ok", quiet)
                else:
                    failed += 1
                    log(f"[SSE] {d} 失败: {err}", quiet)
                time.sleep(0.05)
        _save()
    return result, len(missing), failed


# --------------- 深交所份额 ---------------
def _szse_col(headers: list[str], *names: str) -> int | None:
    for n in names:
        if n in headers:
            return headers.index(n)
    for i, h in enumerate(headers):
        for n in names:
            if n and n in h:
                return i
    return None


def fetch_szse_shares(start: str, end: str, cache: Path, quiet: bool) -> dict[str, dict[str, float]]:
    cache_file = cache / "szse_shares.json"
    cached: dict = _read_json_cache(cache_file) or {}
    url = "https://www.szse.cn/api/report/ShowReport"
    headers = {
        "Host": "www.szse.cn",
        "Referer": "https://www.szse.cn/market/fund/volume/etf/index.html",
        "User-Agent": UA,
    }
    params = {
        "SHOWTYPE": "xlsx",
        "CATALOGID": "scsj_fund_jjgm",
        "TABKEY": "tab1",
        "txtStart": start,
        "txtEnd": end,
        "jjlb": "ETF",
        "random": str(random.random()),
    }
    r = http_get(url, source="深交所份额", headers=headers, params=params, timeout=90, retries=6)
    if not r.content.startswith(b"PK"):
        snippet = r.text[:180].replace("\n", " ")
        raise DataSourceError(
            "深交所份额",
            f"{start}~{end} 返回的不是 Excel，可能被拦截或日期无效。片段: {snippet}",
        )
    try:
        import warnings

        from openpyxl import load_workbook
    except ImportError as e:
        raise DataSourceError("深交所份额", "需要 openpyxl 解析 xlsx：pip install openpyxl") from e

    # SZSE 的 xlsx 在 read_only 模式下表头常只剩「日期」一列，必须完整加载。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(io.BytesIO(r.content), read_only=False, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if not header_row:
        raise DataSourceError("深交所份额", "Excel 为空")
    headers_s = [str(c).strip() if c is not None else "" for c in header_row]
    i_date = _szse_col(headers_s, "日期", "交易日期")
    i_code = _szse_col(headers_s, "基金代码", "证券代码")
    i_shares = _szse_col(headers_s, "基金规模(份)", "基金份额", "基金规模")
    if i_date is None or i_code is None or i_shares is None:
        raise DataSourceError("深交所份额", f"无法识别列名: {headers_s}")

    out: dict[str, dict[str, float]] = {k: dict(v) for k, v in cached.items()}
    n_hit = 0
    for row in rows_iter:
        if not row:
            continue
        code = str(row[i_code] or "").strip()
        if code not in SZ_CODES:
            continue
        d = str(row[i_date] or "").strip()[:10]
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        raw = str(row[i_shares] or "0").replace(",", "").replace(" ", "")
        try:
            shares = float(raw)
        except ValueError:
            continue
        out.setdefault(d, {})
        out[d][code] = shares
        cached.setdefault(d, {})
        cached[d][code] = shares
        n_hit += 1
    wb.close()
    _write_json_cache(cache_file, cached)
    log(f"[SZSE] {start}~{end} 命中 {n_hit} 行", quiet)
    return out


def _daterange_chunks(start: date, end: date, months: int = 5):
    cur = datetime(start.year, start.month, start.day)
    last = datetime(end.year, end.month, end.day)
    while cur <= last:
        nxt = min(cur + timedelta(days=months * 30), last)
        yield cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        cur = nxt + timedelta(days=1)


# --------------- 沪深300 ---------------
def fetch_csi300_klines(start: date, end: date, quiet: bool) -> list[dict]:
    beg_dash = start.isoformat()
    end_dash = end.isoformat()
    span = (end - start).days + 10
    datalen = min(2000, max(80, int(span * 0.8) + 20))
    errors: list[str] = []

    try:
        url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol=sh000300&scale=240&ma=no&datalen={datalen}"
        )
        r = http_get(
            url,
            source="新浪K线",
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": UA},
            timeout=20,
            retries=2,
        )
        data = r.json()
        rows = [
            {"date": x["day"][:10], "close": float(x["close"])}
            for x in data
            if beg_dash <= x["day"][:10] <= end_dash
        ]
        if rows:
            log(f"[K] 新浪 {len(rows)} 条", quiet)
            return rows
        errors.append("新浪返回空")
    except Exception as e:
        errors.append(f"新浪: {e}")

    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000300,day,,,{datalen},qfq"
        r = http_get(
            url,
            source="腾讯K线",
            headers={"Referer": "https://finance.qq.com/", "User-Agent": UA},
            timeout=20,
            retries=2,
        )
        days = r.json()["data"]["sh000300"]["day"]
        rows = [
            {"date": x[0][:10], "close": float(x[2])}
            for x in days
            if beg_dash <= x[0][:10] <= end_dash
        ]
        if rows:
            log(f"[K] 腾讯 {len(rows)} 条", quiet)
            return rows
        errors.append("腾讯返回空")
    except Exception as e:
        errors.append(f"腾讯: {e}")

    try:
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": "1.000300",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        r = http_get(
            url,
            source="东财K线",
            headers={"Referer": "https://quote.eastmoney.com/", "User-Agent": UA},
            timeout=25,
            retries=2,
        )
        klines = (r.json().get("data") or {}).get("klines") or []
        rows = []
        for line in klines:
            parts = line.split(",")
            d = parts[0][:10]
            if beg_dash <= d <= end_dash:
                rows.append({"date": d, "close": float(parts[2])})
        if rows:
            log(f"[K] 东财 {len(rows)} 条", quiet)
            return rows
        errors.append("东财返回空")
    except Exception as e:
        errors.append(f"东财: {e}")

    raise DataSourceError("沪深300K线", "全部备用源失败: " + " | ".join(errors))


def fetch_csi300_circ_mv_latest() -> tuple[float, float, str]:
    errors: list[str] = []
    try:
        r = http_get(
            "https://qt.gtimg.cn/q=sh000300",
            source="腾讯流通市值",
            headers={"Referer": "https://finance.qq.com/", "User-Agent": UA},
            timeout=12,
            retries=2,
        )
        parts = r.text.split('"')[1].split("~")
        circ = float(parts[44]) * 1e8
        total = float(parts[45]) * 1e8
        if circ > 0:
            return circ, total, "tencent_gtimg(流通市值/总市值·亿元)"
        errors.append("腾讯流通市值为 0")
    except Exception as e:
        errors.append(f"腾讯: {e}")

    try:
        url = (
            "https://push2.eastmoney.com/api/qt/stock/get"
            "?secid=1.000300&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2"
            "&fields=f57,f58,f43,f116,f117"
        )
        r = http_get(
            url,
            source="东财流通市值",
            headers={"Referer": "https://quote.eastmoney.com/", "User-Agent": UA},
            timeout=12,
            retries=2,
        )
        d = r.json().get("data") or {}
        circ = float(d["f117"])
        total = float(d["f116"])
        if circ > 0:
            return circ, total, "eastmoney_push2(f117流通/f116总)"
        errors.append("东财流通市值为 0")
    except Exception as e:
        errors.append(f"东财: {e}")

    raise DataSourceError("沪深300流通市值", "全部备用源失败: " + " | ".join(errors))


# --------------- 组装序列 ---------------
def sse_sample_dates(trading_days: list[str]) -> list[str]:
    """短区间逐日；长区间双周采样 + 近 15 个交易日（与原实现一致）。"""
    if len(trading_days) <= 45:
        return list(trading_days)
    recent = trading_days[-15:]
    biweekly = trading_days[::10]
    return sorted(set(biweekly + recent))


def build_history(
    start: date,
    end: date,
    cache: Path,
    *,
    refresh: bool,
    quiet: bool,
    period_label: str = "custom",
) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    lookback_start = start - timedelta(days=LOOKBACK_DAYS)
    nav_age = 0.0 if refresh else 6.0
    pages = min(60, max(4, (end - lookback_start).days // 20 + 4))

    log("[build] 沪深300 K 线…", quiet)
    klines = fetch_csi300_klines(lookback_start, end, quiet)
    if not klines:
        raise DataSourceError("沪深300K线", "区间内没有交易日")
    trading_days = [x["date"] for x in klines]
    close_by_date = {x["date"]: float(x["close"]) for x in klines}

    log("[build] 基金净值…", quiet)
    navs = {c: load_nav(c, pages, cache, nav_age, quiet) for c in ETF_CODES}

    log("[build] 深交所份额…", quiet)
    szse: dict[str, dict[str, float]] = {}
    szse_fail = 0
    for a, b in _daterange_chunks(lookback_start, end, months=1):
        try:
            part = fetch_szse_shares(a, b, cache, quiet)
            for d, m in part.items():
                szse.setdefault(d, {}).update(m)
        except Exception as e:
            szse_fail += 1
            log(f"[SZSE] {a}~{b} 失败: {e}", quiet)
        time.sleep(0.3)

    sse_dates = sse_sample_dates(trading_days)
    log(f"[build] 上交所份额 {len(sse_dates)}/{len(trading_days)} 日…", quiet)
    sse, sse_fetched, sse_failed = fetch_sse_shares_range(sse_dates, cache, quiet)

    log("[build] 最新流通市值…", quiet)
    circ_latest, total_latest, mv_src = fetch_csi300_circ_mv_latest()
    close_latest = float(klines[-1]["close"])
    if end < today_sh() - timedelta(days=2):
        try:
            now_k = fetch_csi300_klines(today_sh() - timedelta(days=14), today_sh(), quiet)
            if now_k:
                close_latest = float(now_k[-1]["close"])
        except Exception as e:
            log(f"[K] 最新收盘回退为窗口末日: {e}", quiet)

    series: list[dict] = []
    last_shares: dict[str, float] = {}
    last_share_dates: dict[str, str] = {}
    skipped_incomplete = 0
    last_missing: list[str] = []

    for d in trading_days:
        close = close_by_date[d]
        circ_mv = scale_circ_mv(circ_latest, close, close_latest)
        fresh: dict[str, float] = {}
        fresh.update(sse.get(d) or {})
        fresh.update(szse.get(d) or {})
        for c, v in fresh.items():
            last_shares[c] = v
            last_share_dates[c] = d
        if any(c not in last_shares for c in ETF_CODES):
            skipped_incomplete += 1
            last_missing = [c for c in ETF_CODES if c not in last_shares]
            continue

        etf_rows = []
        sum_mv = 0.0
        ok = True
        for meta in ETF_LIST:
            code = meta["code"]
            nav_row = nav_asof(navs[code], d)
            if not nav_row or nav_row.get("dwjz") in (None,):
                ok = False
                last_missing = [code]
                break
            dwjz = float(nav_row["dwjz"])
            ljjz = nav_row.get("ljjz")
            shares = float(last_shares[code])
            mv = etf_market_value(shares, dwjz)
            sum_mv += mv
            etf_rows.append(
                {
                    "code": code,
                    "name": meta["name"],
                    "shares": shares,
                    "shares_yi": shares / 1e8,
                    "dwjz": dwjz,
                    "ljjz": float(ljjz) if ljjz not in (None, "") else None,
                    "market_value": mv,
                    "market_value_yi": mv / 1e8,
                    "nav_date": nav_row["date"],
                    "shares_date": last_share_dates.get(code, d),
                }
            )
        if not ok:
            skipped_incomplete += 1
            continue

        ratio = proxy_ratio(sum_mv, circ_mv)
        series.append(
            {
                "date": d,
                "ratio": round(ratio, 4),
                "etf_mv_sum": sum_mv,
                "etf_mv_sum_yi": round(sum_mv / 1e8, 2),
                "csi300_circ_mv": circ_mv,
                "csi300_circ_mv_yi": round(circ_mv / 1e8, 2),
                "csi300_close": close,
                "etfs": {e["code"]: e for e in etf_rows},
            }
        )

    if not series:
        hint = ""
        if last_missing:
            hint = f" 缺份额或净值的代码: {','.join(last_missing)}。"
        raise DataSourceError(
            "序列组装",
            "历史序列为空，请检查份额/净值接口。" + hint,
        )

    series = crop_series(series, start, end)
    if not series:
        raise DataSourceError("序列组装", f"窗口 {start}~{end} 裁剪后没有数据点")

    high = max(series, key=lambda p: p["ratio"])
    low = min(series, key=lambda p: p["ratio"])
    latest = series[-1]

    warnings: list[str] = []
    if sse_failed:
        warnings.append(f"上交所 {sse_failed} 个采样日拉取失败，对应日期已跳过或用前值填充。")
    if szse_fail:
        warnings.append(f"深交所 {szse_fail} 个日期块下载失败，159919 可能前向填充。")
    if skipped_incomplete:
        warnings.append(f"有 {skipped_incomplete} 个交易日因四只 ETF 数据不齐被跳过。")
    warnings.append(
        "历史流通市值按当日收盘 / 最新收盘等比缩放，成分调整后为趋势近似，最新点相对可靠。"
    )

    summary = {
        "ok": True,
        "disclaimer": DISCLAIMER,
        "ratio": latest["ratio"],
        "data_date": latest["date"],
        "generated_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai (UTC+8)",
        "period": {
            "preset": period_label,
            "label": PERIOD_LABELS.get(period_label, period_label),
            "start": series[0]["date"],
            "end": latest["date"],
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "points": len(series),
        },
        "etf_mv_sum_yi": latest["etf_mv_sum_yi"],
        "csi300_circ_mv_yi": latest["csi300_circ_mv_yi"],
        "csi300_total_mv_yi": round(total_latest / 1e8, 2),
        "csi300_close": latest["csi300_close"],
        "range": {
            "high": {"date": high["date"], "ratio": high["ratio"]},
            "low": {"date": low["date"], "ratio": low["ratio"]},
        },
        "circ_mv_source": mv_src,
        "circ_mv_note": (
            "最新流通市值取自行情接口；历史流通市值按当日收盘价相对最新收盘价等比缩放近似。"
        ),
        "formula": (
            "代理占比 = Σ(基金份额 × 单位净值) / 沪深300流通市值 × 100%；"
            "四只ETF：510300/510330/159919/510310"
        ),
        "nav_note": (
            "市值采用单位净值（DWJZ）以与东财基金规模一致；"
            "累计净值（LJJZ）仅供对照，因历史拆分/合并往往偏低。"
        ),
        "unit_note": "上交所 TOT_VOL 为万份，已 ×10000 转为份；深交所报表已是份。",
        "etfs": list(latest["etfs"].values()),
        "warnings": warnings,
        "endpoints": {
            "nav": "https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}",
            "sse_shares": (
                "https://query.sse.com.cn/commonQuery.do "
                "sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L (TOT_VOL 万份×10000)"
            ),
            "szse_shares": "https://www.szse.cn/api/report/ShowReport CATALOGID=scsj_fund_jjgm (份)",
            "csi300_kline": "sina / tencent / eastmoney push2his secid=1.000300",
            "csi300_mv": "qt.gtimg.cn/q=sh000300 或 eastmoney f117/f116",
        },
    }
    return {"summary": summary, "series": series}


def slim_series(series: list[dict]) -> list[dict]:
    out = []
    for p in series:
        row = {
            "date": p["date"],
            "ratio": p["ratio"],
            "etf_mv_sum_yi": p["etf_mv_sum_yi"],
            "csi300_circ_mv_yi": p["csi300_circ_mv_yi"],
            "csi300_close": p["csi300_close"],
        }
        for code in ETF_CODES:
            etf = p["etfs"][code]
            row[f"mv_{code}_yi"] = round(etf["market_value_yi"], 2)
        out.append(row)
    return out


# --------------- 导出 / 绘图 ---------------
def write_csv(path: Path, series: list[dict]) -> None:
    rows = slim_series(series)
    fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_html(path: Path, summary: dict, series: list[dict]) -> None:
    """可选网页：数据表 + 窗口内折线。默认路径不会调用本函数。"""
    windowed = crop_series(
        series,
        summary["period"]["requested_start"],
        summary["period"]["requested_end"],
    )
    payload = build_export_payload(summary, windowed)
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    esc = html_lib.escape
    etf_rows = []
    for e in summary["etfs"]:
        etf_rows.append(
            "<tr>"
            f"<td>{esc(e['code'])}</td><td>{esc(e['name'])}</td>"
            f"<td>{e['shares_yi']:.2f}</td><td>{e['dwjz']}</td>"
            f"<td>{e['market_value_yi']:.2f}</td>"
            f"<td>{esc(str(e['nav_date']))}</td><td>{esc(str(e['shares_date']))}</td>"
            "</tr>"
        )
    warn_html = "".join(f"<li>{esc(w)}</li>" for w in summary.get("warnings") or [])
    label = esc(str(summary["period"].get("label") or ""))
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>国家队持仓代理指标（非官方）</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1a1a1a; max-width: 960px; }}
    .banner {{ background: #fff3cd; border: 1px solid #e0c36a; padding: 12px 16px; border-radius: 8px; font-weight: 600; }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 16px 0; }}
    .kpi {{ background: #f6f8fb; padding: 12px; border-radius: 8px; }}
    .kpi b {{ display: block; font-size: 1.25rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; }}
    footer {{ color: #666; font-size: 13px; margin-top: 24px; }}
  </style>
</head>
<body>
  <p class="banner">{esc(DISCLAIMER)}</p>
  <h1>国家队持仓代理统计</h1>
  <p>窗口：{label} {esc(summary['period']['start'])} ~ {esc(summary['period']['end'])}
     （{summary['period']['points']} 个交易日）</p>
  <div class="kpis">
    <div class="kpi">最新占比<b>{summary['ratio']:.4f}%</b>{esc(summary['data_date'])}</div>
    <div class="kpi">区间最高<b>{summary['range']['high']['ratio']:.4f}%</b>{esc(summary['range']['high']['date'])}</div>
    <div class="kpi">区间最低<b>{summary['range']['low']['ratio']:.4f}%</b>{esc(summary['range']['low']['date'])}</div>
    <div class="kpi">ETF 合计市值<b>{summary['etf_mv_sum_yi']:.2f} 亿</b></div>
    <div class="kpi">沪深300流通市值<b>{summary['csi300_circ_mv_yi']:.2f} 亿</b>点位缩放近似</div>
  </div>
  <canvas id="chart" height="110"></canvas>
  <h2>分项（份额 × 单位净值）</h2>
  <table>
    <thead><tr><th>代码</th><th>名称</th><th>份额(亿份)</th><th>单位净值</th><th>市值(亿)</th><th>净值日</th><th>份额日</th></tr></thead>
    <tbody>
      {''.join(etf_rows)}
    </tbody>
  </table>
  <h2>说明</h2>
  <ul>{warn_html}</ul>
  <footer>默认技能只输出数据；本页仅在传入 --html / --web 时生成。公式：{esc(summary.get('formula') or '')}</footer>
  <script type="application/json" id="payload">{data_json}</script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
    const payload = JSON.parse(document.getElementById('payload').textContent);
    const s = payload.series || [];
    const ctx = document.getElementById('chart');
    if (window.Chart && s.length) {{
      new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: s.map(p => p.date),
          datasets: [{{
            label: '代理占比 (%)',
            data: s.map(p => p.ratio),
            borderColor: '#1f4e79',
            tension: 0.15,
            pointRadius: 0
          }}]
        }},
        options: {{
          plugins: {{ legend: {{ display: true }} }},
          scales: {{ y: {{ title: {{ display: true, text: '%' }} }} }}
        }}
      }});
    }}
  </script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def format_text(summary: dict) -> str:
    lines = [
        f"【免责声明】{DISCLAIMER}",
        "",
        f"区间: {summary['period'].get('label', '')}  "
        f"{summary['period']['start']} ~ {summary['period']['end']}  "
        f"（{summary['period']['points']} 个交易日）",
        f"最新数据日: {summary['data_date']}",
        f"最新代理占比: {summary['ratio']:.4f}%",
        f"区间最高: {summary['range']['high']['ratio']:.4f}%（{summary['range']['high']['date']}）",
        f"区间最低: {summary['range']['low']['ratio']:.4f}%（{summary['range']['low']['date']}）",
        f"四只 ETF 合计市值: {summary['etf_mv_sum_yi']:.2f} 亿元",
        f"沪深300流通市值: {summary['csi300_circ_mv_yi']:.2f} 亿元（历史点为点位缩放近似）",
        f"沪深300收盘: {summary['csi300_close']}",
        f"流通市值来源: {summary['circ_mv_source']}",
        "",
        "分项（份额×单位净值）:",
    ]
    for e in summary["etfs"]:
        lines.append(
            f"  {e['code']} {e['name']}: 份额 {e['shares_yi']:.2f} 亿份 × "
            f"单位净值 {e['dwjz']} → 市值 {e['market_value_yi']:.2f} 亿"
            f"（净值日 {e['nav_date']} / 份额日 {e['shares_date']}）"
        )
    if summary.get("warnings"):
        lines.append("")
        lines.append("说明:")
        for w in summary["warnings"]:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append("不做: 不声称这是官方国家队/汇金持仓，不覆盖其它沪深300ETF或股票直投。")
    return "\n".join(lines)


def build_export_payload(summary: dict, series: list[dict]) -> dict:
    payload = dict(summary)
    payload["series"] = slim_series(series)
    return payload


@dataclass
class CliArgs:
    period: str
    start: str | None
    end: str | None
    cache_dir: Path
    refresh: bool
    clear_cache: bool
    fmt: str
    csv: Path | None
    json_path: Path | None
    html: Path | None
    quiet: bool


def parse_args(argv: list[str] | None) -> CliArgs:
    p = argparse.ArgumentParser(
        description=(
            "国家队持仓代理指标（非官方）。"
            "默认只输出终端摘要或 JSON/CSV 数据，不生成图表或网页。"
        ),
        epilog=(
            "时间窗口：--period 1m|3m|6m|1y 缩短或拉长；也可用 --start/--end。"
            "摘要、CSV、JSON 与可选网页均按该窗口裁剪。"
        ),
    )
    p.add_argument(
        "--period",
        choices=["1m", "3m", "6m", "1y", "ytd"],
        default="1m",
        help="预设窗口：1m=最近1个月，3m=3个月，6m=半年，1y=1年，ytd=年初至今。默认 1m",
    )
    p.add_argument("--start", help="自定义起始日 YYYY-MM-DD（传入则视为自定义窗口）")
    p.add_argument("--end", help="自定义结束日 YYYY-MM-DD，默认今天（上海时区）；可与 --period 合用")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="缓存目录，默认技能目录下 data_cache/")
    p.add_argument("--refresh", action="store_true", help="忽略净值缓存时效，重新拉取净值与行情")
    p.add_argument("--clear-cache", action="store_true", help="开始前清空缓存目录")
    p.add_argument("--format", dest="fmt", choices=["text", "json"], default="text", help="stdout 格式，默认 text")
    p.add_argument("--csv", nargs="?", const="holdings_proxy.csv", default=None, help="导出 CSV（只含所选窗口）")
    p.add_argument("--json", dest="json_path", nargs="?", const="holdings_proxy.json", default=None, help="导出 JSON")
    p.add_argument(
        "--html",
        "--web",
        dest="html",
        nargs="?",
        const="holdings_proxy.html",
        default=None,
        help="可选：生成带图表的网页。未传此参数时不生成图表或 HTML",
    )
    p.add_argument("--quiet", action="store_true", help="不在 stderr 打进度")
    ns = p.parse_args(argv)
    return CliArgs(
        period=ns.period,
        start=ns.start,
        end=ns.end,
        cache_dir=Path(ns.cache_dir),
        refresh=ns.refresh,
        clear_cache=ns.clear_cache,
        fmt=ns.fmt,
        csv=Path(ns.csv) if ns.csv else None,
        json_path=Path(ns.json_path) if ns.json_path else None,
        html=Path(ns.html) if ns.html else None,
        quiet=ns.quiet,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        start, end, preset = resolve_window(args.period, args.start, args.end)
    except ValueError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2

    if args.clear_cache and args.cache_dir.exists():
        import shutil

        shutil.rmtree(args.cache_dir)
        log(f"已清空缓存 {args.cache_dir}", args.quiet)

    try:
        data = build_history(
            start,
            end,
            args.cache_dir,
            refresh=args.refresh,
            quiet=args.quiet,
            period_label=preset,
        )
    except DataSourceError as e:
        print(f"失败: {e}", file=sys.stderr)
        print("请核对网络、接口是否变更，或稍后重试。未写入假数据。", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    summary = data["summary"]
    series = data["series"]
    payload = build_export_payload(summary, series)

    if args.csv:
        write_csv(args.csv, series)
        log(f"已写 CSV {args.csv}", args.quiet)
    if args.json_path:
        write_json(args.json_path, payload)
        log(f"已写 JSON {args.json_path}", args.quiet)
    if args.html:
        write_html(args.html, summary, series)
        log(f"已写网页 {args.html}", args.quiet)

    if args.fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_text(summary))
        extras = []
        if args.csv:
            extras.append(f"CSV: {args.csv}")
        if args.json_path:
            extras.append(f"JSON: {args.json_path}")
        if args.html:
            extras.append(f"网页: {args.html}")
        if extras:
            print("\n导出: " + "  ".join(extras))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        sys.exit(130)
