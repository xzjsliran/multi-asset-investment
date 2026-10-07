"""将策略的数据需求交给第一部分；联网获取方法全部留在 quant-data-kit。"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pandas as pd

from .config import data_plan, schedule, write_json
from .selection import quality_value


def connect_data_kit(path):
    root = Path(path).resolve()
    if not (root / "scripts/quantkit/fundamentals.py").exists():
        raise ValueError("需要更新后的 quant-data-kit，其中包含 fundamentals.py 和 overseas.py。")
    sys.path.insert(0, str(root / "scripts"))
    return root


def prepare(config, data_kit, work, provider="auto", research=None, macro_data=None, cache=None):
    connect_data_kit(data_kit)
    from quantkit.common import Client, frame, now
    from quantkit.pipeline import get_calendar, run as download
    from quantkit.fundamentals import stock_features
    from quantkit.research_import import research_calendar, research_prices
    from quantkit.overseas import align_us
    completed = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    if pd.Timestamp.now(tz="Asia/Shanghai").hour < 16:
        completed -= pd.Timedelta(days=1)
    if pd.Timestamp(config["end"]) > completed:
        raise ValueError("请把回测结束日设为已经完整收盘的日期。")
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out = work / "data"
    if (out / "prices.csv").exists():
        raise ValueError("该目录已有数据，请选择新的 work 目录；cache 可由用户复用。")
    out.mkdir(exist_ok=True)
    plan = data_plan(config)
    start = (pd.Timestamp(config["start"]) - pd.Timedelta(days=plan["lookback_days"])).strftime("%Y-%m-%d")
    cache = Path(cache).resolve() if cache else work / "cache"
    client = Client(cache, timeout=120)
    if research:
        cal = research_calendar(research, start, config["end"])
    else:
        cal, _ = get_calendar(client, start, config["end"], provider)
        cal = cal.loc[cal["date"].between(pd.Timestamp(start), pd.Timestamp(config["end"]))]
    cal.to_csv(out / "calendar.csv", index=False, encoding="utf-8-sig")
    dates = set()
    for sleeve in config["sleeves"]:
        if sleeve["selection"]["method"] in {"quality_value", "fundamental"}:
            dates.update(schedule(cal["date"], config["start"], config["end"], sleeve["rebalance"]).values())
    features = pd.DataFrame()
    assets = {a["code"]: a for a in plan["assets"]}
    if dates:
        if provider == "free" and not research:
            raise ValueError("质量价值选股所需的历史财务数据需要本地 Tushare 权限；可使用已有归档数据，或切换到不依赖财务数据的境内多资产配置。")
        features = stock_features(dates, out, client=client, research=research)
        for sleeve in config["sleeves"]:
            if sleeve["selection"]["method"] not in {"quality_value", "fundamental"}:
                continue
            for signal in schedule(cal["date"], config["start"], config["end"], sleeve["rebalance"]).values():
                picked = quality_value(features, signal, sleeve["selection"])
                for row in picked.to_dict("records"):
                    assets[row["code"]] = {"code": row["code"], "kind": "stock", "name": row["name"]}
    if research:
        prices = research_prices(research, list(assets.values()), cal)
        prices.to_csv(out / "prices.csv", index=False, encoding="utf-8-sig")
        if plan["macro"] and not macro_data:
            raise ValueError("research 原始快照没有本次宏观指标。请提供 macro_data，或通过数据模块补取后再跑。")
    else:
        domestic = [a for a in assets.values() if a["kind"] != "us_etf"]
        if not domestic:
            # 纯海外配置也可用：日历已经取得，不需要伪装成境内行情请求。
            prices = pd.DataFrame()
            if plan["macro"]:
                from quantkit.pipeline import get_macro
                cfg = {"macro": plan["macro"], "provider": provider, "nbs_urls": [], "nbs_crawl_pages": 0}
                events, failures, status = get_macro(client, cfg, start, config["end"])
                events.to_csv(out / "macro_observations.csv", index=False, encoding="utf-8-sig")
        else:
            request = {"start": config["start"], "end": config["end"], "lookback_days": plan["lookback_days"],
                       "assets": domestic, "macro": plan["macro"], "provider": provider, "nbs_urls": [], "nbs_crawl_pages": 0}
            write_json(work / "data-request.json", request)
            # 第一部分会创建自己的目录，因此先写入独立目录再读取合并。
            download(work / "data-request.json", work / "domestic", cache)
            prices = pd.read_csv(work / "domestic/prices.csv")
            macro_file = work / "domestic/macro_observations.csv"
            if macro_file.exists():
                (out / "macro_observations.csv").write_bytes(macro_file.read_bytes())
        overseas = [a for a in assets.values() if a["kind"] == "us_etf"]
        if overseas:
            fx = frame(client.call("us_history", code="CNY=X", start=start, end=config["end"]))
            additions = [align_us(a["code"], frame(client.call("us_history", code=a["code"], start=start, end=config["end"])), fx, cal["date"]) for a in overseas]
            prices = pd.concat([prices, *additions], ignore_index=True)
        prices.to_csv(out / "prices.csv", index=False, encoding="utf-8-sig")
    if macro_data:
        source = Path(macro_data)
        if source.is_dir():
            source = source / "macro_observations.csv"
        (out / "macro_observations.csv").write_bytes(source.read_bytes())
    write_json(work / "data-plan.json", plan)
    write_json(out / "strategy_data_manifest.json", {"created_at": now(), "source": str(Path(research).resolve()) if research else "quant-data-kit",
        "start_with_warmup": start, "end": config["end"], "assets": list(assets.values()), "rows": len(prices),
        "us_alignment": "已完成纽约收盘和已知汇率向后对齐到中国交易日16时；调仓采用该已知报价的低频近似。",
        "feature_dates": [str(x.date()) for x in sorted(dates)], "requests": client.log})
    return out
