"""组合调仓计划：决策、参考价估算、现金约束及账户层净额合并。"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from datetime import datetime, timezone
import math

import pandas as pd

from strategykit.decision import decide, event_on
from .inputs import digest, require_confirmation, timestamp, validate_inputs
from .quotes import validate_quotes


def choose_event(c, a, r, calendar):
    dates = pd.DatetimeIndex(calendar).normalize().unique().sort_values()
    cutoff = timestamp(r["as_of"], "数据截止时间").tz_convert("Asia/Shanghai")
    today = cutoff.tz_localize(None).normalize()
    completed = dates[(dates + pd.Timedelta(hours=16)).tz_localize("Asia/Shanghai") <= cutoff]
    if completed.empty:
        raise ValueError("日历缺少截止时间前的已完成交易日。")
    earliest = today + pd.Timedelta(days=1) if cutoff.hour >= 16 else today
    requested = pd.Timestamp(r["execution_date"]).normalize() if r.get("execution_date") else None
    if requested is not None and requested < today:
        raise ValueError("拟执行日期早于计划时间；历史复核请同时使用当时的as_of。")
    candidates = dates[dates >= max(earliest, requested) if requested is not None else dates >= earliest]
    initial = r["initial_allocation"]
    state = a["strategy_state"]
    for day in candidates:
        e = event_on(c, dates, day, initial)
        if not initial:
            if state.get("last_allocation_execution") and pd.Timestamp(state["last_allocation_execution"]) >= day:
                e["is_outer"] = False
            last = state.get("last_execution_by_sleeve", {})
            e["new_ids"] = [sid for sid in e["new_ids"] if not last.get(sid) or pd.Timestamp(last[sid]) < day]
        if requested is not None or e["is_outer"] or e["new_ids"]:
            e["scheduled_signal_date"] = e["signal_date"]
            e["signal_date"] = min(e["signal_date"], completed[-1])
            e["preliminary"] = bool(e["signal_date"] < e["scheduled_signal_date"] and (e["is_outer"] or e["new_ids"]))
            e["requested_execution_date"] = str(requested.date()) if requested is not None else None
            return e
    raise ValueError("交易日历未覆盖下一次调仓，请通过数据kit补充已核实的未来交易日历。")


def floor_lot(quantity, lot):
    return max(0., math.floor(quantity / lot + 1e-10) * lot)


def make_plan(config, data, account, request, quote_document, confirmation=None):
    c, a, r = validate_inputs(config, account, request)
    approval = require_confirmation(c, a, r, confirmation)
    quotes, rates, fx_times = validate_quotes(quote_document, r["as_of"], r["max_quote_age_days"])
    ids = [s["id"] for s in c["sleeves"]] + ["cash"]
    positions = {(p["sleeve"], p["code"]): copy.deepcopy(p) for p in a["positions"]}
    cash = defaultdict(float)
    for row in a["cash"]:
        if row["currency"] not in rates:
            raise ValueError("现金缺少已知汇率："+row["currency"])
        cash[(row["sleeve"], row["currency"])] += row["amount"]
    for code in {p["code"] for p in a["positions"]}:
        if code not in quotes:
            raise ValueError("已有持仓缺少原始参考价："+code)
    current = {sid: {} for sid in ids}
    for (sid, code), p in positions.items():
        current[sid][code] = p["quantity"] * quotes[code]["price_cny"]
    current_cash = {sid: sum(v*rates[cur] for (s, cur), v in cash.items() if s == sid) for sid in ids}
    equity = sum(sum(v.values()) for v in current.values()) + sum(current_cash.values())
    if equity <= 0:
        raise ValueError("账户总资产须大于零。")
    if not positions and not r["initial_allocation"]:
        raise ValueError("空仓首次配置请在确认请求中设置initial_allocation=true。")
    event = choose_event(c, a, r, data.calendar)
    labels = {asset["code"]: asset.get("name", asset["code"]) for s in c["sleeves"] for asset in s.get("assets", [])}
    if not data.features.empty and {"snapshot_date", "code", "historical_name"} <= set(data.features):
        known_names = data.features.loc[data.features["snapshot_date"] <= event["signal_date"]].sort_values("snapshot_date").dropna(subset=["historical_name"])
        labels.update(known_names.drop_duplicates("code", keep="last").set_index("code")["historical_name"].to_dict())
    for code, quote in quotes.items():
        quote["name"] = labels.get(code, quote.get("name", code))
    state = a["strategy_state"]
    inner = copy.deepcopy(state.get("inner_weights", {}))
    for sid in ids[:-1]:
        total = sum(current[sid].values()) + current_cash[sid]
        if total > 1e-8 and sid not in inner:
            inner[sid] = {code: v/total for code, v in current[sid].items()}
    previous = state.get("previous_allocation", {s["id"]: s["weight"] for s in c["sleeves"]})
    decision = decide(c, data, event["execution_date"], event["signal_date"], current, current_cash,
                      inner, previous, event["new_ids"], event["is_outer"])
    target = {sid: {**current[sid], "__cash__": current_cash[sid]} for sid in ids}
    target.update(decision["desired"])
    # 入选证券必须有截至信号日的新近研究行情；订单价仍来自独立原始报价。
    for chosen in decision["selections"]:
        for code in chosen["code"]:
            series = data.prices.loc[:event["signal_date"], code].dropna()
            if series.empty:
                raise ValueError("入选证券缺少信号行情："+code)
            gap = ((data.calendar > series.index[-1]) & (data.calendar <= event["signal_date"])).sum()
            if gap > r["max_signal_gap_sessions"]:
                raise ValueError("入选证券的研究行情过旧，请补取："+code)
    needed = {code for sid in decision["group"] for code, v in target[sid].items() if code != "__cash__" and v > 0}
    if needed - quotes.keys():
        raise ValueError("目标资产缺少原始参考价，请补取："+", ".join(sorted(needed-quotes.keys())))
    # 将账户现金按允许参与调仓的预算分组；大类事件共享现金，内部事件隔离。
    active = set(decision["group"])
    def bucket(sid, cur):
        return ("portfolio" if event["is_outer"] and sid in active else sid, cur)
    liquid = defaultdict(float)
    for (sid, cur), value in cash.items():
        liquid[bucket(sid, cur)] += value
    projected = copy.deepcopy(positions)
    legs, buy_requests, issues = [], [], []
    for (sid, code), held in positions.items():
        if held["quantity"] > 0 and quotes[code]["age_days"] > r["max_quote_age_days"]:
            issues.append({"sleeve": sid, "code": code, "reason": "持仓估值沿用较旧报价，需更新后复核计划"})
    def leg(sid, code, delta):
        if abs(delta) < 1e-10:
            return
        q = quotes[code]
        key = (sid, code)
        projected.setdefault(key, {"sleeve": sid, "code": code, "quantity": 0., "available_quantity": 0.})
        projected[key]["quantity"] += delta
        if delta < 0:
            projected[key]["available_quantity"] += delta
        liquid[bucket(sid, q["currency"])] -= delta*q["price"]
        legs.append({"sleeve": sid, "code": code, "signed_quantity": delta,
                     "amount_native": delta*q["price"], "amount_cny": delta*q["price_cny"], "currency": q["currency"]})
    for sid in decision["group"]:
        codes = sorted(set(current[sid]) | (set(target[sid]) - {"__cash__"}))
        for code in codes:
            if code not in quotes:  # 目标为零且原本也未持有时无需报价。
                continue
            q = quotes[code]
            held = positions.get((sid, code), {"quantity": 0., "available_quantity": 0.})
            change = target[sid].get(code, 0.) / q["price_cny"] - held["quantity"]
            if abs(change)*q["price_cny"] < max(1e-7, r["minimum_trade_value_cny"]):
                continue
            if not q["usable_for_orders"]:
                issues.append({"sleeve": sid, "code": code, "reason": "报价过旧或当前不可交易，保留持仓并等待更新"})
                continue
            if r["mode"] == "live" and q["currency"] != "CNY" and q.get("execution_session") != str(event["execution_date"].date()):
                issues.append({"sleeve": sid, "code": code, "reason": "该外币证券尚未核实拟执行日的当地交易日历，暂不生成买卖数量"})
                continue
            if change < 0:
                sell = floor_lot(min(-change, held["available_quantity"]), q["sell_lot"])
                leg(sid, code, -sell)
                if (-change-sell)*q["price_cny"] > .01:
                    issues.append({"sleeve": sid, "code": code, "reason": "卖出受可卖数量或最小交易单位限制"})
            else:
                buy = floor_lot(change, q["buy_lot"])
                if (change-buy)*q["price_cny"] > .01:
                    issues.append({"sleeve": sid, "code": code, "reason": "买入按最小交易单位取整，余款保留现金"})
                buy_requests.append({"sleeve": sid, "code": code, "quantity": buy, "bucket": bucket(sid, q["currency"])})
    # 各资金组按币种保留目标现金，外币不足不借款、不隐含兑换。
    reserve = {}
    active_owners = {"portfolio"} if event["is_outer"] else active
    inactive_cash = sum(v*rates[cur] for (sid, cur), v in liquid.items() if sid not in active_owners)
    active_cash = sum(v*rates[cur] for (sid, cur), v in liquid.items() if sid in active_owners)
    base_reserve = sum(target[sid].get("__cash__", 0.) for sid in active)
    extra_reserve = max(0., r["cash_buffer_cny"]-inactive_cash-base_reserve)
    for b in set(row["bucket"] for row in buy_requests):
        owner, cur = b
        reserve_value = sum(target[sid].get("__cash__", 0.) for sid in active) if owner == "portfolio" else target[owner]["__cash__"]
        total_cash = sum(v*rates[cc] for (s, cc), v in liquid.items() if s == owner)
        if active_cash > 0:
            reserve_value += extra_reserve*total_cash/active_cash
        reserve[b] = liquid[b] * min(1., reserve_value/total_cash) if total_cash > 0 else 0.
    funding_gaps = []
    for b in sorted({row["bucket"] for row in buy_requests}):
        rows = [row for row in buy_requests if row["bucket"] == b]
        wanted = sum(row["quantity"]*quotes[row["code"]]["price"] for row in rows)
        budget = max(0., liquid[b] - reserve[b])
        if wanted > budget + .01:
            funding_gaps.append({"scope": b[0], "currency": b[1], "required_buy_cash_native": wanted,
                                 "spendable_cash_native": budget, "shortfall_native": wanted-budget})
        ratio = min(1., budget/wanted) if wanted > 0 else 0.
        for row in rows:
            q = quotes[row["code"]]
            quantity = floor_lot(row["quantity"]*ratio, q["buy_lot"])
            leg(row["sleeve"], row["code"], quantity)
            if quantity < row["quantity"]-1e-8:
                issues.append({"sleeve": row["sleeve"], "code": row["code"], "reason": f"{q['currency']}可用现金或保留现金约束，买入数量缩减"})
    if min(liquid.values(), default=0.) < -1e-7 or any(p["quantity"] < -1e-8 for p in projected.values()):
        raise AssertionError("计划出现负持仓或现金透支。")
    # 大类事件后，将现金按目标归属分配；整手余款归未分配现金。
    post_cash = []
    if event["is_outer"]:
        total = sum(v*rates[cur] for (sid, cur), v in liquid.items() if sid == "portfolio")
        reserved = sum(target[sid].get("__cash__", 0.) for sid in active if sid != "cash")
        scale = min(1., total/reserved) if reserved > 0 else 0.
        for (owner, cur), value in liquid.items():
            if owner != "portfolio":
                post_cash.append({"sleeve": owner, "currency": cur, "amount": max(0., value)})
                continue
            assigned = 0.
            for sid in ids[:-1]:
                amount = value*target[sid].get("__cash__", 0.)*scale/total if total > 0 else 0.
                post_cash.append({"sleeve": sid, "currency": cur, "amount": amount})
                assigned += amount
            post_cash.append({"sleeve": "cash", "currency": cur, "amount": max(0., value-assigned)})
    else:
        post_cash = [{"sleeve": sid, "currency": cur, "amount": max(0., v)} for (sid, cur), v in liquid.items()]
    net = defaultdict(float)
    for row in legs:
        net[row["code"]] += row["signed_quantity"]
    orders = []
    for code, delta in sorted(net.items()):
        if abs(delta) <= 1e-9:
            continue
        q = quotes[code]
        orders.append({"code": code, "name": q.get("name", code), "side": "buy" if delta > 0 else "sell",
                       "quantity": abs(delta), "signed_quantity": delta, "reference_price": q["price"],
                       "price_available_at": q["available_at"], "currency": q["currency"],
                       "amount_native": abs(delta)*q["price"], "amount_cny": abs(delta)*q["price_cny"],
                       "estimated_cash_change_native": -delta*q["price"], "estimated_cost": 0.})
    orders.sort(key=lambda x: (x["side"] != "sell", x["code"]))
    cash_after = sum(row["amount"]*rates[row["currency"]] for row in post_cash)
    if cash_after < r["cash_buffer_cny"]-.01:
        issues.append({"sleeve": "cash", "code": "CASH", "reason": "按当前策略目标产生的卖出回款不足以满足额外保留现金要求，需要调整约束或目标配置"})
    stock_after = sum(p["quantity"]*quotes[code]["price_cny"] for (_, code), p in projected.items())
    error = stock_after+cash_after-equity
    if abs(error) > max(1., equity)*1e-9:
        raise AssertionError("参考价下资金不守恒。")
    target_rows, holding_rows = [], []
    for sid in ids:
        for code, value in target[sid].items():
            target_rows.append({"sleeve": sid, "code": code, "target_value_cny": value, "target_weight": value/equity})
        for code in sorted(set(current[sid]) | {cc for ss, cc in projected if ss == sid} | (set(target[sid]) - {"__cash__"})):
            if code not in quotes:
                continue
            before = positions.get((sid, code), {}).get("quantity", 0.)
            after = projected.get((sid, code), {}).get("quantity", 0.)
            px = quotes[code]["price_cny"]
            holding_rows.append({"sleeve": sid, "code": code, "current_quantity": before, "projected_quantity": after,
                                 "current_available_quantity": positions.get((sid, code), {}).get("available_quantity", 0.),
                                 "current_value_cny": before*px, "projected_value_cny": after*px,
                                 "current_weight": before*px/equity, "target_weight": target[sid].get(code, 0.)/equity,
                                 "projected_weight": after*px/equity,
                                 "deviation_cny": after*px-target[sid].get(code, 0.)})
    next_dates = []
    for day in data.calendar[data.calendar > event["execution_date"]]:
        e = event_on(c, data.calendar, day)
        if e["is_outer"] or e["new_ids"]:
            next_dates.append({"execution_date": str(day.date()), "signal_date": str(e["signal_date"].date()),
                               "outer": e["is_outer"], "sleeves": e["new_ids"]})
            if len(next_dates) == 6:
                break
    action = "scheduled_rebalance" if active else "hold"
    if r["initial_allocation"]:
        action = "initial_allocation"
    status = "preliminary" if event["preliminary"] else ("constrained" if issues else "calculated")
    result = {
        "schema_version": 1, "producer": "quant-rebalance-kit", "document_type": "rebalance_plan",
        "generated_at": datetime.now(timezone.utc).isoformat(), "mode": r["mode"], "status": status,
        "action": action, "position_policy": "long_only", "base_currency": "CNY", "strategy_name": c["name"],
        "strategy_digest": digest(c), "confirmation": approval,
        "timing": {"as_of": r["as_of"], "holdings_as_of": a["as_of"],
                   "signal_date": str(event["signal_date"].date()), "scheduled_signal_date": str(event["scheduled_signal_date"].date()),
                   "execution_date": str(event["execution_date"].date()), "requested_execution_date": event["requested_execution_date"],
                   "requires_signal_refresh": event["preliminary"]},
        "event": {"is_outer": event["is_outer"], "selection_sleeves": event["new_ids"], "participating_sleeves": decision["group"]},
        "account": {"equity_cny": equity, "cash_before_cny": sum(current_cash.values()), "cash_after_cny": cash_after},
        "targets": target_rows, "holdings": holding_rows, "orders": orders, "allocation_legs": legs,
        "cash_before": a["cash"], "cash_after": post_cash, "quotes": list(quotes.values()), "fx": rates, "fx_available_at": fx_times,
        "regime": decision["regime"], "optimization": decision["optimization"],
        "selections": [row for chosen in decision["selections"] for row in json.loads(chosen.to_json(orient="records", date_format="iso"))],
        "issues": issues, "funding_gaps": funding_gaps, "next_events": next_dates,
        "checks": {"position_policy": "long_only", "negative_positions": 0, "negative_cash": 0,
                   "fund_conservation_error_cny": error, "target_weight_sum": sum(x["target_weight"] for x in target_rows),
                   "orders_netted_by_security": True, "cost_rate": 0, "raw_prices_for_quantities": True},
        "state_if_fully_executed": {"status": "projection_not_execution", "strategy_digest": digest(c),
                    "previous_allocation": decision["allocation"], "inner_weights": decision["inner"]},
        "notes": ["全部资产只做多；持仓、目标权重和现金均不得为负。卖出仅限已有可卖持仓。",
                  "买卖数量按列示原始参考价和交易单位估算；执行前更新报价、现金与可卖数量。",
                  "交易费用暂按零计算。各币种独立检查资金，外币不足时减少买入并列示缺口，不隐含兑换。",
                  "调仓日历沿用中国交易日决策口径；实际外币证券另核实当地交易日。",
                  "本次生成计划不更新实际持仓。执行后须按真实成交重新核对账户。"]}
    if event["preliminary"]:
        result["notes"].append("尚未到计划信号日；本预案使用当前已知数据，信号日需重新计算。")
    if r["mode"] == "demo":
        result["notes"].append("本报告使用模拟账户进行历史流程验证，日期及资产均为该历史时点的研究输入。")
    if not next_dates:
        result["notes"].append("当前交易日历未覆盖后续调仓日期，需补充日历后更新。")
    return result
