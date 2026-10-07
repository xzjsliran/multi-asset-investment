"""在指定目录创建独立 Python 环境；不修改全局 Python。"""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--venv", required=True, help="例如工作目录中的 .venv")
    args = p.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("请先安装 Python 3.11 或 3.12，再运行此脚本。")
    target = Path(args.venv).resolve()
    if target.exists() and not (target / "pyvenv.cfg").exists():
        raise SystemExit("目标已存在且不是虚拟环境，请换一个目录。")
    if not target.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
    subprocess.run([str(python), str(root / "scripts/run.py"), "doctor"], check=True)
    print("后续请使用这个 Python：" + str(python))


if __name__ == "__main__":
    main()
