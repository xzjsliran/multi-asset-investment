"""将策略的数据需求交给第一部分；联网获取方法全部留在 quant-data-kit。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import shutil
import sys

import pandas as pd

from .config import allocation_frequencies, data_plan, schedule, write_json
from .selection import quality_value


def connect_data_kit(path):
    root = Path(path).resolve()
    if not (root / "scripts/quantkit/fundamentals.py").exists():
        raise ValueError("需要更新后的 quant-data-kit，其中包含 fundamentals.py 和 overseas.py。")
    sys.path.insert(0, str(root / "scripts"))
    return root


def prepare(config, data_kit, work, provider="auto", research=None, macro_data=None, cache=None):
    connect_data_kit(data_kit)
    from quantkit.common import DATA_SOURCES, MACRO_SOURCES, Client, frame, normalize_asset, now
    from quantkit.catalog import FRED
    from quantkit.pipeline import get_calendar, get_macro, get_market, run as download
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
    sources = config.get("data_sources", {})
    if set(sources) - DATA_SOURCES:
        raise ValueError("data_sources 含未支持字段，请参照数据模块接口说明。")
    imported_macro = pd.DataFrame()
    if macro_data:
        source = Path(macro_data)
        imported_macro = pd.read_csv(source / "macro_observations.csv" if source.is_dir() else source)
    imported_series = set(imported_macro.get("series", []))
    if "signal_eligible" in imported_macro:
        usable = imported_macro["signal_eligible"].astype(str).str.lower().isin(["true", "1", "1.0"])
        monthly = {key for key in imported_series if FRED.get(key, {}).get("frequency") == "monthly"}
        # 旧数据只有当前修订版时，继续下载该序列的历史版本。
        imported_series -= monthly - set(imported_macro.loc[usable, "series"])
    macro_request = {"macro": sorted(set(plan["macro"]) - imported_series),
                     "provider": provider, **{k: sources[k] for k in MACRO_SOURCES if k in sources}}
    start = (pd.Timestamp(config["start"]) - pd.Timedelta(days=plan["lookback_days"])).strftime("%Y-%m-%d")
    cache = Path(cache).resolve() if cache else work / "cache"
    client = Client(cache, timeout=120)
    if research:
        cal = research_calendar(research, start, config["end"])
    else:
        cal, _ = get_calendar(client, start, config["end"], provider)
        cal = cal.loc[cal["date"].between(pd.Timestamp(start), pd.Timestamp(config["end"]))]
    cal.to_csv(out / "calendar.csv", index=False, encoding="utf-8-sig")
    macro_dates = set()
    if macro_request["macro"]:
        for frequency in allocation_frequencies(config):
            macro_dates.update(schedule(cal["date"], config["start"], config["end"], frequency).values())
        macro_dates.add(cal["date"].max())  # 最后一天也用于准备下一次调仓。
    macro_dates = sorted(macro_dates)
    dates = set()
    for sleeve in config["sleeves"]:
        if sleeve["selection"]["method"] in {"quality_value", "fundamental"}:
            dates.update(schedule(cal["date"], config["start"], config["end"], sleeve["rebalance"]).values())
    factor_pools = defaultdict(list)
    for request in plan["equity_factor_pools"]:
        for signal in schedule(cal["date"], config["start"], config["end"], request["frequency"]).values():
            factor_pools[signal].append(request)
    features = pd.DataFrame()
    domestic_quality = None
    assets = {a["code"]: a for a in plan["assets"]}
    if config.get("execution_price") == "open" and any(a["kind"] == "us_etf" for a in assets.values()):
        raise ValueError("中国开盘执行需要同一时点的开盘价。海外 ETF 的已知收盘价不能用于此模拟。")
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
    factor_prices, factor_coverage = pd.DataFrame(), None
    if factor_pools:
        if provider == "free":
            raise ValueError("多因子选股需要历史估值和三张财报的实际披露日。请使用已配置的 Tushare 或已有的标准研究数据。")
        from quantkit.equity_factors import prepare_equity_factors
        benchmark = config.get("benchmark", {})
        benchmark_code = benchmark["code"] if benchmark.get("kind") == "index" else None
        factor_prices, factor_features, factor_coverage = prepare_equity_factors(
            cache / "equity-factors", cal["date"], factor_pools, start, config["end"], benchmark_code, pools=factor_pools,
        )
        keys = ["snapshot_date", "code"]
        features = (factor_features.set_index(keys).combine_first(features.set_index(keys)).reset_index()
                    if not features.empty else factor_features)
        features.to_csv(out / "stock_features.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    factor_codes = set(factor_prices.get("code", []))
    other_assets = [a for a in assets.values() if a["code"] not in factor_codes]
    if research:
        archived = [a for a in other_assets if a["kind"] not in {"qdii_nav", "usd_deposit"}]
        prices = research_prices(research, archived, cal) if archived else pd.DataFrame()
        for a in other_assets:
            if a["kind"] in {"qdii_nav", "usd_deposit"}:
                d, _, _ = get_market(client, normalize_asset(a), start, config["end"], cal, provider, sources)
                prices = pd.concat([prices, d], ignore_index=True)
    else:
        domestic = [a for a in other_assets if a["kind"] != "us_etf"]
        prices = pd.DataFrame()
        if domestic:
            request = {"start": config["start"], "end": config["end"], "lookback_days": plan["lookback_days"],
                       "assets": domestic, **macro_request, **sources}
            write_json(work / "data-request.json", request)
            # 第一部分会创建自己的目录，因此先写入独立目录再读取合并。
            domestic_quality = download(work / "data-request.json", work / "domestic", cache, signal_dates=macro_dates)
            # 标准行情和质量记录一同交给回测前检查。
            shutil.copy2(work / "domestic/quality.json", out / "quality.json")
            prices = pd.read_csv(work / "domestic/prices.csv")
        overseas = [a for a in other_assets if a["kind"] == "us_etf"]
        if overseas:
            fx = frame(client.call("us_history", code="CNY=X", start=start, end=config["end"]))
            additions = [align_us(a["code"], frame(client.call("us_history", code=a["code"], start=start, end=config["end"])), fx, cal["date"]) for a in overseas]
            prices = pd.concat([prices, *additions], ignore_index=True)
    if not factor_prices.empty:
        prices = pd.concat([prices, factor_prices], ignore_index=True)
    pending_prices = out / "prices.partial"
    prices.to_csv(pending_prices, index=False, encoding="utf-8-sig",
                  float_format="%.12g" if factor_coverage is not None else None)
    macro_failures = []
    if domestic_quality:
        acquired = pd.read_csv(work / "domestic/macro_observations.csv")
    elif macro_request["macro"]:
        acquired, _, macro_failures = get_macro(client, macro_request, start, config["end"], signal_dates=macro_dates)
    else:
        acquired = pd.DataFrame()
    combined = [piece for piece in (acquired, imported_macro) if not piece.empty]
    if combined:
        pd.concat(combined, ignore_index=True).drop_duplicates().to_csv(
            out / "macro_observations.csv", index=False, encoding="utf-8-sig")
    write_json(work / "data-plan.json", plan)
    write_json(out / "strategy_data_manifest.json", {"created_at": now(), "source": str(Path(research).resolve()) if research else "quant-data-kit",
        "start_with_warmup": start, "end": config["end"], "assets": list(assets.values()), "rows": len(prices),
        "us_alignment": "已完成纽约收盘和已知汇率向后对齐到中国交易日16时；调仓采用该已知报价的低频近似。",
        "macro_failures": macro_failures, "macro_import": str(Path(macro_data).resolve()) if macro_data else None,
        "macro_signal_dates": [str(day.date()) for day in macro_dates],
        "feature_dates": [str(x.date()) for x in sorted(dates | set(factor_pools))], "requests": client.log,
        **({"equity_factor_coverage":factor_coverage} if factor_coverage is not None else {}),
        "domestic_status": domestic_quality["status"] if domestic_quality else None})
    pending_prices.replace(out / "prices.csv")
    return out
