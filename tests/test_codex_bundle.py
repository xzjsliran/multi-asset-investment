"""解压后的插件在不同路径直接运行；验证发布包不依赖开发目录。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class CodexDistribution(unittest.TestCase):
    def test_extracted_plugin_runs_and_keeps_integrity(self):
        with tempfile.TemporaryDirectory(prefix="codex bundle ") as tmp:
            tmp = Path(tmp)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_codex_release.py"), "--out", str(tmp / "release")],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            built = json.loads(result.stdout)
            archive = Path(built["zip"])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), built["sha256"])
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertTrue(all(n.startswith("multi-asset-investment-codex/") for n in z.namelist()))
                self.assertFalse(any(".." in Path(n).parts for n in z.namelist()))
                z.extractall(tmp / "another folder")
            plugin = tmp / "another folder/multi-asset-investment-codex"
            manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], (ROOT / "VERSION").read_text().strip())
            skills = plugin / manifest["skills"]
            skill = skills / "multi-asset-investment"
            self.assertTrue((skill / "SKILL.md").is_file())
            inventory = json.loads((plugin / "bundle-manifest.json").read_text(encoding="utf-8"))
            for name, digest in inventory["files"].items():
                self.assertEqual(hashlib.sha256((plugin / name).read_bytes()).hexdigest(), digest, name)
                self.assertFalse(any(part in {".venv", "__pycache__", "credentials.json", ".env"} for part in Path(name).parts))
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
