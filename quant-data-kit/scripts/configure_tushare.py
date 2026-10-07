"""在本机交互式输入凭证；不需要把 token 发给智能体。"""
import getpass
import os
from pathlib import Path

import tushare as ts


if __name__ == "__main__":
    if ts.get_token():
        print("本机已有 Tushare 凭证，继续复用，未覆盖。")
    else:
        value = getpass.getpass("请在本地终端粘贴自己的 Tushare token（不回显）：").strip()
        if not value:
            raise SystemExit("输入为空，未保存。")
        ts.set_token(value)
        # SDK 的标准本地保存位置，不进入项目或分发包。
        file = Path.home() / "tk.csv"
        if file.exists() and os.name != "nt":
            file.chmod(0o600)
        print("已保存到本机 Tushare 标准配置；运行 doctor --live-tushare 可测试。")
