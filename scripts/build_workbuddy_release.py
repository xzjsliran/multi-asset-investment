"""从指定核心版本与当前适配层打包 WorkBuddy 本地插件市场；不执行测试或安装。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters/workbuddy"
KITS = ["quant-data-kit", "quant-strategy-kit", "quant-report-kit", "quant-rebalance-kit"]
PLUGIN_NAME = "multi-asset-investment"
MARKETPLACE_NAME = "multi-asset-investment-local"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hashes(directory, exclude=()):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file() and p.name not in exclude}


def build(output, core_ref):
    out = Path(output).resolve()
    if out.exists():
        raise ValueError("输出目录已存在，请使用新目录。")
    core_commit = git("rev-parse", "--verify", core_ref + "^{commit}").decode().strip()
    adapter_commit = git("rev-parse", "HEAD").decode().strip()
    # 固定核心提交，避免将整理文档或后续核心修改混入同一版本的追加附件。
    snapshot = git("archive", "--format=zip", core_commit, "VERSION", "multi-asset-investment", *KITS)
    # WorkBuddy 市场根目录登记插件，插件本体放在 plugins/<名称>，与官方市场清单结构一致。
    market = out / f"{PLUGIN_NAME}-workbuddy"
    plugin = market / "plugins" / PLUGIN_NAME
    skill = plugin / "skills" / PLUGIN_NAME
    with tempfile.TemporaryDirectory(prefix="multi-asset-workbuddy-source-") as temp:
        source = Path(temp)
        with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
            archive.extractall(source)
        version = (source / "VERSION").read_text(encoding="utf-8").strip()
        spec = importlib.util.spec_from_file_location(
            "shared_workbuddy_bundle", source / "multi-asset-investment/scripts/build_folder.py")
        shared = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shared)
        shared.build(skill)

    entry = ADAPTER.joinpath("skill-entry.md").read_text(encoding="utf-8")
    skill_file = skill / "SKILL.md"
    original = skill_file.read_text(encoding="utf-8")
    # 宿主说明放在通用正文之前；统一 Skill 及四个 kit 的业务规则继续复用。
    frontmatter, body = original.split("\n---\n", 1)
    skill_file.write_text(frontmatter + "\n---\n\n" + entry + "\n" + body.lstrip(), encoding="utf-8")
    shutil.copy2(ADAPTER / "workbuddy-runtime.md", skill / "references/workbuddy-runtime.md")
    write_json(skill / "plugin-manifest.json", {
        "schema_version": 1, "files": file_hashes(skill, {"plugin-manifest.json"}),
        "contains_market_data": False, "contains_credentials": False,
    })
    manifest = json.loads((ADAPTER / "plugin.json").read_text(encoding="utf-8"))
    manifest["version"] = version
    write_json(plugin / ".codebuddy-plugin/plugin.json", manifest)
    # 市场清单与插件清单共用同一版本号，避免安装时名称或版本互相矛盾。
    write_json(market / ".codebuddy-plugin/marketplace.json", {
        "name": MARKETPLACE_NAME,
        "description": "多资产投资研究本地插件市场",
        "owner": {"name": "xzjsliran"},
        "plugins": [{"name": PLUGIN_NAME, "description": manifest["description"],
                     "source": f"./plugins/{PLUGIN_NAME}", "version": version,
                     "category": "productivity", "author": manifest["author"],
                     "repository": manifest["repository"], "homepage": manifest["repository"]}],
    })
    shutil.copy2(ADAPTER / "INSTALL.md", market / "INSTALL.md")
    status = {"target": "workbuddy", "version": version, "core_ref": core_ref,
              "core_source_commit": core_commit, "adapter_source_commit": adapter_commit,
              "automated_tests": "not_run", "workbuddy_plugin_validation": "not_run",
              "workbuddy_installation": "not_run", "end_to_end": "not_run",
              "note": "首次适配试用包，按维护者要求跳过验证，供使用者自行测试。"}
    write_json(market / "bundle-manifest.json", {
        "schema_version": 1, **status, "files": file_hashes(market),
        "contains_market_data": False, "contains_credentials": False,
    })
    archive_path = out / f"{PLUGIN_NAME}-workbuddy-v{version}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(market.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(out).as_posix(), (2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (out / "SHA256SUMS-workbuddy.txt").write_text(
        f"{checksum}  {archive_path.name}\n", encoding="utf-8")
    write_json(out / "release-status-workbuddy.json", {**status, "zip_sha256": checksum})
    return {"zip": str(archive_path), "sha256": checksum, **status}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--core-ref", required=True, help="核心源码标签或提交，例如 v0.1.0")
    args = parser.parse_args()
    print(json.dumps(build(args.out, args.core_ref), ensure_ascii=False))
