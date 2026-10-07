"""扩展取数入口：历史选股截面和美国 ETF。运行结果仍放用户工作目录。"""
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

from quantkit.common import Client, frame, write_json
from quantkit.fundamentals import stock_features
from quantkit.overseas import align_us


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("stock-features")
    p.add_argument("--dates-file", required=True, help="JSON 日期数组")
    p.add_argument("--out", required=True)
    p.add_argument("--cache")
    p.add_argument("--research")
    p = commands.add_parser("us-etf")
    p.add_argument("--codes", required=True, help="例如 SHY,QQQ,SPY")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--calendar", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cache", required=True)
    p = commands.add_parser("fred-events")
    p.add_argument("--inputs", nargs="+", required=True)
    p.add_argument("--base", help="可选：合并第一部分已有的宏观事件")
    p.add_argument("--out", required=True, help="新输出目录")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    client = Client(args.cache) if getattr(args, "cache", None) else None
    if args.command == "stock-features":
        dates = json.loads(Path(args.dates_file).read_text(encoding="utf-8"))
        result = stock_features(dates, out, client, args.research)
    elif args.command == "us-etf":
        cal = pd.read_csv(args.calendar)
        fx = frame(client.call("us_history", code="CNY=X", start=args.start, end=args.end))
        result = pd.concat([align_us(code, frame(client.call("us_history", code=code, start=args.start, end=args.end)), fx, pd.to_datetime(cal["date"]))
                            for code in args.codes.split(",")], ignore_index=True)
        result.to_csv(out / "overseas_prices.csv", index=False, encoding="utf-8-sig")
    else:
        from quantkit.vintages import snapshot_events
        result = snapshot_events(pd.concat([pd.read_csv(x) for x in args.inputs], ignore_index=True))
        if args.base:
            result = pd.concat([pd.read_csv(args.base), result], ignore_index=True).drop_duplicates()
        if (out / "macro_observations.csv").exists():
            raise ValueError("输出已有宏观事件，请改用新目录。")
        result.to_csv(out / "macro_observations.csv", index=False, encoding="utf-8-sig")
    if client:
        write_json(out / "extended_sources.json", client.log)
    print(json.dumps({"rows": len(result), "out": str(out.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
