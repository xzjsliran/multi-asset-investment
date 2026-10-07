"""宏观代码映射；月度序列的日期表示统计期，不是发布日期。"""
FRED = {
    "fed_upper": {"id": "DFEDTARU", "frequency": "daily", "unit": "percent_policy_rate"},
    "fed_effective": {"id": "DFF", "frequency": "daily", "unit": "percent_effective_rate"},
    "us_10y": {"id": "DGS10", "frequency": "daily", "unit": "percent_yield"},
    "us_30y": {"id": "DGS30", "frequency": "daily", "unit": "percent_yield"},
    "us_cpi": {"id": "CPIAUCSL", "frequency": "monthly", "unit": "index_1982_84_100_sa"},
    "us_cpi_yoy": {"id": "CPIAUCSL", "frequency": "monthly", "unit": "percent_yoy_sa", "transform": "yoy"},
    "us_unemployment": {"id": "UNRATE", "frequency": "monthly", "unit": "percent_sa"},
    "us_nonfarm": {"id": "PAYEMS", "frequency": "monthly", "unit": "thousand_persons_sa"},
    "us_nonfarm_change": {"id": "PAYEMS", "frequency": "monthly", "unit": "thousand_persons_change_sa", "transform": "diff"}
}
