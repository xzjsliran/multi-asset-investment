"""从已核实的回测证据选出少量、可验证的后续研究。"""
import numpy as np


DISPLAY_RESOLUTION = .00005  # 低于半个基点的差别按报告显示精度不触发。


def drawdown_trace(daily, curve, metrics, names, opening_capital):
    """回撤段按峰值权益计贡献，使用峰值收盘之后至谷底的盈亏。"""
    depth = metrics["max_drawdown"]
    columns = [column for column in daily if column.startswith("pnl_")]
    if not columns or "equity" not in daily or depth >= -DISPLAY_RESOLUTION:
        return None
    trough = int(curve["drawdown"].idxmin())
    peak = int(curve["nav"].iloc[:trough + 1].idxmax())
    from_capital = opening_capital is not None and float(curve["nav"].iloc[peak]) < 1 - 1e-12
    capital = opening_capital if from_capital else float(daily["equity"].iloc[peak])
    first = 0 if from_capital else peak + 1
    losses = daily[columns].iloc[first:trough + 1].sum() / capital
    if not np.isclose(losses.sum(), depth, rtol=1e-7, atol=1e-8):
        return None  # 外部资料的分项路径未对上时，仅保留报告的原指标。
    return {
        "peak": metrics["start"] if from_capital else metrics["drawdown_peak"],
        "from_initial_capital": from_capital,
        "trough": metrics["drawdown_trough"],
        "parts": [{"name": names.get(col[4:], col[4:]), "contribution": float(losses[col])}
                  for col in losses.sort_values().index],
    }


def daily_exposure(run, selected, weights):
    """现金按账户口径。股票仓位只根据声明的股票类型或明确的资产部分计算。"""
    if not run.config.get("sleeves"):
        return {}
    exposure = {}
    if "cash_weight" in selected:
        exposure["cash"] = float(selected["cash_weight"].mean())
    sleeves = run.config["sleeves"]
    equity_sleeves = set()
    securities = {}
    for sleeve in sleeves:
        selection = sleeve.get("selection", {}).get("method")
        name = sleeve.get("name", "")
        if (sleeve["id"] in {"equity", "stock", "stocks"} or "股票" in name or "权益" in name
                or selection in {"quality_value", "fundamental"}):
            equity_sleeves.add(sleeve["id"])
        securities.update({asset["code"]: asset for asset in sleeve.get("assets", [])})
    direct_stocks = {code for code, asset in securities.items() if asset.get("kind") == "stock"}
    if not weights.empty and (equity_sleeves or direct_stocks):
        days = selected["date"]
        # 资产部分中的现金单独列示，未分配的交易日补零权重。
        rows = weights.loc[weights["code"].ne("CASH")
                           & (weights["sleeve"].isin(equity_sleeves) | weights["code"].isin(direct_stocks))]
        weights_by_day = rows.groupby("date")["weight"].sum()
        exposure["equity"] = float(weights_by_day.reindex(days, fill_value=0.).mean())
    elif equity_sleeves:
        columns = [f"invested_weight_{sleeve['id']}" for sleeve in sleeves]
        if set(columns) <= set(selected):
            # 旧版配套结果只有净值或分项盈亏，缺少仓位时留空。
            outside = {asset["code"] for sleeve in sleeves if sleeve["id"] not in equity_sleeves
                       for asset in sleeve.get("assets", [])}
            if not outside & direct_stocks:
                equity_columns = [f"invested_weight_{sid}" for sid in sorted(equity_sleeves)]
                exposure["equity"] = float(selected[equity_columns].to_numpy(dtype=float).sum(axis=1).mean())
    return exposure


def suggestions(data, fact):
    """执行问题优先，每份报告最多选两条，建议保留原证据编号。"""
    by_id = {item["id"]: item for item in data["strategies"]}
    evidence = data["evidence_values"]
    actions = []

    def add(kind, title, observation, test, decision, evidence_ids, strategies):
        actions.append({"kind": kind, "title": title, "observation": observation, "test": test,
                        "decision": decision, "evidence_ids": evidence_ids, "strategies": strategies})

    for item in data["strategies"]:
        details = item["detail"]
        review = item["execution_review"] or {}
        ids = [item["id"]]
        status = review.get("status")
        if status in {"limited", "method_not_evaluated", "no_decisions"}:
            recorded = next(row["id"] for row in data["facts"]
                            if row["text"] == review["text"] and row["strategies"] == ids)
            if status == "no_decisions":
                add("execution", "配置决策覆盖复查", review["text"],
                    "沿用原结果，选择覆盖实际调仓事件的共同区间，再查看决策记录。",
                    "有新的有效决策后，再判断配置变化能否解释该区间的绩效差异。",
                    [recorded], ids)
            else:
                add("execution", "配置回退复查", review["text"],
                    "定位回退日，核对信号、报价、样本窗口及求解记录。数据缺项补齐后按原规则复算。",
                    "如果恢复正常执行后风险收益关系变化，按复算结果更新原判断。若仍没有有效决策，只评价实际执行路径。",
                    [recorded], ids)
        elif details.get("blocked_in_period"):
            text = f"{item['label']}在比较区间记录到{details['blocked_in_period']}次未执行的成交尝试。"
            fid = fact(text, ids, ["blocked.csv"])
            add("execution", "未成交复查", text,
                "对照未成交日期、证券和期后成交明细，核对缺报价是否影响关键调仓或回撤段。",
                "如果未成交使配置明显偏离原目标，优先解释实际持仓路径，再判断配权规则的表现。",
                [fid], ids)
        elif details.get("valuation_gaps_in_period"):
            text = f"{item['label']}在比较区间记录到{details['valuation_gaps_in_period']}条长期缺报价估值记录。"
            fid = fact(text, ids, ["valuation_gaps.csv"])
            add("execution", "缺报价估值复查", text,
                "核对缺报价日期是否落在主要回撤期，优先补足同一标的的历史报价后复算。",
                "若补齐报价后回撤排序或谷底日期变化，改用覆盖充分的结果解释策略差异。",
                [fid], ids)
        if len(actions) == 2:
            return actions
    if actions:
        return actions

    pairs = sorted(data["pairs"], key=lambda pair: abs(pair["deltas"]["max_drawdown"]), reverse=True)
    for pair in pairs:
        if pair["comparison_type"] in {"市场参照", "回退配置比较"}:
            continue
        shallow, deep = (by_id[item] for item in (pair["left"], pair["right"]))
        difference = pair["deltas"]["max_drawdown"]
        if abs(difference) < DISPLAY_RESOLUTION:
            continue
        if difference > 0:
            shallow, deep = deep, shallow
        if deep["scenario"] == "benchmark" or shallow["scenario"] == "benchmark":
            continue
        left = shallow["detail"]["exposure"]
        right = deep["detail"]["exposure"]
        changes = []
        if "equity" in left and "equity" in right and left["equity"] < right["equity"] - DISPLAY_RESOLUTION:
            changes.append(("股票部分仓位", "股票仓位", "equity"))
        if "cash" in left and "cash" in right and left["cash"] > right["cash"] + DISPLAY_RESOLUTION:
            changes.append(("现金仓位", "现金比例", "cash"))
        if not changes:
            continue
        label, condition, key = changes[0]
        observation = (f"共同区间内，{shallow['label']}回撤较浅。"
                       f"{shallow['label']}的日均{label}为{left[key]:.2%}，"
                       f"{deep['label']}的日均{label}为{right[key]:.2%}。")
        ids = [shallow["id"], deep["id"]]
        inputs = [item["source"]["daily_file"] for item in (shallow, deep)]
        if key == "equity" and any(item["detail"]["weight_history"] for item in (shallow, deep)):
            inputs.append("weights.csv")
        fid = fact(observation, ids, [*dict.fromkeys(inputs), "detail.exposure", "metrics.max_drawdown"])
        add("exposure", "低回撤的仓位解释", observation,
            f"在测试期开始前用投资约束或此前数据确定{condition}，加一个固定配置对照。统一资产池、比较区间、调仓日与费率。",
            "若固定配置也带来接近的回撤改善，收窄对动态配置增量效果的判断，同时列示收益代价。",
            [fid], ids)
        break

    # 先定位回撤最深的可解释方案，再用同一峰谷区间检查其他方案。
    ranked = sorted(data["strategies"], key=lambda item: item["metrics"]["max_drawdown"])
    for item in ranked:
        trace = item["detail"].get("drawdown_trace")
        if not trace or item["scenario"] == "benchmark":
            continue
        part = trace["parts"][0]
        if part["contribution"] >= -DISPLAY_RESOLUTION:
            continue
        anchor = "建仓前本金" if trace["from_initial_capital"] else f"{trace['peak']}收盘峰值"
        observation = (f"{item['label']}从{anchor}至{trace['trough']}谷底，"
                       f"{part['name']}的盈亏贡献为{part['contribution'] * 100:+.2f}个百分点。")
        fid = fact(observation, [item["id"]], [item["source"]["daily_file"], "detail.drawdown_trace"])
        test = (f"沿用原持仓，把比较聚焦到{trace['peak']}至{trace['trough']}。"
                "逐项核对资产盈亏，再查看该段持仓及调仓。")
        if trace["from_initial_capital"]:
            test += "起算保留建仓前本金与首日费用。"
        if trace["peak"] == trace["trough"]:
            test = "核对首日建仓的成交额、费用与期末持仓，再比较其对原回测的影响。"
        decision = "如果主要负贡献来自一个资产部分，先检查其集中度。若多个部分一起拖累，再检查同期的分散效果。"
        add("drawdown", "回撤区间的资产贡献", observation, test, decision, [fid], [item["id"]])
        break

    if not actions:
        for item in ranked:
            m = item["metrics"]
            if item["scenario"] == "benchmark" or m["max_drawdown"] >= -DISPLAY_RESOLUTION:
                continue
            claim = evidence[f"metric.{item['id']}.max_drawdown"]
            observation = f"{item['label']}最大回撤为{m['max_drawdown']:.2%}，谷底在{m['drawdown_trough']}。"
            if m["drawdown_peak"] == m["drawdown_trough"]:
                test = "检查首日净值和建仓费用的起算口径，并核对从同一基值起算的各方案结果。"
            else:
                test = (f"按{m['drawdown_peak']}至{m['drawdown_trough']}重算各方案的历史表现，沿用原交易路径。"
                        "需要解释资产来源时补充持仓和逐日分项盈亏。")
            add("drawdown_interval", "回撤区间的绩效对照", observation,
                test,
                "若排序随区间变化，将原结论限定在对应样本，再用锁定规则后的后续区间检查。",
                [claim["fact_id"]], [item["id"]])
            break
    return actions[:2]
