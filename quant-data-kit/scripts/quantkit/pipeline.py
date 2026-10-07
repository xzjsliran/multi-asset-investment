"""把来源、清理和输出串起来；没有策略和自动交易。"""
from __future__ import annotations

import html
import importlib.metadata
from pathlib import Path
import shutil

import pandas as pd

from .common import Client, frame, now, read_request, token_value, write_json, year_chunks
from .cleaning import EVENT_COLUMNS, align_macro, calendar_frame, clean_market, macro_events


def save_csv(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig", date_format="%Y-%m-%dT%H:%M:%S%z")


def get_calendar(client, start, end, provider):
    ops = ["calendar_ts"] if provider == "tushare" else ["calendar_ak"]
    if provider == "auto" and token_value():
        ops.append("calendar_ts")
    failures = []
    for op in ops:
        try:
            r = client.call(op, **({"start": start, "end": end} if op == "calendar_ts" else {}))
            cal = calendar_frame(frame(r), r["source"])
            if cal["date"].min() > pd.Timestamp(start) + pd.Timedelta(days=15):
                raise ValueError("交易日历的起始覆盖不足。")
            if cal["coverage_end"].max() < pd.Timestamp(end):
                raise ValueError("交易日历没有覆盖到完整调仓周期；请换源或缩短日期。")
            return cal, failures
        except Exception as e:
            failures.append({"source": op, "error": str(e)})
    raise ValueError("交易日历不可用：" + str(failures))


def get_market(client, asset, start, end, calendar, provider):
    ops = ["market_ts"] if provider == "tushare" else ["market_em", "market_sina"]
    if provider == "auto" and token_value():
        ops.append("market_ts")
    failures, candidates = [], []
    for op in ops:
        try:
            collected = {}
            for adjust in ("", "hfq"):
                pieces = []
                for first, last in year_chunks(start, end):
                    p = {k: asset[k] for k in ["kind", "symbol", "tx_symbol", "ts_code"]}
                    r = client.call(op, **p, start=first, end=last, adjust=adjust)
                    pieces.append(frame(r))
                collected[adjust] = pd.concat(pieces, ignore_index=True)
            d, summary = clean_market(collected[""], collected["hfq"], asset, calendar, start, end, r)
            if summary["valid_days"] == 0:
                raise ValueError("该来源没有有效价格。")
            candidates.append((d, summary))
            allowed_missing = sum(summary["issues"].get(k, 0) for k in ["before_listing", "after_delisting"])
            if summary["valid_days"] + allowed_missing == summary["expected_days"] and not summary["off_calendar_dates"]:
                return d, summary, failures
            failures.append({"source": op, "error": "存在缺失或无效日期，继续检查备用来源。"})
        except Exception as e:
            failures.append({"source": op, "error": str(e)})
    if candidates:
        # 整段选一个覆盖最好的来源，不拼接不同复权绝对价。
        d, summary = max(candidates, key=lambda x: x[1]["valid_days"])
        return d, summary, failures
    raise ValueError("所有行情来源均不可用：" + str(failures))


def get_macro(client, config, start, end):
    events, failures, status = [], [], []
    for series in config.get("macro", []):
        ops = ["macro"]
        if series in {"cn_pmi", "cn_cpi_yoy"}:
            if config["provider"] == "tushare":
                ops = ["macro_ts"]
            elif config["provider"] == "auto" and token_value():
                ops.append("macro_ts")
        success = False
        for op in ops:
            try:
                result = client.call(op, series=series, start=start, end=end)
                d = macro_events(series, frame(result), result, start, end)
                if d.empty:
                    raise ValueError("所选时间段没有非空指标值。")
                events.append(d)
                from .catalog import FRED
                monthly = series in {"cn_pmi", "cn_cpi_yoy"} or (series in FRED and FRED[series]["frequency"] == "monthly")
                expected_periods = [str(x) for x in pd.period_range(start[:7], end[:7], freq="M")] if monthly else []
                status.append({"series": series, "source": result["source"], "rows": len(d),
                               "first_period": d["period"].min(), "last_period": d["period"].max(),
                               "missing_months": sorted(set(expected_periods) - set(d["period"])),
                               "signal_eligible_rows": int(d["signal_eligible"].sum())})
                success = True
                break
            except Exception as e:
                failures.append({"source": op, "series": series, "error": str(e)})
        if not success:
            status.append({"series": series, "rows": 0, "error": "未取得所需数据"})
    urls = list(config.get("nbs_urls", []))
    for page in range(int(config.get("nbs_crawl_pages", 0))):
        try:
            urls.extend(client.call("nbs_index", page=page)["urls"])
        except Exception as e:
            failures.append({"source": "nbs_index", "error": str(e)})
            break
    for url in dict.fromkeys(urls):
        try:
            event = client.call("nbs_url", url=url)["event"]
            if event["series"] in config.get("macro", []) and start[:7] <= event["period"] <= end[:7]:
                events.append(pd.DataFrame([event]))
        except Exception as e:
            failures.append({"source": "nbs_release", "url": url, "error": str(e)})
    for entry in config.get("nbs_saved_pages", []):
        try:
            from .providers import nbs_article
            event = nbs_article(Path(entry["file"]).read_text(encoding="utf-8"), entry["url"])
            if event["series"] in config.get("macro", []) and start[:7] <= event["period"] <= end[:7]:
                events.append(pd.DataFrame([event]))
        except Exception as e:
            failures.append({"source": "nbs_saved_page", "error": str(e)})
    combined = pd.concat(events, ignore_index=True) if events else pd.DataFrame(columns=EVENT_COLUMNS)
    # 同一统计期保留“当前下载”和“公告原值”两个版本，只有后者默认进历史信号。
    return combined.drop_duplicates().reset_index(drop=True), status, failures


def make_report(out, quality):
    lines = ["# 数据接入检查结果", "", "这次完成的是数据准备；尚未运行资产配置或回测。", "",
             f"请求区间：{quality['requested_start']} 至 {quality['requested_end']}。", "",
             f"实际获取：{quality['fetch_start']} 至 {quality['effective_end']}（含指标预热期）。", "",
             f"状态：{quality['status']}。", "",
             "| 标的 | 实际来源 | 有效/应有交易日 | 成交额缺失日 | 未解释的问题 |",
             "| --- | --- | --- | --- | --- |"]
    for item in quality["market"]:
        if "error" in item:
            lines.append(f"| {item['code']} | 未取得 | — | — | 查看 quality.json 中的具体错误 |")
        else:
            issues = "；".join(f"{k}: {v}" for k, v in item["issues"].items()) or "无"
            lines.append(f"| {item['code']} | {item['source']} | {item['valid_days']}/{item['expected_days']} | {item['amount_missing_days']} | {issues} |")
    lines += ["", "宏观指标：", ""]
    for item in quality["macro"]:
        lines.append(f"- {item['series']}：{item['rows']} 条；" + (f"覆盖 {item['first_period']} 至 {item['last_period']}。" if item['rows'] else "未取得数据。"))
        if item.get("missing_months"):
            lines.append("  缺少月份：" + "、".join(item["missing_months"]) + "；未发布和接口缺项需结合来源核对，未填补。")
    lines += ["", "本次可直接继续的工作：", ""] + ["- " + x for x in quality["notes"]]
    lines += ["", "文件入口：", "", "- prices.csv：原始价、复权价、收益与缺失状态。",
              "- asset_mapping.csv：代码、来源代码、证券类型。", "- calendar.csv：日历、周/月/季末和下一交易日。",
              "- macro_observations.csv：指标数值、统计期、发布时间、版本。",
              "- macro_asof.csv：按国内收盘后决策时点对齐的可用宏观值。",
              "- quality.json：完整问题和数据是否可用的标记。",
              "- manifest.json、raw/：本次请求、版本、来源与原始响应快照。", ""]
    content = "\n".join(lines)
    (out / "数据检查报告.md").write_text(content, encoding="utf-8")
    # 不依赖前端环境，双击即可打开。
    table = pd.DataFrame(quality["market"]).drop(columns=["off_calendar_dates", "issues"], errors="ignore").to_html(index=False, escape=True)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>数据检查报告</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#203047}h1{color:#123f63}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}td,th{padding:8px;border:1px solid #cbd5df}pre{white-space:pre-wrap;font:15px/1.8 system-ui;background:#f3f6f9;padding:24px;border-radius:12px}</style><h1>数据接入检查</h1>'
    page += table + '<pre>' + html.escape(content) + '</pre></html>'
    (out / "数据检查报告.html").write_text(page, encoding="utf-8")


def run(request_path, out_path, cache_path, refresh=False, timeout=75):
    c = read_request(request_path)
    out = Path(out_path).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("输出目录已有内容，请为这次运行指定一个新目录。")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "request.json", c)
    today = pd.Timestamp.now(tz="Asia/Shanghai").tz_localize(None).normalize()
    # 日线以 16:00 后为已完整；白天请求“今天”时先截止昨天。
    if pd.Timestamp.now(tz="Asia/Shanghai").hour < 16:
        today -= pd.Timedelta(days=1)
    end = min(pd.Timestamp(c["end"]), today)
    if pd.Timestamp(c["start"]) > end:
        raise ValueError("请求区间尚未发生，当前无完整日线可取。")
    start = pd.Timestamp(c["start"]) - pd.Timedelta(days=c["lookback_days"])
    # 向后读取到下一季度边界，确保尾段不能误判为周/月/季末。
    horizon = max(end.to_period("Q").end_time.normalize(), end + pd.Timedelta(days=15))
    # 未来年日历尚未公布时，当前已发布日历范围内的周期仍可用。
    horizon = min(horizon, pd.Timestamp(end.year, 12, 31))
    first, last = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    client = Client(cache_path, refresh=refresh, timeout=timeout)
    quality = {"status": "running", "requested_start": c["start"], "requested_end": c["end"],
               "fetch_start": first, "effective_end": last, "market": [], "macro": [], "failures": [], "notes": []}
    write_json(out / "quality.json", quality)
    try:
        cal, failures = get_calendar(client, first, horizon.strftime("%Y-%m-%d"), c["provider"])
        quality["failures"].extend(failures)
        cal_slice = cal.loc[cal["date"].between(start, end)].copy()
        cal_slice["in_requested_range"] = cal_slice["date"].ge(pd.Timestamp(c["start"]))
        save_csv(cal_slice, out / "calendar.csv")
        save_csv(pd.DataFrame(c["assets"]), out / "asset_mapping.csv")
        prices = []
        for asset in c["assets"]:
            print("正在获取 " + asset["code"], flush=True)
            try:
                d, summary, failures = get_market(client, asset, first, last, cal, c["provider"])
                d["in_requested_range"] = d["date"].ge(pd.Timestamp(c["start"]))
                prices.append(d)
                quality["market"].append(summary)
                quality["failures"].extend(dict(x, code=asset["code"]) for x in failures)
            except Exception as e:
                quality["market"].append({"code": asset["code"], "error": str(e)})
            write_json(out / "quality.json", quality)
        all_prices = pd.concat(prices, ignore_index=True) if prices else pd.DataFrame(columns=["date", "code", "close_raw", "close_adjusted", "quality_status"])
        save_csv(all_prices, out / "prices.csv")
        if not all_prices.empty:
            save_csv(all_prices.loc[~all_prices["quality_status"].eq("ok")], out / "missing_and_invalid.csv")
        print("正在获取宏观数据", flush=True)
        events, macro_status, failures = get_macro(client, c, first, last)
        save_csv(events, out / "macro_observations.csv")
        aligned = align_macro(events, cal_slice)
        save_csv(aligned, out / "macro_asof.csv")
        quality["macro"], quality["failures"] = macro_status, quality["failures"] + failures
        price_ready = all("error" not in x and not x["off_calendar_dates"] and
                          all(k in {"before_listing", "after_delisting"} for k in x["issues"]) for x in quality["market"])
        quality["price_research_ready"] = price_ready
        quality["cross_asset_liquidity_ready"] = price_ready and all(x.get("amount_missing_days", 1) == 0 for x in quality["market"])
        quality["macro_signal_coverage"] = {s: {"eligible_observations": int((events["series"].eq(s) & events["signal_eligible"].eq(True)).sum()),
                                                    "pit_verified_observations": int((events["series"].eq(s) & events["pit_verified"].eq(True)).sum())}
                                            for s in c.get("macro", [])}
        quality["macro_asof_coverage"] = {s: {"decision_days": len(cal_slice),
            "usable_days": int(aligned.loc[aligned["series"].eq(s), "value"].notna().sum())}
            for s in c.get("macro", [])}
        quality["status"] = "ready_with_notes" if price_ready and all(x["rows"] > 0 for x in macro_status) else "partial"
        quality["notes"] = [
            "价格研究" + ("可以继续；保留预热期后再计算策略。" if price_ready else "需要先处理缺失或失败的资产。"),
            "成交额、成交量单位不齐的来源可用于价格研究；跨资产流动性筛选需补齐字段或确认单位。",
            "PMI/CPI 当前历史表用于查看；补到统计局原公告的月份才自动进入历史宏观信号。",
            "美国 CPI、失业率和非农为当前修订历史；可用 fred-vintage 另取指定历史版本，再用于宏观回测。",
            "VIX/美联储利率按纽约次日零点可用的教学假设对齐，未声称取得历史逐次发布版本。",
            "世界银行全球 GDP 是年度修订数据，用作背景比较，不直接作为月度交易信号。",
            "商品 ETF 上市前不补造价格；未知缺失不当作停牌，不做线性插值或反向填充。",
            "收盘后决策与下一交易日执行已由日历分开；本套装尚未判断涨跌停、停牌及实际能否成交。"]
        if last != c["end"]:
            quality["notes"].append("请求结束日期已裁剪到本地当前可获取完整日线的日期。")
        make_report(out, quality)
    except Exception as e:
        quality["status"] = "failed"
        quality["fatal_error"] = str(e)
        raise
    finally:
        # 每次运行留下快照，后续刷新缓存不会改动这次的原始证据。
        raw_dir = out / "raw"
        raw_dir.mkdir(exist_ok=True)
        for item in client.log:
            key = item.get("cache_key")
            if key:
                shutil.copy2(client.cache / (key + ".json"), raw_dir / (key + ".json"))
        for index, entry in enumerate(c.get("nbs_saved_pages", [])):
            if Path(entry["file"]).is_file():
                shutil.copy2(entry["file"], raw_dir / f"nbs_saved_{index}.html")
        versions = {}
        for package in ["akshare", "tushare", "pandas", "numpy", "requests"]:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "not_installed"
        write_json(out / "manifest.json", {"created_at": now(), "request": c, "versions": versions,
                   "calls": client.log, "data_in_distribution": False, "credential_saved": False})
        write_json(out / "quality.json", quality)
    return quality
