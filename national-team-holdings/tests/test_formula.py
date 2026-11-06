#!/usr/bin/env python3
"""不访问网络：核对单位换算、窗口裁剪与默认 CLI（不出图）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import holdings  # noqa: E402


class FormulaTests(unittest.TestCase):
    def test_sse_wanfen_to_shares(self):
        shares = holdings.sse_tot_vol_to_shares(4636308.77)
        self.assertAlmostEqual(shares, 46363087700.0, delta=1.0)

    def test_szse_already_shares(self):
        self.assertEqual(holdings.etf_market_value(14059116676.0, 4.3628), 14059116676.0 * 4.3628)

    def test_sample_summary_ratio(self):
        sample = json.loads((ROOT / "examples" / "sample_summary.json").read_text(encoding="utf-8"))
        sum_mv = 0.0
        for etf in sample["etfs"]:
            mv = holdings.etf_market_value(etf["shares"], etf["dwjz"])
            sum_mv += mv
        circ = sample["csi300_circ_mv_yi"] * 1e8
        ratio = holdings.proxy_ratio(sum_mv, circ)
        self.assertAlmostEqual(ratio, sample["ratio"], places=4)
        self.assertAlmostEqual(sum_mv / 1e8, sample["etf_mv_sum_yi"], places=1)

    def test_scale_circ_mv(self):
        scaled = holdings.scale_circ_mv(100.0, 80.0, 100.0)
        self.assertAlmostEqual(scaled, 80.0)

    def test_uses_unit_nav_not_cumulative(self):
        sample = json.loads((ROOT / "examples" / "sample_summary.json").read_text(encoding="utf-8"))
        wrong = sum(holdings.etf_market_value(e["shares"], e["ljjz"]) for e in sample["etfs"])
        right = sum(holdings.etf_market_value(e["shares"], e["dwjz"]) for e in sample["etfs"])
        self.assertLess(wrong, right * 0.6)


class WindowTests(unittest.TestCase):
    def test_resolve_window_custom(self):
        start, end, preset = holdings.resolve_window(None, "2026-04-01", "2026-04-30")
        self.assertEqual(str(start), "2026-04-01")
        self.assertEqual(str(end), "2026-04-30")
        self.assertEqual(preset, "custom")

    def test_period_lengths(self):
        end = "2026-11-05"
        cases = {"1m": 31, "3m": 92, "6m": 183, "1y": 365}
        for period, days in cases.items():
            start, end_d, preset = holdings.resolve_window(period, None, end)
            self.assertEqual(preset, period)
            self.assertEqual(end_d, date(2026, 11, 5))
            self.assertEqual((end_d - start).days, days)

    def test_crop_series_keeps_window_only(self):
        series = [{"date": f"2026-01-{d:02d}", "ratio": d} for d in range(1, 32)]
        cropped = holdings.crop_series(series, "2026-01-10", "2026-01-12")
        self.assertEqual([p["date"] for p in cropped], ["2026-01-10", "2026-01-11", "2026-01-12"])


class NavCacheTests(unittest.TestCase):
    def test_load_nav_refetches_when_cache_does_not_cover_min_date(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "nav_510300.json").write_text(
                json.dumps([{"date": "2026-10-01", "dwjz": 4.0}]),
                encoding="utf-8",
            )
            called = {"n": 0}

            def fake_fetch(code, pages, cache_dir, quiet):
                called["n"] += 1
                return [
                    {"date": "2025-06-30", "dwjz": 3.5},
                    {"date": "2026-10-01", "dwjz": 4.0},
                ]

            orig = holdings.fetch_nav_history
            holdings.fetch_nav_history = fake_fetch
            try:
                rows = holdings.load_nav("510300", 10, cache, 24.0, True, min_date="2025-12-31")
            finally:
                holdings.fetch_nav_history = orig
            self.assertEqual(called["n"], 1)
            self.assertEqual(rows[0]["date"], "2025-06-30")

    def test_load_nav_keeps_cache_when_old_enough(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "nav_510300.json").write_text(
                json.dumps([{"date": "2025-06-30", "dwjz": 3.5}]),
                encoding="utf-8",
            )

            def boom(*_a, **_k):
                raise AssertionError("should use cache")

            orig = holdings.fetch_nav_history
            holdings.fetch_nav_history = boom
            try:
                rows = holdings.load_nav("510300", 10, cache, 24.0, True, min_date="2025-12-31")
            finally:
                holdings.fetch_nav_history = orig
            self.assertEqual(rows[0]["date"], "2025-06-30")


class CliDefaultTests(unittest.TestCase):
    def test_default_has_no_html(self):
        args = holdings.parse_args(["--period", "3m"])
        self.assertIsNone(args.html)
        self.assertIsNone(args.csv)
        self.assertIsNone(args.json_path)
        self.assertEqual(args.period, "3m")
        self.assertIsNone(args.report_date)
        self.assertEqual(args.holders_basket, "csi300")

    def test_report_date_independent_of_period(self):
        args = holdings.parse_args(
            ["--period", "1m", "--report-date", "2025-06-30", "--holders-basket", "wide"]
        )
        self.assertEqual(args.period, "1m")
        self.assertEqual(args.report_date, "2025-06-30")
        self.assertEqual(args.holders_basket, "wide")

    def test_html_and_web_aliases(self):
        a = holdings.parse_args(["--html"])
        self.assertEqual(a.html, Path("holdings_proxy.html"))
        b = holdings.parse_args(["--web", "out/report.html"])
        self.assertEqual(b.html, Path("out/report.html"))

    def test_write_html_contains_disclaimer_and_cropped_points(self):
        summary = {
            "disclaimer": holdings.DISCLAIMER,
            "ratio": 0.8,
            "data_date": "2026-01-12",
            "etf_mv_sum_yi": 1.0,
            "csi300_circ_mv_yi": 100.0,
            "period": {
                "label": "自定义起止日",
                "start": "2026-01-10",
                "end": "2026-01-12",
                "requested_start": "2026-01-10",
                "requested_end": "2026-01-12",
                "points": 3,
            },
            "range": {"high": {"date": "2026-01-12", "ratio": 0.9}, "low": {"date": "2026-01-10", "ratio": 0.7}},
            "etfs": [
                {
                    "code": "510300",
                    "name": "华泰柏瑞沪深300ETF",
                    "shares_yi": 1.0,
                    "dwjz": 1.0,
                    "market_value_yi": 1.0,
                    "nav_date": "2026-01-12",
                    "shares_date": "2026-01-12",
                }
            ],
            "warnings": ["代理指标，非官方持仓。"],
            "formula": "test",
        }
        series = [
            {
                "date": "2026-01-09",
                "ratio": 0.5,
                "etf_mv_sum_yi": 1,
                "csi300_circ_mv_yi": 100,
                "csi300_close": 1,
                "etfs": {c: {"market_value_yi": 0.25} for c in holdings.ETF_CODES},
            },
            {
                "date": "2026-01-11",
                "ratio": 0.8,
                "etf_mv_sum_yi": 1,
                "csi300_circ_mv_yi": 100,
                "csi300_close": 1,
                "etfs": {c: {"market_value_yi": 0.25} for c in holdings.ETF_CODES},
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.html"
            holdings.write_html(path, summary, series)
            text = path.read_text(encoding="utf-8")
        self.assertIn("代理指标，非官方", text)
        self.assertIn("2026-01-11", text)
        self.assertNotIn("2026-01-09", text)


if __name__ == "__main__":
    unittest.main()
