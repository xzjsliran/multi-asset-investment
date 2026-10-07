"""策略模块的统一入口。"""
import argparse
import json
from pathlib import Path
import sys

from strategykit.config import ROOT, data_plan, read_config, write_json
from strategykit.presets import CATALOG, preset


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")
    commands.add_parser("doctor")
    q = commands.add_parser("init")
    q.add_argument("--preset", choices=["friend-demo", "friend-qdii-demo", "classroom-demo", "macro-demo", "risk-parity-demo"], default="friend-demo")
    q.add_argument("--out", required=True)
    q = commands.add_parser("plan")
    q.add_argument("--config", required=True)
    q.add_argument("--out")
    q = commands.add_parser("prepare")
    q.add_argument("--config", required=True)
    q.add_argument("--data-kit", default=str(ROOT.parent / "quant-data-kit"))
    q.add_argument("--work", required=True)
    q.add_argument("--provider", choices=["free", "auto", "tushare"], default="auto")
    q.add_argument("--research")
    q.add_argument("--macro-data")
    q.add_argument("--cache", help="可选的共用缓存目录，便于中断后换输出目录续取")
    q = commands.add_parser("backtest")
    q.add_argument("--config", required=True)
    q.add_argument("--data", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--no-equal-comparison", action="store_true")
    a = p.parse_args()
    if a.command == "catalog":
        print(json.dumps(CATALOG, ensure_ascii=False, indent=2))
    elif a.command == "doctor":
        import importlib.metadata
        print(json.dumps({"python": sys.version.split()[0], "packages": {k: importlib.metadata.version(k) for k in ["numpy", "pandas", "scipy"]}}, ensure_ascii=False))
    elif a.command == "init":
        if Path(a.out).exists():
            raise ValueError("配置文件已经存在，请使用新文件名。")
        write_json(a.out, preset(a.preset))
        print(str(Path(a.out).resolve()))
    elif a.command == "plan":
        result = data_plan(read_config(a.config))
        if a.out:
            write_json(a.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif a.command == "prepare":
        from strategykit.prepare import prepare
        print(prepare(read_config(a.config), a.data_kit, a.work, a.provider, a.research, a.macro_data, a.cache))
    else:
        from strategykit.data import Dataset
        from strategykit.results import export_results
        summary, checks = export_results(read_config(a.config), Dataset.load(a.data), a.out, not a.no_equal_comparison)
        print(json.dumps({"summary": summary, "checks": checks, "results": str(Path(a.out).resolve()),
                          "handoff": str(Path(a.out).resolve() / "handoff.json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
