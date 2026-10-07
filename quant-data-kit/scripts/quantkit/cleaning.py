"""可独立验证的清理规则：交易日、缺失、复权和宏观可用时点。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from .catalog import FRED


def dates(values):
    # Tushare 的 YYYYMMDD 先转字符串，避免被当成纳秒时间戳。
    return pd.to_datetime(values.astype(str), errors="coerce", format="mixed").dt.normalize()


def unique_dates(df):
    d = df.copy()
    if "date" not in d:
        raise ValueError("接口没有 date 字段。")
    d["date"] = dates(d["date"])
    if d["date"].isna().any():
        raise ValueError("日期无法解析，不能直接丢弃后继续计算。")
    exact = int(d.duplicated().sum())
    d = d.drop_duplicates()
    if d.duplicated("date").any():
        raise ValueError("同一日期出现不同记录，需更换或核对来源。")
    return d.sort_values("date").reset_index(drop=True), exact


def calendar_frame(raw, source):
    d = raw.copy()
    if "cal_date" in d:
        all_dates = dates(d["cal_date"])
        coverage_end = all_dates.max()
        d = d.loc[d["is_open"].astype(str).eq("1")].rename(columns={"cal_date": "trade_date"})
    else:
        coverage_end = dates(d["trade_date"]).max()
    t = dates(d["trade_date"]).dropna().drop_duplicates().sort_values().reset_index(drop=True)
    if t.empty:
        raise ValueError("交易日历为空。")
    out = pd.DataFrame({"date": t})
    out["next_trade_date"] = out["date"].shift(-1)
    for label, freq in [("week", "W-FRI"), ("month", "M"), ("quarter", "Q-DEC")]:
        periods = out["date"].dt.to_period(freq)
        last = out.groupby(periods)["date"].transform("max")
        # 不能把日历覆盖末尾的半个月误认为真正月末。
        complete = periods.dt.end_time.dt.normalize() <= coverage_end
        out["is_" + label + "_end"] = out["date"].eq(last) & complete
    out["source"] = source
    out["coverage_end"] = coverage_end
    return out


def clean_market(raw, adjusted, asset, calendar, start, end, metadata):
    raw, raw_dups = unique_dates(raw)
    adjusted, adj_dups = unique_dates(adjusted)
    if raw.empty or adjusted.empty:
        raise ValueError("原始行情或复权行情为空。")
    for field in ["open", "high", "low", "close"]:
        if field not in raw or field not in adjusted:
            raise ValueError("行情缺少 " + field)
        raw[field] = pd.to_numeric(raw[field], errors="coerce")
        adjusted[field] = pd.to_numeric(adjusted[field], errors="coerce")
    a = adjusted[["date", "close"]].rename(columns={"close": "close_adjusted"})
    raw = raw.merge(a, on="date", how="left", validate="one_to_one")
    raw = raw.loc[raw["date"].between(pd.Timestamp(start), pd.Timestamp(end))].copy()
    for col in ["volume", "amount"]:
        raw[col] = pd.to_numeric(raw.get(col, pd.Series(np.nan, index=raw.index)), errors="coerce")
    expected = calendar.loc[calendar["date"].between(pd.Timestamp(start), pd.Timestamp(end)), "date"]
    off_calendar = raw.loc[~raw["date"].isin(expected), "date"].dt.strftime("%Y-%m-%d").tolist()
    raw = raw.loc[raw["date"].isin(expected)]
    grid = pd.DataFrame({"date": expected}).merge(raw, on="date", how="left", validate="one_to_one")
    prices = grid[["open", "high", "low", "close", "close_adjusted"]]
    numeric_ok = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
    ohlc_ok = (grid["high"] >= grid[["open", "close", "low"]].max(axis=1)) & (grid["low"] <= grid[["open", "close", "high"]].min(axis=1))
    activity_ok = (grid["volume"].isna() | grid["volume"].ge(0)) & (grid["amount"].isna() | grid["amount"].ge(0))
    valid = numeric_ok & ohlc_ok & activity_ok
    reason = pd.Series("ok", index=grid.index)
    reason.loc[~valid] = "invalid_price_or_activity"
    reason.loc[grid["close"].isna()] = "missing_unexplained"
    first = raw["date"].min()
    reason.loc[grid["close"].isna() & grid["date"].lt(first)] = "before_first_observation_unverified"
    for field, status, relation in [("list_date", "before_listing", "lt"), ("delist_date", "after_delisting", "gt")]:
        if asset.get(field):
            mask = getattr(grid["date"], relation)(pd.Timestamp(asset[field]))
            reason.loc[mask & grid["close"].isna()] = status
            # 若日期元数据与实价冲突，不把冲突当成正常价格。
            reason.loc[mask & grid["close"].notna()] = "metadata_conflict"
            valid.loc[mask] = False
    grid["quality_status"] = reason
    grid["price_valid"] = valid
    grid["code"] = asset["code"]
    grid["source"] = metadata["source"]
    grid["currency"] = "CNY"
    grid["volume_native"] = grid.pop("volume")
    grid["volume_unit"] = metadata["volume_unit"]
    grid["volume_shares"] = grid["volume_native"] * 100 if metadata["volume_unit"] == "lot_100" else np.nan
    amount_scale = 1000 if metadata["amount_unit"] == "thousand_CNY" else 1
    grid["amount_cny"] = grid.pop("amount") * amount_scale
    grid["close_raw"] = grid.pop("close")
    grid["adjustment_factor_derived"] = grid["close_adjusted"] / grid["close_raw"]
    # 日收益只在相邻两个交易日均有有效复权价时计算；缺失两边不跨日连接。
    research = grid["close_adjusted"].where(valid)
    grid["return_adjusted"] = research.pct_change(fill_method=None)
    grid["price_available_at"] = (grid["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai")
    grid["adjustment"] = "hfq_vendor_series"
    summary = {"code": asset["code"], "source": metadata["source"], "expected_days": len(grid),
               "valid_days": int(valid.sum()), "first_observation": str(first.date()) if pd.notna(first) else None,
               "last_observation": str(raw["date"].max().date()) if not raw.empty else None,
               "exact_duplicates_removed": raw_dups + adj_dups, "off_calendar_dates": off_calendar,
               "issues": {str(k): int(v) for k, v in reason.value_counts().items() if k != "ok"},
               "volume_unit": metadata["volume_unit"], "amount_missing_days": int(grid["amount_cny"].isna().sum())}
    return grid, summary


EVENT_COLUMNS = ["series", "period", "value", "unit", "release_at", "available_at", "availability_rule",
                 "source", "source_url", "vintage", "signal_eligible", "pit_verified"]


def macro_events(series, raw, meta, start, end):
    rows = []
    if series in FRED and FRED[series].get("transform"):
        raw = raw.copy()
        field = "observation_date" if "observation_date" in raw else "DATE"
        raw[field] = pd.to_datetime(raw[field])
        raw = raw.sort_values(field).set_index(field)
        # 补出缺失月份再变换，不能把“上一个记录”误当成“上个月”。
        raw = raw.reindex(pd.date_range(raw.index.min(), raw.index.max(), freq="MS"))
        col = FRED[series]["id"]
        values = pd.to_numeric(raw[col], errors="coerce")
        raw[col] = (values / values.shift(12) - 1) * 100 if FRED[series]["transform"] == "yoy" else values.diff()
        raw = raw.rename_axis(field).reset_index()
    for r in raw.to_dict("records"):
        release, available = None, None
        eligible, pit = False, False
        rule, vintage = "release_and_vintage_unverified", "latest_download"
        if series in {"cn_pmi", "cn_cpi_yoy"}:
            if meta["source"] == "tushare":
                m = str(r["month"])
                period = m[:4] + "-" + m[4:6]
                value = r.get("pmi010000") if series == "cn_pmi" else r.get("nt_yoy")
            else:
                import re
                m = re.search(r"(\d{4})年(\d{1,2})月", r["月份"])
                if not m:
                    raise ValueError("宏观统计月份格式变化。")
                period = f"{m[1]}-{int(m[2]):02d}"
                value = r["制造业-指数"] if series == "cn_pmi" else r["全国-同比增长"]
            unit = "index" if series == "cn_pmi" else "percent_yoy"
            if not pd.Timestamp(start).to_period("M") <= pd.Period(period) <= pd.Timestamp(end).to_period("M"):
                continue
        elif series in FRED or series == "vix":
            field = "observation_date" if "observation_date" in r else "DATE"
            stamp = pd.Timestamp(r[field]).normalize()
            monthly = series in FRED and FRED[series]["frequency"] == "monthly"
            in_range = (pd.Timestamp(start).to_period("M") <= stamp.to_period("M") <= pd.Timestamp(end).to_period("M")) if monthly else (pd.Timestamp(start) <= stamp <= pd.Timestamp(end))
            if not in_range:
                continue
            period = stamp.strftime("%Y-%m" if monthly else "%Y-%m-%d")
            value = r[FRED[series]["id"]] if series in FRED else r["CLOSE"]
            unit = FRED[series]["unit"] if series in FRED else "index_points"
            if monthly:
                vintage = "latest_revised"
            else:
                # DFF 常次个美国营业日上午发布；本版保守延迟四个自然日至纽约中午。
                # 其他日数据采用纽约次日零点假设，均明确未核验逐次发布时间。
                if series == "fed_effective":
                    available = (stamp + pd.Timedelta(days=4, hours=12)).tz_localize("America/New_York").isoformat()
                    rule = "four_calendar_days_noon_new_york_assumption"
                else:
                    available = (stamp + pd.Timedelta(days=1)).tz_localize("America/New_York").isoformat()
                    rule = "next_new_york_midnight_assumption"
                eligible = True
                vintage = "downloaded_daily_history"
        else:
            period, value, unit = str(r["date"]), r["value"], "percent_annual_growth"
            vintage = "latest_revised"
        value = pd.to_numeric(value, errors="coerce")
        if not np.isfinite(value):
            continue
        rows.append(dict(series=series, period=period, value=float(value), unit=unit, release_at=release,
                         available_at=available, availability_rule=rule, source=meta["source"],
                         source_url=meta.get("source_url", ""), vintage=vintage, signal_eligible=eligible, pit_verified=pit))
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def align_macro(events, calendar):
    """只沿用已可获得的观测，不反向填充；超期值单独标为失效。"""
    output = []
    if events.empty:
        return pd.DataFrame(columns=["decision_at", "series", "period", "value", "available_at", "stale"])
    for series, data in events.groupby("series"):
        right = data.loc[data["signal_eligible"].eq(True)].copy()
        right["available_at"] = pd.to_datetime(right["available_at"], utc=True, errors="coerce", format="mixed")
        right = right.dropna(subset=["available_at"]).sort_values("available_at")
        if right.empty:
            continue
        # 公告链接重复可去重；同一发布时点不同值必须检查。
        if right.groupby("available_at")["value"].nunique().gt(1).any():
            raise ValueError(series + " 同一发布时间存在冲突值。")
        right = right.drop_duplicates("available_at")
        left = pd.DataFrame({"decision_at": (calendar["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")})
        out = pd.merge_asof(left.sort_values("decision_at"), right, left_on="decision_at", right_on="available_at", direction="backward")
        out["series"] = series
        age = (out["decision_at"] - out["available_at"]).dt.total_seconds() / 86400
        maximum = 62 if series.startswith("cn_") else (10 if series == "vix" else 14)
        out["stale"] = age.gt(maximum) | age.isna()
        out["value"] = out["value"].where(~out["stale"])
        output.append(out)
    return pd.concat(output, ignore_index=True) if output else pd.DataFrame(columns=["decision_at", "series", "period", "value", "available_at", "stale"])
