"""只读导入既有研究的原始快照；不读取过去保存的入选名单或净值。"""
from pathlib import Path

import pandas as pd

from .fundamentals import concat_files
from .overseas import align_us
from .cleaning import calendar_frame


def research_calendar(research, start, end):
    d = pd.read_parquet(Path(research) / "raw/calendar.parquet")
    cal = calendar_frame(d, "research_tushare_calendar")
    if pd.to_datetime(d["cal_date"].astype(str)).max() < pd.Timestamp(end):
        raise ValueError("research 日历没有覆盖请求末日，请联网补取。")
    return cal.loc[cal["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()


def research_prices(research, assets, calendar):
    root = Path(research) / "raw"
    rows = []
    for a in assets:
        code = a["code"]
        if a["kind"] == "us_etf":
            d = pd.read_parquet(root / f"yahoo_{code}.parquet")
            fx = pd.read_parquet(root / "yahoo_CNY_X.parquet")
            rows.append(align_us(code, d, fx, calendar["date"]))
            continue
        api, adj = ("fund_daily", "fund_adj") if a["kind"] == "etf" else ("daily", "adj_factor")
        d, f = concat_files(root, f"{api}_{code}_*.parquet"), concat_files(root, f"{adj}_{code}_*.parquet")
        for z in [d, f]:
            z["date"] = pd.to_datetime(z["trade_date"].astype(str), format="mixed")
            if z["date"].duplicated().any():
                raise ValueError(code + " 原始快照存在冲突日期。")
        d = d.merge(f[["date", "adj_factor"]], on="date", how="left", validate="one_to_one")
        d = pd.DataFrame({"date": calendar["date"]}).merge(d, on="date", how="left", validate="one_to_one")
        d["close_raw"] = d["close"]
        d["close_adjusted"] = d["close"] * d["adj_factor"]
        d["price_valid"] = d["close_adjusted"].gt(0)
        d["tradable"] = d["price_valid"] & d["vol"].gt(0)
        d["price_available_at"] = (d["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai")
        d["code"], d["currency"], d["source"] = code, "CNY", "research_tushare_raw"
        rows.append(d[["date", "code", "currency", "close_adjusted", "close_raw", "price_valid", "tradable", "price_available_at", "source"]])
    return pd.concat(rows, ignore_index=True)
