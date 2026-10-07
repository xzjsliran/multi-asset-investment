"""账户、计划请求和用户确认记录的验证。"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str).encode()).hexdigest()


def number(value, label, minimum=0.):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label + "须为有限非负数；所有资产只做多，现金不透支。")
    return float(value)


def timestamp(value, label):
    try:
        stamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(label + "须为带时区的时间。") from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(label + "须为带时区的时间，例如2025-06-30T16:00:00+08:00。")
    return stamp


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("币种须使用CNY、USD等三字母代码。")
    return value


def validate_inputs(config, account, request):
    from strategykit.config import validate
    from strategykit.decision import nonnegative
    c, a, r = validate(config), copy.deepcopy(account), copy.deepcopy(request)
    if a.get("schema_version") != 1 or r.get("schema_version") != 1:
        raise ValueError("账户与计划请求schema_version须为1。")
    if r.get("mode") not in {"live", "demo"}:
        raise ValueError("mode须为live或demo。")
    if a.get("source") not in {"actual", "simulation"}:
        raise ValueError("账户source须为actual或simulation；计划推演结果不能直接视为实际持仓。")
    if r["mode"] == "live" and a["source"] != "actual":
        raise ValueError("实际计划必须使用用户确认的实际持仓，不能沿用模拟账户。")
    if r["mode"] == "demo" and a["source"] != "simulation":
        raise ValueError("演示模式须使用明确标记的模拟账户；实际账户请先完成用户确认。")
    if a.get("position_policy", "long_only") != "long_only":
        raise ValueError("账户须为只做多账户。")
    asof, held_at = timestamp(r.get("as_of"), "数据截止时间"), timestamp(a.get("as_of"), "持仓时间")
    if held_at > asof:
        raise ValueError("持仓时间不能晚于本次数据截止时间。")
    if a.get("open_orders"):
        raise ValueError("账户存在未完成委托，请先与用户核对其状态并更新可用资金和持仓。")
    ids = {s["id"] for s in c["sleeves"]}
    if not isinstance(a.get("positions"), list) or not isinstance(a.get("cash"), list):
        raise ValueError("账户需要positions与cash列表；空仓使用空positions。")
    keys = set()
    for p in a["positions"]:
        if p.get("sleeve") not in ids or not isinstance(p.get("code"), str) or not p["code"]:
            raise ValueError("每条持仓须提供代码和所属资产部分；归属不明先向用户确认。")
        key = (p["sleeve"], p["code"])
        if key in keys:
            raise ValueError("同一资产部分中证券持仓重复，请先合并。")
        keys.add(key)
        q = number(p.get("quantity"), "持仓数量")
        available = number(p.get("available_quantity"), "可卖数量")
        if available > q + 1e-10:
            raise ValueError("可卖数量不能超过持仓数量。")
    keys = set()
    for row in a["cash"]:
        sid, cur = row.get("sleeve"), currency(row.get("currency"))
        if sid not in ids | {"cash"} or (sid, cur) in keys:
            raise ValueError("现金归属无效或重复。未分配现金使用sleeve=cash。")
        keys.add((sid, cur))
        number(row.get("amount"), "可用现金")
    r.setdefault("initial_allocation", False)
    if not isinstance(r["initial_allocation"], bool):
        raise ValueError("initial_allocation须为布尔值。")
    for key, default in [("max_quote_age_days", 7), ("max_signal_gap_sessions", 5), ("minimum_trade_value_cny", 0), ("cash_buffer_cny", 0)]:
        r.setdefault(key, default)
        number(r[key], key)
    state = a.setdefault("strategy_state", {})
    if state.get("strategy_digest") and state["strategy_digest"] != digest(c) and not r["initial_allocation"]:
        raise ValueError("持仓关联的策略已变更，请确认新策略后明确是否重新配置。")
    previous = state.get("previous_allocation")
    if previous is not None:
        if set(previous) != ids:
            raise ValueError("上期配置须列出全部资产部分。")
        nonnegative(previous.values(), "上期配置", 1.)
    elif c.get("macro", {}).get("enabled") and c["macro"].get("missing") == "hold" and a["positions"] and not r["initial_allocation"]:
        raise ValueError("宏观缺指标时沿用上期配置的策略，需要确认previous_allocation。")
    elif c.get("allocation", {}).get("method") == "risk_parity" and c["allocation"].get("fallback") == "hold" and a["positions"] and not r["initial_allocation"]:
        raise ValueError("风险平价沿用上期配置时，需要确认previous_allocation。")
    for sid, weights in state.get("inner_weights", {}).items():
        if sid not in ids:
            raise ValueError("上期部分内权重含未知资产部分。")
        nonnegative(weights.values(), "上期部分内权重", 1.)
    for day in [state.get("last_allocation_execution"), *state.get("last_execution_by_sleeve", {}).values()]:
        if day and pd.Timestamp(day) > held_at.tz_convert("Asia/Shanghai").tz_localize(None).normalize():
            raise ValueError("已执行调仓日期不能晚于账户快照。")
    return c, a, r


def review(config, account, request):
    c, a, r = validate_inputs(config, account, request)
    return {"schema_version": 1, "status": "awaiting_user_confirmation" if r["mode"] == "live" else "simulation",
            "context_digest": digest({"strategy": c, "account": a, "request": r}),
            "strategy": {"name": c["name"], "allocation_rebalance": c["allocation_rebalance"],
                         "sleeves": c["sleeves"], "macro": c["macro"], "position_policy": "long_only"},
            "account": a, "request": r,
            "confirmation_template": {"confirmed": False, "context_digest": digest({"strategy": c, "account": a, "request": r}),
                                      "confirmed_at": None, "user_statement": None}}


def require_confirmation(c, a, r, confirmation):
    expected = review(c, a, r)["context_digest"]
    if r["mode"] == "demo":
        return {"status": "simulation", "context_digest": expected, "actual_account_confirmed": False}
    if not confirmation or confirmation.get("confirmed") is not True:
        raise ValueError("请先向用户确认当前策略与实际持仓，并记录用户回复后再生成实际调仓计划。")
    if confirmation.get("context_digest") != expected:
        raise ValueError("确认记录与当前策略、持仓或请求不一致，请重新确认变更内容。")
    timestamp(confirmation.get("confirmed_at"), "确认时间")
    if not isinstance(confirmation.get("user_statement"), str) or not confirmation["user_statement"].strip():
        raise ValueError("确认记录需要用户实际回复，不能由agent替用户填写同意。")
    return {"status": "user_confirmed", "context_digest": expected, "actual_account_confirmed": True,
            "confirmed_at": confirmation["confirmed_at"], "user_statement": confirmation["user_statement"]}
