"""按信号日选股和配权，不访问未来价格。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
import operator


def minimum_variance(returns, max_weight=1., shrinkage=.1):
    """min w'Σw，long-only；收缩协方差让相似股票的优化更稳定。"""
    r = returns.dropna(how="any")
    n = len(r.columns)
    if not n or len(r) < 2:
        raise ValueError("协方差需要至少两期共同收益。")
    if n * max_weight < 1 - 1e-9:
        raise ValueError("股票数量与单股权重上限不相容。")
    cov = np.atleast_2d(r.cov().to_numpy())
    cov = (1 - shrinkage) * cov + shrinkage * np.diag(np.diag(cov))
    cov += np.eye(n) * max(float(np.trace(cov)) / n * 1e-8, 1e-12)
    # 标准化目标量级，避免日方差很小而被优化器过早判定为收敛。
    scaled = cov / max(float(np.trace(cov)) / n, 1e-12)
    result = minimize(lambda w: float(w @ scaled @ w), np.full(n, 1 / n),
                      jac=lambda w: 2 * scaled @ w, method="SLSQP",
                      bounds=[(0., max_weight)] * n,
                      constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1,
                                    "jac": lambda w: np.ones(n)}],
                      options={"ftol": 1e-12, "maxiter": 1000})
    if not result.success or abs(result.x.sum() - 1) > 1e-7 or result.x.min() < -1e-8:
        raise ValueError("最小方差求解未收敛。")
    w = np.maximum(result.x, 0)
    w /= w.sum()
    if w.max() > max_weight + 1e-7:
        raise ValueError("求解结果超过单股上限。")
    return pd.Series(w, index=r.columns), {"observations": len(r), "variance": float(w @ cov @ w),
                                           "equal_variance": float(np.full(n, 1/n) @ cov @ np.full(n, 1/n))}


def quality_value(features, signal, params):
    if features.empty:
        raise ValueError("财务选股缺少 stock_features.csv；先 prepare 或 prepare-research。")
    prior = features.loc[features["snapshot_date"] <= signal]
    if prior.empty:
        raise ValueError("信号日前没有历史选股截面。")
    stamp = prior["snapshot_date"].max()
    if (signal - stamp).days > params.get("snapshot_max_age_days", 7):
        raise ValueError("选股截面太旧；改变调仓频率后请重新补取历史估值。")
    v = prior.loc[prior["snapshot_date"].eq(stamp)].copy()
    if v["code"].duplicated().any():
        raise ValueError("同一选股截面有重复股票。")
    # 财务行必须来自信号日前已公告的报告；不以报告期充当公告日。
    custom = params.get("method") == "fundamental"
    if not custom:
        v = v.loc[(v["ann_date"] < signal) & (v["annual_ann_date"] < signal)]
    v = v.loc[~v["code"].isin(params.get("exclude", []))]
    pool = params.get("universe", "all_a")
    if isinstance(pool, list):
        v = v.loc[v["code"].isin(pool)]
    required = ["list_date", "historical_name"] if custom else ["list_date", "historical_name", "industry", "pe", "pe_ttm", "total_mv"]
    v = v.dropna(subset=required)
    mask = ((signal - v["list_date"]).dt.days >= params.get("min_listing_days", 365))
    mask &= v["delist_date"].isna() | (v["delist_date"] > signal)
    if params.get("exclude_st", True):
        mask &= ~v["historical_name"].str.contains("ST|退", case=False, regex=True)
    v = v.loc[mask].sort_values("code")
    if custom:
        ranking = params.get("rank_by", [{"field": "pe_ttm", "ascending": True}])
        fields = {p["field"] for p in params.get("filters", []) + ranking}
        allowed = {"pe", "pe_ttm", "pb", "dv_ttm", "total_mv", "annual_roe", "annual_ocfps", "debt_to_assets", "profit_dedt", "roe_yearly"}
        if not fields.issubset(allowed) or not fields.issubset(v.columns):
            raise ValueError("自定义选股字段不可用，请查看配置说明中的字段清单。")
        if fields & {"annual_roe", "annual_ocfps"}:
            v = v.loc[v["annual_ann_date"].lt(signal) & (signal-v["annual_end_date"]).dt.days.le(params.get("annual_report_max_days", 730))]
        if fields & {"debt_to_assets", "profit_dedt", "roe_yearly"}:
            v = v.loc[v["ann_date"].lt(signal) & (signal-v["end_date"]).dt.days.le(params.get("latest_report_max_days", 400))]
        v = v.dropna(subset=sorted(fields))
        ops = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}
        for condition in params.get("filters", []):
            v = v.loc[ops[condition["op"]](v[condition["field"]], condition["threshold"])]
        v = v.sort_values([p["field"] for p in ranking] + ["code"], ascending=[p.get("ascending", True) for p in ranking] + [True])
        cap = params.get("max_per_industry", 0)
        if cap > 0:
            v = v.dropna(subset=["industry"])
            v = v.loc[v.groupby("industry").cumcount() < cap]
        selected = v.head(int(params.get("top_n", 5))).copy()
        selected["signal_date"] = signal
        return selected
    # 在历史可参与股票中计算行业规模排名及估值分位，而非在最终五只里排名。
    v["leader_rank"] = v.groupby("industry")["total_mv"].rank(ascending=False, method="first")
    v["size_score"] = v.groupby("industry")["total_mv"].rank(pct=True)
    positive = v.loc[v["pe_ttm"] > 0]
    v["value_score"] = positive.groupby("industry")["pe_ttm"].rank(ascending=False, pct=True)
    financial = v["industry"].isin(params.get("financial_industries", ["银行", "非银金融"]))
    mask = (v["leader_rank"] <= params.get("leaders_per_industry", 5))
    mask &= v["annual_roe"] >= params.get("min_roe", 8)
    mask &= v["pe_ttm"].between(0, params.get("max_pe_ttm", 60), inclusive="right")
    mask &= v["pe"].between(0, params.get("max_pe", 80), inclusive="right")
    mask &= v["profit_dedt"] > 0
    mask &= v["roe_yearly"] > 0
    if params.get("positive_two_years", True):
        mask &= v["annual_count"].ge(2) & v["consecutive_years"].eq(True) & v["min_two_year_profit"].gt(0)
    mask &= financial | ((v["annual_ocfps"] > 0) & (v["debt_to_assets"] < params.get("max_debt", 80)))
    mask &= (signal - v["end_date"]).dt.days <= params.get("latest_report_max_days", 400)
    mask &= (signal - v["annual_end_date"]).dt.days <= params.get("annual_report_max_days", 730)
    v = v.loc[mask].copy()
    v["quality_score"] = v["annual_roe"].rank(pct=True)
    score = params.get("score_weights", {"quality": .5, "value": .35, "size": .15})
    v["score"] = sum(score.get(k, 0) * v[k + "_score"] for k in ["quality", "value", "size"])
    v = v.sort_values(["score", "total_mv", "code"], ascending=[False, False, True])
    cap = int(params.get("max_per_industry", 1))
    if cap > 0:
        v = v.loc[v.groupby("industry").cumcount() < cap]
    selected = v.head(int(params.get("top_n", 5))).copy()
    selected["signal_date"] = signal
    return selected


def select_and_weight(sleeve, signal, execute, prices, features):
    selection, weighting = sleeve["selection"], sleeve["weighting"]
    notes, audit = [], {"sleeve": sleeve["id"], "signal_date": str(signal.date()),
                        "execution_date": str(execute.date()), "method": weighting["method"]}
    past = prices.loc[:signal]  # 唯一的价格窗口入口。
    if selection["method"] in {"quality_value", "fundamental"}:
        chosen = quality_value(features, signal, selection)
        codes = chosen["code"].tolist()
    else:
        codes = [a["code"] for a in sleeve["assets"]
                 if pd.Timestamp(a.get("from", "1900-01-01")) <= execute
                 and execute < pd.Timestamp(a.get("until", "2200-01-01"))]
        chosen = pd.DataFrame({"code": codes})
        if selection["method"] == "momentum":
            lookback = int(selection.get("lookback", 126))
            scores = {}
            for code in codes:
                if code not in past:
                    continue
                window = past[code].tail(lookback + 1)
                if len(window) == lookback + 1 and window.notna().all():
                    scores[code] = float(window.iloc[-1] / window.iloc[0] - 1)
            chosen = pd.DataFrame([{"code": code, "score": value} for code, value in scores.items()],
                                  columns=["code", "score"])
            chosen = chosen.loc[chosen["score"] >= selection.get("min_return", -1)]
            chosen = chosen.sort_values(["score", "code"], ascending=[False, True]).head(selection.get("top_n", 5))
            codes = chosen["code"].tolist()
    # 固定资产和已通过财务筛选的股票缺行情时，明确请求补数，不能偷偷换成下一名。
    missing = [code for code in codes if code not in past or past[code].notna().sum() == 0]
    if missing:
        raise ValueError("入选资产缺少信号日前行情，请补取：" + ", ".join(missing))
    if not codes:
        audit.update(status="no_eligible_assets", notes=["没有符合条件的资产，该部分留现金。"])
        return {}, chosen, audit
    n = len(codes)
    weights = pd.Series(1 / n, index=codes)
    if weighting["method"] == "fixed":
        weights = pd.Series({a["code"]: a.get("weight", 1.) for a in sleeve["assets"] if a["code"] in codes})
        if not np.isfinite(weights).all() or weights.lt(0).any() or weights.sum() <= 0:
            raise ValueError("部分内固定配权须为非负且合计为正。")
        weights /= weights.sum()
    elif weighting["method"] == "min_variance":
        r = past.reindex(columns=codes).tail(weighting.get("lookback", 126) + 1).pct_change(fill_method=None).iloc[1:]
        sample = r.dropna(how="any")
        maximum = weighting.get("max_weight", 1.)
        if len(sample) < weighting.get("min_observations", 60):
            notes.append("共同历史收益不足，明确回退等权。")
        elif n * maximum < 1 - 1e-9:
            notes.append("入选数量不足以满仓且满足单股上限，本期等权并保留现金。")
        else:
            try:
                weights, optimization = minimum_variance(sample, maximum, weighting.get("shrinkage", .1))
                audit.update(optimization)
            except ValueError as exc:
                notes.append(str(exc) + " 本期回退等权。")
        audit["sample_start"] = str(sample.index.min().date()) if len(sample) else None
        audit["sample_end"] = str(sample.index.max().date()) if len(sample) else None
    # 不足计划只数时按缺额留现金，避免少数入选股票突然吃满整个部分。
    invested = min(1., n / int(selection.get("top_n", n))) if selection["method"] != "fixed" else 1.
    if selection.get("fill_shortfall", "cash") == "redistribute":
        invested = 1.
    if weighting["method"] == "min_variance":
        # 回退等权也遵守原部分内的单股上限；少数股票不够分满时留现金。
        capped = weighting.get("max_weight", 1.) / float(weights.max())
        if capped < invested - 1e-9 and not any("现金" in note for note in notes):
            notes.append("等权回退同时保留部分现金，以满足单股上限。")
        invested = min(invested, capped)
    weights *= invested
    if not np.isfinite(weights).all() or weights.lt(0).any() or weights.sum() > 1 + 1e-9:
        raise ValueError("所有资产只做多，部分内权重必须有限、非负且合计不超过100%。")
    chosen = chosen.copy()
    if "historical_name" in chosen:
        chosen["name"] = chosen["historical_name"].fillna(chosen.get("name"))
    elif "name" not in chosen:
        names = {a["code"]: a.get("name", a["code"]) for a in sleeve.get("assets", [])}
        chosen["name"] = chosen["code"].map(names).fillna(chosen["code"])
    chosen["within_weight"] = chosen["code"].map(weights)
    chosen["sleeve"] = sleeve["id"]
    chosen["signal_date"] = signal
    chosen["execution_date"] = execute
    audit.update(status="fallback_equal" if notes else "ok", notes=notes, invested_fraction=invested)
    return weights.to_dict(), chosen, audit
