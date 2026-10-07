"""多资产研究插件入口；在宿主Agent中编排，计算委托给各kit。"""
from __future__ import annotations
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = next((p for p in [PLUGIN, PLUGIN.parent] if (p/"quant-data-kit/scripts/run.py").exists()), PLUGIN)
sys.path.insert(0, str(ROOT/"quant-data-kit/scripts"))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "startup"
    args = sys.argv[2:]
    routes = {"data":"quant-data-kit/scripts/run.py", "extend":"quant-data-kit/scripts/extend.py",
              "strategy":"quant-strategy-kit/scripts/run.py", "report":"quant-report-kit/scripts/run.py",
              "rebalance":"quant-rebalance-kit/scripts/run.py", "ifind":"quant-data-kit/scripts/ifind.py",
              "credentials":"quant-data-kit/scripts/configure_credentials.py"}
    if command in routes:
        return subprocess.run([sys.executable, str(ROOT/routes[command]), *args]).returncode
    if command not in {"startup","doctor","--help","help"}:
        raise ValueError("未知入口。可用startup、doctor、credentials、data、extend、ifind、strategy、report、rebalance。")
    from quantkit.credentials import status
    packages = {}
    for name in ["numpy","pandas","scipy","requests","akshare","tushare","yfinance","pyarrow"]:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    result = {"plugin":"multi-asset-investment", "python":sys.version.split()[0],"packages":packages,
              "modules":{name:(ROOT/path).exists() for name,path in routes.items()},
              "credentials":status(),"network_requests":0}
    if command == "startup":
        result["onboarding"] = ["确认研究年份、资产范围、固定/宏观/风险平价配权、调仓频率。",
            "询问是否有尚未配置的Tushare或iFinD密钥；均为可选，可只用免费源。已有凭证直接复用。",
            "密钥通过本地credentials命令输入，不写入策略配置、报告、安装包。",
            "新增iFinD取数先约定小额度；默认每个工作目录最多4次，优先复用缓存。",
            "实际调仓前确认当前策略、真实持仓、可卖数量、现金和时间。"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError,RuntimeError) as exc:
        raise SystemExit(str(exc))
