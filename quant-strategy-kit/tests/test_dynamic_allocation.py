"""组级现金、波指切换以及场外基金回款的回归。"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from strategykit.allocation import bounded_weights, target_allocation
from strategykit.config import data_plan, validate
from strategykit.data import Dataset
from strategykit.engine import backtest
from strategykit.presets import preset
from strategykit.volatility import volatility_state


def events(series, dates, values):
    return pd.DataFrame([{"series": series, "period": day.strftime("%Y-%m-%d"), "value": value,
                          "available_at": day.tz_localize("UTC"), "signal_eligible": True}
                         for day, value in zip(dates, values)])


class DynamicAllocation(unittest.TestCase):
    def test_shared_cash_is_bounded_and_waits_for_buy_signal(self):
        c = validate(preset("regional-macro-allocation"))
        base = {s["id"]: s["weight"] for s in c["sleeves"]}
        limits = c["allocation_limits"]
        sold, audit = bounded_weights(base, base, dict.fromkeys(limits["groups"], -.05), limits)
        self.assertAlmostEqual(audit["cash_weight"], .25)
        self.assertAlmostEqual(sold["equity"] + sold["stocks"], .15)
        self.assertAlmostEqual(sold["overseas_broad"] + sold["overseas_growth"], .15)
        self.assertAlmostEqual(sold["gold"], .05)
        untouched, audit = bounded_weights(base, base, {}, limits, sold)
        for sid, value in sold.items():
            self.assertAlmostEqual(value, untouched[sid])
        self.assertAlmostEqual(audit["cash_weight"], .25)
        for buys in ({"gold": .05}, dict.fromkeys(limits["groups"], .05)):
            new, audit = bounded_weights(base, base, buys, limits, sold)
            self.assertLess(audit["cash_weight"], .25)
            self.assertAlmostEqual(sum(new.values()) + audit["cash_weight"], 1.)
        self.assertAlmostEqual(audit["cash_weight"], 0.)

    def test_regional_inputs_are_isolated_and_actions_can_cross(self):
        c = validate(preset("regional-macro-allocation"))
        latest = pd.Timestamp("2025-05-30")
        dates = pd.date_range("2025-01-01", periods=4, freq="MS")
        macro = pd.concat([events("us_unemployment", dates, [4, 4, 4.1, 4.2]),
                           events("us_cpi_yoy", dates, [2, 2.1, 2.1, 2.3])], ignore_index=True)
        macro["period"] = macro["period"].str[:7]
        d = Dataset(pd.DataFrame(), pd.DataFrame(), pd.DatetimeIndex([]), pd.DataFrame(), macro, {})
        weights, info = target_allocation(c, d, latest, {})
        self.assertTrue(info["overlay"]["regions"]["CN"]["missing"])
        self.assertFalse(info["overlay"]["regions"]["US"]["missing"])
        self.assertAlmostEqual(weights["bond_cny"], .275)
        self.assertAlmostEqual(weights["gold"], .125)
        self.assertAlmostEqual(weights["overseas_broad"] + weights["overseas_growth"], .15)
        c["macro"]["regions"]["CN"]["rules"][0]["all"][0]["series"] = "us_unemployment"
        with self.assertRaisesRegex(ValueError, "该地区"):
            validate(c)

    def test_domestic_switch_has_separate_warmup_and_no_us_vix(self):
        c = validate(preset("domestic-volatility-allocation"))
        self.assertEqual(data_plan(c)["macro"], ["cn_vix300", "cn_vix50"])
        old = pd.bdate_range("2019-10-01", "2020-02-28")
        new = pd.bdate_range("2019-12-23", "2020-02-28")
        macro = pd.concat([events("cn_vix50", old, [25.] * len(old)),
                           events("cn_vix300", new, [18.] * len(new))])
        spec = c["volatility"]["signals"][0]
        self.assertEqual(volatility_state(spec, macro, pd.Timestamp("2020-01-10"))["series"], "cn_vix50")
        state = volatility_state(spec, macro, pd.Timestamp("2020-02-28"))
        self.assertEqual(state["series"], "cn_vix300")
        self.assertEqual(state["switch_date"], new[29].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(state["slow_average"], 18.)

    def test_us_buffer_holds_until_exit_and_only_reduces_us_equity(self):
        c = validate(preset("vix-dynamic-allocation"))
        self.assertEqual(c["volatility"]["signals"][0]["groups"], ["us_equity"])
        days = pd.bdate_range("2025-01-01", periods=39)
        d = events("vix", days, [17.] * 30 + [21.] * 5 + [19.9, 19.5, 19., 17.9])
        spec = c["volatility"]["signals"][0]
        self.assertTrue(volatility_state(spec, d, days[-2])["defensive"])
        self.assertFalse(volatility_state(spec, d, days[-1])["defensive"])

    def test_fund_redemption_enters_cash_only_after_settlement(self):
        days = pd.bdate_range("2024-12-20", "2025-02-14")
        prices = pd.DataFrame({"050025.OF": 100., "B": 100.}, index=days)
        d = Dataset(prices, prices.notna(), days, pd.DataFrame(),
                    events("vix", [pd.Timestamp("2025-01-31")], [25.]), {})
        c = {"schema_version": 1, "name": "场外回款测试", "start": "2025-01-01", "end": "2025-02-14",
             "initial_capital": 1000., "sleeves": [
                {"id": "fund", "weight": .5,
                 "assets": [{"code": "050025.OF", "kind": "qdii_nav", "redemption_days": 3}]},
                {"id": "bond", "weight": .5, "assets": [{"code": "B", "kind": "etf"}]}],
             "macro": {"enabled": True, "rules": [
                {"name": "减持基金", "all": [{"series": "vix", "op": ">", "threshold": 20}],
                 "weights": {"fund": .3, "bond": .7}}]}}
        result = backtest(c, d)
        fills = result["trades"].loc[result["trades"]["date"] >= pd.Timestamp("2025-02-01")]
        self.assertEqual(fills["date"].dt.strftime("%Y-%m-%d").tolist(), ["2025-02-03", "2025-02-06"])
        self.assertEqual(fills["amount"].tolist(), [-200., 200.])
        self.assertEqual(result["daily"]["unsettled_redemptions_cny"].max(), 200.)
        self.assertTrue(result["daily"]["equity"].eq(1000.).all())


if __name__ == "__main__":
    unittest.main()
