"""将用户约定的指标条件变成大类权重；不让模型事后挑选经济状态。"""
from __future__ import annotations

import operator
import math

import pandas as pd

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


def observation(events, condition, signal):
    series = condition["series"]
    cutoff = (signal + pd.Timedelta(hours=16)).tz_localize("Asia/Shanghai").tz_convert("UTC")
    if events.empty:
        return None, {"series": series, "reason": "没有宏观数据"}
    rows = events.loc[events["series"].eq(series) & events["signal_eligible"].eq(True)
                      & events["available_at"].le(cutoff)].dropna(subset=["value", "available_at"]).copy()
    if rows.empty:
        return None, {"series": series, "reason": "该信号日前没有可用的已公布记录"}
    # 老月份的修订值后来到达时，不能替换最新统计月份。
    rows = rows.sort_values("available_at").drop_duplicates("period", keep="last").sort_values("period")
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


def allocation(config, events, signal, previous):
    base = {s["id"]: s["weight"] for s in config["sleeves"]}
    macro = config.get("macro", {})
    if not macro.get("enabled"):
        return base, {"state": "固定配置", "inputs": [], "missing": False}
    inputs, missing = [], False
    for rule in macro["rules"]:
        groups = {}
        for group in ["all", "any"]:
            values = []
            for cond in rule.get(group, []):
                value, detail = observation(events, cond, signal)
                detail["rule"] = rule["name"]
                inputs.append(detail)
                if value is None:
                    missing = True
                    values.append(None)
                else:
                    values.append(OPS[cond["op"]](value, cond["threshold"]))
            groups[group] = values
        # 三值逻辑：某规则缺指标时不能猜它不成立，从而落到另一种经济状态。
        all_result = False if False in groups["all"] else (None if None in groups["all"] else True)
        any_result = True if not groups["any"] or True in groups["any"] else (None if None in groups["any"] else False)
        if all_result is True and any_result is True:
            return dict(rule["weights"]), {"state": rule["name"], "inputs": inputs, "missing": missing}
        if all_result is not False and any_result is not False and (all_result is None or any_result is None):
            chosen = previous if macro.get("missing", "base_weights") == "hold" else base
            return dict(chosen), {"state": "指标不足，沿用" + ("上期配置" if macro.get("missing") == "hold" else "基础配置"),
                                  "inputs": inputs, "missing": True}
    return base, {"state": "基础配置", "inputs": inputs, "missing": missing}
