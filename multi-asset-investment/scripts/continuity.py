"""在同一研究目录接续已保存的任务，分开保存每版回测和报告。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import sys

from research import (
    COMPLETED_RUNS, check_run_output, check_task, check_work_dir,
    file_sha256, input_fingerprints, load_task, read_json, revise_task, timestamp, write_new,
)


TASK_FILE = "research-task.json"
REQUIRED_DATA = {"calendar.csv", "prices.csv"}


def add_commands(commands):
    track = commands.add_parser("track", help="保存一项研究，或接入已有的回测与比较")
    origin = track.add_mutually_exclusive_group(required=True)
    origin.add_argument("--task", action="append", help="已确认的任务目录，可重复")
    origin.add_argument("--run", action="append", help="已有的运行目录，可重复")
    track.add_argument("--data", help="新任务使用的标准数据目录")
    track.add_argument("--comparison", help="接入旧报告时使用的报告目录")
    resume = commands.add_parser("resume", help="读取当前研究、已有版本和待办事项")
    follow = commands.add_parser("continue", help="接续上次研究，可按明确的变更另存一版")
    follow.add_argument("--changes", help="沿用 research revise 的变更文件")
    follow.add_argument("--data", help="明确使用一个新的标准数据快照，供本版各方案使用")
    for command in (track, resume, follow):
        command.add_argument("--workspace", default=".", help="研究工作目录，默认当前目录")


@contextmanager
def workspace_lock(workspace):
    """同一研究一次只接受一项写操作，进程退出后系统自动释放锁。"""
    with (workspace / ".research-task.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("该研究正在更新，请等当前操作完成后接续。") from exc
        yield


def reference(workspace, path):
    """目录内的引用用相对路径，原数据在外部时保留实际路径。"""
    return str(path.relative_to(workspace) if path.is_relative_to(workspace) else path)


def next_directory(path):
    number, target = 1, path
    while target.exists() or target.is_symlink():
        number += 1
        target = path.with_name(f"{path.name}-{number}")
    return target


def task_record(workspace, task, data=None, fingerprints=None, run=None):
    request = load_task(task)
    return {
        "task": reference(workspace, task),
        "task_sha256": file_sha256(task / "task.json"),
        "data": reference(workspace, data) if data else None,
        "input_fingerprints": fingerprints,
        "data_use": "recorded" if run else "pending",
        "run": reference(workspace, run) if run else None,
        "run_status": None,
        "change": request.get("change"),
    }


def checked_task(workspace, source):
    directory = workspace / source["task"]
    path = directory / "task.json"
    if file_sha256(path) != source["task_sha256"]:
        raise ValueError(f"研究任务已修改，请恢复已确认的任务文件：{path}")
    return directory, load_task(directory)


def import_run(workspace, directory):
    run = read_json(directory / "run.json")
    if run["status"] not in COMPLETED_RUNS:
        raise ValueError(f"旧回测尚未完成，可接入原任务继续：{directory}")
    task_file = Path(run["task"])
    fingerprints = run["input_fingerprints"]
    if not isinstance(fingerprints, dict) or not REQUIRED_DATA <= fingerprints.keys():
        raise ValueError(f"原回测未记录完整的数据版本，请按原任务及标准数据重新运行：{directory}")
    source = task_record(
        workspace, task_file.parent, Path(run["data"]), fingerprints, directory,
    )
    if (
        source["task_sha256"] != run["task_sha256"]
        or file_sha256(task_file.parent / "strategy.json") != run["strategy_sha256"]
    ):
        raise ValueError(f"原任务或策略与运行记录不一致：{directory}")
    source["run_status"] = run["status"]
    return source


def comparison_config(workspace, sources, report=None):
    from reportkit.inputs import single_request
    from reportkit.reporting import validate_artifact

    results = {(workspace / source["run"] / "backtest").resolve(): key for key, source in sources.items()}
    if report:
        validate_artifact(report)
        config = read_json(report / "report_config.json")
        config.pop("report", None)  # 旧报告原件保存其署名、日期与出具信息。
    elif len(results) == 1:
        path, key = next(iter(results.items()))
        request = load_task(workspace / sources[key]["task"])["request"]
        config = single_request(path, main_only=request["comparison"] == "main_only")
        config["metrics"] = request["metrics"]
    else:
        config = {"schema_version": 1, "alignment": "common", "strategies": []}
        for path, key in results.items():
            main = single_request(path, main_only=True)["strategies"][0]
            config["strategies"].append({**main, "id": key})
        config["metrics"] = list(dict.fromkeys(
            metric for source in sources.values()
            for metric in load_task(workspace / source["task"])["request"]["metrics"]
        ))

    comparison_base = report or workspace
    for item in config["strategies"]:
        path = (comparison_base / item.pop("result_dir")).resolve()
        if path not in results:
            raise ValueError(f"请一并接入报告所用的原回测：{path}")
        item.setdefault("scenario", "strategy")
        item["source"] = results[path]
    if {item["source"] for item in config["strategies"]} != set(sources):
        raise ValueError("请只接入原报告所比较的回测。")
    return config


def save(workspace, study):
    study["revisions"][-1]["updated_at"] = timestamp()
    write_new(workspace / TASK_FILE, study)


def read_study(path):
    study = read_json(path)
    if study.get("schema_version") != 1 or not study.get("revisions"):
        raise ValueError("研究任务单版本不受支持，或没有可接续的记录。")
    return study


def add_revision(workspace, study, sources, comparison=None, changes=None, report=None, directory=None):
    previous = study["revisions"]
    number = len(previous) + 1
    version = {
        "version": number, "parent": previous[-1]["version"] if previous else None,
        "directory": reference(workspace, directory) if directory else None,
        "sources": sources, "comparison": comparison, "changes": changes,
        "report": reference(workspace, report) if report else None,
        "stage": "tasks_saved", "status": "ready", "issue": None,
    }
    if all(source["run_status"] in COMPLETED_RUNS for source in sources.values()):
        version["stage"] = "backtests_saved"
    if report:
        version.update(stage="report_saved", status=completed_status(sources))
    previous.append(version)
    save(workspace, study)
    return version


def completed_status(sources):
    return "completed_with_notes" if any(
        source["run_status"] == "completed_with_notes" for source in sources.values()
    ) else "completed"


def track(workspace, tasks=(), runs=(), data=None, report=None):
    if (workspace / TASK_FILE).exists():
        raise ValueError("该目录已有研究任务单。请接续这项研究，或在独立目录记录另一项研究。")
    if (data and runs) or (report and not runs):
        raise ValueError("接入旧回测时沿用运行记录中的数据，--comparison 用于接入旧报告。")
    origins = [Path(path).resolve() for path in (runs or tasks)]
    if len(set(origins)) != len(origins):
        raise ValueError("同一个回测或任务只需接入一次。")
    if any(workspace == path or path in workspace.parents for path in origins):
        raise ValueError("请选择独立的研究工作目录，避免在已有任务或回测产物中接续。")
    if data and (data == workspace or data in workspace.parents):
        raise ValueError("请将标准数据放在独立目录，例如研究目录下的 data/。")
    sources = {}
    for number, path in enumerate(origins, 1):
        sources[f"run{number}"] = (
            import_run(workspace, path) if runs else task_record(workspace, path, data)
        )
    if len(origins) == 1 and runs and report is None:
        standard_report = origins[0] / "report"
        if (standard_report / "manifest.json").is_file():
            report = standard_report
    comparison = comparison_config(workspace, sources, report) if runs else None
    study = {"schema_version": 1, "revisions": []}
    add_revision(workspace, study, sources, comparison, report=report)
    return study


def status(workspace, study):
    version = study["revisions"][-1]
    history = [
        {key: earlier[key] for key in ("version", "stage", "status", "report", "changes")}
        for earlier in study["revisions"]
    ]
    conditions = {}
    for key, source in version["sources"].items():
        task, details = checked_task(workspace, source)
        config = read_json(task / "strategy.json")
        conditions[key] = {
            "request": details["request"],
            "start": config["start"], "end": config["end"],
            "allocation_rebalance": config["allocation_rebalance"],
            "sleeve_rebalance": {sleeve["id"]: sleeve["rebalance"] for sleeve in config["sleeves"]},
        }
    issue = version["issue"]
    next_action = issue["next_action"] if issue else (
        "review_report" if version["stage"] == "report_saved" else "continue_research"
    )
    return {"workspace": str(workspace), "task_file": str(workspace / TASK_FILE),
            **version, "next_action": next_action, "conditions": conditions, "history": history}


def pending(workspace, study, issue):
    study["revisions"][-1].update(status=issue["status"], issue=issue)
    save(workspace, study)
    return status(workspace, study)


def version_directory(workspace, number):
    version_root = workspace / "research-versions"
    if version_root.is_symlink():
        raise ValueError("请在研究工作目录内保存 research-versions。")
    return next_directory(version_root / f"{number:03d}")


def use_data(workspace, sources, data):
    path, fingerprints = data
    for source in sources.values():
        source.update(data=reference(workspace, path), input_fingerprints=fingerprints, data_use="provided")


def prepare_revision(workspace, study, changes, data, root):
    previous = study["revisions"][-1]
    sources = deepcopy(previous["sources"])
    directory = version_directory(workspace, previous["version"] + 1)
    changed = False
    for key, source in sources.items():
        parent, _ = checked_task(workspace, source)
        if changes is not None:
            target = directory / f"{key}-task"
            if revise_task(parent, target, changes, root, allow_unchanged=True):
                replacement = task_record(workspace, target)
                source.update({field: replacement[field] for field in ("task", "task_sha256", "change")})
                changed = True
            else:
                source["change"] = None
        source.update(run=None, run_status=None, data_use="pending")
    if not changed and (data is None or all(
        source["input_fingerprints"] == data[1] for source in sources.values()
    )):
        raise ValueError("本次没有改变研究设定或数据版本。")
    if data:
        use_data(workspace, sources, data)

    comparison = deepcopy(previous["comparison"])
    request_changes = (changes or {}).get("request", {})
    if request_changes.get("comparison") == "standard" and len(sources) == 1:
        comparison = None  # 用户改了对照范围，按新任务和实际回测情景选择。
    if comparison:
        if request_changes.get("comparison") == "main_only":
            primary = [item for item in comparison["strategies"] if item["scenario"] == "strategy"]
            if len({item["source"] for item in primary}) == len(sources):
                comparison["strategies"] = primary
            else:
                comparison = None  # 由新结果补上此前没有选入的主方案。
    if comparison:
        comparison.pop("title", None)  # 原报告标题可能含已改变的频率或日期。
        for key in ("start", "end"):
            if key in (changes or {}) and key in comparison:
                comparison[key] = changes[key]
        if "metrics" in request_changes:
            comparison["metrics"] = request_changes["metrics"]
    return add_revision(workspace, study, sources, comparison, changes, directory=directory)


def record_data_versions(workspace, sources):
    """同一路径下尚未冻结的任务同时取得同一份指纹。"""
    snapshots = {}
    for source in sources.values():
        if source["data"] is None or source["input_fingerprints"] is not None:
            continue
        path = (workspace / source["data"]).resolve()
        if path not in snapshots:
            snapshots[path] = input_fingerprints(path)
        if REQUIRED_DATA <= snapshots[path].keys():
            source["input_fingerprints"] = snapshots[path]


def run_source(workspace, study, source, key, root):
    version = study["revisions"][-1]
    task, request = checked_task(workspace, source)
    if request["missing"]:
        return {"status": "needs_input", "next_action": "clarify_research_request", "missing": request["missing"]}
    if source["run_status"] in COMPLETED_RUNS:
        if (workspace / source["run"] / "backtest" / "handoff.json").is_file():
            return None
        # 已登记的产物可能被移走。用任务和同一数据重新生成，保留已有文件。
        source.update(run_status=None, run=None)
        version.update(stage="tasks_saved", report=None)
    if source["data"] is None:
        return {"status": "needs_input", "next_action": "supply_data", "message": "请提供标准数据目录。"}
    data = (workspace / source["data"]).resolve()
    planned = source["run"]
    run = (workspace / planned) if planned else None
    # 进程可能在已写完 run.json、尚未登记时退出。先检查这份已完成的结果。
    if run and (run / "run.json").is_file():
        result = read_json(run / "run.json")
        if (
            result["status"] in COMPLETED_RUNS
            and result["task_sha256"] == source["task_sha256"]
            and result["input_fingerprints"] == source["input_fingerprints"]
        ):
            source["run_status"] = result["status"]
            save(workspace, study)
            return None
    expected = source["input_fingerprints"]
    if expected is None:
        return {
            "status": "needs_input", "next_action": "supply_data",
            "missing": sorted(name for name in REQUIRED_DATA if not (data / name).is_file()),
        }
    directory = workspace / version["directory"]
    out = next_directory(directory / key)
    check_run_output(out, task.resolve(), data, root)
    # 把即将使用的文件版本和产物目录先记下来，重启后可以找到这次运行。
    source["run"] = reference(workspace, out)
    if source["data_use"] in {"pending", "unavailable"}:
        source["data_use"] = "initial" if version["parent"] is None else "reused"
    save(workspace, study)
    required = (version["comparison"] or {}).get("strategies", [])
    benchmark = any(item["source"] == key and item["scenario"] == "benchmark" for item in required)
    result = check_task(task, data, out, expected_data=expected, require_benchmark=benchmark)
    if result["status"] in COMPLETED_RUNS:
        source["run_status"] = result["status"]
        save(workspace, study)
        return None
    if not out.exists():
        source["run"] = None
    if result.get("next_action") == "restore_or_confirm_data":
        source["data_use"] = "unavailable"
    return result


def build_report(workspace, study, root):
    from reportkit.analysis import analyse
    from reportkit.exports import export_report
    from reportkit.inputs import load_comparison
    from reportkit.reporting import validate_artifact

    version = study["revisions"][-1]
    records = version["sources"]
    if version["comparison"] is None:
        version["comparison"] = comparison_config(workspace, records)
    config = deepcopy(version["comparison"])
    for item in config["strategies"]:
        source = records[item.pop("source")]
        result = workspace / source["run"] / "backtest"
        available = {scenario["id"] for scenario in read_json(result / "handoff.json")["scenarios"]}
        if item["scenario"] not in available:
            raise ValueError(f"本次结果缺少原定对照「{item['label']}」，请检查该对照的数据覆盖。")
        item["result_dir"] = str(result.resolve())
    report = workspace / version["report"] if version["report"] else None
    if report and (report / "manifest.json").is_file():
        try:
            validate_artifact(report)
        except (ValueError, OSError, KeyError):
            pass  # 保留不完整的报告目录，使用新目录重试。
        else:
            return
    report = next_directory(workspace / version["directory"] / "report")
    version["report"] = reference(workspace, report)
    save(workspace, study)
    runs = load_comparison(config, workspace)
    template = root / "quant-report-kit/assets/report-template.html"
    export_report(analyse(config, runs), config, report, template)
    validate_artifact(report)


def continue_study(workspace, study, changes, data, root):
    version = study["revisions"][-1]
    if version["stage"] == "report_saved" and changes is None and data is None:
        return status(workspace, study)
    new_settings = changes is not None and changes != version["changes"]
    new_snapshot = data is not None and any(
        source["run_status"] in COMPLETED_RUNS and source["input_fingerprints"] != data[1]
        for source in version["sources"].values()
    )
    if new_settings or new_snapshot or version["stage"] == "report_saved":
        version = prepare_revision(workspace, study, changes, data, root)
    elif data:
        use_data(workspace, version["sources"], data)
    if version["directory"] is None:
        directory = version_directory(workspace, version["version"])
        version["directory"] = reference(workspace, directory)
    record_data_versions(workspace, version["sources"])
    version.update(status="running", issue=None)
    save(workspace, study)
    for key, source in version["sources"].items():
        try:
            issue = run_source(workspace, study, source, key, root)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            issue = {"status": "failed", "next_action": "review_backtest_error", "message": str(exc)}
        if issue:
            return pending(workspace, study, {**issue, "source": key, "data": source["data"]})
    version["stage"] = "backtests_saved"
    save(workspace, study)
    try:
        build_report(workspace, study, root)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        return pending(workspace, study, {"status": "failed", "next_action": "review_report_error", "message": str(exc)})
    version.update(stage="report_saved", status=completed_status(version["sources"]))
    save(workspace, study)
    return status(workspace, study)


def legacy_runs(workspace):
    """仅查看研究目录的直接子目录。需要选择时列出有完整运行记录的回测。"""
    candidates = []
    for path in workspace.glob("*/run.json"):
        if not path.resolve().is_relative_to(workspace):
            continue
        try:
            run = read_json(path)
            if run["status"] in COMPLETED_RUNS:
                candidates.append(reference(workspace, path.parent))
        except (ValueError, OSError, KeyError):
            continue
    return sorted(candidates)


def handle_command(args, root):
    sys.path.insert(0, str(root / "quant-report-kit/scripts"))
    workspace = Path(args.workspace).resolve()
    check_work_dir(workspace, root)
    path = workspace / TASK_FILE
    if path.is_symlink():
        raise ValueError("请使用研究目录内的实际任务单文件。")
    if args.command == "resume":
        if not path.exists():
            return {"status": "untracked", "workspace": str(workspace), "candidates": legacy_runs(workspace)}
        return status(workspace, read_study(path))
    with workspace_lock(workspace):
        if args.command == "track":
            data = Path(args.data).resolve() if args.data else None
            report = Path(args.comparison).resolve() if args.comparison else None
            return status(workspace, track(workspace, args.task, args.run, data, report))
        changes = read_json(args.changes) if args.changes else None
        if args.changes and (not isinstance(changes, dict) or not changes):
            raise ValueError("修订须为非空 JSON 对象。")
        data = None
        if args.data:
            directory = Path(args.data).resolve()
            if directory == workspace or directory in workspace.parents:
                raise ValueError("请将标准数据放在独立目录，例如研究目录下的 data/。")
            fingerprints = input_fingerprints(directory)
            if not REQUIRED_DATA <= fingerprints.keys():
                raise ValueError("新数据目录需要 calendar.csv 和 prices.csv。")
            data = (directory, fingerprints)
        if path.exists():
            study = read_study(path)
        else:
            candidates = legacy_runs(workspace)
            if len(candidates) != 1:
                return {"status": "untracked", "workspace": str(workspace), "candidates": candidates}
            study = track(workspace, runs=[workspace / candidates[0]])
        return continue_study(workspace, study, changes, data, root)
