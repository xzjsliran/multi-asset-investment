"""多资产日线回测；每个资产部分有自己的持仓与现金。"""
from __future__ import annotations

import copy
from collections import defaultdict

import numpy as np
import pandas as pd

from .config import schedule, validate
from .costs import COST_FIELDS, asset_kinds, charges_fees, costs_for, trade_cost
from .decision import decide


def backtest(config, data):
    config = validate(config)
    sleeves = {s["id"]: s for s in config["sleeves"]}
    ids = list(sleeves) + ["cash"]
    books = {sid: {"units": {}, "cash": 0.} for sid in ids}
    books["cash"]["cash"] = float(config["initial_capital"])
    capital = float(config["initial_capital"])
    costs, kinds = costs_for(config), asset_kinds(config, data.features)
    fee_enabled = charges_fees(costs)
    histories, weight_rows, trades, selections, audits, regimes, blocks, targets = [], [], [], [], [], [], [], []
    valuation_gaps = []
    prices = data.prices
    marked = prices.ffill()  # 只用于已有持仓估值；选股及协方差继续用未填充价格。
    last_seen = prices.notna().apply(lambda x: pd.Series(np.where(x, np.arange(len(x)), np.nan), index=x.index)).ffill()
    schedules = {sid: schedule(data.calendar, config["start"], config["end"], s["rebalance"]) for sid, s in sleeves.items()}
    outer = schedule(data.calendar, config["start"], config["end"], config["allocation_rebalance"])
    macro = config.get("macro", {})
    if macro.get("enabled"):
        outer.update(schedule(data.calendar, config["start"], config["end"], macro.get("rebalance", "monthly")))
    days = data.calendar[(data.calendar >= pd.Timestamp(config["start"])) & (data.calendar <= pd.Timestamp(config["end"]))]
    pending = {}
    inner = {}
    previous_alloc = {sid: s["weight"] for sid, s in sleeves.items()}
    previous_nav = capital
    previous_values = {sid: 0. for sid in ids}
    previous_values["cash"] = capital
    contributions = {sid: 0. for sid in ids}
    previous_px = None

    def values(sid, px):
        result = {}
        for code, units in books[sid]["units"].items():
            if abs(units) < 1e-12:
                continue
            if code not in px or not np.isfinite(px[code]):
                raise ValueError("已有持仓无法估值：" + code)
            result[code] = units * float(px[code])
        return result

    def rebalance_group(group, desired, px, day, signal, transfer):
        """先卖后买；总配置事件共享现金，单独选股只用该部分资金。"""
        before = {sid: values(sid, px) for sid in group}
        blocked_sleeves = set()

        def execute(sid, code, delta, fees, internal=False):
            fee = sum(fees.values())
            books[sid]["units"][code] = books[sid]["units"].get(code, 0.) + delta / px[code]
            books[sid]["cash"] -= delta + fee
            trades.append({"date": day, "signal_date": signal, "sleeve": sid, "code": code,
                           "amount": delta, "side": "buy" if delta > 0 else "sell", "cost": fee,
                           "internal_transfer": internal, **fees})

        cost_day = str(day.date())
        if transfer and fee_enabled:
            # 同一证券在共享资金组内先转移份额，剩余买卖才按市场成交计费。
            sellers, buyers = defaultdict(list), defaultdict(list)
            for sid in group:
                for code in sorted((set(before[sid]) | set(desired[sid])) - {"__cash__"}):
                    delta = desired[sid].get(code, 0.) - before[sid].get(code, 0.)
                    if abs(delta) > 1e-7 and bool(data.tradable.loc[day].get(code, False)):
                        (buyers if delta > 0 else sellers)[code].append((sid, abs(delta)))
            for code in sorted(sellers.keys() & buyers.keys()):
                for seller, remaining in sellers[code]:
                    for index, (buyer, wanted) in enumerate(buyers[code]):
                        amount = min(remaining, wanted)
                        if amount <= 1e-7:
                            continue
                        zero = dict.fromkeys(COST_FIELDS, 0.)
                        # 内部划转的现金对价由随后共享资金的分配统一处理。
                        execute(seller, code, -amount, zero, internal=True)
                        execute(buyer, code, amount, zero, internal=True)
                        buyers[code][index] = (buyer, wanted - amount)
                        remaining -= amount
                        if remaining <= 1e-7:
                            break
            before = {sid: values(sid, px) for sid in group}
        for sid in group:
            for code in sorted(set(before[sid]) | set(desired[sid])):
                delta = desired[sid].get(code, 0.) - before[sid].get(code, 0.)
                if delta >= -1e-7:
                    continue
                if not bool(data.tradable.loc[day].get(code, False)):
                    blocks.append({"date": day, "sleeve": sid, "code": code, "side": "sell", "reason": "无当日可交易报价", "amount": -delta})
                    blocked_sleeves.add(sid)
                    continue
                execute(sid, code, delta, trade_cost(costs, delta, kinds.get(code), cost_day))
        if transfer:
            liquid = sum(books[sid]["cash"] for sid in group)
            reserve = {sid: desired[sid].get("__cash__", 0.) for sid in group}
            # 含费用时先保留目标现金，零费用沿用原来的资金分配口径。
            priority = reserve if fee_enabled else dict.fromkeys(group, 0.)
            needs = {sid: reserve[sid] - priority[sid] for sid in group}
            for sid in group:
                current = values(sid, px)
                for code, target in desired[sid].items():
                    if code == "__cash__":
                        continue
                    amount = max(0., target - current.get(code, 0.))
                    needs[sid] += amount + sum(trade_cost(costs, amount, kinds.get(code), cost_day).values())
            reserve_total = sum(priority.values())
            budget = max(0., liquid - reserve_total)
            reserve_ratio = min(1., liquid / reserve_total) if reserve_total else 1.
            total = sum(needs.values())
            for sid in group:
                buy_cash = budget * needs[sid] / total if total > 1e-9 else 0.
                books[sid]["cash"] = priority[sid] * reserve_ratio + buy_cash
            if total <= 1e-9:
                books["cash"]["cash"] += budget
        for sid in group:
            current = values(sid, px)
            buys = {code: max(0., target - current.get(code, 0.)) for code, target in desired[sid].items() if code != "__cash__"}
            available_buys = {}
            for code, amount in buys.items():
                if amount <= 1e-7:
                    continue
                if code not in px or not np.isfinite(px[code]) or not bool(data.tradable.loc[day].get(code, False)):
                    blocks.append({"date": day, "sleeve": sid, "code": code, "side": "buy", "reason": "无当日可交易报价", "amount": amount})
                    blocked_sleeves.add(sid)
                else:
                    available_buys[code] = amount
            spendable = max(0., books[sid]["cash"] - desired[sid].get("__cash__", 0.))
            buy_costs = {code: trade_cost(costs, amount, kinds.get(code), cost_day)
                         for code, amount in available_buys.items()}
            wanted = sum(amount + sum(buy_costs[code].values()) for code, amount in available_buys.items())
            ratio = min(1., spendable / wanted) if wanted > 0 else 0.
            for code, amount in available_buys.items():
                delta = amount * ratio
                if delta > 1e-7:
                    execute(sid, code, delta, {key: value * ratio for key, value in buy_costs[code].items()})
            after = values(sid, px)
            unresolved = any(abs(desired[sid].get(code, 0.) - after.get(code, 0.)) > .01
                             for code in set(after) | set(desired[sid]) if code != "__cash__")
            # 费用造成的目标差额在本次已接受。仅将有交易障碍的目标留待后续执行。
            if unresolved and (blocked_sleeves or not fee_enabled):
                pending[sid] = (dict(desired[sid]), signal)
            else:
                pending.pop(sid, None)

    for day in days:
        first_trade = len(trades)
        px = marked.loc[day]
        index = prices.index.get_loc(day)
        for sid in ids:
            for code, units in books[sid]["units"].items():
                if units > 1e-10 and index - last_seen.loc[day, code] > config.get("max_valuation_gap", 10):
                    if config.get("long_gap_policy", "error") == "error":
                        raise ValueError(f"{day.date()} 的 {code} 持仓超过估值缺口上限，请补数据，或明确设 long_gap_policy=carry_and_flag 沿用已知价估值。")
                    valuation_gaps.append({"date": day, "sleeve": sid, "code": code,
                                           "missing_sessions": int(index-last_seen.loc[day, code]),
                                           "mark_price": float(px[code]), "action": "carry_last_known_for_valuation_only"})
        pre = {sid: sum(values(sid, px).values()) + books[sid]["cash"] for sid in ids}
        nav = sum(pre.values())
        # 每日盈亏由前一天持仓产生；收盘调仓不获得本日已经发生的涨幅。
        pnl = {sid: pre[sid] - previous_values[sid] for sid in ids}
        if previous_px is not None:
            replay = sum(units * (px[code] - previous_px[code]) for sid in ids
                         for code, units in books[sid]["units"].items() if units > 1e-12)
            if abs(replay - (nav - previous_nav)) > 1e-5 * max(1., capital / 1e6):
                raise AssertionError("持仓盈亏复算不一致。")
        new_ids = [sid for sid in sleeves if day in schedules[sid]]
        is_outer = day in outer
        if is_outer or new_ids:
            signal = outer[day] if is_outer else schedules[new_ids[0]][day]
            decision = decide(config, data, day, signal,
                              {sid: values(sid, px) for sid in ids},
                              {sid: books[sid]["cash"] for sid in ids},
                              inner, previous_alloc, new_ids, is_outer)
            inner, previous_alloc = decision["inner"], decision["allocation"]
            audits.extend(decision["optimization"])
            selections.extend(decision["selections"])
            if decision["regime"] is not None:
                regimes.append(decision["regime"])
            desired, group = decision["desired"], decision["group"]
            for sid in group:
                for code, amount in desired[sid].items():
                    targets.append({"execution_date": day, "signal_date": signal, "sleeve": sid, "code": code,
                                    "target_value": amount, "portfolio_weight": amount / nav})
            rebalance_group(group, desired, px, day, signal, is_outer)
        # 尚未成交的目标金额保留到后续可交易日；新信号会替换该部分旧目标。
        skip = set(ids if is_outer else new_ids)
        for sid in list(pending):
            if sid not in skip:
                desired, signal = pending[sid]
                rebalance_group([sid], {sid: desired}, px, day, signal, False)
        post = {sid: sum(values(sid, px).values()) + books[sid]["cash"] for sid in ids}
        day_costs = dict.fromkeys(COST_FIELDS, 0.)
        for fill in trades[first_trade:]:
            pnl[fill["sleeve"]] -= fill["cost"]
            for field in COST_FIELDS:
                day_costs[field] += fill[field]
        cost = sum(day_costs.values())
        if abs(sum(post.values()) + cost - nav) > 1e-5 or min(books[sid]["cash"] for sid in ids) < -1e-5:
            raise AssertionError("调仓后权益加交易成本须还原调仓前权益，现金不能为负。")
        nav = sum(post.values())
        for sid in ids:
            contributions[sid] += pnl[sid]
            books[sid]["cash"] = max(0., books[sid]["cash"])
            for code, units in books[sid]["units"].items():
                if not np.isfinite(units) or units < -1e-9:
                    raise AssertionError("所有资产只做多；出现无效或负持仓：" + code)
                books[sid]["units"][code] = max(0., units)
        histories.append({"date": day, "nav": nav / capital, "equity": nav,
                          "return": nav / previous_nav - 1, "cash_weight": sum(books[sid]["cash"] for sid in ids) / nav,
                          "transaction_cost": cost, **day_costs,
                          # 轻量的日线记录也提供各部分的非现金仓位，供配套对照使用。
                          **{f"invested_weight_{sid}": (post[sid] - books[sid]["cash"]) / nav for sid in sleeves},
                          **{f"pnl_{sid}": pnl[sid] for sid in ids}})
        for sid in ids:
            for code, amount in {**values(sid, px), "CASH": books[sid]["cash"]}.items():
                if amount > 1e-6:
                    weight_rows.append({"date": day, "sleeve": sid, "code": code, "value": amount, "weight": amount / nav})
        previous_values, previous_nav, previous_px = post, nav, px
    result = pd.DataFrame(histories)
    peak = result["nav"].cummax().clip(lower=1.)
    result["drawdown"] = result["nav"] / peak - 1
    if abs(sum(contributions.values()) - (previous_nav - capital)) > 1e-5:
        raise AssertionError("各部分累计盈亏与账户收益未对齐。")
    return {"daily": result, "weights": pd.DataFrame(weight_rows), "trades": pd.DataFrame(trades),
            "selections": pd.concat(selections, ignore_index=True) if selections else pd.DataFrame(),
            "targets": pd.DataFrame(targets), "blocked": pd.DataFrame(blocks), "optimization": audits,
            "valuation_gaps": pd.DataFrame(valuation_gaps),
            "regimes": regimes, "contributions": contributions,
            "unfilled_at_end": sorted(pending), "initial_capital": capital,
            "final_state": {"as_of": str(days[-1].date()), "units_basis": "adjusted_simulation",
                            "books": copy.deepcopy(books), "inner": inner, "previous_allocation": previous_alloc,
                            "pending": {sid: {"targets": target, "signal_date": str(sig.date())}
                                        for sid, (target, sig) in pending.items()}}}
