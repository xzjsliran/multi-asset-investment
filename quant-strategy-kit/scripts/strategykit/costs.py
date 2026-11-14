"""回测和调仓计划共用的按成交金额计费规则。"""
from datetime import date
import math


ASSET_KINDS = {"stock", "etf", "us_etf"}
BUY_RATES = ("commission_rate", "slippage_rate")
COST_FIELDS = ("commission", "slippage", "stamp_duty")


def rate(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 1:
        raise ValueError(f"{field}须为非负且小于1的小数，例如0.00015表示万分之1.5。")
    return float(value)


def normalize_costs(value):
    fields = {*BUY_RATES, "stamp_duty_rate", "stamp_duty_schedule", "by_kind"}
    if not isinstance(value, dict) or not value or set(value) - fields:
        raise ValueError("transaction_costs 须列明佣金、滑点或印花税设置。显式零成本至少填写一个费率为0。")
    costs = {key: rate(value.get(key, 0), key) for key in (*BUY_RATES, "stamp_duty_rate")}
    overrides = value.get("by_kind", {})
    if not isinstance(overrides, dict) or set(overrides) - ASSET_KINDS:
        raise ValueError("by_kind 仅支持 stock、etf、us_etf。")
    for kind, rates in overrides.items():
        if not isinstance(rates, dict) or set(rates) - set(BUY_RATES):
            raise ValueError(f"{kind} 的费用覆盖仅支持 commission_rate 和 slippage_rate。")
    costs["by_kind"] = {kind: {key: rate(rates[key], key) for key in rates}
                        for kind, rates in overrides.items()}
    schedule = value.get("stamp_duty_schedule", [])
    if not isinstance(schedule, list):
        raise ValueError("stamp_duty_schedule 须为按生效日列出的清单。")
    dates = {}
    for item in schedule:
        if not isinstance(item, dict) or set(item) != {"from", "rate"}:
            raise ValueError("每条印花税变更须填写 from 和 rate。")
        day = date.fromisoformat(str(item["from"])).isoformat()
        if day in dates:
            raise ValueError("印花税生效日重复。")
        dates[day] = rate(item["rate"], "stamp_duty_schedule.rate")
    costs["stamp_duty_schedule"] = [{"from": day, "rate": dates[day]} for day in sorted(dates)]
    max_stamp = max(costs["stamp_duty_rate"], *dates.values(), 0)
    for kind in ASSET_KINDS:
        effective = {**costs, **costs["by_kind"].get(kind, {})}
        if sum(effective[key] for key in BUY_RATES) + (max_stamp if kind == "stock" else 0) >= 1:
            raise ValueError("单边合计费率须小于100%，以保留可用的卖出回款。")
    return costs


def costs_for(config):
    return normalize_costs(config.get("transaction_costs", {"commission_rate": config.get("cost_rate", 0)}))


def charges_fees(costs):
    return (any(costs[key] for key in (*BUY_RATES, "stamp_duty_rate"))
            or any(item["rate"] for item in costs["stamp_duty_schedule"])
            or any(value for rates in costs["by_kind"].values() for value in rates.values()))


def asset_kinds(config, features):
    """配置中的证券和历史财务选出的 A 股都有确定的品种。"""
    kinds = dict.fromkeys(features["code"].unique(), "stock") if "code" in features else {}
    kinds.update({asset["code"]: asset["kind"] for sleeve in config["sleeves"] for asset in sleeve.get("assets", [])})
    return kinds


def trade_cost(costs, amount, kind, day):
    """正数为买入、负数为卖出。费用与成交额同币种，滑点作为现金成本。"""
    stamp_rate = costs["stamp_duty_rate"]
    if amount < 0:
        for item in costs["stamp_duty_schedule"]:
            if item["from"] > day:
                break
            stamp_rate = item["rate"]
    if amount and kind is None and (costs["by_kind"] or (amount < 0 and stamp_rate)):
        raise ValueError("分品种费用需要确认证券类型（stock、etf 或 us_etf）。")
    rates = costs["by_kind"].get(kind, {})
    return {
        "commission": abs(amount) * rates.get("commission_rate", costs["commission_rate"]),
        "slippage": abs(amount) * rates.get("slippage_rate", costs["slippage_rate"]),
        "stamp_duty": abs(amount) * stamp_rate if amount < 0 and kind == "stock" else 0.,
    }


def cost_confirmation_needed(config):
    """旧预设的隐式零费率需要在交互流程中确认。"""
    return "transaction_costs" not in config and not config.get("cost_rate")


def cost_question(mode="simulation"):
    choices = {"simulation_reference": {"commission_rate": 0.00015, "slippage_rate": 0.00015},
               "higher_simulation_cost": {"commission_rate": 0.0003, "slippage_rate": 0.0007},
               "zero_cost": {"commission_rate": 0, "slippage_rate": 0, "stamp_duty_rate": 0}}
    message = ("请确认本次费用。可选参考估计、较高成本情景、明确零成本，或使用自填费率。"
               if mode != "live" else
               "请确认券商费率、滑点估计和适用税费。可用交割单核对；若只能估算，请明确确认假设。")
    return {
        "status": "needs_input", "next_action": "confirm_transaction_costs",
        "missing": ["transaction_costs"], "message": message,
        "reference": "交易成本说明.md",
        "simulation_examples": choices,
    }
