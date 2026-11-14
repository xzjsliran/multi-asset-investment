"""通过插件入口测试任务、覆盖、回退和证据交接。"""
import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "multi-asset-investment/scripts/run.py"


def write_json(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(directory):
    """离线样例同时覆盖有足够样本、短历史和全程回退。"""
    dates = pd.bdate_range("2024-07-01", "2025-07-31")
    rng = np.random.default_rng(8)
    price = 100 * np.exp(np.cumsum(rng.normal(0, [.004, .02], (len(dates), 2)), axis=0))
    prices = pd.DataFrame(price, index=dates, columns=["ETF_A", "ETF_B"])
    config = {
        "schema_version": 1,
        "name": "多资产风险平价策略",
        "start": "2025-01-01",
        "end": "2025-07-31",
        "allocation_rebalance": "quarterly",
        "transaction_costs": {"commission_rate": 0., "slippage_rate": 0., "stamp_duty_rate": 0.,
                              "by_kind": {}, "stamp_duty_schedule": []},
        "sleeves": [
            {"id": "a", "name": "债券", "weight": .5, "assets": [{"code": "ETF_A", "kind": "etf"}]},
            {"id": "b", "name": "权益", "weight": .5, "assets": [{"code": "ETF_B", "kind": "etf"}]},
        ],
        "allocation": {
            "method": "risk_parity",
            "proxies": {
                "a": [{"code": "ETF_A", "kind": "etf"}],
                "b": [{"code": "ETF_B", "kind": "etf"}],
            },
        },
    }
    write_json(directory / "strategy.json", config)
    write_json(directory / "request.json", {
        "objective": "比较固定比例与风险平价的区间回撤",
        "metrics": ["max_drawdown"],
        "comparison": "standard",
        "defaults": {"initial_capital": "统一示例本金"},
    })
    for name in ("normal", "short_history", "fallback"):
        target = directory / name
        target.mkdir()
        series = prices.copy()
        if name == "short_history":
            series.loc[series.index < "2025-03-03", "ETF_B"] = np.nan
        elif name == "fallback":
            # 首次、四月、七月调仓的信号日均缺一只代理价格。
            series.loc[["2024-12-31", "2025-03-31", "2025-06-30"], "ETF_A"] = np.nan
        pd.DataFrame({"date": dates, "coverage_end": "2025-07-31"}).to_csv(
            target / "calendar.csv", index=False
        )
        with (target / "prices.csv").open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["date", "code", "close_adjusted"])
            for day, row in series.iterrows():
                for code, value in row.dropna().items():
                    writer.writerow([day.date(), code, value])


class ResearchWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="multi-asset-research-")
        cls.path = Path(cls.temp.name)
        fixture(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_cli(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, str(RUNNER), *map(str, args)], cwd=self.path,
            capture_output=True, text=True, timeout=30,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result

    def init_task(self, name, request="request.json"):
        task = self.path / name
        result = self.run_cli("research", "init", "--request", request, "--config", "strategy.json", "--out", task)
        return task, result

    def test_incomplete_request_never_reads_data_or_runs(self):
        write_json(self.path / "incomplete.json", {"objective": "研究组合风险"})
        task, _ = self.init_task("incomplete", "incomplete.json")
        result = self.run_cli("research", "run", "--task", task, "--data", "absent", "--out", "never-created")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(set(result["missing"]), {"metrics", "comparison"})
        self.assertFalse((self.path / "never-created").exists())

    def test_short_history_offers_a_start_and_blocks_backtest(self):
        task, _ = self.init_task("coverage-task")
        result = self.run_cli("research", "run", "--task", task, "--data", "short_history", "--out", "coverage-run")
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["proposed_start"], "2025-03-04")
        self.assertGreater(result["risk_window"]["missing_observations"], 0)
        self.assertFalse((self.path / "coverage-run").exists())
        write_json(self.path / "change-start.json", {"start": result["proposed_start"]})
        self.run_cli("research", "revise", "--task", task, "--changes", "change-start.json", "--out", "short-revised")
        revised = self.run_cli("research", "preview", "--task", "short-revised", "--data", "short_history")
        self.assertEqual(revised["status"], "ready_with_notes")
        self.assertEqual(revised["requested"]["start"], "2025-03-04")

    def test_task_cannot_be_replaced_or_run_after_config_tampering(self):
        task, initial = self.init_task("immutable-task")
        self.run_cli(
            "research", "init", "--request", "request.json", "--config", "strategy.json",
            "--out", task, ok=False,
        )
        self.assertEqual(read_json(task / "task.json")["strategy_sha256"], initial["strategy_sha256"])
        modified = read_json(task / "strategy.json")
        write_json(task / "strategy.json", {**modified, "allocation_rebalance": "monthly"})
        error = self.run_cli("research", "preview", "--task", task, "--data", "normal", ok=False)
        self.assertIn("策略文件已修改", error.stderr)

    def test_revised_end_needs_data_for_new_dates(self):
        task, _ = self.init_task("calendar-task")
        write_json(self.path / "change-end.json", {"end": "2025-08-29"})
        self.run_cli("research", "revise", "--task", task, "--changes", "change-end.json", "--out", "later-end")
        data = self.path / "older-data"
        data.mkdir()
        original = self.path / "normal"
        (data / "prices.csv").write_bytes((original / "prices.csv").read_bytes())
        calendar = pd.read_csv(original / "calendar.csv")
        # 上游日历覆盖全年，但旧研究只保存到七月底。
        calendar["coverage_end"] = "2025-12-31"
        calendar.to_csv(data / "calendar.csv", index=False)
        write_json(data / "strategy_data_manifest.json", {"end": "2025-07-31"})
        result = self.run_cli("research", "preview", "--task", "later-end", "--data", data)
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("incomplete_calendar", {issue["code"] for issue in result["blockers"]})

    def test_full_run_and_only_one_frequency_change(self):
        task, request = self.init_task("regular-task")
        self.assertEqual(request["status"], "ready")
        out = self.run_cli("research", "run", "--task", task, "--data", "normal", "--out", "regular-run")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["execution_review"]["fallback_events"], 0)
        report = self.run_cli("report", *out["report_args"])
        data = read_json(Path(report["report"]).parent / "report_data.json")
        self.assertIn("最大回撤", data["summary"][0]["text"])
        write_json(self.path / "change-frequency.json", {"allocation_rebalance": "monthly"})
        after = self.run_cli("research", "revise", "--task", task, "--changes", "change-frequency.json", "--out", "monthly")
        self.assertEqual(set(after["change"]["strategy_fields"]), {"allocation_rebalance"})
        original = read_json(task / "strategy.json")
        revised = read_json(self.path / "monthly/strategy.json")
        self.assertEqual(original["allocation_rebalance"], "quarterly")
        self.assertEqual(revised, {**original, "allocation_rebalance": "monthly"})
        self.run_cli("research", "run", "--task", "monthly", "--data", "normal", "--out", "monthly-run")

    def test_all_fallback_changes_the_report_summary(self):
        task, _ = self.init_task("fallback-task")
        result = self.run_cli("research", "run", "--task", task, "--data", "fallback", "--out", "fallback-run")
        self.assertEqual(result["next_action"], "report_fallback_only")
        review = result["execution_review"]
        self.assertEqual(review["status"], "method_not_evaluated")
        self.assertEqual(review["fallback_events"], 3)
        report = self.run_cli("report", *result["report_args"])
        data = read_json(Path(report["report"]).parent / "report_data.json")
        self.assertIn("无法评价该方法", data["summary"][0]["text"])
        self.assertEqual(data["pairs"][0]["comparison_type"], "回退配置比较")
        claim = data["evidence_values"]["metric.strategy.max_drawdown"]
        draft = write_json(self.path / "fallback-commentary.json", {"sections": [{
            "section": "summary", "title": "回退配置的回撤表现",
            "text": "区间回撤来自实际运行的回退路径。",
            "claims": ["metric.strategy.max_drawdown"],
            "evidence_ids": [claim["fact_id"]],
        }]})
        args = result["report_args"].copy()
        args[args.index("--out") + 1] = "fallback-report-with-commentary"
        self.run_cli("report", *args, "--commentary", draft)
        revised = read_json(self.path / "fallback-report-with-commentary/report_data.json")
        self.assertIn("无法评价该方法", revised["summary"][0]["text"])
        self.assertIn(claim["text"], revised["summary"][1]["text"])
