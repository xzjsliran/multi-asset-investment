"""iFinD可选查询入口；tools不取数，query默认每个工作目录最多尝试4次。"""
import argparse
import json
from quantkit.ifind import IfindClient, SERVICES
from quantkit.common import write_json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["tools","query"])
    p.add_argument("--service", choices=SERVICES, required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--max-calls", type=int, default=4)
    p.add_argument("--tool")
    p.add_argument("--params-file")
    a=p.parse_args()
    client=IfindClient(a.work,a.max_calls)
    if a.command == "tools":
        result=client.list_tools(a.service)
        write_json(Path(a.work)/(a.service+"_tools.json"),result)
        print(json.dumps({"service":a.service,"tools":[x["name"] for x in result.get("tools",[])]},ensure_ascii=False))
    else:
        if not a.tool or not a.params_file:
            p.error("query需要--tool及--params-file；凭证不放在参数文件中。")
        result=client.call(a.service,a.tool,json.loads(Path(a.params_file).read_text(encoding="utf-8")))
        print(json.dumps(result,ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except (ValueError,RuntimeError) as exc:
        raise SystemExit(str(exc))
