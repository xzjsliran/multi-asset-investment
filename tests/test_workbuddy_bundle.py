"""校验 WorkBuddy 本地插件市场包：清单一致、可按安装路径独立运行。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class WorkBuddyDistribution(unittest.TestCase):
    def test_extracted_marketplace_runs_and_keeps_integrity(self):
        with tempfile.TemporaryDirectory(prefix="workbuddy bundle ") as tmp:
            tmp = Path(tmp)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_workbuddy_release.py"),
                 "--core-ref", "HEAD", "--out", str(tmp / "release")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            built = json.loads(result.stdout)
            archive = Path(built["zip"])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), built["sha256"])
            version = (ROOT / "VERSION").read_text().strip()
            self.assertEqual(built["version"], version)
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertTrue(all(n.startswith("multi-asset-investment-workbuddy/") for n in z.namelist()))
                self.assertFalse(any(".." in Path(n).parts for n in z.namelist()))
                z.extractall(tmp / "another folder")
            market = tmp / "another folder/multi-asset-investment-workbuddy"
            manifest = json.loads(
                (market / ".codebuddy-plugin/marketplace.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["name"], "multi-asset-investment-local")
            entries = [p for p in manifest["plugins"] if p["name"] == "multi-asset-investment"]
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertEqual(entry["version"], version)
            # 市场清单的 source 必须指向包内的插件目录，且与插件清单版本一致。
            self.assertEqual(entry["source"], "./plugins/multi-asset-investment")
            plugin = market / entry["source"]
            self.assertTrue(plugin.is_dir(), entry["source"])
            plugin_manifest = json.loads(
                (plugin / ".codebuddy-plugin/plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(plugin_manifest["name"], entry["name"])
            self.assertEqual(plugin_manifest["version"], version)
            skill = plugin / "skills/multi-asset-investment"
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertIn("WorkBuddy 运行入口", (skill / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue((skill / "references/workbuddy-runtime.md").is_file())
            inventory = json.loads((market / "bundle-manifest.json").read_text(encoding="utf-8"))
            for name, digest in inventory["files"].items():
                self.assertEqual(hashlib.sha256((market / name).read_bytes()).hexdigest(), digest, name)
                self.assertFalse(any(part in {".venv", "__pycache__", "credentials.json", ".env"}
                                     for part in Path(name).parts))
            for kit in ["quant-data-kit", "quant-strategy-kit", "quant-report-kit", "quant-rebalance-kit"]:
                self.assertTrue((skill / kit / "requirements.txt").is_file())

            def run(*args):
                p = subprocess.run([sys.executable, str(skill / "scripts/run.py"), *args],
                                   cwd=tmp, capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout

            status = json.loads(run("startup"))
            self.assertEqual(status["network_requests"], 0)
            self.assertTrue(all(status["modules"].values()))
            config = tmp / "strategy.json"
            run("strategy", "init", "--preset", "risk-parity-demo", "--out", str(config))
            plan = json.loads(run("strategy", "plan", "--config", str(config)))
            self.assertTrue(plan)


if __name__ == "__main__":
    unittest.main()
