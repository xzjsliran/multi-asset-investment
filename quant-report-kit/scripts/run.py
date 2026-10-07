#!/usr/bin/env python3
"""报告模块入口。真实结果目录与生成文件均放在kit以外。"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys

import pandas as pd

from reportkit.inputs import SCENARIOS, load_request, load_run, read_json, validate_daily, write_json
from reportkit.analysis import analyse
from reportkit.exports import add_commentary, export_report
from reportkit.reporting import default_name, INTERNAL, validate_artifact

KIT = Path(__file__).resolve().parents[1]


def safe_output(out, inputs=()):
    target = Path(out).resolve()
    for protected in [KIT, *(Path(x).resolve() for x in inputs)]:
        if target == protected or protected in target.parents:
            raise ValueError("请把生成结果放在kit和输入结果目录之外。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError(f"输出位置已有内容，请另选新目录：{target}")
    return target


def single_config(args):
    path = Path(args.result).resolve()
    h = read_json(path/"handoff.json")
    labels = {s["id"]: s["label"] for s in h["scenarios"]}
    selected = ["strategy"] if args.main_only else [s for s in SCENARIOS if s in labels]
    name = args.name or h["strategy_name"]
    if not args.name and INTERNAL.search(name):
        name = default_name(read_json(path/"strategy.json"))
    scenario_names = {"equal_weight":"组合内等权对照", "static":"固定权重对照", "benchmark":"市场基准"}
    c = {"schema_version": 1, "title": args.title or name+"回测报告", "alignment": "common",
         "strategies": [{"id": s, "label": name if s=="strategy" else scenario_names[s], "result_dir": str(path), "scenario": s} for s in selected]}
    if args.metadata:
        c["report"] = read_json(args.metadata)
    if args.start: c["start"]=args.start
    if args.end: c["end"]=args.end
    return c, [load_run(spec, path.parent) for spec in c["strategies"]]


def import_nav(args):
    src = Path(args.csv).resolve()
    out = safe_output(args.out, [src])
    raw = pd.read_csv(src)
    if args.date_column not in raw or args.nav_column not in raw:
        raise ValueError("输入文件没有指定的日期列/净值列。")
    daily = validate_daily(raw[[args.date_column, args.nav_column]].rename(columns={args.date_column:"date", args.nav_column:"nav"}), args.name)
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    out.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out/"daily.csv", index=False, encoding="utf-8-sig")
    write_json(out/"strategy.json", {"name":args.name, "base_currency":args.currency, "sleeves":[], "source_type":"imported_nav"})
    write_json(out/"handoff.json", {"schema_version":1, "producer":"quant-report-kit import-nav", "strategy_name":args.name,
        "base_currency":args.currency, "scenarios":[{"id":"strategy", "label":args.name}],
        "files":[{"path":"daily.csv", "format":"csv", "rows":len(daily), "columns":list(daily.columns)}]})
    write_json(out/"manifest.json", {"source_file":str(src), "source_sha256":hashlib.sha256(src.read_bytes()).hexdigest(), "notes":["导入的是已剔除外部资金进出的同币种策略净值；未提供持仓、成交和决策明细。"]})
    return {"result_dir":str(out), "rows":len(daily)}


def main():
    parser = argparse.ArgumentParser(description="量化结果分析与报告：单策略、多策略比较、外部净值导入")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("single", help="分析一份回测，默认包括其内置对照")
    p.add_argument("--result", required=True); p.add_argument("--out", required=True)
    p.add_argument("--name"); p.add_argument("--title"); p.add_argument("--start"); p.add_argument("--end")
    p.add_argument("--main-only", action="store_true"); p.add_argument("--commentary")
    p.add_argument("--metadata", help="报告机构、署名、版本等实际元信息JSON，可选")
    p = sub.add_parser("build", help="按JSON配置比较任意多个结果")
    p.add_argument("--config", required=True); p.add_argument("--out", required=True); p.add_argument("--commentary")
    p = sub.add_parser("inspect", help="检查结果目录可用于报告的内容")
    p.add_argument("--result", required=True)
    p = sub.add_parser("check", help="复核已生成报告的文件指纹与内容规范")
    p.add_argument("--report-dir", required=True)
    p = sub.add_parser("rebalance", help="将调仓组件输出生成组合调仓计划报告")
    p.add_argument("--plan", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("import-nav", help="把其他工具的策略净值表接入统一比较")
    p.add_argument("--csv", required=True); p.add_argument("--name", required=True); p.add_argument("--currency", required=True)
    p.add_argument("--date-column", default="date"); p.add_argument("--nav-column", default="nav"); p.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "rebalance":
            from reportkit.rebalance import export_rebalance
            result = export_rebalance(args.plan, safe_output(args.out, [args.plan]))
        elif args.command == "check":
            result = validate_artifact(args.report_dir)
        elif args.command == "inspect":
            path = Path(args.result).resolve(); h = read_json(path/"handoff.json")
            result = {"name":h["strategy_name"], "scenarios":h["scenarios"], "checks":[]}
            for s in h["scenarios"]:
                r = load_run({"id":s["id"],"result_dir":str(path),"scenario":s["id"]}, path.parent)
                result["checks"].append({"scenario":r.scenario,"rows":len(r.daily),"start":str(r.daily["date"].iloc[0].date()),"end":str(r.daily["date"].iloc[-1].date()),"tables":list(r.tables)})
        elif args.command == "import-nav":
            result = import_nav(args)
        else:
            config, runs = single_config(args) if args.command == "single" else load_request(args.config)
            out = safe_output(args.out, [r.path for r in runs])
            # 保存绝对输入路径，复制报告配置后仍可重跑。
            for spec, run in zip(config["strategies"], runs): spec["result_dir"] = str(run.path)
            data = add_commentary(analyse(config,runs), read_json(args.commentary) if args.commentary else None)
            result = export_report(data, config, out, KIT/"assets/report-template.html")
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f"未生成报告：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
