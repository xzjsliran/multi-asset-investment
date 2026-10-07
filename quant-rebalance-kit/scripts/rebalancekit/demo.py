"""从用户指定的历史数据复原模拟账户；数据及结果均留在工作目录。"""
from __future__ import annotations

import copy
from pathlib import Path
import pandas as pd

from strategykit.engine import backtest
from strategykit.decision import event_on
from .inputs import write_json
from .quotes import extract_quotes, validate_quotes
from .planning import make_plan
from .exports import export_plan, new_output


def historical_demo(config, data, data_directory, signal_date, output):
    signal = pd.Timestamp(signal_date)
    available = data.calendar[data.calendar > signal]
    if signal not in data.calendar or available.empty:
        raise ValueError("演示需要信号日及随后执行日的交易日历。")
    execution = available[0]
    if signal <= pd.Timestamp(config["start"]):
        raise ValueError("演示信号日需晚于回测起始日，才能取得此前持仓。")
    history = copy.deepcopy(config)
    history["end"] = str(signal.date())
    result = backtest(history, data)
    state = result["final_state"]
    if state["pending"]:
        raise ValueError("该历史截面仍有未成交目标，请换一个已完成调仓的截面。")
    asof = (signal+pd.Timedelta(hours=16)).tz_localize("Asia/Shanghai").isoformat()
    # 仅为流程验证使用分数单位；实际交易单位必须由产品规则输入。
    raw = pd.read_csv(Path(data_directory)/"prices.csv", dtype={"code": str})
    rules = {}
    for code, rows in raw.groupby("code"):
        usd = "close_usd" in rows and rows["close_usd"].notna().any()
        rules[code] = {"currency": "USD" if usd else "CNY", "buy_lot": 0.000001, "sell_lot": 0.000001}
    codes = set(data.prices.columns)
    quoted = extract_quotes(data_directory, codes, asof, rules)
    quotes, rates, _ = validate_quotes(quoted, asof, 7)
    positions, cash = [], []
    last_prices = data.prices.loc[:signal].ffill().iloc[-1]
    for sid, book in state["books"].items():
        for code, units in book["units"].items():
            if units <= 1e-10:
                continue
            # 由历史复权记账金额换成同日原始参考价下的模拟实际份额。
            quantity = units*last_prices[code]/quotes[code]["price_cny"]
            positions.append({"sleeve": sid, "code": code, "quantity": quantity, "available_quantity": quantity})
        cash.append({"sleeve": sid, "currency": "CNY", "amount": max(0., book["cash"])})
    account = {"schema_version": 1, "source": "simulation", "as_of": asof, "positions": positions, "cash": cash,
               "strategy_state": {"previous_allocation": state["previous_allocation"], "inner_weights": state["inner"]}}
    request = {"schema_version": 1, "mode": "demo", "as_of": asof, "execution_date": str(execution.date()), "initial_allocation": False}
    plan = make_plan(config, data, account, request, quoted)
    # 用同一历史事件的回测目标做复核：目标权重应在所有内部部分同时调仓时一致。
    full = copy.deepcopy(config)
    full["end"] = str(execution.date())
    replay = backtest(full, data)
    expected = replay["targets"].loc[replay["targets"].execution_date.eq(execution)]
    target_map = {(x["sleeve"], x["code"]): x["target_weight"] for x in plan["targets"]}
    event = event_on(config, data.calendar, execution)
    all_due = event["is_outer"] and len(event["new_ids"]) == len(config["sleeves"])
    errors = [abs(float(x.portfolio_weight)-target_map.get((x.sleeve, x.code), 0.)) for x in expected.itertuples()]
    max_error = max(errors, default=0.)
    if all_due and (expected.empty or max_error > 1e-10):
        raise AssertionError("同一历史信号的回测目标权重与计划不一致。")
    plan["notes"].append("本次模拟份额由历史回测金额换算，交易单位取百万分之一份用于流程复核。")
    plan["checks"]["historical_target_comparison"] = {"all_sleeves_due": all_due, "max_weight_error": max_error,
            "note": "全组合同时调仓时核对目标权重；估算数量和历史成交数量采用不同日期报价，不要求相同。"}
    out = new_output(output)
    result_path = out/"plan"
    exported = export_plan(plan, config, result_path)
    write_json(out/"account.simulated.json", account)
    write_json(out/"request.json", request)
    write_json(out/"quotes.json", quoted)
    return {**exported, "historical_target_comparison": plan["checks"]["historical_target_comparison"]}
