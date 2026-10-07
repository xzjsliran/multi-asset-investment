import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from strategykit.risk import equal_risk_weights, risk_contributions, allocate_risk
from strategykit.config import validate, data_plan
from strategykit.data import Dataset
from strategykit.engine import backtest
from strategykit.results import export_results


def fixture():
    dates=pd.bdate_range("2024-07-01","2025-07-31")
    rng=np.random.default_rng(8)
    prices=pd.DataFrame(100*np.exp(np.cumsum(rng.normal(0,[.004,.02],(len(dates),2)),axis=0)),index=dates,columns=["A","B"])
    c={"schema_version":1,"name":"多资产风险平价策略","start":"2025-01-01","end":"2025-07-31",
       "initial_capital":1000,"allocation_rebalance":"quarterly",
       "sleeves":[{"id":x,"name":x,"weight":.5,"rebalance":"monthly","assets":[{"code":x,"kind":"etf"}]} for x in ["A","B"]],
       "allocation":{"method":"risk_parity","proxies":{x:[{"code":x,"kind":"etf"}] for x in ["A","B"]}}}
    return validate(c),Dataset(prices,prices.notna(),dates,pd.DataFrame(),pd.DataFrame(),{})


class RiskAllocation(unittest.TestCase):
    def test_diagonal_covariance_matches_inverse_vol_and_equal_risk(self):
        cov=np.diag([.01,.04,.09]); w=equal_risk_weights(cov)
        expected=np.array([10,5,10/3]);expected/=expected.sum()
        np.testing.assert_allclose(w,expected,atol=1e-6)
        np.testing.assert_allclose(risk_contributions(cov,w)["risk_shares"],[1/3]*3,atol=1e-6)

    def test_correlations_change_solution_and_cash_scales_vol_only(self):
        cov=np.array([[.04,.012,-.004],[.012,.0225,0],[-.004,0,.01]])
        w=equal_risk_weights(cov);full=risk_contributions(cov,w);cash=risk_contributions(cov,w*.8)
        np.testing.assert_allclose(full["risk_shares"],[1/3]*3,atol=1e-5)
        self.assertAlmostEqual(cash["annual_volatility"],.8*full["annual_volatility"])
        self.assertFalse(np.allclose(w,(1/np.sqrt(np.diag(cov)))/(1/np.sqrt(np.diag(cov))).sum()))

    def test_future_prices_do_not_change_signal_or_prior_path(self):
        c,d=fixture();signal=pd.Timestamp("2025-03-31")
        w,r=allocate_risk(c,d.prices,signal,{"A":.5,"B":.5})
        changed=d.prices.copy();changed.loc[changed.index>signal,"B"]*=20
        w2,r2=allocate_risk(c,changed,signal,{"A":.5,"B":.5})
        self.assertEqual(w,w2);self.assertEqual(r,r2)
        first=backtest(c,d);altered=copy.deepcopy(d);altered.prices=changed
        second=backtest(c,altered)
        pd.testing.assert_frame_equal(first["daily"].loc[first["daily"].date<=signal],second["daily"].loc[second["daily"].date<=signal])
        self.assertTrue(first["weights"]["weight"].ge(0).all())
        self.assertTrue(first["daily"]["cash_weight"].ge(0).all())

    def test_missing_prices_and_zero_vol_are_auditable_fallbacks(self):
        c,d=fixture();signal=pd.Timestamp("2025-03-31")
        d.prices.loc[signal,"A"]=np.nan
        w,r=allocate_risk(c,d.prices,signal,{"A":.2,"B":.8})
        self.assertEqual(w,{"A":.5,"B":.5});self.assertEqual(r["status"],"fallback_base_weights")
        c["allocation"]["fallback"]="hold"
        self.assertEqual(allocate_risk(c,d.prices,signal,{"A":.2,"B":.8})[0],{"A":.2,"B":.8})
        c["allocation"]["fallback"]="error"
        with self.assertRaises(ValueError):allocate_risk(c,d.prices,signal,{})
        c,d=fixture();d.prices["A"]=100
        self.assertIn("波动",allocate_risk(c,d.prices,signal,{})[1]["reason"])

    def test_proxy_requirements_and_macro_conflict(self):
        c,d=fixture();c["allocation"]["proxies"]["A"][0]["code"]="PROXY"
        self.assertIn("PROXY",[a["code"] for a in data_plan(c)["assets"]])
        c["macro"]={"enabled":True,"rules":[{"name":"条件","all":[{"series":"vix","op":">","threshold":20}],"weights":{"A":.2,"B":.8}}]}
        with self.assertRaisesRegex(ValueError,"不能同时"):validate(c)
        c,d=fixture();del c["allocation"]["proxies"]["A"]
        with self.assertRaisesRegex(ValueError,"代理"):validate(c)

    def test_only_outer_events_recalculate_and_static_is_comparable(self):
        c,d=fixture();r=backtest(c,d)
        self.assertEqual(len(r["regimes"]),3)
        self.assertTrue(all(x["sample_end"]<=x["signal_date"]<x["execution_date"] for x in r["regimes"]))
        with tempfile.TemporaryDirectory() as temp:
            export_results(c,d,temp)
            handoff=json.loads(Path(temp,"handoff.json").read_text())
            self.assertIn("static",[s["id"] for s in handoff["scenarios"]])
            stat=copy.deepcopy(c);stat["allocation"]={"method":"fixed"}
            np.testing.assert_allclose(pd.read_csv(Path(temp,"static_daily.csv")).nav,backtest(stat,d)["daily"].nav)

if __name__ == "__main__":unittest.main()
