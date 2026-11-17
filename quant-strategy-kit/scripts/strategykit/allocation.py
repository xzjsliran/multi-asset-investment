"""在大类上下限内按净意向先减仓、再用共享现金加仓。"""
from collections import defaultdict
import math

import pandas as pd

from .regime import allocation as legacy_allocation, evaluate_rule
from .risk import allocate_risk
from .volatility import volatility_state


REGION_SERIES = {
    "CN": {"cn_pmi", "cn_cpi_yoy", "cn_policy_rate", "cn_unemployment"},
    "US": {"fed_upper", "fed_effective", "us_cpi_yoy", "us_unemployment"},
}
VOLATILITY_SERIES = {"vix", "cn_vix50", "cn_vix300"}


def validate_condition(cond):
    threshold = cond.get("threshold")
    if cond.get("op") not in {">", ">=", "<", "<=", "=="} or type(threshold) not in (int, float) or not math.isfinite(threshold):
        raise ValueError("宏观条件需要受支持的比较符和有限阈值。")
    if cond.get("transform", "level") not in {"level", "change", "mean"}:
        raise ValueError("宏观变化形式为 level、change 或 mean。")
    if type(cond.get("periods", 1)) is not int or cond.get("periods", 1) < 1:
        raise ValueError("宏观回看期数需要为正整数。")


def regional_rules(macro):
    for region, settings in macro.get("regions", {}).items():
        if settings.get("enabled", True):
            yield region, settings


def validate_overlays(config, ids):
    macro, volatility = config["macro"], config.get("volatility", {})
    limits = config.get("allocation_limits")
    if not limits:
        if (macro.get("enabled") and "regions" in macro) or volatility.get("enabled"):
            raise ValueError("地区宏观和波动率调整需要 allocation_limits。")
        return
    groups = limits.get("groups", {})
    members = [sid for group in groups.values() for sid in group]
    if set(members) != set(ids) or len(members) != len(ids) or not all(groups.values()) or "cash" in groups:
        raise ValueError("每个资产部分须属于一个非空的调权大类。cash 是共享现金。")
    shift = limits.setdefault("max_shift", .05)
    cap = limits.setdefault("max_cash", .25)
    if not 0 <= shift <= 1 or not 0 <= cap <= .25:
        raise ValueError("大类浮动须为 0 至 100 个百分点，组合现金上限不得超过 25%。")
    if 1 - sum(s["weight"] for s in config["sleeves"]) > cap + 1e-9:
        raise ValueError("基础配置的剩余现金已超过现金上限。")
    if "regions" in macro and "rules" in macro:
        raise ValueError("地区宏观规则与旧整组目标权重规则请分别保存。")
    for region, settings in macro.get("regions", {}).items():
        if region not in REGION_SERIES or not settings.get("rules"):
            raise ValueError("地区宏观配置支持 CN、US，每个地区须有状态规则。")
        for rule in settings["rules"] + settings.get("modifiers", []):
            conditions = rule.get("all", []) + rule.get("any", [])
            changes = rule.get("changes", {})
            if not rule.get("name") or not conditions or set(changes) - set(groups):
                raise ValueError("宏观规则需有名称、本地指标条件和已配置的目标大类。")
            for cond in conditions:
                if cond.get("series") not in REGION_SERIES[region]:
                    raise ValueError(region + " 的宏观状态须依据该地区的宏观指标。")
                validate_condition(cond)
            if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1
                   for value in changes.values()):
                raise ValueError("changes 使用有符号的权重差，0.05 表示 5 个百分点。")
    if volatility.get("enabled") and not volatility.get("signals"):
        raise ValueError("开启波动率调整后需要至少一条信号。")
    for spec in volatility.get("signals", []):
        for name, value in {"fast": 5, "slow": 30, "enter": 20., "exit": 18., "reduce": .05,
                            "max_age_days": 10}.items():
            spec.setdefault(name, value)
        if spec.get("series") not in VOLATILITY_SERIES or not spec.get("groups") or set(spec["groups"]) - set(groups):
            raise ValueError("波动率规则须引用已有波指和目标大类。")
        if not all(type(spec[k]) is int for k in ("fast", "slow", "max_age_days")):
            raise ValueError("波指均线窗口和有效期使用整数。")
        if not 0 < spec["fast"] < spec["slow"] or spec["max_age_days"] < 1:
            raise ValueError("波动率窗口须短线小于长线，且有效期为正。")
        if not 0 < spec["exit"] < spec["enter"] < math.inf or not 0 < spec["reduce"] <= 1:
            raise ValueError("波指退出阈值须低于进入阈值，减仓比例须为正。")
        if spec.get("switch_to"):
            if spec["series"] != "cn_vix50" or spec["switch_to"] != "cn_vix300" or not spec.get("switch_from"):
                raise ValueError("波指切换需指定上证50、沪深300及允许切换的起始日。")
            spec["switch_from"] = pd.Timestamp(spec["switch_from"]).strftime("%Y-%m-%d")


def indicator_plan(config):
    macro, vol = config["macro"], config.get("volatility", {})
    series = set()
    if macro.get("enabled"):
        states = list(macro.get("rules", []))
        for _, settings in regional_rules(macro):
            states += settings["rules"] + settings.get("modifiers", [])
        series.update(c["series"] for r in states for c in r.get("all", []) + r.get("any", []))
    if vol.get("enabled"):
        series.update(spec[key] for spec in vol["signals"] for key in ("series", "switch_to") if spec.get(key))
    return sorted(series)


def proposals(settings, events, signal):
    """一个地区独立判断状态。在状态数据齐全时才考虑附加条件。"""
    inputs, changes, modifiers = [], defaultdict(float), []
    for rule in settings["rules"]:
        matched, details = evaluate_rule(rule, events, signal)
        inputs.extend(details)
        if matched is None:
            return {}, {"state": "指标不足", "inputs": inputs, "missing": True}
        if matched:
            changes.update(rule.get("changes", {}))
            for modifier in settings.get("modifiers", []):
                apply, details = evaluate_rule(modifier, events, signal)
                inputs.extend(details)
                if apply:
                    modifiers.append(modifier["name"])
                    for group, value in modifier.get("changes", {}).items():
                        changes[group] += value
            return dict(changes), {"state": rule["name"], "inputs": inputs, "modifiers": modifiers, "missing": False}
    return {}, {"state": "无匹配状态", "inputs": inputs, "missing": False}


def bounded_weights(base, weights, changes, limits, previous=None):
    """先约束上游配置，再按各大类净意向卖出、买入，现金始终有来源。"""
    groups = limits["groups"]
    shift, max_cash = limits["max_shift"], limits["max_cash"]
    reference = {g: sum(base[s] for s in members) for g, members in groups.items()}
    lower = {g: max(0., ref - shift) for g, ref in reference.items()}
    upper = {g: min(1., ref + shift) for g, ref in reference.items()}

    def scale(amounts, budget):
        total = sum(amounts.values())
        factor = min(1., max(0., budget) / total) if total > 0 else 0.
        return {g: value * factor for g, value in amounts.items()}

    def clamp(g, value):
        return min(upper[g], max(lower[g], value))

    def project(sleeves):
        # 上游风险配置与旧目标都先约束到当前配置的资金区间。
        values = {g: clamp(g, sum(sleeves[s] for s in members)) for g, members in groups.items()}
        cash = 1 - sum(values.values())
        if cash < -1e-12:
            trims = scale({g: values[g] - lower[g] for g in groups}, -cash)
            return {g: values[g] - trims[g] for g in groups}
        if cash > max_cash + 1e-12:
            topups = scale({g: upper[g] - values[g] for g in groups}, cash - max_cash)
            return {g: values[g] + topups[g] for g in groups}
        return values

    anchor = project(weights)
    before = project(previous) if previous else anchor
    cash = max(0., 1 - sum(before.values()))
    sells, buys = {}, {}
    for g, change in changes.items():
        level = clamp(g, anchor[g] + change)
        if change < 0:
            sells[g] = max(0., before[g] - level)
        if change > 0:
            buys[g] = max(0., level - before[g])
    # 先出售，最终超出现金上限的部分少卖。超出可用资金的加仓按比例缩减。
    sold = scale(sells, max_cash - cash + sum(buys.values()))
    bought = scale(buys, cash + sum(sold.values()))
    after = {g: before[g] - sold.get(g, 0.) + bought.get(g, 0.) for g in groups}
    target = {}
    for g, members in groups.items():
        proportions = weights if sum(weights[s] for s in members) > 0 else base
        total = sum(proportions[sid] for sid in members)
        for sid in members:
            share = proportions[sid] / total if total > 0 else 1. / len(members)
            target[sid] = after[g] * share
    return target, {"reference": reference, "anchor": anchor, "before": before, "after": after, "net_proposals": dict(changes),
                    "sold": sold, "bought": bought, "cash_weight": max(0., 1 - sum(after.values()))}


def target_allocation(config, data, signal, previous):
    base = {s["id"]: s["weight"] for s in config["sleeves"]}
    prior = previous or base
    if config["allocation"]["method"] == "risk_parity":
        weights, info = allocate_risk(config, data.prices, signal, prior)
    else:
        weights, info = legacy_allocation(config, data.macro, signal, prior) if "regions" not in config["macro"] else (
            base, {"state": "固定配置", "inputs": [], "missing": False})
    macro, vol = config["macro"], config.get("volatility", {})
    regions = list(regional_rules(macro)) if macro.get("enabled") else []
    if not regions and not vol.get("enabled"):
        return weights, info
    net, reports = defaultdict(float), {}
    for region, settings in regions:
        changes, detail = proposals(settings, data.macro, signal)
        reports[region] = {**detail, "changes": changes}
        for group, value in changes.items():
            net[group] += value
    signals, protected = [], defaultdict(float)
    if vol.get("enabled"):
        for spec in vol["signals"]:
            state = volatility_state(spec, data.macro, signal)
            signals.append(state)
            if state.get("defensive"):
                for group in spec["groups"]:
                    protected[group] = max(protected[group], spec["reduce"])
        for group, reduction in protected.items():
            net[group] = min(net[group], -reduction)
    # 宏观留出的现金等明确买入意向再使用。同一信号反复出现也不会累计偏离。
    anchor = base if regions and info.get("status") == "fallback_hold" else weights
    weights, adjustment = bounded_weights(base, anchor, net, config["allocation_limits"], previous if regions else None)
    available = any(not row["missing"] for row in [*reports.values(), *signals])
    info = {**info, "overlay": {"regions": reports, "volatility": signals, **adjustment},
            "missing": info["missing"] or any(row["missing"] for row in [*reports.values(), *signals])}
    if info.get("method") != "risk_parity":
        info.update(state="分地区动态配置" if available else "动态配置缺少指标，沿用已有目标比例", fallback=not available)
    return weights, info
