"""交互式配置可选数据源；凭证不会出现在命令行参数中。"""
import argparse
import json
from quantkit.credentials import configure, status

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("provider", choices=["tushare", "ifind", "status"])
    a = p.parse_args()
    if a.provider == "status":
        print(json.dumps(status(), ensure_ascii=False))
    else:
        configure(a.provider)
