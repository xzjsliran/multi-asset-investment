"""策略参数与数据需求；权重都使用小数，0.12 表示 12%。"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pandas as pd

from .allocation import indicator_plan, validate_condition, validate_overlays
from .costs import ASSET_KINDS, normalize_costs, rate
from .equity_selection import DEFAULT_POOL, METHOD, pool_request

ROOT = Path(__file__).resolve().parents[2]
FREQUENCIES = {"weekly": "W-FRI", "monthly": "M", "quarterly": "Q-DEC"}


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str,
                                     allow_nan=False), encoding="utf-8")


def read_config(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def validate(config):
    c = copy.deepcopy(config)
    if c.get("schema_version") != 1:
        raise ValueError("配置 schema_version 应为 1。")
    if c.get("position_policy", "long_only") != "long_only" or c.get("allow_short", False) is not False:
        raise ValueError("所有资产只做多，position_policy须为long_only，不允许空头。")
    c["position_policy"] = "long_only"
    if c.get("leverage", 1) != 1:
        raise ValueError("组合采用自有资金，leverage须为1。")
    for k in ["start", "end"]:
        c[k] = pd.Timestamp(c[k]).strftime("%Y-%m-%d")
    if c["start"] > c["end"]:
        raise ValueError("开始时间晚于结束时间。")
    if c.get("base_currency", "CNY") != "CNY":
        raise ValueError("当前回测使用人民币记账；外币资产先按已知汇率换算。")
    c.setdefault("initial_capital", 1_000_000.)
    if not math.isfinite(float(c["initial_capital"])) or c["initial_capital"] <= 0:
        raise ValueError("初始资金应为正数。")
    legacy_rate = rate(c.get("cost_rate", 0), "cost_rate")
    if "transaction_costs" in c:
        if legacy_rate:
            raise ValueError("请使用 transaction_costs 或旧字段 cost_rate 之一。")
        c["transaction_costs"] = normalize_costs(c["transaction_costs"])
    if c.get("long_gap_policy", "error") not in {"error", "carry_and_flag"}:
        raise ValueError("长期估值缺口处理为 error 或 carry_and_flag。")
    if c.setdefault("allocation_rebalance", "monthly") not in FREQUENCIES:
        raise ValueError("总配置调仓支持 weekly、monthly、quarterly。")
    if c.get("execution_price", "close") not in {"open", "close"}:
        raise ValueError("execution_price 使用 open 或 close。")
    sleeves = c.get("sleeves", [])
    ids = [s["id"] for s in sleeves]
    if not sleeves or len(ids) != len(set(ids)) or "cash" in ids:
        raise ValueError("资产部分的 id 需非空且不重复；cash 留给未投资现金。")
    for s in sleeves:
        if not math.isfinite(float(s["weight"])) or not 0 <= s["weight"] <= 1:
            raise ValueError("各资产部分权重须在 0—1。")
        s.setdefault("rebalance", c["allocation_rebalance"])
        if s["rebalance"] not in FREQUENCIES:
            raise ValueError("调仓频率只能是 weekly、monthly、quarterly。")
        sel = s.setdefault("selection", {"method": "fixed"})
        if sel["method"] not in {"fixed", "momentum", "quality_value", "fundamental", METHOD}:
            raise ValueError("未知选股方法：" + sel["method"])
        if sel["method"] == "fundamental":
            for cond in sel.get("filters", []):
                if cond.get("op") not in {">", ">=", "<", "<=", "=="}:
                    raise ValueError("选股条件比较符不支持。")
        if sel["method"] != "fixed" and int(sel.get("top_n", 5)) < 1:
            raise ValueError("top_n 至少为 1。")
        if sel["method"] in {"fixed", "momentum"} and not s.get("assets"):
            raise ValueError(s["id"] + " 需要明确资产代码。")
        for a in s.get("assets", []):
            if a.get("kind") not in ASSET_KINDS or not a.get("code"):
                raise ValueError("资产需填写 code 和受支持的 kind。")
            if a["kind"] in {"qdii_nav", "usd_deposit"} and c.get("execution_price") == "open":
                raise ValueError("场外基金净值和美元存款使用收盘研究口径。")
            if a["kind"] == "qdii_nav":
                lag = a.get("redemption_days")
                if type(lag) is not int or not 1 <= lag <= 30:
                    raise ValueError("场外基金需填写赎回回款等待的境内交易日数 redemption_days。")
            if not math.isfinite(float(a.get("weight", 1.))) or a.get("weight", 1.) < 0:
                raise ValueError("各证券的固定配权须为有限非负数；所有资产只做多。")
            if a.get("side", "long") != "long":
                raise ValueError("所有资产只做多，不接受空头证券配置。")
        w = s.setdefault("weighting", {"method": "equal"})
        if w["method"] not in {"equal", "fixed", "min_variance", "inverse_volatility"}:
            raise ValueError("未知配权方法：" + w["method"])
        if sel["method"] == METHOD:
            pool_request(sel.get("universe", DEFAULT_POOL))
            if w["method"] != "inverse_volatility":
                raise ValueError("质量价值红利低波使用倒数波动率配权。")
            for field, default, minimum in [("top_n", 20, 1), ("max_per_industry", 2, 1), ("buffer_rank", 0, 0)]:
                value = sel.get(field, default)
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{field} 必须为不小于 {minimum} 的整数。")
            size_weight = sel.get("small_cap_weight", 0.)
            if type(size_weight) not in (int, float) or not 0. <= size_weight <= .70:
                raise ValueError("small_cap_weight 须为 0 至 0.70 之间的数值。")
            w.setdefault("max_weight", .08)
            if not 0 < w["max_weight"] <= 1:
                raise ValueError("倒数波动率的单股权重上限须在 (0,1]。")
        elif w["method"] == "inverse_volatility":
            raise ValueError("倒数波动率配权需要质量价值红利低波的 120 日波动率截面。")
        if w["method"] == "min_variance":
            if not 0 < w.get("max_weight", 1) <= 1 or not 0 <= w.get("shrinkage", .1) <= 1:
                raise ValueError("最小方差上限需在 (0,1]，收缩系数需在 [0,1]。")
            if not 2 <= w.get("min_observations", 60) <= w.get("lookback", 126):
                raise ValueError("最少观测应介于 2 和协方差回看天数之间。")
    if sum(s["weight"] for s in sleeves) > 1 + 1e-9:
        raise ValueError("总权重不能超过 100%；不足的部分留现金。")
    macro = c.setdefault("macro", {"enabled": False})
    validate_overlays(c, ids)
    if macro.get("enabled") and "regions" not in macro:
        if macro.get("rebalance", "monthly") not in FREQUENCIES:
            raise ValueError("宏观调整频率无效。")
        if macro.get("missing", "base_weights") not in {"base_weights", "hold"}:
            raise ValueError("宏观缺失处理为 base_weights 或 hold。")
        if not macro.get("rules"):
            raise ValueError("开启动态配置后需要至少一条规则。")
        for rule in macro["rules"]:
            if set(rule["weights"]) != set(ids):
                raise ValueError("每种经济状态需列出所有资产部分的目标权重。")
            vals = list(rule["weights"].values())
            if any(not math.isfinite(x) or x < 0 for x in vals) or sum(vals) > 1 + 1e-9:
                raise ValueError("动态权重需非负、合计不超过 100%。")
            conditions = rule.get("all", []) + rule.get("any", [])
            if not conditions:
                raise ValueError("宏观规则需要 all 或 any 条件。")
            for cond in conditions:
                validate_condition(cond)
    allocation = c.setdefault("allocation", {"method": "fixed"})
    if allocation.get("method") not in {"fixed", "risk_parity"}:
        raise ValueError("大类配权支持 fixed、risk_parity。")
    if allocation["method"] == "risk_parity":
        if macro.get("enabled") and "regions" not in macro:
            raise ValueError("旧式整组宏观权重与风险平价互斥；叠加时使用地区宏观规则。")
        allowed = {"method", "proxies", "lookback", "min_observations", "shrinkage", "invested_weight", "fallback"}
        if set(allocation) - allowed:
            raise ValueError("存在未支持的风险平价参数。")
        if "proxies" not in allocation:
            allocation["proxies"] = {
                s["id"]: s.get("risk_proxy", s.get("assets", [])) for s in sleeves
                if not s.get("assets") or any(a["kind"] != "usd_deposit" for a in s["assets"])
            }
        proxies = allocation["proxies"]
        if not proxies or not set(proxies) <= set(ids):
            raise ValueError("风险平价需提供当前配置中参与配权的代理篮子 proxies。")
        selected_weight = sum(s["weight"] for s in sleeves if s["id"] in proxies)
        reserved = sum(s["weight"] for s in sleeves if s["id"] not in proxies)
        for k, v in {"lookback":126, "min_observations":60, "shrinkage":.1,
                     "invested_weight":selected_weight, "fallback":"base_weights"}.items():
            allocation.setdefault(k, v)
        if not all(isinstance(allocation[k], int) and not isinstance(allocation[k], bool) for k in ["lookback", "min_observations"]):
            raise ValueError("风险估计窗口须为整数交易日。")
        if not 2 <= allocation["min_observations"] <= allocation["lookback"]:
            raise ValueError("风险平价最少样本应介于2和回看天数之间。")
        if not 0 < allocation["shrinkage"] <= 1 or not 0 < allocation["invested_weight"] <= 1 - reserved + 1e-9:
            raise ValueError("风险平价投资比例须为正，且能够保留其余资产的基础预算。")
        if allocation["fallback"] not in {"base_weights", "hold", "error"}:
            raise ValueError("风险平价回退为 base_weights、hold 或 error。")
        for basket in allocation["proxies"].values():
            if not isinstance(basket, list) or not basket:
                raise ValueError("每个风险代理篮子须为非空资产列表。")
            for a in basket:
                if a.get("kind") not in ASSET_KINDS or not a.get("code"):
                    raise ValueError("风险代理需明确code和kind。")
                if set(a) & {"from", "until"} or a.get("side", "long") != "long":
                    raise ValueError("风险代理采用事先固定的只做多篮子；分段研究请分别指定代理。")
                if not math.isfinite(float(a.get("weight", 1))) or a.get("weight", 1) < 0:
                    raise ValueError("风险代理权重须为有限非负数。")
            if sum(a.get("weight", 1) for a in basket) <= 0 or len({a["code"] for a in basket}) != len(basket):
                raise ValueError("风险代理权重合计须为正，篮子代码不能重复。")
    return c


def allocation_frequencies(config):
    frequencies = {config["allocation_rebalance"]}
    macro = config.get("macro", {})
    if macro.get("enabled") and "regions" not in macro:
        frequencies.add(macro.get("rebalance", "monthly"))
    return sorted(frequencies)


def schedule(calendar, start, end, frequency):
    """返回执行日 -> 信号日；首日建仓也只使用此前一个交易日。"""
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize().unique().sort_values()
    inside = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
    if inside.empty:
        raise ValueError("请求区间没有交易日。")
    first = dates.get_indexer([inside[0]])[0]
    if first == 0:
        raise ValueError("需要研究开始前的行情和至少一个交易日，用于初次建仓信号。")
    events = {inside[0]: dates[first - 1]}
    periods = dates.to_period(FREQUENCIES[frequency])
    for i in range(1, len(dates)):
        if dates[i] in inside and periods[i] != periods[i - 1]:
            events[dates[i]] = dates[i - 1]
    return events


def all_assets(c):
    found = {}
    for s in c["sleeves"]:
        for a in s.get("assets", []):
            clean = {k: v for k, v in a.items() if k not in {"from", "until", "weight"}}
            if a["code"] in found and found[a["code"]]["kind"] != a["kind"]:
                raise ValueError("同一代码的资产类型冲突。")
            found[a["code"]] = clean
    if c.get("benchmark"):
        found[c["benchmark"]["code"]] = c["benchmark"]
    for basket in c.get("allocation", {}).get("proxies", {}).values():
        for a in basket:
            if a["code"] in found and found[a["code"]]["kind"] != a["kind"]:
                raise ValueError("风险代理与持仓资产的代码类型冲突。")
            found.setdefault(a["code"], {k: v for k, v in a.items() if k != "weight"})
    return list(found.values())


def data_plan(c):
    stocks = [s for s in c["sleeves"] if s["selection"]["method"] in {"quality_value", "fundamental", METHOD}]
    indicators = indicator_plan(c)
    windows = [s["weighting"].get("lookback", 0) for s in c["sleeves"]]
    windows += [s["selection"].get("lookback", 0) for s in c["sleeves"]]
    windows += [c.get("allocation", {}).get("lookback", 0)]
    if c.get("volatility", {}).get("enabled"):
        windows += [s["slow"] for s in c["volatility"]["signals"]]
    days = max(400, int(max(windows, default=0) * 1.8) + 30)
    return {"start": c["start"], "end": c["end"], "lookback_days": days,
            "assets": all_assets(c), "macro": indicators,
            "fundamental_selection": bool(stocks),
            "fundamental_frequencies": sorted({s["rebalance"] for s in stocks}),
            "equity_factor_pools": [{"sleeve": s["id"], "frequency": s["rebalance"],
                                     **pool_request(s["selection"].get("universe", DEFAULT_POOL))}
                                    for s in stocks if s["selection"]["method"] == METHOD],
            "source_notes": ["境内行情沿用第一部分的数据模块。",
                             "财务选股需要历史估值、财报公告日、历史行业和名称；通过本地 Tushare 或已有 research 原始快照获取。",
                             "场外 QDII 按已公布复权净值建模；美元活期按生效日牌价计息后换算人民币。",
                             "美国 ETF 用复权价格，按已知 USD/CNY 换算人民币；每条美股报价从下一次中国决策时点起使用。"]}
