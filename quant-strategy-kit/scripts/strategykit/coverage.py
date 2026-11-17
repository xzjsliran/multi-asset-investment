"""在回测前核对交易日历、证券行情和风险估计所需的共同样本。"""
from __future__ import annotations

import pandas as pd

from .config import all_assets, schedule
from .data import benchmark_start
from .equity_selection import DEFAULT_POOL, FIELDS, METHOD, pool_snapshot
from .risk import proxy_returns
from .regime import allocation
from .selection import active_assets


def date_text(day):
    return str(day.date()) if pd.notna(day) else None


def issue(code, message, **details):
    return {"code": code, "message": message, **details}


def asset_coverage(config, prices, sessions):
    result = []
    for asset in all_assets(config):
        code = asset["code"]
        valid = prices.reindex(columns=[code])[code].dropna()
        # 这一天仅满足固定证券的信号日前价格条件。优化另需共同收益样本。
        later = prices.index[prices.index > valid.index.min()]
        result.append({
            "code": code,
            "name": asset.get("name", code),
            "first_price": date_text(valid.index.min()),
            "last_price": date_text(valid.index.max()),
            "first_signal_eligible_session": date_text(later.min()),
            "missing_sessions": int(valid.reindex(sessions).isna().sum()),
        })
    return result


def fixed_history_gaps(config, data):
    """复用固定标的的生效区间和调仓日，检查引擎所要求的信号日前价格。"""
    first_price = {code: series.first_valid_index() for code, series in data.prices.items()}
    missing = []
    for sleeve in config["sleeves"]:
        if sleeve["selection"]["method"] != "fixed":
            continue
        events = schedule(data.calendar, config["start"], config["end"], sleeve["rebalance"])
        for execution, signal in events.items():
            for asset in active_assets(sleeve, execution):
                code = asset["code"]
                first = first_price.get(code)
                if pd.isna(first) or first > signal:
                    missing.append({
                        "code": code,
                        "sleeve": sleeve["id"],
                        "execution_date": date_text(execution),
                        "first_price": date_text(first),
                    })
    return missing


def proposed_start(config, data, gaps):
    if any(gap["first_price"] is None for gap in gaps):
        return None
    earliest_signal = max(pd.Timestamp(gap["first_price"]) for gap in gaps)
    later = data.calendar[(data.calendar > earliest_signal) & (data.calendar < pd.Timestamp(config["end"]))]
    if not len(later):
        return None
    candidate = date_text(later[0])
    # 日期变化也会改变生效证券和首次建仓信号。按修订后的配置复核一次。
    adjusted = {**config, "start": candidate}
    if fixed_history_gaps(adjusted, data):
        return None
    return candidate


def check_risk_window(config, data, warnings, blockers):
    settings = config["allocation"]
    if settings["method"] != "risk_parity":
        return None
    events = schedule(data.calendar, config["start"], config["end"], config["allocation_rebalance"])
    minimum = settings["min_observations"]
    first, first_eligible, insufficient = None, None, []
    for execution, signal in events.items():
        sample, eligible = proxy_returns(settings, data.prices, signal)
        count = len(sample)
        if first is None:
            first = {
                "execution_date": date_text(execution),
                "signal_date": date_text(signal),
                "observations": count,
                "min_observations": minimum,
                "missing_observations": max(0, minimum - count),
                "has_signal_prices": bool(eligible),
            }
        if not eligible or count < minimum:
            insufficient.append(date_text(execution))
        elif first_eligible is None:
            first_eligible = date_text(execution)
    first["first_eligible_execution"] = first_eligible
    first["insufficient_events"] = insufficient
    if insufficient:
        target = blockers if settings["fallback"] == "error" else warnings
        target.append(issue(
            "risk_warmup", "部分调仓的风险代理缺少信号日行情或共同收益样本。",
            affected_events=len(insufficient),
            first_affected_date=insufficient[0],
            configured_fallback=settings["fallback"],
        ))
    return first


def check_fundamentals(config, data, blockers):
    """核对历史选股所需的财务截面时点。"""
    sleeves = [
        sleeve for sleeve in config["sleeves"]
        if sleeve["selection"]["method"] in {"quality_value", "fundamental", METHOD}
    ]
    if not sleeves:
        return
    snapshots = (
        pd.DatetimeIndex(data.features["snapshot_date"].dropna().unique()).sort_values()
        if "snapshot_date" in data.features else pd.DatetimeIndex([])
    )
    for sleeve in sleeves:
        if sleeve["selection"]["method"] == METHOD:
            missing = sorted(FIELDS - set(data.features))
            events = schedule(data.calendar, config["start"], config["end"], sleeve["rebalance"])
            universe = sleeve["selection"].get("universe", DEFAULT_POOL)
            bad = []
            for signal in events.values():
                if not missing:
                    try:
                        pool_snapshot(data.features, signal, universe)
                        continue
                    except ValueError:
                        pass
                bad.append(date_text(signal))
            if missing or bad:
                blockers.append(issue("missing_equity_factor_snapshot", "缺少指定股票池的历史因子截面。",
                                      sleeve=sleeve["id"], universe=universe, missing_fields=missing, signal_dates=bad))
            continue
        max_age = sleeve["selection"].get("snapshot_max_age_days", 7)
        events = schedule(data.calendar, config["start"], config["end"], sleeve["rebalance"])
        for signal in events.values():
            prior = snapshots[snapshots <= signal]
            if not len(prior) or (signal - prior[-1]).days > max_age:
                blockers.append(issue(
                    "missing_fundamental_snapshot", "该资产部分在信号日缺少可用的历史财务截面。",
                    sleeve=sleeve["id"], signal_date=date_text(signal),
                ))
                break


def check_macro(config, data, warnings):
    macro = config["macro"]
    if not macro["enabled"]:
        return
    events = schedule(data.calendar, config["start"], config["end"], config["allocation_rebalance"])
    events.update(schedule(data.calendar, config["start"], config["end"], macro.get("rebalance", "monthly")))
    previous = {sleeve["id"]: sleeve["weight"] for sleeve in config["sleeves"]}
    missing = []
    for execution, signal in sorted(events.items()):
        previous, decision = allocation(config, data.macro, signal, previous)
        if decision.get("fallback"):
            missing.append(date_text(execution))
    if missing:
        warnings.append(issue(
            "macro_inputs_unavailable", "部分调仓缺少按当时已公布时间可用的宏观指标。",
            affected_events=len(missing), first_affected_date=missing[0],
        ))


def preview(config, data):
    """用最终供给引擎的标准数据预检。任何区间或标的变动均由用户决定。"""
    start, end = pd.Timestamp(config["start"]), pd.Timestamp(config["end"])
    dates = data.calendar
    sessions = dates[(dates >= start) & (dates <= end)]
    blockers, warnings, options = [], [], []
    candidate = None
    risk_window = None
    if len(sessions) < 2:
        blockers.append(issue("insufficient_calendar", "研究区间至少需要两个交易日。"))
    elif sessions[0] == dates[0]:
        blockers.append(issue("no_initial_signal", "至少需要一个研究开始前的交易日作为首次建仓信号。"))
    declared_end = data.provenance.get("calendar_coverage_end")
    if declared_end and pd.Timestamp(declared_end) < end:
        blockers.append({
            "code": "incomplete_calendar",
            "message": "交易日历没有覆盖请求结束日。请补充日历或确认缩短区间。",
            "calendar_end": declared_end,
        })
    elif not declared_end and len(dates) and len(pd.bdate_range(dates[-1] + pd.Timedelta(days=1), end)):
        # 没有覆盖元数据时，尾部工作日可能是节假日也可能是漏数，不能直接截断。
        blockers.append({
            "code": "unverified_calendar_end",
            "message": "已载入日历末日之后仍有工作日，需核实休市或补齐日历。",
            "calendar_end": date_text(dates[-1]),
        })
    if not blockers:
        gaps = fixed_history_gaps(config, data)
        if gaps:
            candidate = proposed_start(config, data, gaps)
            blockers.append({
                "code": "missing_fixed_history",
                "message": "部分固定证券在其调仓信号日前没有可用行情。",
                "first_affected": gaps[0],
                "affected_codes": sorted({gap["code"] for gap in gaps}),
            })
            if candidate:
                options.append({
                    "action": "change_start",
                    "start": candidate,
                    "message": "固定证券已满足信号日前行情。修订区间后重新检查完整覆盖。",
                })
        risk_window = check_risk_window(config, data, warnings, blockers)
        check_fundamentals(config, data, blockers)
        check_macro(config, data, warnings)
    if config.get("execution_price") == "open" and data.open_prices is None:
        blockers.append(issue("missing_open_prices", "开盘执行需要后复权开盘价。"))
    if blockers:
        options.append({
            "action": "supply_data",
            "message": "可提供同一证券、同一计价及复权口径的数据。修改证券或区间前应取得用户确认。",
        })
    benchmark = config.get("benchmark")
    missing_benchmark = benchmark and (not len(sessions) or
                        data.prices.reindex(index=sessions, columns=[benchmark["code"]]).isna().any().iloc[0])
    if benchmark and not missing_benchmark:
        level, _, _ = benchmark_start(data, benchmark, sessions[0], config.get("execution_price") == "open")
        missing_benchmark = pd.isna(level) or level <= 0
    if missing_benchmark:
        warnings.append({
            "code": "benchmark_coverage",
            "message": "基准行情未完整覆盖研究区间，配套报告将说明该对照缺失。",
            "asset": benchmark["code"],
        })
    quality = data.provenance.get("domestic_quality", {})
    if quality.get("status") == "partial":
        warnings.append({
            "code": "partial_source_data",
            "message": "境内取数有缺项，请根据本次策略的实际覆盖和缺口决定能否继续。",
            "quality_file": quality.get("path"),
        })
    assets = asset_coverage(config, data.prices, sessions)
    by_code = {item["code"]: item for item in assets}
    for row in quality.get("market", []):
        if row["code"] in by_code:
            by_code[row["code"]]["source"] = row.get("source")
            by_code[row["code"]]["issues"] = row.get("issues", {})
    # 证券可能按有效期有意晚进早出。仅对覆盖其有效期的区间计入行情缺口。
    incomplete = set()
    for sleeve in config["sleeves"]:
        for asset in sleeve["assets"]:
            active = sessions[
                (sessions >= pd.Timestamp(asset.get("from", "1900-01-01")))
                & (sessions < pd.Timestamp(asset.get("until", "2200-01-01")))
            ]
            code = asset["code"]
            if data.prices.reindex(index=active, columns=[code])[code].isna().any():
                incomplete.add(code)
    if incomplete:
        warnings.append(issue(
            "asset_price_gaps", "有效期内存在行情缺口。缺报价日的成交按既定规则处理。",
            affected_codes=sorted(incomplete),
        ))
    return {
        "status": "needs_input" if blockers else ("ready_with_notes" if warnings else "ready"),
        "next_action": "review_coverage_options" if blockers else "run_backtest",
        "requested": {"start": config["start"], "end": config["end"]},
        "effective_start": date_text(sessions.min()) if not blockers else None,
        "available_sessions": {"start": date_text(sessions.min()), "end": date_text(sessions.max()), "count": len(sessions)},
        "proposed_start": candidate,
        "assets": assets,
        "risk_window": risk_window,
        "blockers": blockers,
        "warnings": warnings,
        "options": options,
    }
