"""将用户约定的指标条件变成大类权重；不让模型事后挑选经济状态。"""
from __future__ import annotations

import operator
import math

import pandas as pd

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


def known_observations(events, series, signal):
    """同一统计期只保留决策时能看到的最新版本。"""
    cutoff = (signal + pd.Timedelta(hours=16)).tz_localize("Asia/Shanghai").tz_convert("UTC")
    if events.empty:
        return events, cutoff
    rows = events.loc[events["series"].eq(series) & events["signal_eligible"].eq(True)
                      & events["available_at"].le(cutoff)].dropna(subset=["value", "available_at"]).copy()
    return rows.sort_values("available_at").drop_duplicates("period", keep="last").sort_values("period"), cutoff


def observation(events, condition, signal):
    series = condition["series"]
    rows, cutoff = known_observations(events, series, signal)
    if rows.empty:
        return None, {"series": series, "reason": "该信号日前没有可用的已公布记录"}
    last = rows.iloc[-1]
    age = (cutoff - last["available_at"]).total_seconds() / 86400
    default_age = 800 if series == "world_gdp_growth" else (62 if len(str(last["period"])) == 7 else 14)
    if age > condition.get("max_age_days", default_age):
        return None, {"series": series, "reason": "最新记录超过设定有效期", "age_days": age}
    if len(str(last["period"])) == 7:
        period_age = (signal - pd.Period(last["period"], freq="M").end_time.normalize()).days
        if period_age > condition.get("max_period_age_days", 90):
            return None, {"series": series, "reason": "快照虽新，统计月份已经过旧"}
    n = int(condition.get("periods", 1))
    transform = condition.get("transform", "level")
    count = n + 1 if transform == "change" else (n if transform == "mean" else 1)
    if len(rows) < count:
        return None, {"series": series, "reason": "历史期数不足"}
    sample = rows.tail(count)
    if len(str(last["period"])) == 7 and count > 1:
        periods = pd.PeriodIndex(sample["period"], freq="M")
        if any((periods[i].ordinal - periods[i - 1].ordinal) != 1 for i in range(1, len(periods))):
            return None, {"series": series, "reason": "月份不连续，不能把跨月差当作本月变化"}
    value = float(last["value"])
    if transform == "change":
        value -= float(sample.iloc[0]["value"])
    elif transform == "mean":
        value = float(sample["value"].mean())
    if not math.isfinite(value):
        return None, {"series": series, "reason": "指标值无效"}
    return value, {"series": series, "value": value, "period": str(last["period"]),
                   "available_at": last["available_at"].isoformat(), "transform": transform}


def evaluate_rule(rule, events, signal):
    """缺失值保留为未知。只有匹配结果确定时才采用该规则。"""
    groups, inputs = {}, []
    for group in ("all", "any"):
        values = []
        for cond in rule.get(group, []):
            value, detail = observation(events, cond, signal)
            inputs.append({**detail, "rule": rule["name"]})
            values.append(None if value is None else OPS[cond["op"]](value, cond["threshold"]))
        groups[group] = values
    required, alternatives = groups["all"], groups["any"]
    if False in required or (alternatives and all(x is False for x in alternatives)):
        return False, inputs
    if None in required or (alternatives and True not in alternatives):
        return None, inputs
    return True, inputs


def allocation(config, events, signal, previous):
    base = {s["id"]: s["weight"] for s in config["sleeves"]}
    macro = config.get("macro", {})
    if not macro.get("enabled"):
        return base, {"state": "固定配置", "inputs": [], "missing": False}
    inputs, missing = [], False
    for rule in macro["rules"]:
        matched, details = evaluate_rule(rule, events, signal)
        inputs.extend(details)
        missing |= any("reason" in row for row in details)
        if matched is True:
            return dict(rule["weights"]), {"state": rule["name"], "inputs": inputs, "missing": missing}
        if matched is None:
            chosen = previous if macro.get("missing", "base_weights") == "hold" else base
            return dict(chosen), {"state": "指标不足，沿用" + ("上期配置" if macro.get("missing") == "hold" else "基础配置"),
                                  "inputs": inputs, "missing": True, "fallback": True}
    return base, {"state": "基础配置", "inputs": inputs, "missing": missing}
