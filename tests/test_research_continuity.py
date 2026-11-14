"""通过独立的命令进程验证跨会话的真实续做路径。"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_research_workflow import RUNNER, fixture, read_json, write_json


def hashes(directory):
    return {path.relative_to(directory): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


class ResearchContinuity(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="multi-asset-continuity-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        fixture(self.path)
        write_json(self.path / "monthly.json", {"allocation_rebalance": "monthly"})

    def cli(self, *args, error=None):
        result = subprocess.run(
            [sys.executable, str(RUNNER), *map(str, args)], cwd=self.path,
            capture_output=True, text=True, timeout=30,
        )
        if error:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            return None
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def task(self, target="task-old", config="strategy.json"):
        self.cli("research", "init", "--config", config, "--request", "request.json", "--out", target)

    def old_run(self, task="task-old", out="run-old", report=True):
        run = self.cli("research", "run", "--task", task, "--data", "normal", "--out", out)
        if report:
            self.cli("report", *run["report_args"])
        return run

    def test_resume_requests_costs_then_keeps_monthly_revision_and_opening_fees(self):
        cfg = read_json(self.path / "strategy.json")
        cfg.pop("transaction_costs")
        write_json(self.path / "implicit.json", {**cfg, "cost_rate": 0})
        self.task(config="implicit.json")
        self.cli("research", "track", "--task", "task-old", "--data", "normal")
        self.assertTrue(self.cli("research", "resume")["conditions"]["run1"]["cost_confirmation_needed"])
        blocked = self.cli("research", "continue", "--changes", "monthly.json")
        self.assertEqual(blocked["next_action"], "confirm_transaction_costs")
        self.assertIsNone(blocked["sources"]["run1"]["run"])
        self.assertIsNone(blocked["report"])
        self.assertIn("simulation_reference", blocked["issue"]["simulation_examples"])
        write_json(self.path / "confirmed.json", {"transaction_costs": {"commission_rate": .00015, "slippage_rate": .00015}})
        done = self.cli("research", "continue", "--changes", "confirmed.json")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["conditions"]["run1"]["allocation_rebalance"], "monthly")
        path = self.path / done["report"]
        report = read_json(path / "report_data.json")
        self.assertIn("建仓前本金", report["period"]["anchor"])
        for item in report["strategies"]:
            self.assertGreater(item["detail"]["transaction_cost"], 0)
            self.assertLess(next(row["nav"] for row in report["curves"] if row["strategy_id"] == item["id"]), 1)
        self.cli("report", "check", "--report-dir", path)
        repeated = self.cli("research", "continue", "--changes", "confirmed.json")
        self.assertEqual(repeated["report"], done["report"])

    def test_fresh_session_continues_one_run_with_two_selected_scenarios(self):
        self.task()
        old = self.old_run()
        before = {name: hashes(self.path / name) for name in ("task-old", "normal", "run-old")}
        untracked = self.cli("research", "resume")
        self.assertEqual(untracked["candidates"], ["run-old"])
        self.assertFalse((self.path / "research-task.json").exists())
        report = self.cli("research", "continue", "--changes", "monthly.json")
        resumed = self.cli("research", "resume")
        self.assertEqual(resumed, report)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["stage"], "report_saved")
        self.assertEqual(resumed["version"], 2)
        self.assertEqual(resumed["parent"], 1)
        self.assertEqual(resumed["history"][0]["report"], "run-old/report")
        source = resumed["sources"]["run1"]
        self.assertEqual(source["data_use"], "reused")
        self.assertEqual(source["input_fingerprints"], old["input_fingerprints"])
        self.assertEqual(source["change"]["strategy_fields"], {
            "allocation_rebalance": {"from": "quarterly", "to": "monthly"},
        })
        self.assertEqual(resumed["conditions"]["run1"]["sleeve_rebalance"], {"a": "quarterly", "b": "quarterly"})
        config = read_json(self.path / source["task"] / "strategy.json")
        self.assertEqual(config, {
            **read_json(self.path / "task-old/strategy.json"), "allocation_rebalance": "monthly",
        })
        report_dir = self.path / resumed["report"]
        self.cli("report", "check", "--report-dir", report_dir)
        report_config = read_json(report_dir / "report_config.json")
        self.assertEqual(report_config["metrics"], ["max_drawdown"])
        self.assertEqual([item["scenario"] for item in report_config["strategies"]], ["strategy", "static"])
        self.assertEqual(before, {name: hashes(self.path / name) for name in before})
        self.assertEqual(resumed, self.cli("research", "continue"))

    def test_explicit_old_comparison_repeats_both_independent_strategies(self):
        self.task()
        first = self.old_run(report=False)
        original = read_json(self.path / "strategy.json")
        write_json(self.path / "fixed.json", {**original, "name": "多资产固定比例策略", "allocation": {"method": "fixed"}})
        self.task("task-fixed", "fixed.json")
        second = self.old_run("task-fixed", "run-fixed", report=False)
        write_json(self.path / "compare.json", {
            "schema_version": 1, "metrics": ["max_drawdown"], "title": "季度调仓配置比较",
            "report": {"report_id": "Q-001", "reviewer": "原版复核人"},
            "strategies": [
                {"id": "fixed", "label": "固定比例方案", "result_dir": second["results"]},
                {"id": "risk", "label": "风险平价方案", "result_dir": first["results"]},
            ],
        })
        self.cli("report", "build", "--config", "compare.json", "--out", "old-comparison")
        ambiguous = self.cli("research", "continue", "--changes", "monthly.json")
        self.assertEqual(ambiguous["status"], "untracked")
        self.assertEqual(ambiguous["candidates"], ["run-fixed", "run-old"])
        self.cli("research", "track", "--run", "run-old", "--run", "run-fixed", "--comparison", "old-comparison")
        done = self.cli("research", "continue", "--changes", "monthly.json")
        cfg = read_json(self.path / done["report"] / "report_config.json")
        self.assertEqual([item["id"] for item in cfg["strategies"]], ["fixed", "risk"])
        self.assertEqual([item["label"] for item in cfg["strategies"]], ["固定比例方案", "风险平价方案"])
        self.assertNotIn("report", cfg)
        self.assertNotIn("title", cfg)
        self.assertEqual(len({item["result_dir"] for item in cfg["strategies"]}), 2)
        for item in cfg["strategies"]:
            actual = read_json(Path(item["result_dir"]) / "strategy.json")
            self.assertEqual(actual["allocation_rebalance"], "monthly")

    def test_data_replacement_requires_explicit_choice_and_saved_patch_survives(self):
        self.task()
        old = self.old_run()
        normal = self.path / "normal"
        calendars = (normal / "calendar.csv").read_bytes()
        (normal / "calendar.csv").write_bytes(calendars + b"\n")
        blocked = self.cli("research", "continue", "--changes", "monthly.json")
        self.assertEqual(blocked["status"], "needs_input")
        self.assertEqual(blocked["issue"]["changed_files"], ["calendar.csv"])
        self.assertEqual(blocked["issue"]["next_action"], "restore_or_confirm_data")
        self.assertIsNone(blocked["sources"]["run1"]["run"])
        self.assertEqual(blocked["sources"]["run1"]["input_fingerprints"], old["input_fingerprints"])
        self.assertEqual(self.cli("research", "resume")["changes"], {"allocation_rebalance": "monthly"})
        outcome = self.cli("research", "continue", "--data", "normal")
        self.assertEqual(outcome["version"], 2)
        self.assertEqual(outcome["status"], "completed")
        source = outcome["sources"]["run1"]
        self.assertEqual(source["data_use"], "provided")
        self.assertNotEqual(source["input_fingerprints"], old["input_fingerprints"])

    def test_precheck_blocks_extended_period_and_continues_after_changed_dates(self):
        self.task()
        self.old_run()
        write_json(self.path / "longer.json", {"end": "2025-10-01", "allocation_rebalance": "monthly"})
        blocked = self.cli("research", "continue", "--changes", "longer.json")
        self.assertEqual(blocked["stage"], "tasks_saved")
        self.assertEqual(blocked["status"], "needs_input")
        self.assertIn("incomplete_calendar", {item["code"] for item in blocked["issue"]["blockers"]})
        self.assertEqual(self.cli("research", "resume")["issue"], blocked["issue"])
        write_json(self.path / "shorter.json", {"end": "2025-07-01"})
        after = self.cli("research", "continue", "--changes", "shorter.json")
        self.assertEqual(after["parent"], blocked["version"])
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["conditions"]["run1"]["allocation_rebalance"], "monthly")
        self.assertEqual(after["conditions"]["run1"]["end"], "2025-07-01")

    def test_new_monthly_financial_signal_needs_point_in_time_snapshot(self):
        cfg = read_json(self.path / "strategy.json")
        cfg["sleeves"][0]["selection"] = {"method": "fundamental", "top_n": 1}
        cfg["sleeves"][0]["assets"][0]["kind"] = "stock"
        cfg["allocation"] = {"method": "fixed"}
        write_json(self.path / "fundamental.json", cfg)
        cols = ["code", "snapshot_date", "ann_date", "annual_ann_date", "end_date",
                "annual_end_date", "list_date", "delist_date", "consecutive_years", "historical_name", "name", "pe_ttm"]
        rows = [
            ["ETF_A", day, "2024-08-15", "2024-04-20", "2024-06-30", "2023-12-31",
             "2010-01-01", "", "true", "甲公司", "甲公司", "15"]
            for day in ("2024-12-31", "2025-03-31", "2025-06-30")
        ]
        data = self.path / "normal/stock_features.csv"
        data.write_text("\n".join(",".join(row) for row in [cols, *rows]) + "\n", encoding="utf-8")
        self.task(config="fundamental.json")
        self.old_run()
        write_json(self.path / "inner.json", {"sleeve_rebalance": {"a": "monthly"}})
        blocked = self.cli("research", "continue", "--changes", "inner.json")
        self.assertEqual(blocked["status"], "needs_input")
        self.assertEqual(blocked["conditions"]["run1"]["allocation_rebalance"], "quarterly")
        issue = next(item for item in blocked["issue"]["blockers"] if item["code"] == "missing_fundamental_snapshot")
        self.assertEqual(issue["signal_date"], "2025-01-31")

    def test_completed_first_backtest_is_reused_after_second_snapshot_is_restored(self):
        self.task()
        self.old_run(report=False)
        cfg = read_json(self.path / "strategy.json")
        write_json(self.path / "fixed.json", {**cfg, "name": "固定比例配置", "allocation": {"method": "fixed"}})
        self.task("task-fixed", "fixed.json")
        shutil.copytree(self.path / "normal", self.path / "fixed-data")
        data = self.path / "fixed-data/prices.csv"
        original = data.read_bytes()
        self.cli("research", "run", "--task", "task-fixed", "--data", "fixed-data", "--out", "run-fixed")
        self.cli("research", "track", "--run", "run-old", "--run", "run-fixed")
        data.unlink()
        blocked = self.cli("research", "continue", "--changes", "monthly.json")
        self.assertEqual(blocked["issue"]["source"], "run2")
        self.assertEqual(blocked["issue"]["changed_files"], ["prices.csv"])
        first = self.path / blocked["sources"]["run1"]["run"]
        contents = hashes(first)
        data.write_bytes(original)
        done = self.cli("research", "continue", "--changes", "monthly.json")
        self.assertEqual(done["version"], blocked["version"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(hashes(first), contents)
        self.assertEqual(done["sources"]["run2"]["data_use"], "reused")

    def test_tracks_before_data_and_resumes_after_workspace_move(self):
        self.task()
        tracked = self.cli("research", "track", "--task", "task-old")
        self.assertEqual(tracked["stage"], "tasks_saved")
        blocked = self.cli("research", "continue")
        self.assertEqual(blocked["issue"]["next_action"], "supply_data")
        done = self.cli("research", "continue", "--data", "normal")
        self.assertEqual(done["version"], 1)
        self.assertEqual(done["status"], "completed")
        moved = self.path / "moved"
        moved.mkdir()
        for name in ("research-task.json", "research-versions", "task-old", "normal"):
            shutil.move(self.path / name, moved / name)
        after = self.cli("research", "continue", "--workspace", moved, "--changes", "monthly.json")
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["sources"]["run1"]["data_use"], "reused")
        self.assertTrue((moved / after["report"] / "报告.html").is_file())

    def test_restores_completed_run_and_report_after_interrupted_checkpoint(self):
        self.task()
        self.cli("research", "track", "--task", "task-old", "--data", "normal")
        done = self.cli("research", "continue")
        path = self.path / "research-task.json"
        interrupted = read_json(path)
        version = interrupted["revisions"][-1]
        report = self.path / version["report"]
        before = hashes(report)
        version.update(stage="tasks_saved", status="running")
        version["sources"]["run1"]["run_status"] = None
        write_json(path, interrupted)
        resumed = self.cli("research", "continue")
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["report"], done["report"])
        self.assertEqual(resumed["sources"]["run1"]["run"], done["sources"]["run1"]["run"])
        self.assertEqual(hashes(report), before)
