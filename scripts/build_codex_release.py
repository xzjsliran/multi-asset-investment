"""从共享源码生成 Codex 插件和 ZIP；不复制研究数据、环境或私有配置。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters/codex"


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_revision():
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build(output):
    out = Path(output).resolve()
    if out.exists():
        raise ValueError("输出目录已经存在，请使用新目录。")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("VERSION 应为版本号，例如 0.1.0。")
    plugin = out / "multi-asset-investment-codex"
    skill = plugin / "skills/multi-asset-investment"
    spec = importlib.util.spec_from_file_location(
        "shared_bundle", ROOT / "multi-asset-investment/scripts/build_folder.py"
    )
    shared = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shared)
    shared.build(skill)
    # 只叠加宿主入口；四个模块均从共享源码生成。
    with (skill / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n## Codex 环境\n\n在 Codex 中先读取[运行约定](references/codex-runtime.md)，按本次安装位置定位解释器、工作目录和各模块。\n")
    shutil.copy2(ADAPTER / "codex-runtime.md", skill / "references/codex-runtime.md")
    (skill / "agents").mkdir(exist_ok=True)
    shutil.copy2(ADAPTER / "openai.yaml", skill / "agents/openai.yaml")
    # 通用清单在叠加宿主说明后重新计算，避免保留旧哈希。
    skill_files = {
        p.relative_to(skill).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(skill.rglob("*"))
        if p.is_file() and p.name != "plugin-manifest.json"
    }
    write_json(skill / "plugin-manifest.json", {
        "schema_version": 1, "files": skill_files,
        "contains_market_data": False, "contains_credentials": False,
    })
    manifest = json.loads((ADAPTER / "plugin.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    write_json(plugin / ".codex-plugin/plugin.json", manifest)
    write_json(plugin / ".agents/plugins/marketplace.json", {
        "name": "multi-asset-investment-local",
        "interface": {"displayName": "多资产投资研究"},
        "plugins": [{"name": "multi-asset-investment", "source": {"source": "local", "path": "./"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
                     "category": "Productivity"}],
    })
    shutil.copy2(ADAPTER / "INSTALL.md", plugin / "INSTALL.md")
    files = {p.relative_to(plugin).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(plugin.rglob("*")) if p.is_file()}
    write_json(plugin / "bundle-manifest.json", {
        "schema_version": 1, "target": "codex", "version": version,
        "source_commit": source_revision(), "files": files,
        "contains_market_data": False, "contains_credentials": False,
    })
    archive = out / f"multi-asset-investment-codex-v{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(plugin.rglob("*")):
            if not p.is_file():
                continue
            info = zipfile.ZipInfo(p.relative_to(out).as_posix(), (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out / "SHA256SUMS").write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    return {"plugin": str(plugin), "zip": str(archive), "sha256": checksum,
            "version": version, "files": len(files) + 1, "source_commit": source_revision()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    try:
        print(json.dumps(build(parser.parse_args().out), ensure_ascii=False))
    except ValueError as exc:
        raise SystemExit(str(exc))
