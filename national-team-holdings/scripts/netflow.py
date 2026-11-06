"""方法 2/3：日份额变动与净申赎（非官方，不能识别买方）。

净申赎额_t ≈ (份额_t − 份额_{t-1}) × 单位净值_t
份额口径剔除价格噪音，只看规模申赎。
"""
from __future__ import annotations

from typing import Any

ACCURACY_NOTE = (
    "日频份额变化与净申赎估算，剔除价格涨跌对市值的影响；"
    "不能识别买方身份，不是国家队持仓。"
    "长区间上交所份额为采样+前向填充时，非采样日 Δ份额为 0，采样日为该间隔内累计变化。"
)


def net_creation_flow(shares_t: float, shares_prev: float, unit_nav_t: float) -> float:
    """净申赎额（元）≈ Δ份额 × 当日单位净值。"""
    return (float(shares_t) - float(shares_prev)) * float(unit_nav_t)


def attach_share_deltas(series: list[dict], codes: list[str]) -> list[dict]:
    """为序列逐日补上 Δ份额与净申赎。首日若无前值则字段为 None。

    不修改入参字典（浅拷贝每一日与各 ETF 行）。
    """
    prev: dict[str, dict] | None = None
    out: list[dict] = []
    for point in series:
        row = dict(point)
        etfs_in = dict(point.get("etfs") or {})
        etfs: dict[str, dict] = {}
        net_sum = 0.0
        delta_sum = 0.0
        has_delta = False
        for code in codes:
            src = etfs_in.get(code)
            if not src:
                continue
            etf = dict(src)
            shares = etf.get("shares")
            dwjz = etf.get("dwjz")
            if prev is not None and code in prev and shares is not None and dwjz is not None:
                prev_shares = prev[code].get("shares")
                if prev_shares is not None:
                    delta = float(shares) - float(prev_shares)
                    flow = net_creation_flow(float(shares), float(prev_shares), float(dwjz))
                    etf["delta_shares"] = delta
                    etf["delta_shares_yi"] = delta / 1e8
                    etf["net_flow"] = flow
                    etf["net_flow_yi"] = flow / 1e8
                    net_sum += flow
                    delta_sum += delta
                    has_delta = True
                else:
                    etf["delta_shares"] = None
                    etf["delta_shares_yi"] = None
                    etf["net_flow"] = None
                    etf["net_flow_yi"] = None
            else:
                etf["delta_shares"] = None
                etf["delta_shares_yi"] = None
                etf["net_flow"] = None
                etf["net_flow_yi"] = None
            etfs[code] = etf
        row["etfs"] = {**etfs_in, **etfs}
        row["delta_shares_sum"] = delta_sum if has_delta else None
        row["delta_shares_sum_yi"] = (delta_sum / 1e8) if has_delta else None
        row["net_flow_sum"] = net_sum if has_delta else None
        row["net_flow_sum_yi"] = (net_sum / 1e8) if has_delta else None
        out.append(row)
        if etfs:
            prev = {c: etfs[c] for c in etfs}
    return out


def _etf_meta(etf_list: list[dict] | tuple[dict, ...]) -> dict[str, dict]:
    return {e["code"]: e for e in etf_list}


def summarize_netflow(
    series: list[dict],
    codes: list[str],
    etf_list: list[dict] | tuple[dict, ...] | None = None,
) -> dict[str, Any]:
    """从已带 Δ份额的窗口序列汇总最新一日与区间累计。"""
    names = _etf_meta(etf_list or ())
    points_with_flow = [p for p in series if p.get("net_flow_sum") is not None]
    latest = series[-1] if series else None
    first = series[0] if series else None

    by_etf: list[dict] = []
    period_delta = 0.0
    period_flow = 0.0
    latest_delta = 0.0
    latest_flow = 0.0
    if latest:
        for code in codes:
            end_etf = (latest.get("etfs") or {}).get(code) or {}
            start_etf = (first.get("etfs") or {}).get(code) or {} if first else {}
            shares_end = end_etf.get("shares")
            shares_start = start_etf.get("shares")
            delta = None
            if shares_end is not None and shares_start is not None:
                delta = float(shares_end) - float(shares_start)
                period_delta += delta
            day_delta = end_etf.get("delta_shares")
            day_flow = end_etf.get("net_flow")
            if day_delta is not None:
                latest_delta += float(day_delta)
            if day_flow is not None:
                latest_flow += float(day_flow)
            # 区间净申赎用逐日加总（与累计 Δ份额×各日净值一致），缺日则退回 Δ份额×末日净值
            daily_flows = []
            for p in points_with_flow:
                etf = (p.get("etfs") or {}).get(code) or {}
                if etf.get("net_flow") is not None:
                    daily_flows.append(float(etf["net_flow"]))
            etf_period_flow = sum(daily_flows) if daily_flows else None
            if etf_period_flow is not None:
                period_flow += etf_period_flow
            meta = names.get(code) or {}
            by_etf.append(
                {
                    "code": code,
                    "name": meta.get("name") or end_etf.get("name") or code,
                    "shares_start": shares_start,
                    "shares_end": shares_end,
                    "shares_start_yi": (float(shares_start) / 1e8) if shares_start is not None else None,
                    "shares_end_yi": (float(shares_end) / 1e8) if shares_end is not None else None,
                    "period_delta_shares": delta,
                    "period_delta_shares_yi": (delta / 1e8) if delta is not None else None,
                    "period_net_flow": etf_period_flow,
                    "period_net_flow_yi": (etf_period_flow / 1e8) if etf_period_flow is not None else None,
                    "latest_day_delta_shares": day_delta,
                    "latest_day_net_flow_yi": (float(day_flow) / 1e8) if day_flow is not None else None,
                    "shares_date": end_etf.get("shares_date"),
                    "nav_date": end_etf.get("nav_date"),
                }
            )

    data_date = latest["date"] if latest else None
    return {
        "id": "2/3",
        "title": "份额变动与净申赎",
        "data_date": data_date,
        "accuracy_note": ACCURACY_NOTE,
        "formula": "净申赎额_t ≈ (份额_t − 份额_{t-1}) × 单位净值_t；区间累计为窗口内逐日加总。",
        "latest_day_net_flow_yi": round(latest_flow / 1e8, 4) if latest and latest.get("net_flow_sum") is not None else None,
        "latest_day_delta_shares": latest_delta if latest and latest.get("delta_shares_sum") is not None else None,
        "latest_day_delta_shares_yi": round(latest_delta / 1e8, 4) if latest and latest.get("delta_shares_sum") is not None else None,
        "period_net_flow_yi": round(period_flow / 1e8, 4) if points_with_flow else None,
        "period_delta_shares": period_delta if first and latest else None,
        "period_delta_shares_yi": round(period_delta / 1e8, 4) if first and latest else None,
        "points_with_flow": len(points_with_flow),
        "by_etf": by_etf,
        "ok": bool(series),
    }
