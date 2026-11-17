"""多资产日线回测；每个资产部分有自己的持仓与现金。"""
from __future__ import annotations

import copy
from collections import defaultdict

import numpy as np
import pandas as pd

from .config import allocation_frequencies, schedule, validate
from .costs import COST_FIELDS, asset_kinds, charges_fees, costs_for, trade_cost
from .decision import decide
from .positions import internal_transfers, market_changes, position_changes


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
    at_open = config.get("execution_price") == "open"
    if at_open and data.open_prices is None:
        raise ValueError("开盘成交需要标准行情中的后复权开盘价。")
    execution_marks = data.open_prices.combine_first(marked.shift(1)) if at_open else marked
    last_seen = prices.notna().apply(lambda x: pd.Series(np.where(x, np.arange(len(x)), np.nan), index=x.index)).ffill()
    schedules = {sid: schedule(data.calendar, config["start"], config["end"], s["rebalance"]) for sid, s in sleeves.items()}
    outer = {day: signal for f in allocation_frequencies(config)
             for day, signal in schedule(data.calendar, config["start"], config["end"], f).items()}
    days = data.calendar[(data.calendar >= pd.Timestamp(config["start"])) & (data.calendar <= pd.Timestamp(config["end"]))]
    pending, shared_pending = {}, set()
    redemption_days = {a["code"]: a["redemption_days"] for s in sleeves.values()
                       for a in s.get("assets", []) if a["kind"] == "qdii_nav"}
    receivables = dict.fromkeys(ids, 0.)
    payments = defaultdict(list)
    shared_cash_target = 0.
    inner = {}
    previous_alloc = {}
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

    def rebalance_group(desired, px, day, signals, transfer, outstanding):
        """先卖后买；总配置事件共享现金，单独选股只用该部分资金。"""
        group = list(desired)
        changes = {sid: position_changes(values(sid, px), desired[sid]) for sid in group}
        # 延后成交只补原来未完成的一侧。已完成的仓位继续随行情漂移。
        for sid, remainder in outstanding.items():
            changes[sid] = {code: delta for code, delta in changes[sid].items()
                            if delta * remainder.get(code, 0.) > 0}
        blocked_sleeves = set()
        if at_open:
            quoted = data.open_prices.loc[day].notna()
            buyable = data.open_buyable.loc[day] if data.open_buyable is not None else quoted
            sellable = data.open_sellable.loc[day] if data.open_sellable is not None else quoted
        else:
            quoted = data.tradable.loc[day]
            buyable = data.close_buyable.loc[day] if data.close_buyable is not None else quoted
            sellable = data.close_sellable.loc[day] if data.close_sellable is not None else quoted
        cost_day = str(day.date())

        def execute(sid, code, delta, fees, internal=False):
            fee = sum(fees.values())
            books[sid]["units"][code] = books[sid]["units"].get(code, 0.) + delta / px[code]
            books[sid]["cash"] -= delta + fee
            lag = redemption_days.get(code, 0) if delta < 0 and not internal else 0
            if lag:
                owner = "cash" if transfer else sid
                net = -delta - fee
                books[sid]["cash"] -= net
                receivables[owner] += net
                payments[data.calendar.get_loc(day) + lag].append((owner, net))
            changes[sid][code] -= delta
            trades.append({"date": day, "signal_date": signals[sid], "sleeve": sid, "code": code,
                           "amount": delta, "side": "buy" if delta > 0 else "sell", "cost": fee,
                           "internal_transfer": internal, "settlement_sessions": lag, **fees})

        if len(group) > 1:
            eligible = {sid: {code: delta for code, delta in changes[sid].items()
                              if bool((buyable if delta > 0 else sellable).get(code, False))} for sid in group}
            moves = list(internal_transfers(eligible))
            if moves and not transfer:
                # 内部调仓各自结算。先验证本部分现金和卖出回款能覆盖划入。
                balance = {sid: books[sid]["cash"] for sid in group}
                for seller, buyer, code, amount in moves:
                    balance[seller] += amount
                    balance[buyer] -= amount
                    eligible[seller][code] += amount
                    eligible[buyer][code] -= amount
                for sid, deltas in market_changes(eligible).items():
                    for code, delta in deltas.items():
                        if delta < -1e-7 and code not in redemption_days:
                            balance[sid] -= delta + sum(trade_cost(costs, delta, kinds.get(code), cost_day).values())
                if any(balance[sid] < desired[sid].get("__cash__", 0.) - 1e-8 for sid in group):
                    moves = []
            for seller, buyer, code, amount in moves:
                zero = dict.fromkeys(COST_FIELDS, 0.)
                execute(seller, code, -amount, zero, internal=True)
                execute(buyer, code, amount, zero, internal=True)
            external = market_changes(changes)
            for sid, deltas in changes.items():
                for code, delta in deltas.items():
                    deferred = delta - external[sid].get(code, 0.)
                    if abs(deferred) > .01:
                        blocks.append({"date": day, "sleeve": sid, "code": code,
                                       "side": "buy" if deferred > 0 else "sell",
                                       "reason": "内部划转暂不可执行，保留账户内相抵持仓", "amount": abs(deferred)})
            changes = external
        for sid in group:
            for code, delta in changes[sid].items():
                if delta >= -1e-7:
                    continue
                if not bool(sellable.get(code, False)):
                    reason = "开盘缺价或触及跌停" if at_open else "无当日可交易报价"
                    blocks.append({"date": day, "sleeve": sid, "code": code, "side": "sell", "reason": reason, "amount": -delta})
                    blocked_sleeves.add(sid)
                    continue
                execute(sid, code, delta, trade_cost(costs, delta, kinds.get(code), cost_day))
        blocked_sells = bool(blocked_sleeves)
        if transfer:
            liquid = sum(books[sid]["cash"] for sid in group)
            reserve = {sid: desired[sid].get("__cash__", 0.) for sid in group}
            # 含费用时先保留目标现金，零费用沿用原来的资金分配口径。
            priority = reserve if fee_enabled else dict.fromkeys(group, 0.)
            needs = {sid: reserve[sid] - priority[sid] for sid in group}
            for sid in group:
                for code, amount in changes[sid].items():
                    if amount <= 1e-7:
                        continue
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
            available_buys = {}
            for code, amount in changes[sid].items():
                if amount <= 1e-7:
                    continue
                if code not in px or not np.isfinite(px[code]) or not bool(buyable.get(code, False)):
                    reason = "开盘缺价或触及涨停" if at_open else "无当日可交易报价"
                    blocks.append({"date": day, "sleeve": sid, "code": code, "side": "buy", "reason": reason, "amount": amount})
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
            remaining = {code: delta for code, delta in changes[sid].items() if abs(delta) > .01}
            # 费用导致的目标偏差当期接受。只重试交易障碍留下的差额。
            waiting_shared = (transfer or sid in shared_pending) and receivables["cash"] > 1e-7
            waiting_funds = any(delta > 0 for delta in remaining.values()) and (
                waiting_shared or receivables[sid] > 1e-7)
            if remaining and (sid in blocked_sleeves or (transfer and blocked_sells) or waiting_funds):
                pending[sid] = (dict(desired[sid]), signals[sid], remaining)
                if transfer:
                    shared_pending.add(sid)
            else:
                pending.pop(sid, None)
                shared_pending.discard(sid)

    for day in days:
        first_trade = len(trades)
        close_px = marked.loc[day]
        px = execution_marks.loc[day]
        index = prices.index.get_loc(day)
        for sid, amount in payments.pop(index, []):
            books[sid]["cash"] += amount
            receivables[sid] -= amount
        for sid in ids:
            for code, units in books[sid]["units"].items():
                if units > 1e-10 and index - last_seen.loc[day, code] > config.get("max_valuation_gap", 10):
                    if config.get("long_gap_policy", "error") == "error":
                        raise ValueError(f"{day.date()} 的 {code} 持仓超过估值缺口上限，请补数据，或明确设 long_gap_policy=carry_and_flag 沿用已知价估值。")
                    valuation_gaps.append({"date": day, "sleeve": sid, "code": code,
                                           "missing_sessions": int(index-last_seen.loc[day, code]),
                                           "mark_price": float(px[code]), "action": "carry_last_known_for_valuation_only"})
        pre = {sid: sum(values(sid, px).values()) + books[sid]["cash"] + receivables[sid] for sid in ids}
        nav = sum(pre.values())
        # 执行价以前的盈亏归前一天持仓。
        pnl = {sid: pre[sid] - previous_values[sid] for sid in ids}
        if previous_px is not None:
            replay = sum(units * (px[code] - previous_px[code]) for sid in ids
                         for code, units in books[sid]["units"].items() if units > 1e-12)
            if abs(replay - (nav - previous_nav)) > 1e-5 * max(1., capital / 1e6):
                raise AssertionError("持仓盈亏复算不一致。")
        new_ids = [sid for sid in sleeves if day in schedules[sid]]
        is_outer = day in outer
        desired, signals, outstanding = {}, {}, {}
        if is_outer or new_ids:
            signal = outer[day] if is_outer else schedules[new_ids[0]][day]
            opening_codes = data.open_prices.loc[day].dropna().index if at_open else None
            decision = decide(config, data, day, signal,
                              {sid: values(sid, px) for sid in ids},
                              {sid: books[sid]["cash"] + receivables[sid] for sid in ids},
                              inner, previous_alloc, new_ids, is_outer, opening_codes)
            inner, previous_alloc = decision["inner"], decision["allocation"]
            audits.extend(decision["optimization"])
            selections.extend(decision["selections"])
            if decision["regime"] is not None:
                regimes.append(decision["regime"])
            desired, group = decision["desired"], decision["group"]
            if is_outer:
                shared_cash_target = desired["cash"]["__cash__"]
                shared_pending.clear()
            else:
                shared_pending.difference_update(new_ids)
            signals = dict.fromkeys(group, signal)
            for sid in group:
                for code, amount in desired[sid].items():
                    targets.append({"execution_date": day, "signal_date": signal, "sleeve": sid, "code": code,
                                    "target_value": amount, "portfolio_weight": amount / nav})
        # 同日处理新差额与旧委托的剩余部分。持仓在回测截止日照常估值。
        retry = {}
        for sid, (target, signal, remaining) in pending.items():
            if sid not in desired:
                if not is_outer and sid in shared_pending:
                    retry[sid] = (target, signal, remaining)
                else:
                    desired[sid], signals[sid], outstanding[sid] = target, signal, remaining
        if desired:
            rebalance_group(desired, px, day, signals, is_outer, outstanding)
        if retry:
            retry_targets = {sid: target for sid, (target, _, _) in retry.items()}
            retry_targets["cash"] = {"__cash__": shared_cash_target}
            rebalance_group(retry_targets, px, day, {sid: sig for sid, (_, sig, _) in retry.items()},
                            True, {sid: left for sid, (_, _, left) in retry.items()})
        post = {sid: sum(values(sid, px).values()) + books[sid]["cash"] + receivables[sid] for sid in ids}
        day_costs = dict.fromkeys(COST_FIELDS, 0.)
        market_amount = 0.
        for fill in trades[first_trade:]:
            pnl[fill["sleeve"]] -= fill["cost"]
            if not fill["internal_transfer"]:
                market_amount += abs(fill["amount"])
            for field in COST_FIELDS:
                day_costs[field] += fill[field]
        cost = sum(day_costs.values())
        if abs(sum(post.values()) + cost - nav) > 1e-5 or min(books[sid]["cash"] for sid in ids) < -1e-5:
            raise AssertionError("调仓后权益加交易成本须还原调仓前权益，现金不能为负。")
        turnover = market_amount / nav
        if at_open:
            # 开盘成交后到收盘的涨跌记录在更新后的持仓上。
            settled = {sid: sum(values(sid, close_px).values()) + books[sid]["cash"] + receivables[sid] for sid in ids}
            for sid in ids:
                pnl[sid] += settled[sid] - post[sid]
            post = settled
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
                          "unsettled_redemptions_cny": sum(receivables.values()),
                          "transaction_cost": cost, "turnover": turnover, **day_costs,
                          # 轻量的日线记录也提供各部分的非现金仓位，供配套对照使用。
                          **{f"invested_weight_{sid}": (post[sid] - books[sid]["cash"] - receivables[sid]) / nav for sid in sleeves},
                          **{f"pnl_{sid}": pnl[sid] for sid in ids}})
        for sid in ids:
            for code, amount in {**values(sid, close_px), "CASH": books[sid]["cash"],
                                 "RECEIVABLE": receivables[sid]}.items():
                if amount > 1e-6:
                    weight_rows.append({"date": day, "sleeve": sid, "code": code, "value": amount, "weight": amount / nav})
        previous_values, previous_nav, previous_px = post, nav, close_px
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
                            "unsettled_redemptions": {sid: amount for sid, amount in receivables.items() if amount > 1e-7},
                            "pending": {sid: {"targets": target, "signal_date": str(sig.date()), "remaining": remaining}
                                        for sid, (target, sig, remaining) in pending.items()}}}
