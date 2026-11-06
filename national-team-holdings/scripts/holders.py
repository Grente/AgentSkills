"""方法 6：半年报/年报「十大持有人」披露（最接近真实持有水平，滞后）。

优先新浪财经 JSON（CaihuiFundInfoService.getFundHolder）；失败则回退证券之星 HTML。
季报通常只披露单一投资者 ≥20%（常匿名为机构1/2），本模块默认用中报/年报具名名单。

匹配中央汇金 / 汇金资管 / 中国证券金融 / 全国社保等别名。
香港中央结算（沪深股通）不是国家队，明确排除。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

ACCURACY_NOTE = (
    "最接近真实具名持有水平，但来自基金半年报/年报，滞后可达约 6 个月，不是日频数字。"
    "未进入前十不等于持仓为零。季报往往只披露单一投资者持有 ≥20% 的匿名机构。"
    "不是官方国家队持仓统计。"
)

SINA_HOLDER_API = (
    "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/"
    "CaihuiFundInfoService.getFundHolder"
)
SINA_HOLDER_DATE_API = (
    "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/"
    "CaihuiFundInfoService.getFundHolderDate"
)
SINA_HOLDER_PAGE = "https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJCYR.php?symbol={code}"
STOCKSTAR_PAGE = "https://fund.stockstar.com/funds/f10/fundholder_{code}.html"

# 核心四只沪深300 ETF（与方法 1 / 2/3 默认篮子一致）
CSI300_ETFS = (
    {"code": "510300", "name": "华泰柏瑞沪深300ETF", "market": "SH"},
    {"code": "510330", "name": "华夏沪深300ETF", "market": "SH"},
    {"code": "159919", "name": "嘉实沪深300ETF", "market": "SZ"},
    {"code": "510310", "name": "易方达沪深300ETF", "market": "SH"},
)

# 公开报道中较常见的汇金重仓宽基 ETF，仅用于方法 6 的可选扩展篮子
WIDE_INDEX_ETFS = (
    {"code": "510050", "name": "华夏上证50ETF", "market": "SH"},
    {"code": "159915", "name": "易方达创业板ETF", "market": "SZ"},
    {"code": "510500", "name": "南方中证500ETF", "market": "SH"},
    {"code": "512100", "name": "南方中证1000ETF", "market": "SH"},
    {"code": "588000", "name": "华夏上证科创板50ETF", "market": "SH"},
)

# 纳入：汇金 / 证金 / 社保等常见披露名。不用过于宽的「社保」单字。
NATIONAL_TEAM_ALIASES = (
    "中央汇金",
    "汇金资管",
    "汇金投资",
    "中国证券金融",
    "证金公司",
    "中证金融",
    "全国社保",
    "社保基金",
    "社会保障基金",
    "全国社会保障",
    "基本养老保险",
    "基本养老",
)

# 沪深股通 / 北向不是国家队
HOLDER_EXCLUDE_ALIASES = (
    "香港中央结算",
    "香港中央結算",
    "Hong Kong Securities Clearing",
    "HKSCC",
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


class HolderSourceError(RuntimeError):
    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"[{source}] {message}")


def etf_basket(name: str) -> tuple[dict, ...]:
    key = (name or "csi300").strip().lower()
    if key == "wide":
        seen = set()
        out: list[dict] = []
        for row in tuple(CSI300_ETFS) + tuple(WIDE_INDEX_ETFS):
            if row["code"] in seen:
                continue
            seen.add(row["code"])
            out.append(row)
        return tuple(out)
    return tuple(CSI300_ETFS)


def is_national_team_holder(name: str) -> bool:
    """具名持有人是否像国家队相关主体。北向/港结算明确排除。"""
    text = (name or "").strip()
    if not text:
        return False
    if any(alias in text for alias in HOLDER_EXCLUDE_ALIASES):
        return False
    return any(alias in text for alias in NATIONAL_TEAM_ALIASES)


def parse_sina_report_dates(payload: dict) -> list[str]:
    data = ((payload or {}).get("result") or {}).get("data") or []
    dates: list[str] = []
    for row in data:
        raw = str(row.get("PUBLISHDATE") or row.get("publishdate") or "").strip()[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            dates.append(raw)
    # 接口通常已按新到旧；再排一次保证最新在前
    return sorted(set(dates), reverse=True)


def parse_sina_holders(payload: dict) -> list[dict[str, Any]]:
    data = ((payload or {}).get("result") or {}).get("data") or []
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(data, 1):
        name = str(item.get("cyrmc") or item.get("HOLDER_NAME") or "").strip()
        if not name:
            continue
        shares_raw = item.get("cyfe") if item.get("cyfe") not in (None, "") else item.get("HOLD_SHARES")
        pct_raw = item.get("zfeb") if item.get("zfeb") not in (None, "") else item.get("HOLD_RATIO")
        try:
            shares = float(str(shares_raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
        try:
            pct = float(str(pct_raw).replace("%", "").replace(",", ""))
        except (TypeError, ValueError):
            pct = None
        rows.append(
            {
                "rank": i,
                "holder_name": name,
                "shares": shares,
                "pct_of_fund": pct,
            }
        )
    return rows


_STOCKSTAR_DATE_RE = re.compile(r"截止日[：:]\s*(\d{4}-\d{2}-\d{2})")
_STOCKSTAR_ROW_RE = re.compile(
    r"<td[^>]*>\s*(\d+)\s*</td>\s*"
    r"<td[^>]*>\s*([^<]+?)\s*</td>\s*"
    r"<td[^>]*>\s*([0-9,]+)\s*</td>\s*"
    r"<td[^>]*>\s*([0-9.]+)\s*</td>",
    re.I,
)


def parse_stockstar_holders(html: str) -> tuple[str | None, list[dict[str, Any]]]:
    """解析证券之星十大持有人页。份额通常比新浪更精确（新浪常四舍五入）。"""
    report_date = None
    m = _STOCKSTAR_DATE_RE.search(html or "")
    if m:
        report_date = m.group(1)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in _STOCKSTAR_ROW_RE.finditer(html or ""):
        rank_s, name, shares_s, pct_s = match.groups()
        name = re.sub(r"\s+", "", name).strip()
        if not name or name in ("机构名称", "持有人名称"):
            continue
        key = (name, shares_s)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "rank": int(rank_s),
                "holder_name": name,
                "shares": float(shares_s.replace(",", "")),
                "pct_of_fund": float(pct_s),
            }
        )
    return report_date, rows


def nav_asof(rows: list[dict], day: str) -> dict | None:
    last = None
    for row in rows:
        if row.get("date") and row["date"] <= day and row.get("dwjz") not in (None,):
            last = row
        elif row.get("date") and row["date"] > day:
            break
    return last


def estimate_holder_market_value(
    shares: float,
    nav_rows: list[dict] | None,
    report_date: str | None,
    latest_nav: float | None = None,
    latest_nav_date: str | None = None,
) -> tuple[float | None, str | None, str | None]:
    """市值估算（元）。优先报告期末单位净值，否则最新净值。"""
    if nav_rows and report_date:
        row = nav_asof(nav_rows, report_date)
        if row and row.get("dwjz"):
            return float(shares) * float(row["dwjz"]), str(row["date"]), "period_end_nav"
    if latest_nav not in (None,):
        return float(shares) * float(latest_nav), latest_nav_date, "latest_nav"
    return None, None, None


def select_report_date(available: list[str], requested: str | None) -> str | None:
    if not available:
        return requested
    if not requested:
        return available[0]
    if requested in available:
        return requested
    # 若指定日不在列表，选不超过该日的最近报告期
    older = [d for d in available if d <= requested]
    if older:
        return older[0]
    return available[0]


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


def default_http_get(url: str, *, source: str, headers: dict | None = None, params: dict | None = None, timeout: int = 18):
    import random

    import requests

    last: Exception | None = None
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    for i in range(3):
        try:
            r = sess.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1) + random.random() * 0.2)
    raise HolderSourceError(source, f"请求失败 {url}: {last}")


def fetch_sina_report_dates(
    code: str,
    *,
    http_get: Callable = default_http_get,
) -> list[str]:
    r = http_get(
        SINA_HOLDER_DATE_API,
        source=f"新浪持有人报告期/{code}",
        headers={
            "Referer": SINA_HOLDER_PAGE.format(code=code),
            "User-Agent": UA,
        },
        params={"symbol": code},
        timeout=20,
    )
    try:
        payload = r.json()
    except Exception as e:
        raise HolderSourceError(f"新浪持有人报告期/{code}", f"JSON 解析失败: {e}") from e
    dates = parse_sina_report_dates(payload)
    if not dates:
        raise HolderSourceError(f"新浪持有人报告期/{code}", "未返回任何报告期")
    return dates


def fetch_sina_holders(
    code: str,
    report_date: str,
    *,
    http_get: Callable = default_http_get,
) -> list[dict[str, Any]]:
    r = http_get(
        SINA_HOLDER_API,
        source=f"新浪十大持有人/{code}",
        headers={
            "Referer": SINA_HOLDER_PAGE.format(code=code),
            "User-Agent": UA,
        },
        params={"symbol": code, "date": report_date},
        timeout=20,
    )
    try:
        payload = r.json()
    except Exception as e:
        raise HolderSourceError(f"新浪十大持有人/{code}", f"JSON 解析失败: {e}") from e
    rows = parse_sina_holders(payload)
    if not rows:
        raise HolderSourceError(f"新浪十大持有人/{code}", f"{report_date} 未返回持有人")
    return rows


def fetch_stockstar_holders(
    code: str,
    *,
    http_get: Callable = default_http_get,
) -> tuple[str | None, list[dict[str, Any]]]:
    r = http_get(
        STOCKSTAR_PAGE.format(code=code),
        source=f"证券之星十大持有人/{code}",
        headers={"Referer": "https://fund.stockstar.com/", "User-Agent": UA},
        timeout=20,
    )
    encoding = getattr(r, "apparent_encoding", None) or getattr(r, "encoding", None) or "gb18030"
    raw = getattr(r, "content", None)
    if isinstance(raw, (bytes, bytearray)):
        text = raw.decode(str(encoding), errors="replace")
    else:
        text = getattr(r, "text", "") or ""
    report_date, rows = parse_stockstar_holders(text)
    if not rows:
        raise HolderSourceError(f"证券之星十大持有人/{code}", "页面未解析到持有人表格")
    return report_date, rows


def load_report_dates(
    code: str,
    cache: Path,
    max_age_hours: float,
    quiet: bool,
    log: Callable | None,
    http_get: Callable = default_http_get,
) -> list[str]:
    path = cache / f"holder_dates_sina_{code}.json"
    if _cache_fresh(path, max_age_hours):
        data = _read_json_cache(path)
        if isinstance(data, list) and data:
            if log:
                log(f"[持有人] {code} 报告期使用缓存 {len(data)} 条", quiet)
            return [str(x) for x in data]
    dates = fetch_sina_report_dates(code, http_get=http_get)
    _write_json_cache(path, dates)
    if log:
        log(f"[持有人] {code} 报告期 {dates[0]} 起共 {len(dates)} 期", quiet)
    return dates


def load_holders_for_date(
    code: str,
    report_date: str,
    cache: Path,
    max_age_hours: float,
    quiet: bool,
    log: Callable | None,
    http_get: Callable = default_http_get,
) -> tuple[list[dict[str, Any]], str, str]:
    """返回 (rows, source_label, source_url)。"""
    path = cache / f"holders_sina_{code}_{report_date}.json"
    if _cache_fresh(path, max_age_hours):
        data = _read_json_cache(path)
        if isinstance(data, list) and data:
            if log:
                log(f"[持有人] {code} {report_date} 使用缓存 {len(data)} 条", quiet)
            return data, "sina_getFundHolder", SINA_HOLDER_PAGE.format(code=code)
    try:
        rows = fetch_sina_holders(code, report_date, http_get=http_get)
        _write_json_cache(path, rows)
        return rows, "sina_getFundHolder", SINA_HOLDER_PAGE.format(code=code)
    except Exception as sina_err:
        if log:
            log(f"[持有人] {code} 新浪失败，尝试证券之星: {sina_err}", quiet)
        star_date, rows = fetch_stockstar_holders(code, http_get=http_get)
        if star_date and report_date and star_date != report_date:
            # 证券之星通常只有最新一期；日期不一致时仍返回但由调用方决定是否采用
            pass
        cache_star = cache / f"holders_stockstar_{code}_{star_date or report_date}.json"
        _write_json_cache(cache_star, rows)
        return rows, "stockstar_html", STOCKSTAR_PAGE.format(code=code)


def annotate_holders(
    rows: list[dict[str, Any]],
    *,
    nav_rows: list[dict] | None,
    report_date: str | None,
    latest_nav: float | None = None,
    latest_nav_date: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        matched = is_national_team_holder(item.get("holder_name") or "")
        item["matched_national_team"] = matched
        mv, nav_date, nav_kind = estimate_holder_market_value(
            float(item["shares"]),
            nav_rows,
            report_date,
            latest_nav=latest_nav,
            latest_nav_date=latest_nav_date,
        )
        item["market_value"] = mv
        item["market_value_yi"] = (mv / 1e8) if mv is not None else None
        item["nav_date"] = nav_date
        item["nav_basis"] = nav_kind
        out.append(item)
    return out


def build_holder_section(
    *,
    etf_list: list[dict] | tuple[dict, ...],
    navs: dict[str, list[dict]] | None,
    cache: Path,
    refresh: bool,
    quiet: bool,
    requested_report_date: str | None,
    log: Callable | None = None,
    http_get: Callable = default_http_get,
) -> dict[str, Any]:
    """拉取篮子内 ETF 最新（或指定）报告期十大持有人，筛出国家队相关主体。"""
    cache.mkdir(parents=True, exist_ok=True)
    max_age = 0.0 if refresh else 24.0
    warnings: list[str] = []
    by_etf: list[dict[str, Any]] = []
    matched_all: list[dict[str, Any]] = []
    report_dates_used: set[str] = set()
    sources: set[str] = set()

    for meta in etf_list:
        code = meta["code"]
        try:
            dates = load_report_dates(code, cache, max_age, quiet, log, http_get=http_get)
            report_date = select_report_date(dates, requested_report_date)
            if requested_report_date and requested_report_date not in dates:
                warnings.append(
                    f"{code} 指定报告期 {requested_report_date} 不在已披露列表，改用 {report_date}。"
                )
            rows, source_label, source_url = load_holders_for_date(
                code, report_date or dates[0], cache, max_age, quiet, log, http_get=http_get
            )
            nav_rows = (navs or {}).get(code)
            latest_nav = None
            latest_nav_date = None
            if nav_rows:
                last = next((r for r in reversed(nav_rows) if r.get("dwjz") not in (None,)), None)
                if last:
                    latest_nav = float(last["dwjz"])
                    latest_nav_date = last.get("date")
            annotated = annotate_holders(
                rows,
                nav_rows=nav_rows,
                report_date=report_date,
                latest_nav=latest_nav,
                latest_nav_date=latest_nav_date,
            )
            matched = [h for h in annotated if h.get("matched_national_team")]
            matched_shares = sum(float(h["shares"]) for h in matched)
            matched_mv = sum(float(h["market_value"]) for h in matched if h.get("market_value") is not None)
            matched_pct = sum(float(h["pct_of_fund"]) for h in matched if h.get("pct_of_fund") is not None)
            sources.add(source_label)
            if report_date:
                report_dates_used.add(report_date)
            etf_row = {
                "code": code,
                "name": meta.get("name") or code,
                "report_date": report_date,
                "source": source_label,
                "source_url": source_url,
                "source_api": SINA_HOLDER_API if source_label.startswith("sina") else STOCKSTAR_PAGE.format(code=code),
                "holders": annotated,
                "matched_holders": matched,
                "matched_shares": matched_shares,
                "matched_shares_yi": matched_shares / 1e8,
                "matched_pct_of_fund": round(matched_pct, 4) if matched else 0.0,
                "matched_market_value_yi": (matched_mv / 1e8) if matched and matched_mv else 0.0,
                "ok": True,
            }
            by_etf.append(etf_row)
            for h in matched:
                matched_all.append(
                    {
                        "code": code,
                        "etf_name": meta.get("name") or code,
                        "report_date": report_date,
                        "holder_name": h["holder_name"],
                        "shares": h["shares"],
                        "shares_yi": float(h["shares"]) / 1e8,
                        "pct_of_fund": h.get("pct_of_fund"),
                        "market_value_yi": h.get("market_value_yi"),
                        "nav_date": h.get("nav_date"),
                        "nav_basis": h.get("nav_basis"),
                        "source": source_label,
                        "source_url": source_url,
                    }
                )
            time.sleep(0.12)
        except Exception as e:
            warnings.append(str(e))
            by_etf.append(
                {
                    "code": code,
                    "name": meta.get("name") or code,
                    "report_date": None,
                    "ok": False,
                    "error": str(e),
                    "holders": [],
                    "matched_holders": [],
                }
            )

    comparable = len(report_dates_used) == 1 and any(e.get("ok") for e in by_etf)
    total_shares = sum(float(e.get("matched_shares") or 0) for e in by_etf if e.get("ok"))
    total_mv = sum(float(e.get("matched_market_value_yi") or 0) for e in by_etf if e.get("ok"))
    ok_count = sum(1 for e in by_etf if e.get("ok"))
    report_date_label = next(iter(report_dates_used)) if comparable else None
    if len(report_dates_used) > 1:
        warnings.append(
            "各 ETF 最新披露报告期不一致，合计仅作「各自最新一期」加总，不可视为同一时点持仓。"
        )
    if ok_count == 0:
        warnings.append("十大持有人全部数据源失败，未编造数字。")

    source_note = "；".join(sorted(sources)) if sources else "无"
    return {
        "id": "6",
        "title": "定期报告十大持有人（披露持仓）",
        "report_date": report_date_label,
        "data_date": report_date_label,
        "report_dates": sorted(report_dates_used, reverse=True),
        "accuracy_note": ACCURACY_NOTE,
        "formula": (
            "筛选半年报/年报前十名持有人中名称匹配汇金/证金/社保等别名的记录；"
            "市值 ≈ 持有份额 × 报告期末（或最新）单位净值。北向/香港中央结算不计入。"
        ),
        "source": source_note,
        "source_api": SINA_HOLDER_API,
        "source_page": SINA_HOLDER_PAGE,
        "ok": ok_count > 0,
        "comparable": comparable,
        "matched_holders": matched_all,
        "by_etf": by_etf,
        "total_matched_shares": total_shares,
        "total_matched_shares_yi": total_shares / 1e8,
        "total_matched_mv_yi": round(total_mv, 4),
        "etf_ok_count": ok_count,
        "etf_count": len(list(etf_list)),
        "warnings": warnings,
        "aliases": list(NATIONAL_TEAM_ALIASES),
        "excluded": list(HOLDER_EXCLUDE_ALIASES),
    }
