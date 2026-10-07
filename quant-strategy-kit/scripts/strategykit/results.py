"""计算并导出结果数据。报告组读取这些文件，自行设计图表和文字。"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .config import all_assets, write_json, validate
from .engine import backtest


# 即使本次没有成交、选股或缺口，下一组也能按固定表头读取。
TABLE_COLUMNS = {
    "daily": ["date", "nav", "equity", "return", "cash_weight", "drawdown"],
    "weights": ["date", "sleeve", "code", "value", "weight"],
    "trades": ["date", "signal_date", "sleeve", "code", "amount", "side", "cost"],
    "selections": ["signal_date", "execution_date", "sleeve", "code", "name", "within_weight"],
    "targets": ["execution_date", "signal_date", "sleeve", "code", "target_value", "portfolio_weight"],
    "blocked": ["date", "sleeve", "code", "side", "reason", "amount"],
    "valuation_gaps": ["date", "sleeve", "code", "missing_sessions", "mark_price", "action"],
}


def metrics(daily):
    r = daily["return"]
    years = max((daily["date"].iloc[-1] - daily["date"].iloc[0]).days / 365.25, 1/365.25)
    vol = float(r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 else 0.
    return {"start": str(daily["date"].iloc[0].date()), "end": str(daily["date"].iloc[-1].date()),
            "total_return": float(daily["nav"].iloc[-1] - 1),
            "annual_return": float(daily["nav"].iloc[-1] ** (1 / years) - 1),
            "annual_volatility": vol, "max_drawdown": float(daily["drawdown"].min()),
            "sharpe_zero_rf": float(r.mean() * 252 / vol) if vol > 1e-12 else None,
            "final_equity": float(daily["equity"].iloc[-1]), "trading_days": len(daily)}


def export_results(config, data, out, compare_equal=True):
    config = validate(config)
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("结果目录已有文件，请使用新的目录保存本次回测。")
    out.mkdir(parents=True, exist_ok=True)
    files = []

    def save_table(name, frame, role):
        frame.to_csv(out / name, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
        files.append({"path": name, "format": "csv", "role": role,
                      "rows": len(frame), "columns": frame.columns.tolist()})

    def save_json(name, value, role):
        write_json(out / name, value)
        files.append({"path": name, "format": "json", "role": role})

    result = backtest(config, data)
    daily = result["daily"]
    summary = metrics(daily)
    curves = pd.DataFrame({"date": daily["date"], "strategy": daily["nav"]})
    comparison = [{"scenario": "strategy", "label": "当前策略", **summary}]

    def add_comparison(scenario, label, frame, filename):
        comparison.append({"scenario": scenario, "label": label, **metrics(frame)})
        curves[scenario] = frame.set_index("date")["nav"].reindex(daily["date"]).to_numpy()
        save_table(filename, frame, label + "的每日账户数据")

    if compare_equal and any(s["weighting"]["method"] == "min_variance" for s in config["sleeves"]):
        equal = copy.deepcopy(config)
        for s in equal["sleeves"]:
            if s["weighting"]["method"] == "min_variance":
                s["weighting"]["method"] = "equal"
        add_comparison("equal_weight", "仅将最小方差改为等权", backtest(equal, data)["daily"], "equal_weight_daily.csv")
    if config.get("macro", {}).get("enabled") or config.get("allocation", {}).get("method") == "risk_parity":
        static = copy.deepcopy(config)
        # 保留相同调仓日，单独比较动态比例的影响。
        base = {s["id"]: s["weight"] for s in static["sleeves"]}
        for rule in static["macro"].get("rules", []):
            rule["weights"] = dict(base)
        if static.get("allocation", {}).get("method") == "risk_parity":
            static["allocation"] = {"method": "fixed"}
        add_comparison("static", "固定权重、同样调仓日", backtest(static, data)["daily"], "static_daily.csv")
    benchmark = config.get("benchmark", {}).get("code")
    omitted = []
    if benchmark:
        p = data.prices.reindex(index=daily["date"], columns=[benchmark])[benchmark]
        if p.notna().all():
            b = pd.DataFrame({"date": daily["date"].values, "nav": (p / p.iloc[0]).values})
            b["return"] = b["nav"].pct_change(fill_method=None).fillna(0)
            b["equity"] = b["nav"] * config["initial_capital"]
            b["drawdown"] = b["nav"] / b["nav"].cummax().clip(lower=1) - 1
            add_comparison("benchmark", benchmark + " 持有", b, "benchmark_daily.csv")
        else:
            omitted.append({"scenario": "benchmark", "code": benchmark, "reason": "基准行情未完整覆盖回测日期"})
    annual = daily.groupby(daily["date"].dt.year)["return"].agg(
        lambda x: float((1+x).prod()-1)).rename("return").reset_index().rename(columns={"date": "year"})
    if not np.isclose((1 + annual["return"]).prod(), daily["nav"].iloc[-1], atol=1e-10):
        raise AssertionError("年度收益连乘未能还原总收益。")

    roles = {"daily": "当前策略每日账户", "weights": "收盘调仓后的实际持仓",
             "trades": "实际成交记录", "selections": "选中证券及当时的筛选字段",
             "targets": "各次调仓目标", "blocked": "缺报价而未执行的成交尝试", "valuation_gaps": "长期缺报价的估值记录"}
    for key, columns in TABLE_COLUMNS.items():
        df = result[key]
        df = df.reindex(columns=columns + [c for c in df.columns if c not in columns])
        save_table(f"{key}.csv", df, roles[key])
    save_table("annual.csv", annual, "当前策略各年度收益")
    save_table("curves.csv", curves, "已对齐日期的各方案净值")
    for key in ["optimization", "regimes", "contributions", "unfilled_at_end", "final_state"]:
        save_json(f"{key}.json", result[key], key)
    save_json("strategy.json", config, "本次完整策略配置")
    save_json("summary.json", summary, "当前策略汇总指标")
    save_json("comparison.json", comparison, "各方案的同口径指标")
    save_json("assets.json", {"sleeves": [{"id": s["id"], "name": s.get("name", s["id"]), "base_weight": s["weight"]}
                                         for s in config["sleeves"]] + [{"id": "cash", "name": "未分配现金"}],
                             "configured_assets": all_assets(config)}, "资产部分及配置中的证券名称")
    fallback = sum(x["status"] == "fallback_equal" for x in result["optimization"])
    missing_macro = sum(x["missing"] for x in result["regimes"] if x.get("method") != "risk_parity")
    checks = {"weights_sum_max_error": float((result["weights"].groupby("date")["weight"].sum()-1).abs().max()),
              "contribution_error": float(sum(result["contributions"].values())-(summary["final_equity"]-config["initial_capital"])),
              "future_signal_trades": int((pd.to_datetime(result["trades"]["signal_date"]) >= pd.to_datetime(result["trades"]["date"])).sum()) if not result["trades"].empty else 0,
              "fallback_equal_count": fallback, "missing_macro_count": missing_macro,
              "risk_allocation_fallback_count": sum(x.get("method") == "risk_parity" and x.get("status") != "success" for x in result["regimes"]),
              "blocked_attempts": len(result["blocked"]), "long_valuation_gap_rows": len(result["valuation_gaps"]), "transaction_cost": 0,
              "position_policy": "long_only", "negative_position_rows": int(result["weights"]["weight"].lt(0).sum()),
              "negative_target_rows": int(result["targets"]["target_value"].lt(0).sum()),
              "minimum_cash_weight": float(daily["cash_weight"].min())}
    save_json("checks.json", checks, "资金、时序及数据缺口核对")
    notes = ["所有资产只做多；目标权重及持仓非负，不使用融资杠杆。", "交易成本和现金收益均为零，允许分数份额。",
             "信号日收盘形成规则，下一中国交易日收盘记账；当日收益先归旧持仓，再调仓。",
             "复权单位用于收益记账；未完整模拟涨跌停和整手成交。",
             "境内QDII使用其自身人民币场内价格，包含市场折溢价的变化；不重复乘汇率。"]
    if any(a["kind"] == "us_etf" for a in all_assets(config)):
        notes.append("直接美国ETF按已知汇率折成人民币，成交采用已完成报价的日线近似。")
    if config.get("allocation", {}).get("method") == "risk_parity":
        notes.append("大类风险平价采用事先配置的代理篮子及信号日前收益；目标风险贡献基于收缩协方差，实际组合风险贡献由回测盈亏另行计算。代理估计不等于实际持仓的历史业绩。")
    if any(s["selection"]["method"] in {"quality_value", "fundamental"} for s in config["sleeves"]):
        notes.append("财务按公告日过滤；供应商的历史财务修订及行业回溯仍可能影响结果。")
    if any(s["selection"]["method"] == "momentum" for s in config["sleeves"]):
        notes.append("动量选择使用配置中的明确候选池。")
    save_json("manifest.json", {"schema_version": 1, "data": data.provenance, "notes": notes,
              "omitted_comparisons": omitted,
              "price_digest": hashlib.sha256(pd.util.hash_pandas_object(data.prices, index=True).values.tobytes()).hexdigest()}, "数据来源与本次计算口径")
    write_json(out / "handoff.json", {
        "schema_version": 1, "producer": "quant-strategy-kit", "strategy_name": config["name"],
        "base_currency": config.get("base_currency", "CNY"), "initial_capital": config["initial_capital"],
        "start": summary["start"], "end": summary["end"],
        "units": {"money": "CNY", "return_weight_volatility_drawdown": "decimal", "nav": "initial_1"},
        "conventions": {"position_policy": "long_only", "execution": "signal_close_then_next_CN_session_close", "daily_weights": "after_rebalance",
                        "trade_amount": "buy_positive_sell_negative", "annualization_days": 252,
                        "cagr_years": "calendar_days_between_first_and_last_row_divided_by_365.25", "risk_free_rate": 0,
                        "missing_json": "null", "missing_csv": "empty_cell", "csv_encoding": "utf-8-sig"},
        "scenarios": [{"id": row["scenario"], "label": row["label"]} for row in comparison],
        "diagnostics": {**checks, "unfilled_at_end": result["unfilled_at_end"], "omitted_comparisons": omitted},
        "files": files})
    return summary, checks
