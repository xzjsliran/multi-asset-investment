"""回测和调仓计划共用的持仓差额与账户内部划转。"""
from collections import defaultdict


def position_changes(current, target):
    """计算同一估值口径下的证券金额差额。目标中省略的证券视为零权重。"""
    changes = {}
    for code in sorted((current.keys() | target.keys()) - {"__cash__"}):
        change = target.get(code, 0.) - current.get(code, 0.)
        if abs(change) > 1e-7:
            changes[code] = change
    return changes


def internal_transfers(changes):
    """匹配同账户同证券的反向差额。入参可统一使用金额或份额。"""
    sells, buys = defaultdict(list), defaultdict(list)
    for sleeve, deltas in changes.items():
        for code, change in deltas.items():
            if change < 0:
                sells[code].append((sleeve, -change))
            elif change > 0:
                buys[code].append([sleeve, change])
    for code in sorted(sells.keys() & buys.keys()):
        for seller, remaining in sells[code]:
            for buyer in buys[code]:
                amount = min(remaining, buyer[1])
                if amount > 1e-10:
                    yield seller, buyer[0], code, amount
                    remaining -= amount
                    buyer[1] -= amount
                if remaining <= 1e-10:
                    break


def market_changes(changes):
    """只保留账户净差额，按各部分的同向需求分配。"""
    net, side_total = defaultdict(float), defaultdict(float)
    for deltas in changes.values():
        for code, amount in deltas.items():
            net[code] += amount
            side_total[code, amount > 0] += amount
    return {
        sleeve: {code: amount * (net[code] / side_total[code, amount > 0])
                 for code, amount in deltas.items() if net[code] * amount > 0}
        for sleeve, deltas in changes.items()
    }
