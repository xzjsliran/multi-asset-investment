"""在共同起止日上重新汇总结果；不修改信号、持仓或回测。"""
from __future__ import annotations
import itertools
import json
import numpy as np
import pandas as pd

from .inputs import records
from .reporting import public_runs, decorate
from .long_only import check_long_only


def selection_text(c):
    method = c.get("method")
    if method == "fixed":
        return "固定持有配置中的证券"
    if method == "momentum":
        shortfall = {"cash":"保留现金", "redistribute":"分给入选证券"}.get(c.get("fill_shortfall", "cash"),c.get("fill_shortfall"))
        return f"按过去{c.get('lookback', 126)}个交易日的收益排序，最多选{c.get('top_n', 5)}只；最低区间收益{c.get('min_return', -1):.1%}；未选满部分：{shortfall}"
    if method == "quality_value":
        fields = {"top_n":"最多持股数", "max_per_industry":"每行业最多", "leaders_per_industry":"行业候选数", "min_roe":"最低ROE(%)", "max_pe_ttm":"最高PE_TTM", "max_pe":"最高PE", "max_debt":"最高资产负债率(%)", "min_listing_days":"至少上市天数", "positive_two_years":"连续两年盈利", "score_weights":"质量/估值/规模评分权重"}
        def display(value):
            if isinstance(value, bool):
                return "是" if value else "否"
            if isinstance(value, dict):
                names = {"quality":"质量", "value":"估值", "size":"规模"}
                return "、".join(f"{names.get(k,k)} {v:g}" if isinstance(v,(int,float)) else f"{names.get(k,k)} {v}" for k,v in value.items())
            return str(value)
        return "质量与估值筛选；" + "；".join(f"{v}：{display(c[k])}" for k,v in fields.items() if k in c)
    return json.dumps(c, ensure_ascii=False)


def weighting_text(c):
    method = c.get("method")
    if method == "equal": return "等权配置"
    if method == "min_variance":
        return f"最小方差；回看{c.get('lookback', 126)}个交易日，至少{c.get('min_observations', 60)}条有效样本；单只权重上限{c.get('max_weight', 1):.1%}；协方差收缩系数{c.get('shrinkage', .1)}"
    return json.dumps(c, ensure_ascii=False)


def macro_text(c, names):
    if not c.get("enabled"): return "固定大类比例，未启用宏观条件调整"
    freq = {"weekly":"每周", "monthly":"每月", "quarterly":"每季度"}
    parts = [freq.get(c.get("rebalance", "monthly"), str(c.get("rebalance")))+"检查"]
    for r in c.get("rules", []):
        transform = {"level":"水平值", "change":"变化值", "mean":"均值"}
        def describe(x):
            return f"{x.get('series')} {transform.get(x.get('transform','level'),x.get('transform'))} {x.get('op')} {x.get('threshold')}（回看{x.get('periods',1)}期）"
        groups=[]
        if r.get("all"): groups.append("全部满足："+" 且 ".join(describe(x) for x in r["all"]))
        if r.get("any"): groups.append("至少满足一项："+" 或 ".join(describe(x) for x in r["any"]))
        conditions = "；并且".join(groups)
        target = "、".join(f"{names.get(k,k)} {v:.1%}" for k,v in r.get("weights", {}).items())
        parts.append(f"{r.get('name','条件')}：{conditions} → {target}")
    missing = {"base_weights":"回到基础权重", "hold":"保留上次配置"}.get(c.get("missing", "base_weights"), str(c.get("missing")))
    parts.append("指标缺失时"+missing)
    return "；".join(parts)


def performance(daily):
    d = daily[["date", "nav"]].copy().reset_index(drop=True)
    d["nav"] = d["nav"] / d["nav"].iloc[0]
    d["return"] = d["nav"].pct_change(fill_method=None).fillna(0)
    d["drawdown"] = d["nav"] / d["nav"].cummax() - 1
    r = d["return"]
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    vol = float(r.std(ddof=1) * np.sqrt(252))
    trough = int(d["drawdown"].idxmin())
    peak = int(d.loc[:trough, "nav"].idxmax())
    recovered = d.loc[trough:].loc[d["nav"] >= d.loc[peak, "nav"]]
    info = {"start": d["date"].iloc[0].strftime("%Y-%m-%d"), "end": d["date"].iloc[-1].strftime("%Y-%m-%d"),
            "observations": len(d), "return_intervals": len(d)-1, "total_return": float(d["nav"].iloc[-1]-1),
            "annual_return": float(d["nav"].iloc[-1] ** (1 / years) - 1), "annual_volatility": vol,
            "max_drawdown": float(d["drawdown"].min()),
            "sharpe_zero_rf": float(r.mean()*252/vol) if vol > 1e-12 else None,
            "drawdown_peak": d.loc[peak, "date"].strftime("%Y-%m-%d"),
            "drawdown_trough": d.loc[trough, "date"].strftime("%Y-%m-%d"),
            "recovery_date": recovered["date"].iloc[0].strftime("%Y-%m-%d") if len(recovered) else None}
    return d, info


def rule_rows(run):
    c = run.config
    freq = {"weekly": "每周", "monthly": "每月", "quarterly": "每季度"}
    rows = [{"parameter": "大类调仓", "value": freq.get(c.get("allocation_rebalance"), "买入持有/未提供")},
            {"parameter": "交易成本率", "value": str(c.get("cost_rate", "未提供"))},
            {"parameter": "基础币种", "value": run.currency}]
    allocation = c.get("allocation", {})
    if allocation.get("method") == "risk_parity":
        rows.append({"parameter":"大类配权", "value":f"风险平价；回看{allocation.get('lookback',126)}日，最少{allocation.get('min_observations',60)}条共同样本；协方差收缩{allocation.get('shrinkage',.1)}；投资比例{allocation.get('invested_weight',1):.1%}"})
        rows.append({"parameter":"风险估计代理", "value":"；".join(sid+"："+"、".join(a.get("name",a["code"])+"("+a["code"]+")" for a in basket) for sid,basket in allocation.get("proxies",{}).items())})
    else:
        rows.append({"parameter":"大类配权", "value":"宏观条件配额" if c.get("macro",{}).get("enabled") else "固定比例"})
    for s in c.get("sleeves", []):
        name = s.get("name", s["id"])
        rows += [{"parameter": f"{name}·基础比例", "value": f"{s.get('weight', 0):.1%}"},
                 {"parameter": f"{name}·证券", "value": "、".join(f"{a.get('name', a['code'])}({a['code']})" if a.get('name') else a['code'] for a in s.get("assets", [])) or "按历史截面选股"},
                 {"parameter": f"{name}·内部调仓", "value": freq.get(s.get("rebalance"), str(s.get("rebalance", "未提供")))},
                 {"parameter": f"{name}·筛选", "value": selection_text(s.get("selection", {}))},
                 {"parameter": f"{name}·配权", "value": weighting_text(s.get("weighting", {}))}]
    rows.append({"parameter": "宏观规则", "value": "未启用宏观条件；大类比例采用风险平价" if allocation.get("method") == "risk_parity" else macro_text(c.get("macro", {}), {s["id"]:s.get("name",s["id"]) for s in c.get("sleeves", [])})})
    if c.get("benchmark_only"):
        rows.append({"parameter": "基准证券", "value": c["benchmark_only"]["code"]})
    return rows


def flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key in sorted(value):
            if key == "name" or (not prefix and key in {"start", "end", "initial_capital", "schema_version", "benchmark"}):
                continue
            out.update(flatten(value[key], f"{prefix}.{key}".strip(".")))
        return out
    if isinstance(value, list):
        # 按资产部分ID映射，使文件中的顺序变化不伪装成规则变化。
        if all(isinstance(v, dict) and "id" in v for v in value):
            return flatten({v["id"]: v for v in value}, prefix)
        return {prefix: json.dumps(value, ensure_ascii=False, sort_keys=True)}
    return {prefix: value}


def period_table(daily, frequency):
    period = daily["date"].dt.to_period(frequency).astype(str)
    values = (daily["return"]+1).groupby(period).prod()-1
    return [{"period": p, "return": float(v)} for p, v in values.items()]


def bounded(frame, start, end, date_col="date"):
    if frame is None or frame.empty or date_col not in frame:
        return pd.DataFrame()
    d = frame.copy()
    dates = pd.to_datetime(d[date_col])
    return d.loc[dates.between(start, end)].copy()


def detail(run, selected, metrics, start, end):
    from .risk import analyse_risk
    names = {s["id"]: s.get("name", s["id"]) for s in run.config.get("sleeves", [])}
    names["cash"] = "未分配现金"
    out = {"rules": rule_rows(run), "contributions": [], "holdings": [], "weight_history": [],
           "trades": [], "selections": [], "regimes": [], "optimization": [], "diagnostics": [],
           "source_notes": run.documents.get("manifest", {}).get("notes", []), "notices": list(run.notices)}
    out["risk"] = analyse_risk(selected, names)
    # 从每日分部分盈亏截取共同区间；不能把完整历史贡献混入短期比较。
    pnl_cols = [c for c in selected if c.startswith("pnl_")]
    if pnl_cols and "equity" in selected:
        if not np.isfinite(selected[pnl_cols].to_numpy(dtype=float)).all():
            raise ValueError(f"{run.label}的分部分盈亏包含空值或无效数值。")
        denom = float(selected["equity"].iloc[0])
        for col in pnl_cols:
            amount = float(selected[col].iloc[1:].sum())
            sid = col[4:]
            out["contributions"].append({"sleeve": sid, "name": names.get(sid, sid), "pnl": amount, "contribution": amount/denom})
        error = sum(x["contribution"] for x in out["contributions"]) - metrics["total_return"]
        if not np.isclose(error, 0, atol=1e-8):
            raise ValueError(f"{run.label}的分部分盈亏无法还原所选区间收益。")
        out["contributions"].sort(key=lambda x: x["contribution"], reverse=True)
    weights = bounded(run.tables.get("weights"), start, end)
    if not weights.empty:
        if weights.duplicated(["date", "sleeve", "code"]).any():
            raise ValueError(f"{run.label}的持仓表有重复行。")
        if not np.isfinite(weights["weight"].to_numpy(dtype=float)).all():
            raise ValueError(f"{run.label}的持仓权重有空值。")
        sums = weights.groupby("date")["weight"].sum()
        if not np.allclose(sums, 1., atol=1e-6):
            raise ValueError(f"{run.label}持仓权重合计不为1。")
        if not set(pd.to_datetime(selected["date"])) <= set(pd.to_datetime(weights["date"])):
            raise ValueError(f"{run.label}持仓表缺少回测日期。")
        if "equity" in selected and "value" in weights:
            account = selected.set_index("date")["equity"]
            expected = pd.to_datetime(weights["date"]).map(account) * weights["weight"]
            if not np.allclose(weights["value"], expected, rtol=1e-7, atol=.01):
                raise ValueError(f"{run.label}的持仓金额、权重和账户权益不一致。")
        history = weights.groupby(["date", "sleeve"], as_index=False)["weight"].sum()
        history["name"] = history["sleeve"].map(names).fillna(history["sleeve"])
        out["weight_history"] = records(history)
        last = weights.loc[pd.to_datetime(weights["date"]).eq(end)].copy()
        ticker_names = {a["code"]: a.get("name", a["code"]) for s in run.config.get("sleeves", []) for a in s.get("assets", [])}
        picks = bounded(run.tables.get("selections"), pd.Timestamp("1900-01-01"), end, "execution_date")
        if not picks.empty and "name" in picks:
            ticker_names.update(picks.sort_values("execution_date").dropna(subset=["name"]).set_index("code")["name"].to_dict())
        ticker_names["CASH"] = "现金"
        last["name"] = last["code"].map(ticker_names).fillna(last["code"])
        last["sleeve_name"] = last["sleeve"].map(names).fillna(last["sleeve"])
        out["holdings"] = records(last.sort_values("weight", ascending=False))
    for key, date_col in [("trades", "date"), ("selections", "execution_date")]:
        out[key] = records(bounded(run.tables.get(key), start, end, date_col))
    for key in ["regimes", "optimization"]:
        out[key] = [row for row in run.documents.get(key, []) if str(start.date()) <= row.get("execution_date", "") <= str(end.date())]
    checks = run.documents.get("checks", {})
    # 输入检查属于完整运行，不伪装成共同区间内计数。
    labels = {"fallback_equal_count": "配权回退等权次数", "missing_macro_count": "宏观缺项次数", "risk_allocation_fallback_count":"大类风险配权回退次数", "blocked_attempts": "未执行的成交尝试", "long_valuation_gap_rows": "长期缺报价记录数", "future_signal_trades": "信号时序错误数"}
    out["diagnostics"] = [{"item": label, "count": int(checks[k]), "scope": "原始完整回测"} for k, label in labels.items() if k in checks]
    if run.documents.get("unfilled_at_end"):
        out["notices"].append("原始回测末日仍有待成交目标：" + "、".join(run.documents["unfilled_at_end"]))
    return out


def analyse(config, runs):
    for run in runs:
        check_long_only(run)
    runs = public_runs(config, runs)
    start = max(r.daily["date"].iloc[0] for r in runs)
    end = min(r.daily["date"].iloc[-1] for r in runs)
    if config.get("start"):
        start = max(start, pd.Timestamp(config["start"]))
    if config.get("end"):
        end = min(end, pd.Timestamp(config["end"]))
    if start >= end:
        raise ValueError("所选策略没有至少两个日期的共同区间。")
    selections = [r.daily.loc[r.daily["date"].between(start, end)].copy().reset_index(drop=True) for r in runs]
    dates = selections[0]["date"]
    if len(dates) < 2 or any(not x["date"].equals(dates) for x in selections[1:]):
        raise ValueError("共同区间的交易日期不一致，请先统一日历或补齐缺失；报告不会插值补净值。")
    start, end = dates.iloc[0], dates.iloc[-1]
    data = {"schema_version": 1, "title": config.get("title", "资产配置策略研究报告"),
            "period": {"start": str(start.date()), "end": str(end.date()), "observations": len(dates),
                       "currency": runs[0].currency, "anchor": "共同起始日收盘净值归一为1；起始日之前及当日已发生的收益不计入比较"},
            "strategies": [], "curves": [], "monthly": [], "annual": [], "pairs": [], "facts": [],
            "methods": {"annualization_days": 252, "cagr": "按首末日期之间天数/365.25折算", "risk_free_rate": 0,
                        "return_units": "decimal", "comparison": "按共同估值日期重算绩效，延续各策略原始持仓及调仓路径"},
            "input_fingerprints": {r.id: r.hashes for r in runs}}
    fact_no = 0

    def fact(text, strategy_ids, sources):
        nonlocal fact_no
        fact_no += 1
        data["facts"].append({"id": f"F{fact_no:03d}", "text": text, "strategies": strategy_ids, "evidence": sources})

    for run, selected in zip(runs, selections):
        d, m = performance(selected)
        _, original = performance(run.daily)
        item = {"id": run.id, "label": run.label, "scenario": run.scenario, "metrics": m, "original_metrics": original,
                "currency": run.currency, "initial_capital": run.handoff.get("initial_capital"),
                "source": {"directory": str(run.path), "daily_file": SCENARIO_FILE(run), "price_digest": run.documents.get("manifest", {}).get("price_digest")},
                "config": run.config, "detail": detail(run, selected, m, start, end)}
        data["strategies"].append(item)
        for row in records(d):
            row.update(strategy_id=run.id, strategy=run.label)
            row["date"] = row["date"][:10]
            data["curves"].append(row)
        for key, freq in [("monthly", "M"), ("annual", "Y")]:
            data[key] += [{"strategy_id": run.id, "strategy": run.label, **row} for row in period_table(d, freq)]
        fact(f"{run.label}在共同区间累计收益{m['total_return']:.2%}，年化波动{m['annual_volatility']:.2%}，最大回撤{m['max_drawdown']:.2%}。", [run.id], ["metrics", "curves"])
        contrib = item["detail"]["contributions"]
        risk = item["detail"]["risk"]
        if risk["status"] == "ok":
            largest = max(risk["rows"], key=lambda x:x["risk_share"])
            fact(f"{run.label}的{largest['name']}风险贡献占比最高，为{largest['risk_share']:.2%}；各部分波动贡献之和为组合年化波动率{risk['annual_volatility']:.2%}。", [run.id], ["detail.risk", "metrics.annual_volatility"])
        if contrib:
            best = contrib[0]
            wording = "贡献最高" if best["contribution"] >= 0 else "拖累最小"
            fact(f"{run.label}中，{best['name']}{wording}，贡献{best['contribution']*100:+.2f}个百分点；各部分贡献合计{sum(x['contribution'] for x in contrib)*100:+.2f}个百分点。", [run.id], ["detail.contributions", "metrics.total_return"])
        regimes = item["detail"]["regimes"]
        if run.config.get("macro", {}).get("enabled") and regimes:
            counts = pd.Series([x.get("state", "未标注") for x in regimes]).value_counts().to_dict()
            fact(f"{run.label}在区间内检查配置{len(regimes)}次：" + "，".join(f"{k}{v}次" for k,v in counts.items()) + "。", [run.id], ["detail.regimes"])
        for notice in item["detail"]["diagnostics"]:
            if notice["count"]:
                fact(f"{run.label}的原始完整回测记录了{notice['item']}{notice['count']}条/次。", [run.id], ["detail.diagnostics"])
    by_id = {x["id"]: x for x in data["strategies"]}
    for a,b in itertools.combinations(runs, 2):
        x,y = by_id[a.id],by_id[b.id]
        fa,fb = flatten(a.config),flatten(b.config)
        diffs = [{"parameter": k, "left": fa.get(k), "right": fb.get(k)} for k in sorted(set(fa)|set(fb)) if fa.get(k)!=fb.get(k)]
        pair = {"left": a.id, "right": b.id,
                "deltas": {k: y["metrics"][k]-x["metrics"][k] for k in ["total_return", "annual_return", "annual_volatility", "max_drawdown"]},
                "rule_differences": diffs, "same_price_inputs": bool(x["source"].get("price_digest")) and x["source"].get("price_digest")==y["source"].get("price_digest")}
        data["pairs"].append(pair)
        delta = pair["deltas"]
        def difference(v):
            return "不足0.01" if 0 < abs(v*100) < .005 else f"{v*100:+.2f}"
        fact(f"相对{a.label}，{b.label}的累计收益相差{difference(delta['total_return'])}个百分点，年化波动相差{difference(delta['annual_volatility'])}个百分点，最大回撤数值相差{difference(delta['max_drawdown'])}个百分点（正值表示回撤较浅）。", [a.id,b.id], ["pairs.deltas", "pairs.rule_differences", "source.price_digest"])
    if len(runs)>1:
        high = max(data["strategies"],key=lambda s:s["metrics"]["total_return"])
        low = max(data["strategies"],key=lambda s:s["metrics"]["max_drawdown"])
        if high["id"] == low["id"]:
            data["lead"] = f"共同区间内，{high['label']}同时取得了最高累计收益和最浅最大回撤。"
        else:
            data["lead"] = f"共同区间内，{high['label']}累计收益最高，{low['label']}最大回撤最浅。"
    else:
        data["lead"] = data["facts"][0]["text"]
    data["scope_notes"] = []
    if any(r.daily["date"].iloc[0]!=start or r.daily["date"].iloc[-1]!=end for r in runs):
        data["scope_notes"].append("各策略原始日期范围不同或本次指定了子区间；主图和指标统一按共同区间重算，各自全区间结果另列。")
    if len({str(r.config.get("cost_rate", "unknown")) for r in runs}) > 1:
        data["scope_notes"].append("这些策略采用不同或未注明的交易成本设置，差异已保留在规则表中。")
    data["scope_notes"].append("收益最高、回撤最浅分别描述本次历史结果；策略设置不同的比较同时包含资产、比例和规则变化。")
    return decorate(data, config)


def SCENARIO_FILE(run):
    from .inputs import SCENARIOS
    return SCENARIOS[run.scenario]
