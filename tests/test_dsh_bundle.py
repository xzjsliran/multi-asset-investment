"""校验 DSH 插件组合包：清单一致、解压后可独立运行、宿主入口能注册统一 Skill。"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

try:
    import yaml
except ImportError:  # 依赖不保证安装，退回文本断言。
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "multi-asset-investment-dsh"
SKILL = "multi-asset-investment"
NODE = shutil.which("node")


class DSHDistribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="dsh bundle ")
        root = Path(cls.tmp.name)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_dsh_release.py"),
             "--core-ref", "HEAD", "--out", str(root / "release")],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            cls.tmp.cleanup()
            raise AssertionError(result.stderr)
        cls.built = json.loads(result.stdout)
        cls.archive = Path(cls.built["zip"])
        with zipfile.ZipFile(cls.archive) as archive:
            cls.names = archive.namelist()
            cls.bad_member = archive.testzip()
            # 解压到含空格的路径，模拟使用者本机的安装位置。
            archive.extractall(root / "another folder")
        cls.package = root / "another folder" / PACKAGE
        cls.skill = cls.package / "skills" / SKILL

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_archive_layout_and_declared_manifests(self):
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), self.built["sha256"])
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(self.built["version"], version)
        self.assertIsNone(self.bad_member)
        self.assertTrue(all(name.startswith(f"{PACKAGE}/") for name in self.names))
        self.assertFalse(any(".." in Path(name).parts for name in self.names))

        manifest = json.loads((self.package / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], SKILL)
        self.assertEqual(manifest["version"], version)
        self.assertEqual(manifest["type"], "module")
        self.assertEqual(manifest["exports"]["."], "./index.js")
        # 组合入口必须指向包内实际存在的 patch，插件管理器据此登记插件行。
        patch_name = manifest["dsh"]["bundle"]["patch"]
        self.assertEqual(patch_name, "./cordis.patch.yml")
        patch_path = (self.package / patch_name).resolve()
        self.assertTrue(patch_path.is_file())
        self.assertTrue((self.package / "index.js").is_file())
        patch = patch_path.read_text(encoding="utf-8")
        if yaml is not None:
            rows = [row for item in yaml.safe_load(patch) for row in item.get("insert", [])]
            self.assertEqual([row["id"] for row in rows], [SKILL])
            self.assertEqual([row["name"] for row in rows], [manifest["name"]])
        else:
            self.assertEqual(re.findall(r"^\s+- id: (\S+)$", patch, re.M), [SKILL])
            self.assertIn(f"name: '{manifest['name']}'", patch)
        meta = json.loads((self.package / "locale/zh.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["meta"]["title"], "多资产投资研究")

    def test_declared_hashes_cover_shipped_files(self):
        inventories = ((self.package, "bundle-manifest.json"), (self.skill, "plugin-manifest.json"))
        for directory, name in inventories:
            inventory = json.loads((directory / name).read_text(encoding="utf-8"))
            self.assertFalse(inventory["contains_market_data"])
            self.assertFalse(inventory["contains_credentials"])
            self.assertTrue(inventory["files"])
            for name, digest in inventory["files"].items():
                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest, name)
                self.assertFalse(any(part in {".venv", "__pycache__", "credentials.json", ".env"}
                                     for part in Path(name).parts))
        bundle = json.loads((self.package / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(bundle["target"], "dsh")
        for key in ("core_source_commit", "adapter_source_commit"):
            self.assertRegex(bundle[key], r"^[0-9a-f]{40}$")

    def test_extracted_skill_runs_offline(self):
        skill_file = self.skill / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")
        self.assertIn("DSH 运行入口", text)
        self.assertIn("name: multi-asset-investment", text)
        self.assertTrue((self.skill / "references/dsh-runtime.md").is_file())
        for kit in ["quant-data-kit", "quant-strategy-kit", "quant-report-kit", "quant-rebalance-kit"]:
            self.assertTrue((self.skill / kit / "requirements.txt").is_file())

        def run(*args):
            result = subprocess.run([sys.executable, str(self.skill / "scripts/run.py"), *args],
                                    cwd=self.tmp.name, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        status = json.loads(run("startup"))
        self.assertEqual(status["network_requests"], 0)
        self.assertTrue(all(status["modules"].values()))
        config = Path(self.tmp.name) / "strategy.json"
        run("strategy", "init", "--preset", "risk-parity-allocation", "--out", str(config))
        plan = json.loads(run("strategy", "plan", "--config", str(config)))
        self.assertTrue(plan)

    @unittest.skipUnless(NODE, "未安装 Node.js，跳过宿主入口检查")
    def test_host_entry_registers_skill(self):
        harness = Path(self.tmp.name) / "harness.mjs"
        harness.write_text(
            "import {{ apply, inject }} from {source};\n"
            "const registered = [];\n"
            "const ctx = {{ effect: (fn) => fn(),\n"
            "  skills: {{ register: (skill) => {{ registered.push(skill); return () => {{}}; }} }} }};\n"
            "apply(ctx, {{}});\n"
            "process.stdout.write(JSON.stringify({{ inject, registered }}));\n".format(
                source=json.dumps((self.package / "index.js").as_uri())),
            encoding="utf-8",
        )
        result = subprocess.run([NODE, str(harness)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        registered = json.loads(result.stdout)
        self.assertEqual(registered["inject"], ["skills"])
        self.assertEqual(len(registered["registered"]), 1)
        skill = registered["registered"][0]
        self.assertEqual(skill["name"], SKILL)
        self.assertEqual(skill["source"], "dsh-plugin")
        self.assertTrue(skill["description"].strip())
        self.assertEqual(Path(skill["path"]).resolve(), (self.skill / "SKILL.md").resolve())
        self.assertEqual(skill["resourceBase"]["kind"], "directory")
        self.assertEqual(Path(skill["resourceBase"]["path"]).resolve(), self.skill.resolve())
        # 正文不含 frontmatter，且以宿主入口开头，四个模块由技能目录定位。
        self.assertTrue(skill["content"].startswith("## DSH 运行入口"))
        self.assertIn("## 首次启动", skill["content"])
        self.assertNotIn("description:", skill["content"][:200])


if __name__ == "__main__":
    unittest.main()
