"""管理历史研究任务及其修订，沿用各模块的计算与文件接口。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_new(path, value):
    """完整写入后再发布任务文件。调用方已独占创建其输出目录。"""
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


METRICS = {
    "total_return": "累计收益",
    "annual_return": "年化收益",
    "annual_volatility": "年化波动",
    "max_drawdown": "最大回撤",
    "sharpe_zero_rf": "夏普比率（无风险利率为零）",
}
REQUIRED_FIELDS = ("objective", "metrics", "comparison")
DATA_FILES = (
    "calendar.csv", "prices.csv", "stock_features.csv", "macro_observations.csv",
    "strategy_data_manifest.json", "quality.json",
)
COMPLETED_RUNS = {"completed", "completed_with_notes"}


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("研究需求须为 JSON 对象。")
    unknown = set(request) - {*REQUIRED_FIELDS, "defaults", "specified"}
    if unknown:
        raise ValueError(f"未知研究需求字段：{'、'.join(sorted(unknown))}。")
    objective = request.get("objective")
    if objective is not None and (not isinstance(objective, str) or not objective.strip()):
        raise ValueError("objective 须为非空的研究目标。")
    metrics = request.get("metrics")
    if metrics is not None and (
        not isinstance(metrics, list)
        or not metrics
        or any(not isinstance(metric, str) or metric not in METRICS for metric in metrics)
        or len(metrics) != len(set(metrics))
    ):
        raise ValueError(f"metrics 须选用受支持且不重复的指标：{'、'.join(METRICS)}。")
    if request.get("comparison") not in (None, "standard", "main_only"):
        raise ValueError("comparison 须为 standard（包含实际可用的配套对照）或 main_only。")
    defaults = request.get("defaults", {})
    if not isinstance(defaults, dict) or any(
        not key.strip() or not isinstance(reason, str) or not reason.strip()
        for key, reason in defaults.items()
    ):
        raise ValueError("defaults 须列明使用的默认字段及其理由。")
    specified = request.get("specified", [])
    if not isinstance(specified, list) or any(not isinstance(key, str) or not key.strip() for key in specified):
        raise ValueError("specified 须列出用户明确提出的字段。")
    if set(specified) & defaults.keys():
        raise ValueError("同一字段不能同时列作用户明确提出和默认。")
    return {**request, "defaults": defaults, "specified": specified}


def check_work_dir(path, root):
    skill = Path(__file__).resolve().parents[1]
    code_dirs = [skill, *(root.glob("quant-*-kit"))]
    if path == root or any(path == code or code in path.parents for code in code_dirs):
        raise ValueError("请把研究任务及产物放在插件或源码目录之外。")


def save_task(path, config, request, root, parent=None, change=None):
    check_work_dir(path, root)
    request = validate_request(request)
    missing = [key for key in REQUIRED_FIELDS if request.get(key) is None]
    path.mkdir(parents=True, exist_ok=False)
    write_new(path / "strategy.json", config)
    task = {
        "schema_version": 1,
        "created_at": timestamp(),
        "status": "needs_input" if missing else "ready",
        "missing": missing,
        "request": request,
        "strategy_file": "strategy.json",
        "strategy_sha256": file_sha256(path / "strategy.json"),
        "parent_task": str(parent) if parent else None,
        "change": change,
    }
    write_new(path / "task.json", task)
    return task


def missing_costs(config, task):
    from strategykit.costs import cost_confirmation_needed, cost_question

    if cost_confirmation_needed(config) and "cost_rate" not in task["request"]["specified"]:
        return cost_question()
    return None


def load_task(path):
    task = read_json(path / "task.json")
    if task.get("schema_version") != 1 or task.get("strategy_file") != "strategy.json":
        raise ValueError("研究任务文件版本或策略文件名不受支持。")
    if file_sha256(path / "strategy.json") != task.get("strategy_sha256"):
        raise ValueError("任务中的策略文件已修改。请通过修订生成新任务。")
    request = validate_request(task["request"])
    missing = [key for key in REQUIRED_FIELDS if request.get(key) is None]
    task.update(status="needs_input" if missing else "ready", missing=missing)
    return task


def revise_task(parent, path, patch, root, allow_unchanged=False):
    """支持常见追问，尤其是只改一个调仓设置的受控对照。"""
    from strategykit.config import validate
    from strategykit.equity_selection import DEFAULT_POOL, METHOD

    old_task = load_task(parent)
    if not isinstance(patch, dict) or not patch:
        raise ValueError("修订须为非空 JSON 对象。")
    fields = ("start", "end", "allocation_rebalance", "transaction_costs")
    if set(patch) - {*fields, "request", "sleeve_rebalance", "sleeve_universe"}:
        raise ValueError("修订支持研究需求、起止日、股票池、调仓频率及交易费用。")
    request_changes = patch.get("request", {})
    if not isinstance(request_changes, dict):
        raise ValueError("request 修订须为 JSON 对象。")
    request = {**old_task["request"], **request_changes}
    defaults = request.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("defaults 须为 JSON 对象。")
    request["defaults"] = dict(defaults)
    specified = request.get("specified")
    if not isinstance(specified, list) or any(not isinstance(key, str) for key in specified):
        raise ValueError("specified 须列出用户明确提出的字段。")
    specified = list(specified)
    config = read_json(parent / "strategy.json")
    before = deepcopy(config)
    for key in fields:
        if key in patch:
            config[key] = patch[key]
            request["defaults"].pop(key, None)
            specified.append(key)
    if "transaction_costs" in patch:
        config.pop("cost_rate", None)
        request["defaults"].pop("cost_rate", None)
        specified = [key for key in specified if key != "cost_rate"]
    changes = patch.get("sleeve_rebalance", {})
    if not isinstance(changes, dict):
        raise ValueError("sleeve_rebalance 须以资产部分 id 为键。")
    if set(changes) - {sleeve["id"] for sleeve in config["sleeves"]}:
        raise ValueError("修订中有未知资产部分。")
    pools = patch.get("sleeve_universe", {})
    factor_sleeves = {sleeve["id"] for sleeve in config["sleeves"] if sleeve["selection"]["method"] == METHOD}
    if not isinstance(pools, dict) or set(pools) - factor_sleeves:
        raise ValueError("sleeve_universe 须以多因子股票部分的 id 为键。")
    sleeve_changes = {}
    for sleeve in config["sleeves"]:
        sid = sleeve["id"]
        if sid in changes and sleeve["rebalance"] != changes[sid]:
            key = f"sleeves.{sid}.rebalance"
            sleeve_changes[key] = {"from": sleeve["rebalance"], "to": changes[sid]}
            sleeve["rebalance"] = changes[sid]
            request["defaults"].pop(key, None)
            specified.append(key)
        old_pool = sleeve["selection"].get("universe", DEFAULT_POOL)
        if sid in pools and old_pool != pools[sid]:
            key = f"sleeves.{sid}.selection.universe"
            sleeve_changes[key] = {"from": old_pool, "to": pools[sid]}
            sleeve["selection"]["universe"] = pools[sid]
            request["defaults"].pop(key, None)
            specified.append(key)
    for key in REQUIRED_FIELDS:
        if key in request_changes and key not in request_changes.get("defaults", {}):
            request["defaults"].pop(key, None)
            if request_changes[key] is not None:
                specified.append(key)
    request["specified"] = sorted(set(specified) - request["defaults"].keys())
    config = validate(config)
    config_changes = {
        key: {"from": before.get(key), "to": config.get(key)}
        for key in fields if before.get(key) != config.get(key)
    }
    config_changes.update(sleeve_changes)
    if not config_changes:
        if allow_unchanged and (not request_changes or request == old_task["request"]):
            return None
        if request == old_task["request"]:
            raise ValueError("修订未改变研究任务。")
    change = {"strategy_fields": config_changes, "request_fields": sorted(request_changes)}
    return save_task(path, config, request, root, parent, change)


def input_fingerprints(data_path):
    return {name: file_sha256(data_path / name) for name in DATA_FILES if (data_path / name).is_file()}


def check_task(task_path, data_path, out=None, expected_data=None, require_benchmark=False):
    """预检和回测使用同一份已加载数据，避免重复读文件。"""
    from strategykit.config import read_config
    from strategykit.coverage import preview
    from strategykit.data import Dataset

    task = load_task(task_path)
    if task["missing"]:
        return {"status": "needs_input", "missing": task["missing"], "next_action": "clarify_research_request"}
    config = read_config(task_path / "strategy.json")
    if out and (issue := missing_costs(config, task)):
        return issue
    task_digest = file_sha256(task_path / "task.json")
    fingerprints = input_fingerprints(data_path) if out or expected_data is not None else {}
    if expected_data is not None and fingerprints != expected_data:
        changed = sorted(
            name for name in expected_data.keys() | fingerprints.keys()
            if expected_data.get(name) != fingerprints.get(name)
        )
        return {
            "status": "needs_input", "next_action": "restore_or_confirm_data",
            "message": "原数据版本已改变或文件缺失。请恢复原数据，或明确指定新的标准数据目录。",
            "changed_files": changed,
        }
    try:
        data = Dataset.load(data_path)
    except FileNotFoundError as exc:
        return {
            "status": "needs_input",
            "next_action": "supply_data",
            "message": f"标准数据缺少 {Path(exc.filename).name}，请补齐或运行现有数据准备命令。",
        }
    coverage = preview(config, data)
    if require_benchmark and any(item["code"] == "benchmark_coverage" for item in coverage["warnings"]):
        coverage.update(status="needs_input", next_action="supply_data")
        coverage["blockers"].append({
            "code": "selected_benchmark_coverage",
            "message": "本次比较包含原定市场基准，需补齐其行情。",
        })
    if out is None or coverage["status"] == "needs_input":
        return coverage
    if fingerprints != input_fingerprints(data_path):
        raise ValueError("加载期间数据发生变化，请用已保存的完整数据快照重试。")
    trace = {
        "task": str(task_path / "task.json"),
        "task_sha256": task_digest,
        "strategy_sha256": task["strategy_sha256"],
        "data": str(data_path),
        "input_fingerprints": fingerprints,
    }
    return run_task(out, config, data, coverage, task["request"], trace)


def run_task(out, config, data, coverage, request, trace):
    """复用策略引擎。run.json 在本次产物完整保存后写入。"""
    from strategykit.results import export_results

    out.mkdir(parents=True, exist_ok=False)
    result_dir = out / "backtest"
    try:
        summary, checks = export_results(config, data, result_dir)
    except (ValueError, RuntimeError, AssertionError, OSError) as exc:
        result = {
            "status": "backtest_failed",
            "message": str(exc),
            "next_action": "review_backtest_error",
        }
    else:
        handoff = read_json(result_dir / "handoff.json")
        execution = handoff["execution_review"]
        warnings = coverage["status"] != "ready" or execution["status"] != "ready"
        next_action = "review_limits_then_report" if warnings else "generate_report"
        if execution["status"] == "method_not_evaluated":
            next_action = "report_fallback_only"
        # 报告沿用任务单的指标和对照设定，宿主可直接调用。
        report_args = ["single", "--result", str(result_dir), "--out", str(out / "report")]
        report_args += ["--metrics", *request["metrics"]]
        if request["comparison"] == "main_only":
            report_args.append("--main-only")
        result = {
            "status": "completed_with_notes" if warnings else "completed",
            "next_action": next_action,
            "summary": summary,
            "checks": checks,
            "execution_review": execution,
            "results": str(result_dir),
            "handoff": str(result_dir / "handoff.json"),
            "report_args": report_args,
        }
    write_new(out / "coverage.json", coverage)
    result.update(trace, coverage=str(out / "coverage.json"), finished_at=timestamp())
    write_new(out / "run.json", result)
    return result


def check_run_output(out, task, data, root):
    check_work_dir(out, root)
    if any(out == path or out in path.parents or path in out.parents for path in (task, data)):
        raise ValueError("请为回测指定独立的输出目录。")


def main(argv, root):
    from continuity import add_commands, handle_command

    parser = argparse.ArgumentParser(prog="run.py research", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init", help="保存研究需求及其策略配置")
    create.add_argument("--request", required=True)
    create.add_argument("--config", required=True)
    revise = commands.add_parser("revise", help="保留旧任务，另存所要求的修订")
    revise.add_argument("--task", required=True)
    revise.add_argument("--changes", required=True)
    show = commands.add_parser("show", help="查看已保存的任务")
    show.add_argument("--task", required=True)
    check = commands.add_parser("preview", help="预检数据覆盖和策略样本")
    run = commands.add_parser("run", help="通过预检后运行回测并保存执行复核")
    for command in (check, run):
        command.add_argument("--task", required=True)
        command.add_argument("--data", required=True)
    for command in (create, revise, run):
        command.add_argument("--out", required=True)
    add_commands(commands)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(root / "quant-strategy-kit/scripts"))
    from strategykit.config import read_config

    if args.command == "init":
        out = Path(args.out).resolve()
        result = save_task(out, read_config(args.config), read_json(args.request), root)
        result["task_dir"] = str(out)
    elif args.command == "revise":
        out = Path(args.out).resolve()
        result = revise_task(Path(args.task).resolve(), out, read_json(args.changes), root)
        result["task_dir"] = str(out)
    elif args.command == "show":
        result = load_task(Path(args.task).resolve())
    elif args.command in {"preview", "run"}:
        task, data = Path(args.task).resolve(), Path(args.data).resolve()
        out = Path(args.out).resolve() if args.command == "run" else None
        if out is not None:
            check_run_output(out, task, data, root)
        result = check_task(task, data, out)
    else:
        result = handle_command(args, root)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result["status"] in {"backtest_failed", "failed"} else 0
