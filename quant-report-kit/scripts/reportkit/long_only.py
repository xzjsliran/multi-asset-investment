"""报告输入的持仓方向检查；负收益、回撤、卖出金额不属于空头持仓。"""
import math
import numpy as np
import pandas as pd


def check_long_only(run):
    c = run.config
    if c.get("position_policy", "long_only") != "long_only" or c.get("allow_short", False) is not False or c.get("leverage", 1) != 1:
        raise ValueError("本产品仅支持全部资产只做多、无融资杠杆的策略。")
    def weights(values, label, cap=True):
        vals = list(values)
        if any(not math.isfinite(float(v)) or float(v) < 0 for v in vals) or (cap and sum(vals) > 1+1e-9):
            raise ValueError(label+"违反只做多约束。")
    weights([s.get("weight", 0.) for s in c.get("sleeves", [])], "大类权重")
    for s in c.get("sleeves", []):
        weights([a.get("weight", 1.) for a in s.get("assets", [])], "证券配权", False)
        if any(a.get("side", "long") != "long" for a in s.get("assets", [])):
            raise ValueError("证券配置包含空头方向。")
    for rule in c.get("macro", {}).get("rules", []):
        weights(rule.get("weights", {}).values(), "动态权重")
    for table, cols in {"weights": ["weight", "value", "quantity"],
                        "targets": ["portfolio_weight", "target_value"],
                        "selections": ["within_weight"]}.items():
        frame = run.tables.get(table, pd.DataFrame())
        for col in cols:
            if col in frame:
                v = pd.to_numeric(frame[col], errors="coerce")
                if not np.isfinite(v).all() or v.lt(0).any():
                    raise ValueError(f"{table}.{col}出现无效值或负持仓，违反只做多约束。")
    if "cash_weight" in run.daily:
        v = pd.to_numeric(run.daily["cash_weight"], errors="coerce")
        if not np.isfinite(v).all() or v.lt(-1e-10).any() or v.gt(1+1e-9).any():
            raise ValueError("每日现金权重违反只做多和现金不透支要求。")
    has_holdings = not run.tables.get("weights", pd.DataFrame()).empty
    return {"policy": "long_only", "holdings_verified": has_holdings,
            "assessment": "配置与已提供持仓通过只做多检查" if has_holdings else "配置按只做多要求检查；未提供持仓明细，无法验证实际持仓方向"}
