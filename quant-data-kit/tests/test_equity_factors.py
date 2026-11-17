"""公告日、TTM 累计额和分红实施的回归测试。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from quantkit.equity_factor_inputs import index_members
from quantkit.equity_factors import cash_dividend_versions, dividend_snapshot, financial_snapshot, statement_versions, universe_snapshot


class EquityFinancialDates(unittest.TestCase):
    def test_index_membership_uses_only_latest_available_snapshot(self):
        sample = pd.DataFrame({"con_code": ["A", "B", "A"], "trade_date": ["20250530", "20250630", "20250731"]})
        pool = {"name": "test", "size": 1, "index_code": "000906.SH"}
        with patch("quantkit.equity_factor_inputs.cached_query", side_effect=[sample.iloc[:0], sample]):
            codes, asof = index_members(Path("."), pool, pd.Timestamp("2025-07-11"))
        self.assertEqual(codes, ["B"])
        self.assertEqual(asof, pd.Timestamp("2025-06-30"))

    def test_all_a_excludes_preopening_beijing_and_marks_candidate_count(self):
        values = pd.DataFrame({"ts_code": ["000001.SZ", "688001.SH", "920001.BJ", "511260.SH"]})
        pools = [{"name": "all_a"}, {"name": "star_market"}]
        earlier, _ = universe_snapshot(Path("."), pd.Timestamp("2021-10-29"), pools, values)
        later, counts = universe_snapshot(Path("."), pd.Timestamp("2021-11-30"), pools, values)
        self.assertEqual(list(earlier.ts_code), ["000001.SZ", "688001.SH"])
        self.assertEqual(later.loc[later.star_market_member, "ts_code"].tolist(), ["688001.SH"])
        self.assertEqual(counts, {"all_a": 3, "star_market": 1})

    def test_ttm_needs_all_three_as_of_reports_and_ignores_late_revision(self):
        periods = ["20200930","20201231","20210930"]
        common = {"ts_code":"A","end_date":periods,"ann_date":["20201020","20210330","20211020"],
                  "f_ann_date":["20201025","20210330","20211025"],"report_type":"1","update_flag":"1"}
        income = pd.DataFrame(common).assign(n_income_attr_p=[10.,15.,16.])
        cashflow = pd.DataFrame(common).assign(n_cashflow_act=[20.,30.,28.],c_pay_acq_const_fiolta=[5.,6.,9.])
        later = cashflow.iloc[[-1]].assign(ann_date="20211102",f_ann_date="20211102",n_cashflow_act=999.)
        cashflow = pd.concat([cashflow,later])
        balance = pd.DataFrame(common).assign(total_hldr_eqy_exc_min_int=[90.,100.,120.],goodwill=[2.,3.,4.])
        statements = {
            "income":statement_versions(income, ["n_income_attr_p"]),
            "cashflow":statement_versions(cashflow, ["n_cashflow_act","c_pay_acq_const_fiolta"]),
            "balancesheet":statement_versions(balance, ["total_hldr_eqy_exc_min_int","goodwill"]),
        }
        before = financial_snapshot(statements,pd.Timestamp("2021-10-22")).iloc[0]
        self.assertEqual(before.statement_period,pd.Timestamp("2020-12-31"))
        result = financial_snapshot(statements,pd.Timestamp("2021-10-29")).iloc[0]
        self.assertEqual(result.pub_date,pd.Timestamp("2021-10-25"))
        self.assertEqual((result.net_profit_ttm,result.operating_cashflow_ttm,result.capex_ttm),(21,38,10))
        self.assertAlmostEqual(result.roe_ttm,21./105.)

    def test_dividend_requires_implementation_announcement_and_ex_date(self):
        raw = pd.DataFrame({"ts_code":["A","B","C"],"end_date":"20201231","ann_date":"20210401",
                            "imp_ann_date":["20210520","20210615","20210520"],
                            "ex_date":["20210524","20210524","20210603"],"div_proc":"实施",
                            "cash_div_tax":.2,"base_share":100.})
        earned, missing = dividend_snapshot(cash_dividend_versions(raw),pd.Timestamp("2021-05-31"))
        self.assertEqual(list(earned.index),["A"])
        self.assertEqual(earned.at["A","dividend_cash_cny"],200000.)
        self.assertFalse(missing)
