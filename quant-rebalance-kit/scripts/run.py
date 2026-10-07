#!/usr/bin/env python3
"""组合管理与调仓计划：核对、报价提取、计算和历史演示。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

KIT = Path(__file__).resolve().parents[1]
# 与其他kit一起存放；不复制策略引擎。
sys.path.insert(0, str(KIT.parent/"quant-strategy-kit/scripts"))

from strategykit.config import read_config
from strategykit.data import Dataset
from rebalancekit.inputs import read_json, write_json, review
from rebalancekit.exports import export_plan, new_output, work_path
from rebalancekit.planning import make_plan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ["review", "plan"]:
        q = sub.add_parser(command)
        for arg in ["config", "account", "request", "out"]:
            q.add_argument("--"+arg, required=True)
        if command == "plan":
            q.add_argument("--data", required=True)
            q.add_argument("--quotes", required=True)
            q.add_argument("--confirmation")
    q = sub.add_parser("quotes")
    for arg in ["data", "rules", "as-of", "out"]:
        q.add_argument("--"+arg, required=True)
    q = sub.add_parser("demo")
    for arg in ["config", "data", "signal-date", "out"]:
        q.add_argument("--"+arg, required=True)
    q = sub.add_parser("import-account", help="把持仓和现金CSV转换为待用户确认的账户JSON")
    for arg in ["positions-csv", "cash-csv", "as-of", "out"]:
        q.add_argument("--"+arg, required=True)
    q.add_argument("--source", choices=["actual", "simulation"], default="actual")
    args = p.parse_args()
    try:
        if args.command == "import-account":
            import pandas as pd
            from rebalancekit.inputs import timestamp
            timestamp(args.as_of, "持仓时间")
            positions = pd.read_csv(args.positions_csv, dtype={"code": str, "sleeve": str})
            cash = pd.read_csv(args.cash_csv, dtype={"sleeve": str, "currency": str})
            if not {"sleeve", "code", "quantity", "available_quantity"} <= set(positions):
                raise ValueError("持仓CSV须含sleeve,code,quantity,available_quantity。")
            if not {"sleeve", "currency", "amount"} <= set(cash):
                raise ValueError("现金CSV须含sleeve,currency,amount。")
            target = work_path(args.out)
            if target.exists():
                raise ValueError("账户文件已存在，请使用新文件名。")
            account = {"schema_version": 1, "source": args.source, "as_of": args.as_of,
                       "positions": json.loads(positions.to_json(orient="records", double_precision=15)), "cash": json.loads(cash.to_json(orient="records", double_precision=15))}
            write_json(target, account)
            result = {"account": str(target.resolve()), "status": "awaiting_user_confirmation"}
        elif args.command == "review":
            result = review(read_config(args.config), read_json(args.account), read_json(args.request))
            out = new_output(args.out); out.mkdir(parents=True, exist_ok=True)
            write_json(out/"review.json", result)
            write_json(out/"confirmation.json", result["confirmation_template"])
            result = {"review": str(out/"review.json"), "confirmation": str(out/"confirmation.json"), "status": result["status"]}
        elif args.command == "quotes":
            from rebalancekit.quotes import extract_quotes
            rules = read_json(args.rules)
            result = extract_quotes(args.data, rules.keys(), args.as_of, rules)
            target = work_path(args.out)
            if target.exists():
                raise ValueError("报价文件已存在，请使用新文件名。")
            write_json(target, result)
            result = {"quotes": str(target.resolve()), "count": len(result["quotes"])}
        elif args.command == "demo":
            from rebalancekit.demo import historical_demo
            result = historical_demo(read_config(args.config), Dataset.load(args.data), args.data, args.signal_date, args.out)
        else:
            c = read_config(args.config)
            plan = make_plan(c, Dataset.load(args.data), read_json(args.account), read_json(args.request), read_json(args.quotes),
                             read_json(args.confirmation) if args.confirmation else None)
            result = export_plan(plan, c, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print("未生成调仓计划："+str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
