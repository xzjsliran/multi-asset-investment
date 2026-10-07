"""只输出结构化计算结果；客户展示由report kit完成。"""
from __future__ import annotations
import hashlib
from pathlib import Path
import pandas as pd

from .inputs import write_json


COLUMNS = {
    "targets": ["sleeve", "code", "target_value_cny", "target_weight"],
    "holdings": ["sleeve", "code", "current_quantity", "current_available_quantity", "projected_quantity", "current_value_cny", "projected_value_cny", "current_weight", "target_weight", "projected_weight", "deviation_cny"],
    "orders": ["code", "name", "side", "quantity", "signed_quantity", "reference_price", "price_available_at", "currency", "amount_native", "amount_cny", "estimated_cash_change_native", "estimated_cost"],
    "cash_after": ["sleeve", "currency", "amount"],
    "allocation_legs": ["sleeve", "code", "signed_quantity", "amount_native", "amount_cny", "currency"],
}


def work_path(path):
    out = Path(path).resolve()
    project = Path(__file__).resolve().parents[3]
    for kit in project.glob("quant-*-kit"):
        if kit == out or kit in out.parents:
            raise ValueError("账户、数据和结果须保存在kit以外的工作目录。")
    return out


def new_output(path):
    out = work_path(path)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("输出位置已有内容，请使用新的目录。")
    return out


def export_plan(plan, config, output):
    out = new_output(output)
    out.mkdir(parents=True, exist_ok=True)
    for key, cols in COLUMNS.items():
        pd.DataFrame(plan[key], columns=cols).to_csv(out/(key+".csv"), index=False, encoding="utf-8-sig")
    write_json(out/"plan.json", plan)
    write_json(out/"strategy.json", config)
    write_json(out/"checks.json", plan["checks"])
    write_json(out/"ai_context.json", {"document_type": "rebalance_plan", "plan_file": "plan.json",
               "strategy_file": "strategy.json", "mode": plan["mode"], "timing": plan["timing"],
               "evidence": {"strategy_targets": "plan.json#/targets", "orders": "plan.json#/orders",
                            "constraints": "plan.json#/issues", "macro": "plan.json#/regime", "selection": "plan.json#/selections"},
               "instructions": ["使用计划中已计算的权重、数量及证据，不能自行添加宏观判断或预测。",
                                "明确计划日期及模拟/实际账户状态；预案需在信号日更新。",
                                "计划生成后实际持仓不变；成交后重新核对。",
                                "全部资产只做多；负买卖差额表示减持，不能解释为开空仓。"]})
    write_json(out/"handoff.json", {"schema_version": 1, "producer": "quant-rebalance-kit", "document_type": "rebalance_plan",
               "position_policy": "long_only", "base_currency": "CNY",
               "units": {"value": "CNY", "order_price": "native_currency", "weight": "decimal", "quantity": "native_security_units"},
               "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}})
    return {"result_dir": str(out), "handoff": str(out/"handoff.json"), "orders": len(plan["orders"]), "status": plan["status"]}
