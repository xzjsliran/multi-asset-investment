"""回测与账户调仓共用的决策计算；输入估值，输出目标金额，不模拟成交。"""
from __future__ import annotations

import copy
import math
import pandas as pd

from .config import FREQUENCIES
from .regime import allocation
from .selection import select_and_weight
from .risk import allocate_risk


def nonnegative(values, label, maximum=None):
    vals = list(values)
    if any(not math.isfinite(float(v)) or float(v) < 0 for v in vals):
        raise ValueError(label + "必须为有限非负数；所有资产只做多。")
    if maximum is not None and sum(vals) > maximum + 1e-9:
        raise ValueError(label + "合计超过允许上限。")


def event_on(config, calendar, execution_date, initial=False):
    """按真实交易日跨期识别事件；查询起点不会被当作初次建仓日。"""
    dates = pd.DatetimeIndex(calendar).normalize().unique().sort_values()
    day = pd.Timestamp(execution_date).normalize()
    if day not in dates or dates.get_loc(day) == 0:
        raise ValueError("执行日须在已核实交易日历中，且日历须含前一交易日。")
    signal = dates[dates.get_loc(day) - 1]
    def due(frequency):
        return initial or day.to_period(FREQUENCIES[frequency]) != signal.to_period(FREQUENCIES[frequency])
    outer = due(config["allocation_rebalance"])
    if config.get("macro", {}).get("enabled"):
        outer |= due(config["macro"].get("rebalance", "monthly"))
    return {"execution_date": day, "signal_date": signal, "is_outer": outer,
            "new_ids": [s["id"] for s in config["sleeves"] if due(s["rebalance"])]}


def decide(config, data, execution_date, signal_date, current_values, sleeve_cash,
           inner, previous_alloc, new_ids, is_outer):
    """current_values: {sleeve: {code: CNY}}；现金同币种估值，全部非负。

    信号只读 signal_date 及之前的数据；估值由调用方显式提供。
    因此回测可用成交日估值，而当前计划用截至计划时点的参考报价。
    """
    day, signal = pd.Timestamp(execution_date), pd.Timestamp(signal_date)
    if signal >= day:
        raise ValueError("信号日须早于拟执行日。")
    sleeves = {s["id"]: s for s in config["sleeves"]}
    ids = list(sleeves) + ["cash"]
    if not set(new_ids) <= set(sleeves):
        raise ValueError("调仓事件含未知资产部分。")
    for sid in ids:
        nonnegative(current_values.get(sid, {}).values(), "当前持仓金额")
        nonnegative([sleeve_cash.get(sid, 0.)], "当前现金")
    nonnegative(previous_alloc.values(), "上期大类权重", 1.)
    inner = copy.deepcopy(inner)
    pre = {sid: sum(current_values.get(sid, {}).values()) + sleeve_cash.get(sid, 0.) for sid in ids}
    nav = sum(pre.values())
    if nav <= 0:
        raise ValueError("账户总资产须大于零。")
    audits, selected = [], []
    def select(sid):
        w, chosen, audit = select_and_weight(sleeves[sid], signal, day, data.prices, data.features)
        nonnegative(w.values(), "部分内权重", 1.)
        inner[sid] = w
        audits.append(audit)
        if not chosen.empty:
            selected.append(chosen)
    for sid in new_ids:
        select(sid)
    desired, regime = {}, None
    alloc = dict(previous_alloc)
    if is_outer:
        if config.get("allocation", {}).get("method") == "risk_parity":
            alloc, info = allocate_risk(config, data.prices, signal, previous_alloc)
        else:
            alloc, info = allocation(config, data.macro, signal, previous_alloc)
        nonnegative(alloc.values(), "大类权重", 1.)
        if set(alloc) != set(sleeves):
            raise ValueError("大类权重必须包含全部资产部分。")
        regime = {"execution_date": str(day.date()), "signal_date": str(signal.date()), **info, "weights": alloc}
        for sid in sleeves:
            if sid not in inner:
                select(sid)
            within = inner[sid] if sid in new_ids or pre[sid] <= 1e-8 else {
                code: value / pre[sid] for code, value in current_values.get(sid, {}).items()}
            nonnegative(within.values(), "部分内权重", 1.)
            budget = nav * alloc[sid]
            desired[sid] = {code: budget * w for code, w in within.items()}
            desired[sid]["__cash__"] = budget * max(0., 1 - sum(within.values()))
        desired["cash"] = {"__cash__": nav * max(0., 1 - sum(alloc.values()))}
        group = ids
    else:
        group = list(new_ids)
        for sid in group:
            desired[sid] = {code: pre[sid] * w for code, w in inner[sid].items()}
            desired[sid]["__cash__"] = pre[sid] * max(0., 1 - sum(inner[sid].values()))
    for targets in desired.values():
        nonnegative(targets.values(), "目标持仓金额")
    if abs(sum(sum(v.values()) for v in desired.values()) - sum(pre[sid] for sid in group)) > max(1., nav) * 1e-9:
        raise AssertionError("目标金额与本次参与调仓的预算不一致。")
    return {"desired": desired, "group": group, "inner": inner, "allocation": alloc,
            "regime": regime, "optimization": audits, "selections": selected, "equity": nav}
