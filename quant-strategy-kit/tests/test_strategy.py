"""行为检查：资金守恒、信号时序、独立调仓、宏观公布时点和优化结果。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT.parent / "quant-data-kit/scripts"))

from strategykit.config import validate, schedule
from strategykit.data import Dataset
from strategykit.engine import backtest
from strategykit.selection import minimum_variance, select_and_weight, quality_value
from strategykit.regime import allocation, observation
from strategykit.results import export_results, TABLE_COLUMNS
from strategykit.presets import preset
from quantkit.overseas import align_us
from quantkit.fundamentals import build_snapshot, prepare_tables
from quantkit.vintages import snapshot_events


def fixture():
    dates = pd.bdate_range("2024-10-01", "2025-04-30")
    t = np.arange(len(dates))
    px = pd.DataFrame({"A": 100*np.exp(.0005*t+.01*np.sin(t)), "B": 100*np.exp(.0002*t+.004*np.cos(t))}, index=dates)
    data = Dataset(px, px.notna(), dates, pd.DataFrame(), pd.DataFrame(), {"source": "人工测试数据"})
    sleeves = [{"id": name, "name": name, "weight": .5, "rebalance": "quarterly",
                "assets": [{"code": code, "kind": "stock"}], "selection": {"method": "fixed"},
                "weighting": {"method": "equal"}} for name, code in [("one", "A"), ("two", "B")]]
    c = validate({"schema_version": 1, "name": "测试", "start": "2025-01-01", "end": "2025-04-30",
                  "initial_capital": 1000., "allocation_rebalance": "quarterly", "sleeves": sleeves, "macro": {"enabled": False}})
    return c, data


class StrategyRules(unittest.TestCase):
    def test_result_handoff_can_be_read_without_renderer_or_engine(self):
        c, d = fixture()
        # 独立消费CSV/JSON，重新复算资金与年度收益；结果目录不应生成展示文件。
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'results'
            summary, _ = export_results(c, d, out)
            handoff = json.loads((out / 'handoff.json').read_text())
            self.assertEqual({p.suffix for p in out.iterdir()}, {'.csv', '.json'})
            self.assertEqual({x['path'] for x in handoff['files']}, {p.name for p in out.iterdir()} - {'handoff.json'})
            for entry in handoff['files']:
                if entry['format'] == 'csv':
                    frame = pd.read_csv(out / entry['path'])
                    self.assertEqual(frame.columns.tolist(), entry['columns'])
                    self.assertEqual(len(frame), entry['rows'])
            daily, annual, weights = [pd.read_csv(out / (key+'.csv')) for key in ['daily', 'annual', 'weights']]
            self.assertAlmostEqual((1+annual['return']).prod(), daily.nav.iloc[-1])
            self.assertAlmostEqual(weights.loc[weights.date.eq(weights.date.max()), 'value'].sum(), summary['final_equity'])
            for key in ['blocked', 'valuation_gaps']:
                self.assertEqual(pd.read_csv(out / (key+'.csv')).columns.tolist(), TABLE_COLUMNS[key])
            self.assertEqual(json.loads((out / 'unfilled_at_end.json').read_text()), [])

    def test_qdii_variant_changes_equity_instruments_preserves_bond_and_rules(self):
        base, qdii = preset('cross-border-allocation'), validate(preset('qdii-equity-allocation'))
        for original, changed in zip(base['sleeves'], qdii['sleeves']):
            if original['id'] in {'overseas_growth', 'overseas_broad'}:
                self.assertEqual(changed['assets'][0]['kind'], 'etf')
                self.assertEqual(changed['assets'][0]['currency'], 'CNY')
                self.assertEqual(changed['weight'], original['weight'])
            else:
                self.assertEqual(original, changed)

    def test_first_signal_is_previous_day(self):
        c, d = fixture()
        s = schedule(d.calendar, c["start"], c["end"], "monthly")
        self.assertEqual(s[pd.Timestamp("2025-01-01")], pd.Timestamp("2024-12-31"))
        self.assertEqual(s[pd.Timestamp("2025-02-03")], pd.Timestamp("2025-01-31"))

    def test_buys_do_not_receive_execution_days_prior_gain(self):
        c, d = fixture()
        d.prices[:] = 100.
        d.prices.loc["2025-01-01":, "A"] = 200.
        r = backtest(c, d)
        self.assertTrue(np.allclose(r["daily"]["nav"], 1.))

    def test_buy_hold_has_analytic_nav_and_no_daily_rebalance(self):
        c, d = fixture()
        c["end"] = "2025-03-20"
        r = backtest(c, d)
        x = d.prices.loc[c["start"]:c["end"]]
        expected = .5*x.A/x.A.iloc[0] + .5*x.B/x.B.iloc[0]
        np.testing.assert_allclose(r["daily"]["nav"], expected, atol=1e-12)
        self.assertEqual(r["trades"]["date"].nunique(), 1)

    def test_weekly_stock_does_not_rebalance_other_sleeve(self):
        c, d = fixture()
        c["end"] = "2025-03-20"
        c["sleeves"][0]["rebalance"] = "weekly"
        c["sleeves"][0]["assets"] += [{"code": "B", "kind": "stock"}]
        r = backtest(c, d)
        self.assertGreater(r["trades"].loc[r["trades"].sleeve.eq("one"), "date"].nunique(), 8)
        self.assertEqual(r["trades"].loc[r["trades"].sleeve.eq("two"), "date"].nunique(), 1)
        np.testing.assert_allclose(r["weights"].groupby("date").weight.sum(), 1, atol=1e-12)

    def test_future_price_changes_do_not_change_prefix(self):
        c, d = fixture()
        c["sleeves"][0]["assets"] += [{"code": "B", "kind": "stock"}]
        c["sleeves"][0]["weighting"] = {"method": "min_variance", "lookback": 50, "min_observations": 30}
        c["sleeves"][0]["rebalance"] = "weekly"
        r1 = backtest(c, d)
        changed = copy.deepcopy(d)
        changed.prices.loc["2025-03-01":, "A"] *= 100
        r2 = backtest(c, changed)
        a, b = [r["daily"].loc[r["daily"].date.lt("2025-03-01"), "nav"] for r in [r1, r2]]
        np.testing.assert_array_equal(a.values, b.values)

    def test_zero_cost_self_financing_with_macro_budget_changes(self):
        c, d = fixture()
        c["macro"] = {"enabled": True, "rebalance": "monthly", "rules": [{"name": "高波动", "all": [{"series": "vix", "op": ">", "threshold": 20, "max_age_days": 300}], "weights": {"one": .2, "two": .6}}]}
        d.macro = pd.DataFrame([{ "series": "vix", "period": "2024-12-30", "value": 30., "available_at": pd.Timestamp("2024-12-31T00:00:00Z"), "signal_eligible": True}])
        r = backtest(c, d)
        self.assertAlmostEqual(sum(r["contributions"].values()), r["daily"].equity.iloc[-1]-1000, places=8)
        self.assertTrue((r["daily"]["cash_weight"] > 0).all())
        self.assertTrue((pd.to_datetime(r["trades"].signal_date) < pd.to_datetime(r["trades"].date)).all())

    def test_missing_quote_defers_trade_and_preserves_cash(self):
        c, d = fixture()
        d.tradable.loc["2025-01-01", "A"] = False
        r = backtest(c, d)
        bought = r["trades"].loc[(r["trades"].code == "A") & (r["trades"].side == "buy")]
        self.assertEqual(bought.date.iloc[0], pd.Timestamp("2025-01-02"))
        self.assertGreater(r["daily"].cash_weight.iloc[0], .49)

    def test_min_variance_analytical_diagonal_covariance(self):
        r = pd.DataFrame({"A": [.02, -.02, .02, -.02], "B": [.01, .01, -.01, -.01]})
        w, info = minimum_variance(r, shrinkage=0)
        np.testing.assert_allclose(w, [.2, .8], atol=1e-6)
        self.assertLess(info["variance"], info["equal_variance"])

    def test_min_variance_cap_and_singular_covariance(self):
        r = pd.DataFrame({"A": [.02, -.02, .02, -.02], "B": [.01, .01, -.01, -.01]})
        w, _ = minimum_variance(r, max_weight=.6)
        np.testing.assert_allclose(w, [.4, .6], atol=1e-6)
        r["B"] = r["A"]
        w, _ = minimum_variance(r, shrinkage=0)
        self.assertAlmostEqual(w.sum(), 1.)

    def test_insufficient_history_is_explicit_equal_fallback(self):
        c, d = fixture()
        s = c["sleeves"][0]
        s["assets"] += [{"code": "B", "kind": "stock"}]
        s["weighting"] = {"method": "min_variance", "lookback": 126, "min_observations": 100}
        w, _, audit = select_and_weight(s, pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-01"), d.prices, d.features)
        self.assertEqual(audit["status"], "fallback_equal")
        self.assertEqual(w, {"A": .5, "B": .5})

    def test_macro_future_release_is_invisible(self):
        c, d = fixture()
        d.macro = pd.DataFrame([{"series": "cn_pmi", "period": "2024-12", "value": 55,
                                 "available_at": pd.Timestamp("2025-01-02T00:00:00Z"), "signal_eligible": True}])
        value, _ = observation(d.macro, {"series": "cn_pmi"}, pd.Timestamp("2024-12-31"))
        self.assertIsNone(value)
        value, _ = observation(d.macro, {"series": "cn_pmi"}, pd.Timestamp("2025-01-02"))
        self.assertEqual(value, 55)

    def test_late_revision_of_old_month_does_not_replace_new_month(self):
        e = pd.DataFrame([{"series": "cn_pmi", "period": p, "value": v, "available_at": pd.Timestamp(t), "signal_eligible": True}
                          for p, v, t in [("2024-11", 49, "2024-12-01T00:00Z"), ("2024-12", 51, "2025-01-01T00:00Z"), ("2024-11", 48, "2025-01-02T00:00Z")]])
        value, _ = observation(e, {"series": "cn_pmi"}, pd.Timestamp("2025-01-03"))
        self.assertEqual(value, 51)

    def test_unverified_macro_values_not_used(self):
        e = pd.DataFrame([{"series": "us_cpi_yoy", "period": "2024-12", "value": 5, "available_at": pd.Timestamp("2025-01-01T00:00Z"), "signal_eligible": False}])
        self.assertIsNone(observation(e, {"series": "us_cpi_yoy"}, pd.Timestamp("2025-01-10"))[0])

    def test_us_same_date_close_not_visible_in_china(self):
        d = pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "Close": [100, 200], "Adj Close": [100, 200], "Volume": [1000, 1000]})
        fx = pd.DataFrame({"date": ["2025-01-01", "2025-01-02"], "Close": [7., 7.]})
        out = align_us("TEST", d, fx, pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]))
        self.assertTrue(pd.isna(out.close_adjusted.iloc[0]))
        self.assertEqual(out.close_adjusted.iloc[1], 700)
        self.assertEqual(out.close_adjusted.iloc[2], 1400)

    def test_overweight_configuration_is_rejected(self):
        c, _ = fixture()
        c["sleeves"][0]["weight"] = .8
        with self.assertRaises(ValueError):
            validate(c)

    def test_annual_financials_filter_by_announcement_not_period(self):
        basic = pd.DataFrame([{"ts_code": "600001.SH", "name": "测试", "list_date": "20000101", "delist_date": None}])
        members = pd.DataFrame([{"ts_code": "600001.SH", "l1_name": "工业", "l1_code": "I", "l3_name": "设备", "in_date": "20000101", "out_date": None}])
        fina = pd.DataFrame([{"ts_code": "600001.SH", "ann_date": a, "end_date": p, "roe": roe, "roe_yearly": roe,
                              "debt_to_assets": 40, "profit_dedt": 10, "ocfps": 1, "update_flag": "0"}
                             for a, p, roe in [("20230301", "20221231", 10), ("20240301", "20231231", 12), ("20250401", "20241231", 100)]])
        names = pd.DataFrame([{"ts_code": "600001.SH", "name": "测试", "start_date": "20000101", "end_date": None, "ann_date": "20000101"}])
        v = pd.DataFrame([{"ts_code": "600001.SH", "trade_date": "20250331", "pe": 10, "pe_ttm": 10, "total_mv": 100}])
        result = build_snapshot("2025-03-31", v, *prepare_tables(basic, members, fina, names))
        self.assertEqual(result.annual_roe.iloc[0], 12)
        self.assertEqual(result.annual_count.iloc[0], 2)

    def test_custom_filter_does_not_inherit_preset_leader_or_cashflow_rules(self):
        features = pd.DataFrame([{"code": "600001.SH", "snapshot_date": pd.Timestamp("2024-12-31"),
                                 "ann_date": pd.Timestamp("2024-11-01"), "annual_ann_date": pd.Timestamp("2024-03-01"),
                                 "annual_end_date": pd.Timestamp("2023-12-31"), "end_date": pd.Timestamp("2024-09-30"),
                                 "list_date": pd.Timestamp("2000-01-01"), "delist_date": pd.NaT,
                                 "historical_name": "测试公司", "industry": "工业", "pe_ttm": 15., "annual_roe": 12.,
                                 "annual_ocfps": -10., "debt_to_assets": 95.}])
        params = {"method": "fundamental", "top_n": 1, "filters": [{"field": "pe_ttm", "op": "<", "threshold": 30},
                    {"field": "annual_roe", "op": ">", "threshold": 10}], "rank_by": [{"field": "pe_ttm", "ascending": True}]}
        selected = quality_value(features, pd.Timestamp("2024-12-31"), params)
        self.assertEqual(selected.code.tolist(), ["600001.SH"])
        changed = features.copy()
        changed["annual_ann_date"] = pd.Timestamp("2025-03-01")
        self.assertTrue(quality_value(changed, pd.Timestamp("2024-12-31"), params).empty)

    def test_equal_fallback_honors_cap_by_retaining_cash(self):
        c, d = fixture()
        s = c["sleeves"][0]
        s["assets"] += [{"code": "B", "kind": "stock"}]
        s["weighting"] = {"method": "min_variance", "lookback": 40, "min_observations": 30, "max_weight": .4}
        w, _, audit = select_and_weight(s, pd.Timestamp("2024-12-31"), pd.Timestamp("2025-01-01"), d.prices, d.features)
        self.assertEqual(w, {"A": .4, "B": .4})
        self.assertAlmostEqual(audit["invested_fraction"], .8)

    def test_long_quote_gap_keeps_cashflow_without_trading_on_missing_prices(self):
        c, d = fixture()
        c["end"] = "2025-02-28"
        c["long_gap_policy"] = "carry_and_flag"
        c["max_valuation_gap"] = 3
        d.prices.loc["2025-01-15":"2025-02-10", "A"] = np.nan
        d.tradable.loc["2025-01-15":"2025-02-10", "A"] = False
        r = backtest(c, d)
        self.assertGreater(len(r["valuation_gaps"]), 0)
        x = d.prices.loc[c["start"]:c["end"]].ffill()
        np.testing.assert_allclose(r["daily"].nav, .5*x.A/x.A.iloc[0]+.5*x.B/x.B.iloc[0], atol=1e-12)

    def test_fred_snapshots_do_not_mingle_revisions_in_derived_change(self):
        rows = []
        for asof, vals in [("2025-01-10", [100, 110]), ("2025-02-10", [102, 112])]:
            for date, v in zip(["2024-11-01", "2024-12-01"], vals):
                rows.append({"series_id": "PAYEMS", "vintage_asof": asof, "date": date, "value": v})
        events = snapshot_events(pd.DataFrame(rows))
        added = events.loc[events.series.eq("us_nonfarm_change")]
        self.assertEqual(added.value.tolist(), [10., 10.])
        self.assertTrue((pd.to_datetime(added.available_at, utc=True) > pd.to_datetime(added.vintage, utc=True)).all())

    def test_missing_macro_falls_back_to_declared_base(self):
        c, d = fixture()
        c["macro"] = {"enabled": True, "missing": "base_weights", "rules": [{"name": "增长", "all": [{"series": "cn_pmi", "op": ">", "threshold": 50}], "weights": {"one": .9, "two": .1}}]}
        weights, detail = allocation(c, pd.DataFrame(), pd.Timestamp("2024-12-31"), {"one": .1, "two": .9})
        self.assertEqual(weights, {"one": .5, "two": .5})
        self.assertTrue(detail["missing"])


if __name__ == "__main__":
    unittest.main()
