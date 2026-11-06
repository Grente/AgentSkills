#!/usr/bin/env python3
"""不访问网络：十大持有人解析、国家队别名匹配、报告期选择与缓存组装。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import holders  # noqa: E402
import holdings  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class AliasTests(unittest.TestCase):
    def test_huijin_and_ssf_match(self):
        self.assertTrue(holders.is_national_team_holder("中央汇金资产管理有限责任公司"))
        self.assertTrue(holders.is_national_team_holder("中央汇金投资有限责任公司"))
        self.assertTrue(holders.is_national_team_holder("华夏基金-中央汇金资产管理有限责任公司-华夏基金-汇金资管单一资产管理计划"))
        self.assertTrue(holders.is_national_team_holder("中国证券金融股份有限公司"))
        self.assertTrue(holders.is_national_team_holder("全国社会保障基金理事会"))
        self.assertTrue(holders.is_national_team_holder("全国社保基金一二三组合"))
        self.assertTrue(holders.is_national_team_holder("基本养老保险基金"))

    def test_stock_connect_excluded(self):
        self.assertFalse(holders.is_national_team_holder("香港中央结算有限公司"))
        self.assertFalse(holders.is_national_team_holder("Hong Kong Securities Clearing Company Ltd"))

    def test_unrelated_names(self):
        self.assertFalse(holders.is_national_team_holder("太平人寿保险有限公司"))
        self.assertFalse(holders.is_national_team_holder(""))
        self.assertFalse(holders.is_national_team_holder("华泰柏瑞沪深300ETF联接基金"))


class ParseTests(unittest.TestCase):
    def test_parse_sina_holders_and_filter(self):
        payload = json.loads((FIXTURES / "sina_holders_510300.json").read_text(encoding="utf-8"))
        rows = holders.parse_sina_holders(payload)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["holder_name"], "中央汇金资产管理有限责任公司")
        self.assertAlmostEqual(rows[0]["shares"], 37858500000.0)
        self.assertAlmostEqual(rows[0]["pct_of_fund"], 42.62)
        matched = [r for r in rows if holders.is_national_team_holder(r["holder_name"])]
        names = [r["holder_name"] for r in matched]
        self.assertEqual(len(matched), 2)
        self.assertIn("中央汇金资产管理有限责任公司", names)
        self.assertIn("中央汇金投资有限责任公司", names)
        self.assertNotIn("香港中央结算有限公司", names)

    def test_parse_sina_report_dates(self):
        payload = json.loads((FIXTURES / "sina_holder_dates_510300.json").read_text(encoding="utf-8"))
        dates = holders.parse_sina_report_dates(payload)
        self.assertEqual(dates[0], "2025-12-31")
        self.assertIn("2025-06-30", dates)

    def test_parse_stockstar_html(self):
        html = (FIXTURES / "stockstar_holders_510300.html").read_text(encoding="utf-8")
        report_date, rows = holders.parse_stockstar_holders(html)
        self.assertEqual(report_date, "2025-12-31")
        self.assertEqual(rows[0]["shares"], 37858474974.0)
        names = [r["holder_name"] for r in rows]
        self.assertIn("全国社会保障基金理事会", names)
        matched = [r for r in rows if holders.is_national_team_holder(r["holder_name"])]
        self.assertEqual(len(matched), 3)

    def test_select_report_date(self):
        available = ["2025-12-31", "2025-06-30", "2024-12-31"]
        self.assertEqual(holders.select_report_date(available, None), "2025-12-31")
        self.assertEqual(holders.select_report_date(available, "2025-06-30"), "2025-06-30")
        self.assertEqual(holders.select_report_date(available, "2025-09-30"), "2025-06-30")

    def test_market_value_prefers_period_end_nav(self):
        navs = [
            {"date": "2025-12-31", "dwjz": 4.0},
            {"date": "2026-01-05", "dwjz": 4.5},
        ]
        mv, nav_date, kind = holders.estimate_holder_market_value(
            100.0, navs, "2025-12-31", latest_nav=4.5, latest_nav_date="2026-01-05"
        )
        self.assertAlmostEqual(mv, 400.0)
        self.assertEqual(nav_date, "2025-12-31")
        self.assertEqual(kind, "period_end_nav")

    def test_wide_basket_includes_csi300_and_extras(self):
        csi = {e["code"] for e in holders.etf_basket("csi300")}
        wide = {e["code"] for e in holders.etf_basket("wide")}
        self.assertEqual(csi, {"510300", "510330", "159919", "510310"})
        self.assertTrue(csi.issubset(wide))
        self.assertIn("510050", wide)
        self.assertIn("159915", wide)


class HolderSectionCacheTests(unittest.TestCase):
    def test_build_section_from_cache_without_network(self):
        payload = json.loads((FIXTURES / "sina_holders_510300.json").read_text(encoding="utf-8"))
        parsed = holders.parse_sina_holders(payload)
        dates = holders.parse_sina_report_dates(
            json.loads((FIXTURES / "sina_holder_dates_510300.json").read_text(encoding="utf-8"))
        )

        def boom(*_a, **_k):
            raise AssertionError("offline test must not hit the network")

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "holder_dates_sina_510300.json").write_text(
                json.dumps(dates, ensure_ascii=False), encoding="utf-8"
            )
            (cache / "holders_sina_510300_2025-12-31.json").write_text(
                json.dumps(parsed, ensure_ascii=False), encoding="utf-8"
            )
            navs = {"510300": [{"date": "2025-12-31", "dwjz": 4.1785}]}
            section = holders.build_holder_section(
                etf_list=[{"code": "510300", "name": "华泰柏瑞沪深300ETF", "market": "SH"}],
                navs=navs,
                cache=cache,
                refresh=False,
                quiet=True,
                requested_report_date=None,
                log=None,
                http_get=boom,
            )
        self.assertTrue(section["ok"])
        self.assertEqual(section["report_date"], "2025-12-31")
        self.assertEqual(section["id"], "6")
        self.assertIn("滞后", section["accuracy_note"])
        matched = section["matched_holders"]
        self.assertEqual(len(matched), 2)
        total_pct = sum(h["pct_of_fund"] for h in matched)
        self.assertAlmostEqual(total_pct, 82.76, places=2)
        self.assertGreater(section["total_matched_mv_yi"], 0)
        names = [h["holder_name"] for h in matched]
        self.assertNotIn("香港中央结算有限公司", names)

    def test_source_failure_does_not_invent_numbers(self):
        def boom(*_a, **_k):
            raise holders.HolderSourceError("新浪十大持有人/510300", "模拟失败")

        with tempfile.TemporaryDirectory() as td:
            section = holders.build_holder_section(
                etf_list=[{"code": "510300", "name": "华泰柏瑞沪深300ETF", "market": "SH"}],
                navs={},
                cache=Path(td),
                refresh=True,
                quiet=True,
                requested_report_date=None,
                log=None,
                http_get=boom,
            )
        self.assertFalse(section["ok"])
        self.assertEqual(section["matched_holders"], [])
        self.assertTrue(any("失败" in w or "未编造" in w or "模拟" in w for w in section["warnings"]))


class MultiStatTextTests(unittest.TestCase):
    def test_format_text_lists_three_methods(self):
        summary = {
            "disclaimer": holdings.DISCLAIMER,
            "ratio": 0.8765,
            "data_date": "2026-11-04",
            "etf_mv_sum_yi": 4277.3,
            "csi300_circ_mv_yi": 488016.99,
            "csi300_close": 4101.3,
            "circ_mv_source": "fixture",
            "period": {
                "label": "最近 1 个月",
                "start": "2026-10-04",
                "end": "2026-11-04",
                "points": 20,
            },
            "range": {"high": {"date": "2026-11-01", "ratio": 0.9}, "low": {"date": "2026-10-10", "ratio": 0.8}},
            "etfs": [
                {
                    "code": "510300",
                    "name": "华泰柏瑞沪深300ETF",
                    "shares_yi": 1.0,
                    "dwjz": 1.0,
                    "market_value_yi": 1.0,
                    "nav_date": "2026-11-04",
                    "shares_date": "2026-11-04",
                }
            ],
            "warnings": ["历史流通市值为点位缩放近似。"],
            "methods": {
                "proxy_ratio": holdings.build_proxy_method_block(
                    {
                        "ratio": 0.8765,
                        "data_date": "2026-11-04",
                        "etf_mv_sum_yi": 4277.3,
                        "csi300_circ_mv_yi": 488016.99,
                        "range": {"high": {"date": "2026-11-01", "ratio": 0.9}, "low": {"date": "2026-10-10", "ratio": 0.8}},
                    }
                ),
                "net_creation": {
                    "ok": True,
                    "data_date": "2026-11-04",
                    "accuracy_note": "日频份额变化。",
                    "latest_day_net_flow_yi": 1.25,
                    "latest_day_delta_shares_yi": 0.3,
                    "period_net_flow_yi": -10.5,
                    "period_delta_shares_yi": -2.0,
                    "by_etf": [
                        {
                            "code": "510300",
                            "name": "华泰柏瑞沪深300ETF",
                            "shares_start_yi": 463.0,
                            "shares_end_yi": 461.0,
                            "period_delta_shares_yi": -2.0,
                            "period_net_flow_yi": -8.0,
                            "latest_day_net_flow_yi": 1.0,
                        }
                    ],
                },
                "disclosed_holders": {
                    "ok": True,
                    "report_date": "2025-12-31",
                    "accuracy_note": "滞后约半年。",
                    "source": "sina_getFundHolder",
                    "source_api": holders.SINA_HOLDER_API,
                    "comparable": True,
                    "total_matched_shares_yi": 735.13,
                    "total_matched_mv_yi": 3000.0,
                    "by_etf": [
                        {
                            "ok": True,
                            "code": "510300",
                            "name": "华泰柏瑞沪深300ETF",
                            "report_date": "2025-12-31",
                            "matched_shares_yi": 735.13,
                            "matched_pct_of_fund": 82.76,
                            "matched_market_value_yi": 3000.0,
                            "matched_holders": [
                                {
                                    "holder_name": "中央汇金资产管理有限责任公司",
                                    "shares": 37858500000,
                                    "pct_of_fund": 42.62,
                                    "market_value_yi": 1582.0,
                                    "nav_date": "2025-12-31",
                                    "nav_basis": "period_end_nav",
                                }
                            ],
                        }
                    ],
                },
            },
        }
        text = holdings.format_text(summary)
        self.assertIn("方法1", text)
        self.assertIn("方法2/3", text)
        self.assertIn("方法6", text)
        self.assertIn("0.8765%", text)
        self.assertIn("1.2500 亿元", text)
        self.assertIn("中央汇金资产管理有限责任公司", text)
        self.assertIn("2025-12-31", text)
        self.assertIn("不是国家队", text)
        payload = holdings.build_export_payload(summary, [])
        self.assertIn("proxy_ratio", payload["methods"])
        self.assertIn("net_creation", payload["methods"])
        self.assertIn("disclosed_holders", payload["methods"])


if __name__ == "__main__":
    unittest.main()
