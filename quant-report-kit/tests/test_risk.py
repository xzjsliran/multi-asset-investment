import unittest
from pathlib import Path
import sys
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from reportkit.risk import analyse_risk
from reportkit.analysis import performance


def sample():
    # 第二部分反向收益代表真实分散作用，不含负持仓。
    first=np.array([0,.01,-.02,.015,.003,-.004,.018,-.011])
    second=-first*.25
    total=first+second
    equity=1000*np.cumprod(1+total)
    previous=np.r_[1000,equity[:-1]]
    return pd.DataFrame({"date":pd.bdate_range("2025-01-01",periods=len(total)),"nav":equity/1000,"equity":equity,
                         "pnl_a":first*previous,"pnl_b":second*previous,"pnl_cash":0})


class RiskReporting(unittest.TestCase):
    def test_negative_risk_and_additive_vol_match_report(self):
        d=sample();r=analyse_risk(d,{"a":"股票","b":"债券"})
        self.assertLess(r["rows"][1]["risk_share"],0)
        self.assertAlmostEqual(sum(x["risk_share"] for x in r["rows"]),1)
        self.assertAlmostEqual(sum(x["volatility_contribution"] for x in r["rows"]),performance(d)[1]["annual_volatility"])
        self.assertIsNone(r["correlations"][-1]["correlation"])

    def test_subperiod_does_not_use_earlier_pnl(self):
        d=sample().iloc[3:].copy();r=analyse_risk(d,{})
        d.iloc[0,d.columns.get_loc('pnl_a')]=999999
        self.assertEqual(r,analyse_risk(d,{}))
        self.assertEqual(r["start"],"2025-01-06")

    def test_incomplete_daily_pnl_rejected_even_if_period_sum_matches(self):
        d=sample();d.loc[2,"pnl_a"]+=1;d.loc[3,"pnl_a"]-=1
        with self.assertRaisesRegex(ValueError,"逐日"):analyse_risk(d,{})

    def test_no_pnl_and_zero_risk_do_not_invent_percentages(self):
        d=sample();self.assertEqual(analyse_risk(d[['date','nav']],{})['status'],'unavailable')
        d[['pnl_a','pnl_b']]=0;d['equity']=1000;d['nav']=1
        r=analyse_risk(d,{})
        self.assertEqual(r['status'],'zero_volatility')
        self.assertTrue(all(x['risk_share'] is None for x in r['rows']))

if __name__ == "__main__":unittest.main()
