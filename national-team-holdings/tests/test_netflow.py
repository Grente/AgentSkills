#!/usr/bin/env python3
"""不访问网络：份额变动与净申赎公式。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import netflow  # noqa: E402


def _etf(shares: float, dwjz: float, name: str = "测试ETF") -> dict:
    return {"shares": shares, "dwjz": dwjz, "name": name, "shares_date": "x", "nav_date": "x"}


class NetflowFormulaTests(unittest.TestCase):
    def test_net_creation_flow(self):
        # Δ 1 亿份 × 4 元 = 4 亿元
        flow = netflow.net_creation_flow(2e8, 1e8, 4.0)
        self.assertAlmostEqual(flow, 4e8)

    def test_attach_and_summarize_strips_price_noise(self):
        codes = ["510300", "510330"]
        series = [
            {
                "date": "2026-01-08",
                "ratio": 0.8,
                "etf_mv_sum_yi": 12,
                "csi300_circ_mv_yi": 1000,
                "csi300_close": 4000,
                "etfs": {
                    "510300": _etf(100e8, 4.0, "华泰柏瑞"),
                    "510330": _etf(50e8, 4.0, "华夏"),
                },
            },
            {
                "date": "2026-01-09",
                "ratio": 0.81,
                "etf_mv_sum_yi": 12.3,
                "csi300_circ_mv_yi": 1000,
                "csi300_close": 4010,
                "etfs": {
                    # 份额不变、净值上涨 → 净申赎应为 0
                    "510300": _etf(100e8, 4.1, "华泰柏瑞"),
                    "510330": _etf(50e8, 4.1, "华夏"),
                },
            },
            {
                "date": "2026-01-10",
                "ratio": 0.85,
                "etf_mv_sum_yi": 16.4,
                "csi300_circ_mv_yi": 1000,
                "csi300_close": 4020,
                "etfs": {
                    "510300": _etf(110e8, 4.1, "华泰柏瑞"),  # +10 亿份
                    "510330": _etf(48e8, 4.1, "华夏"),  # -2 亿份
                },
            },
        ]
        with_delta = netflow.attach_share_deltas(series, codes)
        self.assertIsNone(with_delta[0]["net_flow_sum"])
        self.assertAlmostEqual(with_delta[1]["net_flow_sum_yi"], 0.0, places=6)
        # 10e8 * 4.1 + (-2e8) * 4.1 = 32.8 亿元
        self.assertAlmostEqual(with_delta[2]["net_flow_sum_yi"], 32.8, places=4)

        window = with_delta[1:]  # 模拟 lookback 裁剪后窗口首日仍带 Δ
        summary = netflow.summarize_netflow(
            window,
            codes,
            [
                {"code": "510300", "name": "华泰柏瑞沪深300ETF"},
                {"code": "510330", "name": "华夏沪深300ETF"},
            ],
        )
        self.assertEqual(summary["id"], "2/3")
        self.assertEqual(summary["data_date"], "2026-01-10")
        self.assertAlmostEqual(summary["latest_day_net_flow_yi"], 32.8, places=4)
        self.assertAlmostEqual(summary["latest_day_delta_shares_yi"], 8.0, places=4)
        self.assertAlmostEqual(summary["period_net_flow_yi"], 32.8, places=4)
        # 份额趋势用窗口首末日，不含 lookback：100→110 与 50→48
        by = {e["code"]: e for e in summary["by_etf"]}
        self.assertAlmostEqual(by["510300"]["period_delta_shares_yi"], 10.0, places=4)
        self.assertAlmostEqual(by["510330"]["period_delta_shares_yi"], -2.0, places=4)
        self.assertIn("不是国家队", summary["accuracy_note"])


if __name__ == "__main__":
    unittest.main()
