"""只打包方法、说明和测试；不收录环境、数据、缓存和个人配置。"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = Path(args.out).resolve()
    files = []
    for relative in ["SKILL.md", "README.md", "requirements.txt", ".gitignore"]:
        files.append(root / relative)
    files.extend(root / "assets" / name for name in ["request.example.json", "fedwatch-columns.json"])
    for folder, suffixes in [("scripts", {".py"}), ("tests", {".py"}), ("references", {".md"})]:
        files.extend(f for f in (root / folder).rglob("*") if f.is_file() and f.suffix in suffixes and "__pycache__" not in f.parts)
    manifest = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise SystemExit("ZIP 已存在，未覆盖，请使用新文件名。")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files):
            z.write(f, arcname=str(Path(root.name) / f.relative_to(root)))
        z.writestr(str(Path(root.name) / "bundle-manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"zip": str(destination), "files": len(files)+1, "bytes": destination.stat().st_size,
                      "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
