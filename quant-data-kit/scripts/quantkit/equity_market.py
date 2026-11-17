"""用同源日线、复权因子和涨跌停价构建历史股票行情。"""
from pathlib import Path

import numpy as np
import pandas as pd

from .equity_factor_inputs import MARKET_FIELDS, batch, opening_suspensions


def join_prices(tables):
    daily, adjustment, limit = tables
    keys = ["ts_code", "trade_date"]
    return daily.merge(adjustment, on=keys, how="left", validate="one_to_one").merge(
        limit, on=keys, how="left", validate="one_to_one")


def market_history(root, codes, calendar, start, end, by_date):
    """全 A 按交易日取数，较小的池按证券取数。两条路径使用相同字段。"""
    if by_date:
        days = calendar.strftime("%Y%m%d")
        jobs = [(f"{api}_market_{day}", api, dict(trade_date=day, fields=fields, page_size=5000))
                for day in days for api, fields in MARKET_FIELDS.items()]
        batch(root, jobs, 12, "全市场日线、复权与涨跌停")
        parts = []
        code_type = pd.CategoricalDtype(codes)
        for day in days:
            frames = [pd.read_parquet(root / f"{api}_market_{day}.parquet") for api in MARKET_FIELDS]
            if frames[0].empty or any(not f.trade_date.eq(day).all() for f in frames):
                raise ValueError(f"{day} 全市场行情快照缺失或日期无效。")
            selected = [f.loc[f.ts_code.isin(codes)] for f in frames]
            joined = join_prices(selected)
            joined["ts_code"] = joined.ts_code.astype(code_type)
            parts.append(joined)
        tape = pd.concat(parts, ignore_index=True)
        del parts
        yield from tape.groupby("ts_code", observed=True, sort=False)
        return

    # 较长区间分段请求，避免单证券历史超过行情接口单次上限。
    start, end = (pd.Timestamp(day).strftime("%Y%m%d") for day in (start, end))
    spans = [(start, end)] if len(calendar) <= 4500 else [
        (calendar[i].strftime("%Y%m%d"), calendar[min(i+4499, len(calendar)-1)].strftime("%Y%m%d"))
        for i in range(0, len(calendar), 4500)]
    jobs = [(f"{api}_{code}_{a}_{b}", api, dict(ts_code=code, start_date=a, end_date=b, fields=fields))
            for code in codes for a, b in spans for api, fields in MARKET_FIELDS.items()]
    batch(root, jobs, 12, "股票日线、复权与涨跌停")
    for code in codes:
        tables = [pd.concat([pd.read_parquet(root / f"{api}_{code}_{a}_{b}.parquet") for a, b in spans],
                            ignore_index=True) for api in MARKET_FIELDS]
        if any(not table.ts_code.eq(code).all() for table in tables):
            raise ValueError(f"{code} 的行情代码不一致。")
        yield code, join_prices(tables)


def equity_market(root, codes, calendar, signals, start, end, by_date):
    """成交方向和估值分开。停牌无价格的日期保留为空参与窗口判断。"""
    root = Path(root)
    halted = opening_suspensions(root, start, end).groupby("ts_code").trade_date.agg(set).to_dict()
    prices, features = [], []
    issues = {"missing_adjustment_rows": 0, "missing_price_limit_rows": 0}
    for code, quotes in market_history(root, codes, calendar, start, end, by_date):
        quotes.index = pd.to_datetime(quotes.trade_date, format="%Y%m%d")
        quotes.index.name = "date"
        d = quotes.loc[quotes.index.isin(calendar)].sort_index()
        if d.empty:
            continue
        d = d.astype({name: float for name in ("open", "close", "adj_factor", "up_limit", "down_limit")})
        issues["missing_adjustment_rows"] += int(d.adj_factor.isna().sum())
        issues["missing_price_limit_rows"] += int(d[["up_limit", "down_limit"]].isna().any(axis=1).sum())
        o = d.open.gt(0) & d.adj_factor.gt(0) & ~d.trade_date.isin(halted.get(code, ()))
        c = d.close.gt(0) & d.vol.gt(0) & d.adj_factor.gt(0)
        close = (d.close * d.adj_factor).where(c)
        fields = {"open_adjusted": (d.open * d.adj_factor).where(o), "close_adjusted": close,
                  "buyable_at_open": o & ~np.isclose(d.open, d.up_limit, rtol=0, atol=.0001),
                  "sellable_at_open": o & ~np.isclose(d.open, d.down_limit, rtol=0, atol=.0001),
                  "buyable_at_close": c & ~np.isclose(d.close, d.up_limit, rtol=0, atol=.0001),
                  "sellable_at_close": c & ~np.isclose(d.close, d.down_limit, rtol=0, atol=.0001)}
        prices.append(pd.DataFrame(fields, index=d.index).assign(
            code=code, close_raw=d.close, open_raw=d.open, currency="CNY", tradable=c).reset_index())
        close = close.reindex(calendar)
        stats = pd.DataFrame({
            "amount_20_cny": d.amount.reindex(calendar).fillna(0.).rolling(20).mean() * 1000,
            "volatility_120": close.pct_change(fill_method=None).rolling(120).std() * np.sqrt(252),
            "return_20": close / close.shift(20) - 1,
            "signal_tradable": c.reindex(calendar, fill_value=False),
        }).reindex(signals)
        features.append(stats.rename_axis("snapshot_date").assign(ts_code=code).reset_index())
    if not prices:
        raise ValueError("指定股票池在请求区间内没有历史日线。")
    return pd.concat(prices, ignore_index=True), pd.concat(features, ignore_index=True), issues
