"""用程序生成的小样本检验数据规则；不附带任何真实行情。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from quantkit.cleaning import align_macro, calendar_frame, clean_market, macro_events, unique_dates
from quantkit.common import normalize_asset, year_chunks
from quantkit.expectations import clean_fedwatch
from quantkit.providers import nbs_article, market_tx, apply_sina_factors


class DataRules(unittest.TestCase):
    def setUp(self):
        self.asset = normalize_asset({"code": "510300.SH", "kind": "etf"})
        self.cal = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"])})
        self.raw = pd.DataFrame({"date": self.cal["date"], "open": [10, 11, 12, 13], "close": [10, 11, 12, 13],
                                 "high": [11, 12, 13, 14], "low": [9, 10, 11, 12], "volume": [1, 2, 3, 4], "amount": [10, 22, 36, 52]})
        self.meta = {"source": "synthetic", "volume_unit": "lot_100", "amount_unit": "thousand_CNY"}

    def clean(self, raw=None, adjusted=None, asset=None):
        return clean_market(self.raw if raw is None else raw, self.raw if adjusted is None else adjusted,
                            asset or self.asset, self.cal, "2025-01-02", "2025-01-07", self.meta)

    def test_mapping_preserves_leading_zero(self):
        a = normalize_asset({"code": "000001.SZ", "kind": "stock"})
        self.assertEqual(a["tx_symbol"], "sz000001")
        with self.assertRaises(ValueError):
            normalize_asset({"code": "000001", "kind": "stock"})

    def test_metadata_needs_source(self):
        with self.assertRaises(ValueError):
            normalize_asset({"code": "510300.SH", "kind": "etf", "list_date": "2012-05-28"})

    def test_wrong_market_and_b_shares_not_labeled_cny(self):
        for asset in [{"code": "159980.SH", "kind": "etf"}, {"code": "900901.SH", "kind": "stock"}, {"code": "200002.SZ", "kind": "stock"}]:
            with self.assertRaises(ValueError):
                normalize_asset(asset)

    def test_year_segments_have_no_gaps(self):
        self.assertEqual(list(year_chunks("2023-12-29", "2025-01-03")), [("2023-12-29", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-01-03")])

    def test_duplicate_conflict_is_not_silently_selected(self):
        conflict = self.raw.iloc[[0]].copy()
        conflict["close"] = 99
        with self.assertRaises(ValueError):
            unique_dates(pd.concat([self.raw, conflict]))

    def test_exact_duplicates_are_counted(self):
        d, count = unique_dates(pd.concat([self.raw, self.raw.iloc[[0]]]))
        self.assertEqual((len(d), count), (4, 1))

    def test_missing_price_not_filled_or_bridged(self):
        d, summary = self.clean(self.raw.drop(index=1), self.raw.drop(index=1))
        self.assertTrue(pd.isna(d.iloc[1]["close_raw"]))
        self.assertTrue(pd.isna(d.iloc[2]["return_adjusted"]))
        self.assertAlmostEqual(d.iloc[3]["return_adjusted"], 13/12-1)
        self.assertEqual(summary["issues"]["missing_unexplained"], 1)

    def test_invalid_ohlc_excluded_from_returns(self):
        raw = self.raw.copy()
        raw.loc[1, "high"] = 2
        d, _ = self.clean(raw)
        self.assertFalse(d.iloc[1]["price_valid"])
        self.assertTrue(pd.isna(d.iloc[2]["return_adjusted"]))

    def test_unadjusted_and_adjusted_remain_distinct(self):
        adjusted = self.raw.copy()
        for col in ["open", "close", "high", "low"]:
            adjusted[col] *= 2
        d, _ = self.clean(adjusted=adjusted)
        self.assertEqual(d.iloc[0]["close_raw"], 10)
        self.assertEqual(d.iloc[0]["close_adjusted"], 20)
        self.assertEqual(d.iloc[0]["volume_shares"], 100)
        self.assertEqual(d.iloc[0]["amount_cny"], 10000)

    def test_unknown_start_not_claimed_as_listing(self):
        d, _ = self.clean(self.raw.iloc[1:], self.raw.iloc[1:])
        self.assertEqual(d.iloc[0]["quality_status"], "before_first_observation_unverified")

    def test_verified_listing_not_filled(self):
        asset = dict(self.asset, list_date="2025-01-03", metadata_source="synthetic_fixture")
        d, _ = self.clean(self.raw.iloc[1:], self.raw.iloc[1:], asset)
        self.assertEqual(d.iloc[0]["quality_status"], "before_listing")
        self.assertTrue(pd.isna(d.iloc[0]["close_raw"]))

    def test_calendar_holiday_and_partial_period(self):
        dates = pd.date_range("2025-01-01", "2025-02-28", freq="B")
        dates = dates.difference(pd.date_range("2025-01-28", "2025-02-04"))
        cal = calendar_frame(pd.DataFrame({"trade_date": dates}), "synthetic")
        self.assertEqual(cal.loc[cal["is_month_end"], "date"].iloc[0], pd.Timestamp("2025-01-27"))
        self.assertFalse(cal.loc[cal["date"].eq("2025-01-15"), "is_month_end"].iloc[0])
        partial = calendar_frame(pd.DataFrame({"trade_date": dates[:8]}), "synthetic")
        self.assertFalse(partial["is_month_end"].any())

    def test_calendar_next_trade_is_not_next_calendar_day(self):
        cal = calendar_frame(pd.DataFrame({"trade_date": self.cal["date"]}), "synthetic")
        self.assertEqual(cal.iloc[1]["next_trade_date"], pd.Timestamp("2025-01-06"))

    def test_adjustment_key_cannot_fall_back_to_raw(self):
        class Response:
            def json(self):
                return {"data": {"sh510300": {"day": [["2025-01-02", 1, 1, 1, 1, 1]]}}}
        with patch("quantkit.providers.get", return_value=Response()), self.assertRaises(ValueError):
            market_tx({"tx_symbol": "sh510300", "start": "2025-01-01", "end": "2025-01-03", "adjust": "hfq"})

    def test_factor_one_needs_explicit_evidence(self):
        d = apply_sina_factors(self.raw, [{"d": "1900-01-01", "f": "1", "s": "1", "u": "0"}])
        self.assertEqual(d["close"].tolist(), self.raw["close"].tolist())

    def test_cash_addition_not_mistaken_for_factor(self):
        with self.assertRaises(ValueError):
            apply_sina_factors(self.raw, [{"d": "1900-01-01", "f": "1", "s": "1", "u": "0.5"}])

    def test_future_factor_not_applied_to_past(self):
        d = apply_sina_factors(self.raw, [{"d": "1900-01-01", "f": "1"}, {"d": "2025-01-06", "f": "2"}])
        self.assertEqual(d["close"].tolist(), [10, 11, 24, 26])

    def test_missing_early_factor_not_backfilled(self):
        with self.assertRaises(ValueError):
            apply_sina_factors(self.raw, [{"d": "2025-01-03", "f": "2"}])

    def test_monthly_statistics_not_available_on_month_first(self):
        d = macro_events("cn_pmi", pd.DataFrame({"月份": ["2025年01月份"], "制造业-指数": [50]}),
                         {"source": "akshare_eastmoney"}, "2025-01-01", "2025-01-31")
        self.assertFalse(d.iloc[0]["signal_eligible"])
        self.assertIsNone(d.iloc[0]["available_at"])
        self.assertTrue(align_macro(d, self.cal).empty)

    def test_us_close_not_available_in_china_same_date(self):
        d = macro_events("vix", pd.DataFrame({"DATE": ["01/02/2025"], "CLOSE": [20]}),
                         {"source": "cboe"}, "2025-01-01", "2025-01-07")
        aligned = align_macro(d, self.cal)
        self.assertTrue(pd.isna(aligned.iloc[0]["value"]))
        self.assertEqual(aligned.iloc[1]["value"], 20)

    def test_future_macro_change_does_not_change_past(self):
        a = macro_events("vix", pd.DataFrame({"DATE": ["01/02/2025", "01/06/2025"], "CLOSE": [20, 30]}),
                         {"source": "cboe"}, "2025-01-01", "2025-01-07")
        b = a.copy()
        b.loc[1, "value"] = 999
        pd.testing.assert_series_equal(align_macro(a, self.cal)["value"].iloc[:3], align_macro(b, self.cal)["value"].iloc[:3])

    def test_stale_vix_not_carried_indefinitely(self):
        a = macro_events("vix", pd.DataFrame({"DATE": ["01/02/2025"], "CLOSE": [20]}),
                         {"source": "cboe"}, "2025-01-01", "2025-01-07")
        out = align_macro(a, pd.DataFrame({"date": pd.to_datetime(["2025-02-03"])}))
        self.assertTrue(out.iloc[0]["stale"])
        self.assertTrue(pd.isna(out.iloc[0]["value"]))

    def test_cpi_yoy_uses_12_months(self):
        dates = pd.date_range("2024-01-01", periods=13, freq="MS")
        raw = pd.DataFrame({"observation_date": dates, "CPIAUCSL": [100]*12+[103]})
        d = macro_events("us_cpi_yoy", raw, {"source": "fred"}, "2025-01-01", "2025-01-31")
        self.assertAlmostEqual(d.iloc[0]["value"], 3)
        self.assertFalse(d.iloc[0]["signal_eligible"])

    def test_nonfarm_change_units_and_missing_month(self):
        raw = pd.DataFrame({"observation_date": ["2024-12-01", "2025-01-01", "2025-03-01"], "PAYEMS": [100, 102, 110]})
        d = macro_events("us_nonfarm_change", raw, {"source": "fred"}, "2025-01-01", "2025-03-31")
        self.assertEqual(d["value"].tolist(), [2])
        self.assertEqual(d.iloc[0]["unit"], "thousand_persons_change_sa")

    def test_nbs_pmi_release_time(self):
        s = '<meta name="ArticleTitle" content="2025年3月中国采购经理指数运行情况"><meta name="PubDate" content="2025-03-31"><p>发布时间：2025-03-31 09:30</p><p>制造业采购经理指数（PMI）为50.5%。</p>'
        e = nbs_article(s, "https://www.stats.gov.cn/example.html")
        self.assertEqual(e["period"], "2025-03")
        self.assertEqual(e["value"], 50.5)
        self.assertEqual(e["available_at"], "2025-03-31T09:30:00+08:00")

    def test_nbs_cpi_sign_and_date_only(self):
        s = '<meta name="ArticleTitle" content="2025年2月份居民消费价格同比下降0.7%"><meta name="PubDate" content="2025-03-09"><p>全国居民消费价格同比下降0.7%。</p>'
        e = nbs_article(s, "https://www.stats.gov.cn/example.html")
        self.assertEqual(e["value"], -0.7)
        self.assertEqual(e["available_at"], "2025-03-10T00:00:00+08:00")

    def test_nbs_unknown_template_fails(self):
        with self.assertRaises(ValueError):
            nbs_article("<p>没有来源和日期</p>", "https://www.stats.gov.cn/example.html")

    def fedwatch(self):
        return pd.DataFrame({"observation_at": ["2025-01-02T20:00:00-05:00"]*3,
            "meeting_date": ["2025-01-29"]*3, "target_lower": [4, 4.25, 4.5], "target_upper": [4.25, 4.5, 4.75],
            "probability": [.2, .7, .1], "current_target_upper": [4.5]*3,
            "source_url": ["https://www.cmegroup.com/synthetic"]*3, "provenance_note": ["synthetic test only"]*3})

    def test_rate_expectation_is_probability_not_yield(self):
        _, out = clean_fedwatch(self.fedwatch())
        self.assertAlmostEqual(out.iloc[0]["hike_probability"], .1)
        self.assertAlmostEqual(out.iloc[0]["cut_probability"], .2)
        self.assertAlmostEqual(out.iloc[0]["expected_target_midpoint"], 4.35)

    def test_wrong_probability_sum_rejected(self):
        data = self.fedwatch()
        data.loc[0, "probability"] = .8
        with self.assertRaises(ValueError):
            clean_fedwatch(data)

    def test_missing_timezone_rejected(self):
        data = self.fedwatch()
        data["observation_at"] = "2025-01-02 20:00:00"
        with self.assertRaises(ValueError):
            clean_fedwatch(data)

    def test_future_meeting_observation_rejected(self):
        data = self.fedwatch()
        data["observation_at"] = "2025-02-02T20:00:00-05:00"
        with self.assertRaises(ValueError):
            clean_fedwatch(data)


if __name__ == "__main__":
    unittest.main()
