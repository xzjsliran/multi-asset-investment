"""从公开 GitHub 获取上游 Skill，输出本地导入包，不改 WorkBuddy 设置。"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import urllib.request
import zipfile

SOURCES = {
    "tushare-data": {"repo": "waditu-tushare/skills", "ref": "5e12b31d09123e262c5fb38564e80c26d05cb830", "prefix": "tushare-data/"},
    "akshare-open": {"repo": "Z-AErIs/akshare-open", "ref": "4414efd78267bcb97642752d8e3c68c0d228a0c6", "prefix": ""}
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dest", required=True)
    p.add_argument("--skill", choices=["all", *SOURCES], default="all")
    args = p.parse_args()
    base = Path(args.dest).resolve()
    base.mkdir(parents=True, exist_ok=True)
    for name, info in SOURCES.items():
        if args.skill not in {"all", name}:
            continue
        target, output = base / name, base / (name + ".zip")
        if target.exists() or output.exists():
            raise SystemExit(name + " 已存在，未覆盖；请换新目录。")
        url = f'https://codeload.github.com/{info["repo"]}/zip/{info["ref"]}'
        req = urllib.request.Request(url, headers={"User-Agent": "quant-data-kit/0.1"})
        with urllib.request.urlopen(req, timeout=40) as response:
            blob = response.read(30_000_001)
        if len(blob) > 30_000_000:
            raise ValueError("上游压缩包超出本工具的 30MB 预期大小。")
        selected = []
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            total = 0
            for member in z.infolist():
                parts = PurePosixPath(member.filename).parts
                if member.is_dir() or len(parts) < 2 or ".." in parts:
                    continue
                relative = "/".join(parts[1:])
                if not relative.startswith(info["prefix"]):
                    continue
                relative = relative[len(info["prefix"]):]
                if not relative or any(part.startswith(".") for part in PurePosixPath(relative).parts):
                    continue
                # 只获取文档与 Skill 所需资源，不执行上游安装脚本。
                if not (relative == "SKILL.md" or relative.startswith(("scripts/", "references/", "assets/")) or relative.lower().startswith(("license", "readme"))):
                    continue
                total += member.file_size
                if total > 50_000_000:
                    raise ValueError("解压内容超过预期大小。")
                selected.append((relative, z.read(member)))
        if not any(name == "SKILL.md" for name, _ in selected):
            raise ValueError("未找到 SKILL.md，上游目录可能变化。")
        skill_text = next(data.decode("utf-8") for relative, data in selected if relative == "SKILL.md")
        match = re.search(r"^name:\s*([a-z0-9-]+)\s*$", skill_text, flags=re.M)
        if not match:
            raise ValueError("Skill 名称需要人工核对。")
        # ZIP 根目录与 SKILL.md 的 name 一致，上游正文保持原样。
        target = base / match.group(1)
        if target.exists():
            raise ValueError("同名 Skill 目录已存在，未覆盖。")
        # 全部读完并检查后再写入目标。
        target.mkdir()
        for relative, data in selected:
            file = target / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        origin = {"url": url, "sha256_download": hashlib.sha256(blob).hexdigest(), "skill_name": match.group(1), **info}
        (target / "SOURCE.json").write_text(json.dumps(origin, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
            for file in target.rglob("*"):
                if file.is_file():
                    z.write(file, arcname=str(Path(target.name) / file.relative_to(target)))
        print("已准备导入包：" + str(output))


if __name__ == "__main__":
    main()
