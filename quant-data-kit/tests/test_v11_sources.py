"""净值公布时点、活期计息和波指来源的回归检查。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from quantkit.instruments import bank_rate, deposit_marks, nav_marks
from quantkit.local_volatility import volatility_closes
from quantkit.providers import nbs_article, pbc_rate_article
from quantkit.vintages import realtime_events


class Sources(unittest.TestCase):
    def test_fund_marks_use_announcement_and_prefer_first_version(self):
        raw = pd.DataFrame([
            ["20250102", "20250103", 1., 1.1], ["20250103", "20250106", 1.1, 1.21],
            ["20250102", "20250110", 1.05, 1.155],
        ], columns=["nav_date", "ann_date", "unit_nav", "adj_nav"])
        dates = pd.bdate_range("2025-01-03", "2025-01-13")
        out = nav_marks("050025.OF", raw, dates).set_index("date")
        self.assertFalse(out.at["2025-01-03", "price_valid"])
        self.assertEqual(out.at["2025-01-06", "close_raw"], 1.)
        self.assertEqual(out.at["2025-01-07", "close_adjusted"], 1.21)
        self.assertEqual(out.at["2025-01-13", "close_raw"], 1.1)

    def test_usd_marks_accrue_with_old_rate_and_respect_fx_availability(self):
        history = pd.DataFrame({"date": ["2025-01-02", "2025-01-03"], "Close": [7.0, 7.2]})
        rates = pd.DataFrame([{"from": "2025-01-01", "rate_percent": 3.65, "source": "test"}])
        dates = pd.bdate_range("2025-01-03", "2025-01-07")
        out = deposit_marks("USD_DEPOSIT", history, rates, dates).set_index("date")
        self.assertEqual(out.at["2025-01-03", "close_raw"], 7.)
        self.assertAlmostEqual(out.at["2025-01-06", "close_adjusted"], 7.2 * 1.0001 ** 4)
        fred = deposit_marks("USD_DEPOSIT", history, rates, dates, "fred_DEXCHUS").set_index("date")
        self.assertFalse(fred.at["2025-01-06", "price_valid"])
        self.assertEqual(fred.at["2025-01-07", "close_raw"], 7.2)
        self.assertEqual(bank_rate("<table><tr><td>*美 元</td><td>0.05</td></tr></table>"), .05)

    def test_hcvix_preserves_separate_source_dates(self):
        charts = [{"series": [{"name": "HCVIX50", "data": [[1423440000000, 22], [1423440000000, 22]]},
                              {"name": "HCVIX300", "data": [[1577059200000, 21], [1577145600000, 18],
                                                         [1577145600000, 20]]}]}]
        got = volatility_closes("<script>var charts = " + json.dumps(charts) + ";</script>")
        self.assertEqual(got[["Date", "cn_vix50"]].dropna()["Date"].tolist(), ["2015-02-09"])
        self.assertEqual(got["cn_vix300"].dropna().tolist(), [21.])

    def test_official_release_time_and_local_auxiliary_variables(self):
        pmi = '<title>7月中国制造业采购经理指数</title><meta name="PubDate" content="2015-08-01 09:00"><p>制造业采购经理指数（PMI）为50.0%</p>'
        emp = '<title>10月份国民经济运行情况</title><meta name="PubDate" content="2026-11-16"><p>10月份，全国城镇调查失业率为5.1%</p>'
        pbc = '<title>公开市场业务交易公告</title><meta name="publishdate" content="2025-01-03"><p>逆回购</p><table><tr><td>7天</td><td>1.5%</td></tr></table>'
        self.assertEqual(nbs_article(pmi, "https://www.stats.gov.cn/example.html")["period"], "2015-07")
        row = nbs_article(emp, "https://www.stats.gov.cn/example.html")
        self.assertEqual((row["series"], row["period"], row["value"]), ("cn_unemployment", "2026-10", 5.1))
        self.assertTrue(row["available_at"].startswith("2026-11-17"))
        self.assertEqual(pbc_rate_article(pbc, "https://www.pbc.gov.cn/example.html")["value"], 1.5)

    def test_alfred_replays_only_the_effective_version(self):
        values = pd.DataFrame([
            {"date": "2024-01-01", "value": "3.9", "realtime_start": "2024-02-02", "realtime_end": "2024-03-07"},
            {"date": "2024-01-01", "value": "4.0", "realtime_start": "2024-03-08", "realtime_end": "2024-03-29"},
        ])
        with patch("quantkit.vintages.alfred_observations", return_value=values):
            events = realtime_events("us_unemployment", "2024-01-01", "2024-03-29")
        self.assertEqual(events["value"].tolist(), [3.9, 4.])
        self.assertTrue(events["available_at"].iloc[1].startswith("2024-03-09"))


if __name__ == "__main__":
    unittest.main()
