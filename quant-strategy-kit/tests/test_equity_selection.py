"""核对缓冲区、单股上限和开盘成交的记账时点。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from strategykit.data import Dataset
from strategykit.engine import backtest
from strategykit.equity_selection import inverse_volatility, pool_snapshot, select_equities


class EquitySelectionRules(unittest.TestCase):
    def test_pool_change_requires_a_complete_snapshot_and_rejects_future_membership(self):
        signal = pd.Timestamp("2025-06-30")
        codes = [f"{600000+i}.SH" for i in range(800)]
        old = pd.DataFrame({"snapshot_date": signal, "code": codes, "csi800_member": True})
        self.assertEqual(len(pool_snapshot(old, signal, "csi800")), 800)
        with self.assertRaisesRegex(ValueError, "all_a"):
            pool_snapshot(old, signal, "all_a")
        broad = old.assign(all_a_member=True, all_a_pool_size=800)
        self.assertEqual(len(pool_snapshot(broad, signal, [codes[-1]])), 1)
        with self.assertRaisesRegex(ValueError, "all_a"):
            pool_snapshot(broad.iloc[1:], signal, "all_a")
        late = old.assign(csi800_member_asof=signal + pd.Timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "成分日期无效"):
            pool_snapshot(late, signal, "csi800")

    def test_buffer_prioritizes_retained_stock_and_respects_industry_cap(self):
        ranks = pd.DataFrame({"code":list("ABCDE"),"rank":[1,2,3,4,5],"industry":["金属","电子","电子","金属","钢铁"],
                              "volatility_120":[.4,.4,.4,.4,.4],"historical_name":list("ABCDE")})
        sleeve = {"id":"stocks","selection":{"top_n":3,"buffer_rank":4,"max_per_industry":1},
                  "weighting":{"max_weight":.4}}
        signal,execution = pd.to_datetime(["2025-06-30","2025-07-01"])
        with patch("strategykit.equity_selection.rank_equities",return_value=(ranks,{})):
            weights, chosen, audit = select_equities(sleeve,pd.DataFrame(),signal,execution,["D","C","E"])
        self.assertEqual(chosen.code.tolist(),["C","D","E"])
        self.assertEqual(audit["retained_count"],2)
        self.assertAlmostEqual(sum(weights.values()),1.)

    def test_inverse_volatility_redistributes_and_leaves_cash_if_cap_requires(self):
        v = pd.Series([.01]+[.3]*19)
        weight = inverse_volatility(v,.08)
        self.assertAlmostEqual(weight.iloc[0],.08)
        self.assertAlmostEqual(weight.sum(),1.)
        np.testing.assert_allclose(weight.iloc[1:],.92/19)
        self.assertAlmostEqual(inverse_volatility(v.iloc[:12],.08).sum(),.96)

    def test_open_execution_marks_the_intraday_gain_of_new_holdings(self):
        days = pd.DatetimeIndex(pd.to_datetime(["2025-06-30","2025-07-01","2025-07-02"]))
        opens = pd.DataFrame({"A":[100.,110.,120.]},index=days)
        closes = pd.DataFrame({"A":[100.,121.,132.]},index=days)
        d = Dataset(closes,closes.notna(),days,pd.DataFrame(),pd.DataFrame(),{},open_prices=opens)
        c = {"schema_version":1,"name":"开盘记账验证","start":"2025-07-01","end":"2025-07-02",
             "execution_price":"open","allocation_rebalance":"monthly","initial_capital":1100.,
             "sleeves":[{"id":"equity","weight":1.,"assets":[{"code":"A","kind":"stock"}]}]}
        result = backtest(c,d)
        np.testing.assert_allclose(result["daily"].nav,[1.10,1.20])
        self.assertAlmostEqual(result["final_state"]["books"]["equity"]["units"]["A"],10.)
        self.assertEqual(len(result["trades"]),1)
        self.assertEqual(result["trades"].signal_date.iloc[0],days[0])

    def test_multi_asset_rebalance_trades_only_differences_and_defers_restricted_side(self):
        days = pd.DatetimeIndex(pd.to_datetime(["2025-06-30", "2025-07-01", "2025-07-31",
                                               "2025-08-01", "2025-08-04"]))
        prices = pd.DataFrame({"A":[100.,100.,110.,110.,110.], "B":100., "G":100., "U":100.}, index=days)
        can_buy = prices.notna()
        can_buy.at[days[3], "B"] = False
        data = Dataset(prices,prices.notna(),days,pd.DataFrame(),pd.DataFrame(),{},close_buyable=can_buy)
        config = {"schema_version":1, "start":"2025-07-01", "end":"2025-08-04", "initial_capital":40000.,
                  "sleeves":[{"id":i, "weight":.25, "assets":[{"code":i,"kind":kind}]}
                             for i,kind in zip("ABGU", ("stock","etf","etf","us_etf"))]}
        result = backtest(config,data)
        trades = result["trades"].loc[lambda d: d.date.gt(days[1])].set_index("code")
        self.assertEqual(trades.amount.to_dict(), {"A":-750., "B":250., "G":250., "U":250.})
        self.assertEqual(trades.at["B", "date"], days[4])
        self.assertEqual(result["blocked"].code.tolist(), ["B"])
        quarter = backtest({**config,"allocation_rebalance":"quarterly"},data)
        self.assertTrue(quarter["trades"].date.eq(days[1]).all())
