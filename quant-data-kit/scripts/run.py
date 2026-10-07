"""人和智能体共用的入口：doctor / init / fetch / nbs。"""
from pathlib import Path
import argparse
import importlib.metadata
import json
import shutil
import sys


def main():
    p = argparse.ArgumentParser(description="量化教学数据接入套装")
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="检查依赖和本地凭证是否存在，不显示凭证")
    d.add_argument("--live-tushare", action="store_true", help="用本地凭证测试交易日历连接")
    d.add_argument("--cache", default="runs/cache")
    init = sub.add_parser("init", help="生成一份可修改的请求配置")
    init.add_argument("--out", required=True)
    f = sub.add_parser("fetch", help="按配置取数、清理并输出报告")
    f.add_argument("--config", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--cache", default="runs/cache")
    f.add_argument("--refresh", action="store_true")
    f.add_argument("--timeout", type=int, default=75)
    n = sub.add_parser("nbs", help="解析指定统计局原公告，检查发布时间和当期值")
    n.add_argument("--url", required=True)
    n.add_argument("--html", help="可选：官网访问不通时，解析用户另存的原公告 HTML")
    n.add_argument("--out", required=True)
    e = sub.add_parser("expectations", help="整理从 CME 下载的利率预期表")
    e.add_argument("--input", required=True)
    e.add_argument("--out", required=True)
    v = sub.add_parser("fred-vintage", help="获取历史版本，需要个人 FRED_API_KEY")
    for key in ["series", "start", "end", "asof", "out"]:
        v.add_argument("--" + key, required=True)
    args = p.parse_args()
    if args.command == "doctor":
        packages = {}
        for name in ["akshare", "tushare", "pandas", "numpy", "requests", "beautifulsoup4", "tzdata"]:
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = "missing"
        import os
        has_token = bool(os.getenv("TUSHARE_TOKEN"))
        try:
            import tushare as ts
            has_token = has_token or bool(ts.get_token())
        except Exception:
            pass
        result = {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 11),
                  "packages": packages, "local_tushare_token_present": has_token, "vnpy_required": False}
        if args.live_tushare:
            from quantkit.common import Client
            response = Client(args.cache).call("probe_ts")
            result["tushare_test_rows"] = len(response["data"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["python_ok"] and "missing" not in packages.values() else 2
    if args.command == "init":
        target = Path(args.out)
        if target.exists():
            raise ValueError("配置已存在，未覆盖。")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / "assets/request.example.json", target)
        print("配置已生成：" + str(target.resolve()))
        return 0
    if args.command == "nbs":
        from quantkit.common import write_json
        from quantkit.providers import nbs_article, nbs_url
        if Path(args.out).exists():
            raise ValueError("输出文件已存在，未覆盖。")
        event = nbs_article(Path(args.html).read_text(encoding="utf-8"), args.url) if args.html else nbs_url({"url": args.url})["event"]
        write_json(Path(args.out), event)
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    if args.command == "expectations":
        from quantkit.expectations import import_fedwatch
        print(json.dumps(import_fedwatch(args.input, args.out), ensure_ascii=False))
        return 0
    if args.command == "fred-vintage":
        from quantkit.vintages import save_vintage
        print(json.dumps(save_vintage(args.series, args.start, args.end, args.asof, args.out), ensure_ascii=False))
        return 0
    from quantkit.pipeline import run
    result = run(args.config, args.out, args.cache, args.refresh, args.timeout)
    print(json.dumps({"status": result["status"], "price_research_ready": result["price_research_ready"],
                      "out": str(Path(args.out).resolve())}, ensure_ascii=False))
    return 0 if result["status"] == "ready_with_notes" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
