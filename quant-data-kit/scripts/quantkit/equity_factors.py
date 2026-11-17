"""按实际披露日生成所选股票池的财务、估值与量价截面。"""
from pathlib import Path

import numpy as np
import pandas as pd

from .equity_factor_inputs import DIVIDEND_FIELDS, STATEMENTS, batch, equity_inputs, index_members
from .equity_market import equity_market
from .fundamentals import concat_files


STATEMENT_FIELDS = {name: fields.split(",") for name, fields in STATEMENTS.items()}
FINANCIAL_FIELDS = ["net_profit_ttm", "operating_cashflow_ttm", "capex_ttm", "roe_ttm", "goodwill_ratio"]


def dates(frame, columns):
    for name in columns:
        frame[name] = pd.to_datetime(frame[name].astype("string"), format="%Y%m%d", errors="coerce")
    return frame


def statement_versions(table, fields):
    """实际披露日缺失的报表不用。保留原报表及另行公告的后续版本。"""
    table = dates(table.drop_duplicates().copy(), ["ann_date", "f_ann_date", "end_date"])
    table["published"] = table[["f_ann_date", "ann_date"]].max(axis=1)
    table = table.loc[table.f_ann_date.notna() & table.published.gt(table.end_date)].copy()
    table["priority"] = np.where(table.report_type.astype(str).eq("5"), 0, 2)
    table["priority"] += table.update_flag.astype(str).eq("1").astype(int)
    keys = ["ts_code", "end_date", "published"]
    table = table.loc[table.priority.eq(table.groupby(keys).priority.transform("min"))].copy()
    # 同一披露日、同一优先级仍给出不同数值时，该字段不参与打分。
    for field in fields:
        table.loc[table.groupby(keys)[field].transform("nunique").gt(1), field] = np.nan
    return table.sort_values(keys).drop_duplicates(keys, keep="first")[keys + fields]


def financial_snapshot(statements, signal):
    """用当时已披露的合并报表重建 TTM，并保留所有组成项中最晚的公告日。"""
    joined = []
    key = ["ts_code", "end_date"]
    for name, table in statements.items():
        known = table.loc[table.published.le(signal)].drop_duplicates(key, keep="last")
        joined.append(known.rename(columns={"published": name+"_published"}).set_index(key))
    reports = pd.concat(joined, axis=1, join="inner").reset_index()
    publication = [name+"_published" for name in statements]
    reports["pub_date"] = reports[publication].max(axis=1)
    current = reports.sort_values(key).drop_duplicates("ts_code", keep="last")
    flows = ["n_income_attr_p", "n_cashflow_act", "c_pay_acq_const_fiolta"]
    equity = "total_hldr_eqy_exc_min_int"
    prior = reports.set_index(key)
    last_year = pd.MultiIndex.from_arrays([current.ts_code, current.end_date - pd.DateOffset(years=1)])
    last_fy = pd.MultiIndex.from_arrays([current.ts_code, pd.to_datetime((current.end_date.dt.year-1).astype(str)+"1231")])
    same_quarter, annual = (
        prior.reindex(period).reset_index(drop=True) for period in (last_year, last_fy)
    )
    current = current.reset_index(drop=True)
    fy = current.end_date.dt.month.eq(12)
    ttm = current[flows].where(fy, current[flows] + annual[flows] - same_quarter[flows], axis=0)
    denominator = (current[equity] + same_quarter[equity]) / 2
    result = current[["ts_code","end_date"]].rename(columns={"end_date":"statement_period"}).copy()
    result["pub_date"] = pd.concat([current.pub_date, same_quarter.pub_date, annual.pub_date.where(~fy)], axis=1).max(axis=1)
    result["net_profit_ttm"], result["operating_cashflow_ttm"], result["capex_ttm"] = (ttm[field] for field in flows)
    result["roe_ttm"] = ttm.n_income_attr_p / denominator.where(current[equity].gt(0) & same_quarter[equity].gt(0))
    result["goodwill_ratio"] = current.goodwill / current[equity].where(current[equity].gt(0))
    return result


def cash_dividend_versions(table):
    table = table.loc[table.div_proc.eq("实施")].drop_duplicates().copy()
    table = dates(table, ["ann_date","imp_ann_date","ex_date","end_date"])
    table["dividend_pub_date"] = table[["imp_ann_date", "ann_date"]].max(axis=1).where(table.imp_ann_date.notna())
    # 基准股本单位为万股。按公司实际派发的税前现金总额计算股息率。
    table["cash_cny"] = (table.cash_div_tax * table.base_share.where(table.base_share.gt(0)) * 10_000)
    return table.loc[table.ex_date.notna() & table.cash_div_tax.gt(0)].sort_values("dividend_pub_date")


def dividend_snapshot(table, signal):
    window = table.loc[table.ex_date.gt(signal - pd.DateOffset(years=1)) & table.ex_date.le(signal)]
    known = window.loc[window.dividend_pub_date.le(signal)].drop_duplicates(["ts_code","end_date","ex_date"], keep="last")
    grouped = known.groupby("ts_code")
    nan_rows = known.loc[known.cash_cny.isna(), "ts_code"]
    unknown_date = window.loc[window.dividend_pub_date.isna() & window.ann_date.le(signal), "ts_code"]
    dividends = grouped.agg(dividend_cash_cny=("cash_cny","sum"), dividend_pub_date=("dividend_pub_date","max"))
    return dividends, set(nan_rows) | set(unknown_date)


BSE_OPEN = pd.Timestamp("2021-11-15")
MARKET_CODES = {"all_a": r"6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[0-9]{6}\.BJ", "star_market": r"68[89][0-9]{3}\.SH"}


def universe_snapshot(root, signal, pools, values):
    """按每个要求的范围准备时点候选。标记同一证券可同时属于多个池。"""
    members, asof, custom = {}, {}, set()
    for pool in pools:
        name = pool["name"]
        if name in MARKET_CODES:
            symbols = values.ts_code
            listed = symbols.str.fullmatch(MARKET_CODES[name])
            if signal < BSE_OPEN:
                listed &= ~symbols.str.endswith(".BJ")
            members[name] = symbols[listed].to_list()
        elif name == "custom":
            custom.update(pool["codes"])
        else:
            members[name], asof[name] = index_members(root, pool, signal)
    codes = sorted(custom.union(*members.values()))
    if not codes or any(not symbols for symbols in members.values()):
        raise ValueError(f"{signal:%Y-%m-%d} 缺少所需的历史估值或股票池。")
    frame = pd.DataFrame({"ts_code": codes, "snapshot_date": signal})
    for name, symbols in members.items():
        flag = name + "_member"
        frame[flag] = frame.ts_code.isin(symbols)
        if name in asof:
            frame[flag + "_asof"] = asof[name]
        if name in MARKET_CODES:
            frame[name + "_pool_size"] = len(symbols)
    return frame, {name: len(symbols) for name, symbols in members.items()}


def prepare_equity_factors(cache, calendar, signals, start, end, benchmark="H00300.CSI", *, pools=None):
    """返回标准行情、时点因子和股票池覆盖。旧调用默认准备中证 800。"""
    root = Path(cache)
    signals = pd.DatetimeIndex(sorted(set(signals)))
    calendar = pd.DatetimeIndex(calendar)
    if pools is None:
        pools = {signal: [{"name": "csi800", "index_code": "000906.SH", "size": 800}] for signal in signals}
    equity_inputs(root, signals, start, end, benchmark, pools)
    snapshots, counts = {}, {}
    for signal in signals:
        values = pd.read_parquet(root / f"valuation_{signal:%Y%m%d}.parquet")
        if values.empty or not values.trade_date.eq(signal.strftime("%Y%m%d")).all():
            raise ValueError(f"{signal:%Y-%m-%d} 缺少有效的历史估值截面。")
        frame, counts[str(signal.date())] = universe_snapshot(root, signal, pools[signal], values)
        snapshots[signal] = frame.merge(values[["ts_code","pe_ttm","pb","total_mv"]],
                                        on="ts_code", how="left", validate="one_to_one")
    codes = sorted(set().union(*(frame.ts_code for frame in snapshots.values())))
    broad = any("all_a" in row for row in counts.values())
    prices, market, issues = equity_market(root, codes, calendar, signals, start, end, broad)
    dividend_jobs = [(f"dividend_{code}", "dividend", dict(ts_code=code, fields=DIVIDEND_FIELDS)) for code in codes]
    batch(root, dividend_jobs, 12, "历史分红")
    tables = [pd.read_parquet(root / f"dividend_{code}.parquet") for code in codes]
    dividends = cash_dividend_versions(pd.concat([t for t in tables if not t.empty] or tables[:1], ignore_index=True))
    financial = {
        name: statement_versions(concat_files(root, f"{name}_*.parquet").loc[lambda x: x.ts_code.isin(codes)], fields)
        for name, fields in STATEMENT_FIELDS.items()
    }
    basic = dates(concat_files(root, "stocks_*.parquet"), ["list_date", "delist_date"])
    if basic.ts_code.duplicated().any():
        raise ValueError("股票基础表存在重复代码。")
    # 北交所开市前的股转挂牌时间不计入北交所上市年限。
    bj = basic.ts_code.str.endswith(".BJ")
    basic.loc[bj,"list_date"] = basic.loc[bj,"list_date"].clip(lower=BSE_OPEN)
    names = dates(pd.read_parquet(root / "name_history.parquet"), ["ann_date","start_date","end_date"])
    industries = dates(concat_files(root, "members_*.parquet"), ["in_date","out_date"])
    features = []
    for signal, frame in snapshots.items():
        historical = names.loc[names.start_date.le(signal) & (names.end_date.isna() | names.end_date.ge(signal))
                               & (names.ann_date.isna() | names.ann_date.le(signal))]
        historical = historical.sort_values(["start_date","ann_date"]).drop_duplicates("ts_code", keep="last")
        sector = industries.loc[industries.in_date.le(signal) & (industries.out_date.isna() | industries.out_date.gt(signal))]
        sector = sector.sort_values(["in_date","l1_code"]).drop_duplicates("ts_code", keep="last")
        fields = [
            basic[["ts_code","list_date","delist_date"]],
            historical[["ts_code","name"]].rename(columns={"name":"historical_name"}),
            sector[["ts_code","l1_name"]].rename(columns={"l1_name":"industry"}),
            market.loc[market.snapshot_date.eq(signal)].drop(columns="snapshot_date"),
            financial_snapshot(financial, signal),
        ]
        for table in fields:
            frame = frame.merge(table, on="ts_code", how="left", validate="one_to_one")
        frame["signal_tradable"] = frame["signal_tradable"].eq(True)
        earned, missing = dividend_snapshot(dividends, signal)
        frame = frame.join(earned, on="ts_code")
        frame["market_value_cny"] = frame.pop("total_mv") * 10_000
        frame["dividend_yield_ttm"] = (frame.dividend_cash_cny.fillna(0.) / frame.market_value_cny).where(
            ~frame.ts_code.isin(missing))
        features.append(frame)
    features = pd.concat(features, ignore_index=True).rename(columns={"ts_code":"code"})
    if benchmark:
        suffix = f"{pd.Timestamp(start):%Y%m%d}_{pd.Timestamp(end):%Y%m%d}"
        index = pd.read_parquet(root / f"index_{benchmark}_{suffix}.parquet")
        benchmark_prices = pd.DataFrame({
            "date": pd.to_datetime(index.trade_date, format="%Y%m%d"), "code": benchmark, "currency": "CNY",
            "open_adjusted": index.open, "close_adjusted": index.close, "tradable": False,
        })
        prices = pd.concat([prices, benchmark_prices], ignore_index=True)
    prices["price_valid"] = prices.close_adjusted.notna()
    prices["price_available_at"] = (prices.date + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai")
    coverage = {
        "historical_csi800_snapshots": sum("csi800" in row for row in counts.values()),
        "pool_counts_by_signal": counts,
        "historical_codes": len(codes),
        "financial_rows": int(features.pub_date.notna().sum()),
        "industry_rows": int(features.industry.notna().sum()),
        "open_price_rows": int(prices.open_adjusted.notna().sum()),
        **issues,
    }
    return prices, features, coverage
