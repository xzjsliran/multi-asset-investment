"""历史股票池的质量、价值与红利低波排序及持仓缓冲。"""
import re

import numpy as np
import pandas as pd


METHOD = "quality_value_dividend"
DEFAULT_POOL = "csi800"
INDEX_POOLS = {
    "csi800": ("000906.SH", 800), "csi300": ("000300.SH", 300),
    "csi500": ("000905.SH", 500), "csi1000": ("000852.SH", 1000),
    "sse50": ("000016.SH", 50), "star50": ("000688.SH", 50),
}
MARKET_POOLS = ("all_a", "star_market")
FIELDS = {
    "snapshot_date", "code", "historical_name", "industry", "list_date", "delist_date", "signal_tradable",
    "amount_20_cny", "volatility_120", "return_20", "pe_ttm", "pb", "market_value_cny",
    "pub_date", "net_profit_ttm", "operating_cashflow_ttm", "roe_ttm", "capex_ttm", "goodwill_ratio",
    "dividend_yield_ttm", "dividend_pub_date",
}


def pool_request(universe):
    """把配置中的池子转成数据模块需要的历史成分查询。"""
    if isinstance(universe, list):
        pattern = r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|\d{6}\.BJ)"
        if (not universe or any(not isinstance(code, str) or not re.fullmatch(pattern, code) for code in universe)
                or len(set(universe)) != len(universe)):
            raise ValueError("自选股票池须列出非空且不重复的 A 股代码，例如 600519.SH。")
        return {"name": "custom", "codes": sorted(universe)}
    if universe in MARKET_POOLS:
        return {"name": universe}
    if isinstance(universe, str) and universe in INDEX_POOLS:
        code, size = INDEX_POOLS[universe]
        return {"name": universe, "index_code": code, "size": size}
    raise ValueError("多因子股票池须为 " + "、".join([*INDEX_POOLS, *MARKET_POOLS]) + "，或 A 股代码列表。")


def pool_snapshot(features, signal, universe):
    """旧中证 800 数据继续可用，其他池须有对应日期的完整候选记录。"""
    scope = pool_request(universe)
    name = scope["name"]
    v = features.loc[features.snapshot_date.eq(signal)]
    if name == "custom":
        v = v.loc[v.code.isin(scope["codes"])]
        expected = len(scope["codes"])
    else:
        flag = name + "_member"
        v = v.loc[v[flag].eq(True)] if flag in v else v.iloc[:0]
        expected = scope.get("size")
        size_field = name + "_pool_size"
        if name in MARKET_POOLS and size_field in v:
            sizes = v[size_field].unique()
            expected = sizes[0] if len(sizes) == 1 else None
        date_field = flag + "_asof"
        if date_field in v and (v[date_field].nunique() != 1 or not v[date_field].le(signal).all()):
            raise ValueError(f"{signal:%Y-%m-%d} 的 {name} 历史成分日期无效。")
    if not len(v) or len(v) != expected or v.code.duplicated().any():
        raise ValueError(f"{signal:%Y-%m-%d} 缺少完整的 {name} 历史股票池截面。")
    return v.copy()


def standardize(factors, industry):
    """全截面 MAD 截尾，再在样本足够的行业内标准化。"""
    median = factors.median()
    mad = factors.sub(median).abs().median()
    clipped = factors.clip(lower=median-3*mad, upper=median+3*mad, axis=1)
    pooled = clipped.sub(clipped.mean()).div(clipped.std(ddof=0).replace(0., np.nan)).fillna(0.)
    group = clipped.groupby(industry)
    mean, spread = group.transform("mean"), group.transform("std", ddof=0)
    within = clipped.sub(mean).div(spread.replace(0., np.nan)).fillna(0.)
    return within.where(industry.map(industry.value_counts()).ge(5), pooled, axis=0)


def rank_equities(features, signal, *, small_cap_weight=0., universe=DEFAULT_POOL):
    """按信号日股票池做基础过滤、硬过滤和截面打分。"""
    missing = FIELDS - set(features)
    if missing:
        raise ValueError("多因子选股所需的历史截面字段缺失：" + "、".join(sorted(missing)))
    v = pool_snapshot(features, signal, universe)
    universe_count = len(v)
    history = (v.historical_name.notna() & ~v.historical_name.fillna("").str.contains(r"ST|退", case=False, regex=True)
               & v.industry.notna() & v.delist_date.fillna(pd.Timestamp.max).gt(signal)
               & (signal-v.list_date).dt.days.ge(365))
    v = v.loc[history & v.signal_tradable & v.amount_20_cny.ge(30_000_000)]
    v = v.loc[v.volatility_120.gt(0) & np.isfinite(v.volatility_120) & np.isfinite(v.return_20)]
    audit = {"universe_count": universe_count, "base_pool_count":len(v)}
    vol_cap, return_cap = v.volatility_120.quantile(.8), v.return_20.quantile(.9)
    valued = v.loc[v.pe_ttm.gt(0) & v.pb.gt(0)].copy()
    sector_cap = valued.groupby("industry").pe_ttm.transform("quantile", .7)
    valued = valued.loc[valued.pe_ttm.le(sector_cap)]
    audit["valuation_pass_count"] = len(valued)
    cash_ratio = valued.operating_cashflow_ttm / valued.net_profit_ttm.where(valued.net_profit_ttm.gt(0))
    good = (valued.pub_date.le(signal) & (valued.dividend_pub_date.isna() | valued.dividend_pub_date.le(signal))
            & cash_ratio.ge(.8) & (valued.goodwill_ratio.isna() | valued.goodwill_ratio.le(.20))
            & valued.capex_ttm.ge(0) & valued.market_value_cny.gt(0))
    scored = valued.loc[good & valued.volatility_120.le(vol_cap) & valued.return_20.le(return_cap)].copy()
    scored["fcf_ttm"] = scored.operating_cashflow_ttm - scored.capex_ttm
    inputs = pd.DataFrame({
        "earnings_yield":1/scored.pe_ttm,
        "book_yield":1/scored.pb,
        "roe":scored.roe_ttm,
        "fcf_yield":scored.fcf_ttm/scored.market_value_cny,
        "dividend":scored.dividend_yield_ttm,
        "low_volatility":-scored.volatility_120,
    })
    if small_cap_weight:
        # 使用当时的总市值，让行业内市值较小的股票得到较高的规模分数。
        inputs["small_cap"] = -np.log(scored.market_value_cny)
    inputs = inputs.replace([np.inf, -np.inf], np.nan).dropna()
    scored = scored.loc[inputs.index].copy()
    z = standardize(inputs, scored.industry)
    scored = scored.join(z.add_prefix("z_"))
    # 红利低波维持 30%，价值和质量现金流平分扣除小市值后的权重。
    value_quality = z[["earnings_yield", "book_yield", "roe", "fcf_yield"]].sum(axis=1)
    dividend_low_vol = z[["dividend", "low_volatility"]].sum(axis=1)
    score = (.70 - small_cap_weight) / 4 * value_quality + .15 * dividend_low_vol
    if small_cap_weight:
        score += small_cap_weight * z["small_cap"]
    scored["total_score"] = score
    scored = scored.sort_values(["total_score","code"],ascending=[False,True])
    scored["rank"] = np.arange(1,len(scored)+1)
    return scored, {**audit, "scored_count":len(scored), "pool_volatility_upper":float(vol_cap) if len(v) else None,
                    "pool_return_upper":float(return_cap) if len(v) else None}


def inverse_volatility(volatility, cap):
    """将超出单股上限的目标额按逆波动比例重新分配。"""
    raw = 1/volatility
    weights = pd.Series(0., index=raw.index)
    remaining = min(1., len(raw)*cap)
    while len(raw):
        proposed = raw * (remaining/raw.sum())
        capped = proposed.gt(cap)
        if not capped.any():
            weights.loc[raw.index] = proposed
            break
        weights.loc[proposed[capped].index] = cap
        remaining = max(0., remaining-int(capped.sum())*cap)
        raw = raw.loc[~capped]
    return weights


def select_equities(sleeve, features, signal, execution, held, opening_codes=None):
    params = sleeve["selection"]
    ranked, audit = rank_equities(features, signal, small_cap_weight=params.get("small_cap_weight", 0.),
                                  universe=params.get("universe", DEFAULT_POOL))
    audit["universe"] = params.get("universe", DEFAULT_POOL)
    count = params.get("top_n",20)
    depth = params.get("buffer_rank",0)
    was_held = ranked.code.isin(held)
    keep = was_held & ranked["rank"].le(depth)
    ranked["retained_by_buffer"] = keep
    if opening_codes is not None:
        # 开盘有无报价只约束执行资格，上一日的打分与排名保持原口径。
        ranked = ranked.loc[ranked.code.isin(opening_codes)]
    ranked = ranked.sort_values(["retained_by_buffer","rank"],ascending=[False,True])
    chosen = ranked.loc[ranked.groupby("industry").cumcount().lt(params.get("max_per_industry",2))].head(count).copy()
    weight = inverse_volatility(chosen.set_index("code").volatility_120,sleeve["weighting"]["max_weight"])
    chosen["within_weight"] = chosen.code.map(weight)
    chosen["name"] = chosen.historical_name
    chosen["signal_date"], chosen["execution_date"], chosen["sleeve"] = signal, execution, sleeve["id"]
    audit.update({"status":"ok" if len(chosen)==count else "selection_shortfall", "sleeve":sleeve["id"],
                  "method":"inverse_volatility", "signal_date":str(signal.date()), "execution_date":str(execution.date()),
                  "selected_count":len(chosen), "retained_count":int(chosen.retained_by_buffer.sum()),
                  "invested_fraction":float(weight.sum())})
    return weight.to_dict(),chosen,audit
