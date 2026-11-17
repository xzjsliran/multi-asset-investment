"""下载指定历史日期可见的 FRED 数据，并保留版本证据。"""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import os
from pathlib import Path
import re

import pandas as pd
import requests


PUBLIC_SNAPSHOT = "alfred_public_snapshot"
GRAPH_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
GRAPH_LINES = 12


def public_snapshot(series_ids, vintages, start, end):
    """公开图表每条曲线使用明确的序列和日期。响应须回显相同版本。"""
    pairs = [(series_id, day) for day in vintages for series_id in series_ids]
    if not 1 <= len(pairs) <= GRAPH_LINES or len(set(pairs)) != len(pairs):
        raise ValueError("ALFRED 公开接口每批需要 1 至 12 个不重复的序列版本。")
    expected = {f"{series_id}_{day.replace('-', '')}": (series_id, day) for series_id, day in pairs}
    response = requests.get(GRAPH_URL, params={
        "id": ",".join(series for series, _ in pairs),
        "vintage_date": ",".join(day for _, day in pairs),
        "cosd": start, "coed": end,
    }, timeout=(6, 25))
    response.raise_for_status()
    data = pd.read_csv(StringIO(response.text))
    if set(data.columns) != {"observation_date", *expected}:
        raise ValueError("ALFRED 返回的序列或版本与所请求日期不一致。")
    data = data.rename(columns={"observation_date": "date"})
    data["date"] = pd.to_datetime(data["date"], errors="raise").dt.strftime("%Y-%m-%d")
    if data["date"].duplicated().any():
        raise ValueError("ALFRED 公开快照存在重复统计日期。")
    parts = []
    for column, (series, day) in expected.items():
        subset = data.loc[data["date"].between(start, min(day, end)), ["date", column]].copy()
        subset["value"] = pd.to_numeric(subset.pop(column), errors="coerce")
        parts.append(subset.assign(series_id=series, vintage_asof=day, source=PUBLIC_SNAPSHOT))
    return pd.concat(parts, ignore_index=True)


def signal_vintages(signal_dates):
    """北京时间16点处于同一个纽约日。按此前已结束的纽约日请求版本。"""
    days = pd.DatetimeIndex(signal_dates).normalize().unique().sort_values()
    return (days - pd.Timedelta(days=1)).strftime("%Y-%m-%d").tolist()


def fred_vintage(series_id, start, end, asof):
    if not re.fullmatch(r"[A-Z0-9]+", series_id):
        raise ValueError("请使用 FRED 原始序列代码，例如 PAYEMS。")
    if pd.Timestamp(start) > pd.Timestamp(end) or pd.Timestamp(asof) > pd.Timestamp.now().normalize():
        raise ValueError("检查观察区间和历史版本日期。")
    if os.getenv("FRED_API_KEY", "").strip():
        d = alfred_observations(series_id, start, end, asof, asof)
    else:
        d = public_snapshot([series_id], [asof], start, end)
    if d.empty:
        raise ValueError("指定日期下没有可用观测。")
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d["series_id"] = series_id
    d["vintage_asof"] = asof
    # 版本日期只有日，没有可核验的逐笔发布时间。
    d["available_at"] = (pd.Timestamp(asof)+pd.Timedelta(days=1)).tz_localize("America/New_York").isoformat()
    d["availability_rule"] = "snapshot_next_new_york_midnight"
    return d


def alfred_observations(series_id, start, end, first_version, last_version):
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        raise ValueError("历史版本需要个人 FRED_API_KEY，也可导入已有快照。")
    url = "https://api.stlouisfed.org/fred/series/observations"
    try:
        r = requests.get(url, params={"api_key": key, "file_type": "json", "series_id": series_id,
                "observation_start": start, "observation_end": end,
                "realtime_start": first_version, "realtime_end": last_version, "limit": 100000}, timeout=(8, 30))
        r.raise_for_status()
        body = r.json()
        if int(body.get("count", 0)) > len(body.get("observations", [])):
            raise ValueError("历史版本结果超过单页，请缩短区间。")
    except Exception as exc:
        raise ValueError(str(exc).replace(key, "[FRED_API_KEY]")) from None
    d = pd.DataFrame(body.get("observations", []))
    if d.empty:
        raise ValueError("指定日期下没有可用观测。")
    return d


def realtime_events(series, start, end):
    """一次取得版本生效区间，在每次发布或修订日重建当时的完整月度截面。"""
    from .catalog import FRED
    spec = FRED[series]
    if spec["frequency"] != "monthly":
        raise ValueError("ALFRED 批量版本用于月度指标。")
    observation_start = (pd.Timestamp(start) - pd.DateOffset(months=13)).strftime("%Y-%m-%d")
    history = alfred_observations(spec["id"], observation_start, end, start, end)
    versions = []
    for day in sorted(history["realtime_start"].unique()):
        view = history.loc[history["realtime_start"].le(day) & history["realtime_end"].ge(day), ["date", "value"]]
        versions.append(view.assign(series_id=spec["id"], vintage_asof=day))
    events = snapshot_events(pd.concat(versions, ignore_index=True))
    return events.loc[events["series"].eq(series)].copy()


def monthly_history(client, series, start, end, signal_dates):
    """优先取版本变动档案。公开通道按实际信号日批量获取所需快照。"""
    from .catalog import FRED
    from .common import frame

    ready, failures = {}, []
    if os.getenv("FRED_API_KEY", "").strip():
        for key in series:
            try:
                d = frame(client.call("fred_realtime", series=key, start=start, end=end))
                if d.empty:
                    raise ValueError("未返回历史版本。")
                ready[key] = d
            except (RuntimeError, ValueError) as exc:
                failures.append({"source": "alfred_api", "series": key, "error": str(exc)})
    remaining = sorted(set(series) - set(ready))
    vintages = signal_vintages(signal_dates)
    if not remaining or not vintages:
        return ready, failures
    ids = sorted({FRED[key]["id"] for key in remaining})
    batch_size = GRAPH_LINES // len(ids)
    batches = [vintages[i:i + batch_size] for i in range(0, len(vintages), batch_size)]
    first = (pd.Timestamp(start) - pd.DateOffset(months=13)).strftime("%Y-%m-%d")

    def read_batch(days):
        try:
            raw = client.call("alfred_public", series_ids=ids, vintages=days, start=first, end=end)
            return frame(raw), []
        except (RuntimeError, ValueError) as exc:
            return pd.DataFrame(), [{"source": PUBLIC_SNAPSHOT, "series": remaining, "vintages": days, "error": str(exc)}]

    snapshots = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for d, errors in pool.map(read_batch, batches):
            failures.extend(errors)
            if not d.empty:
                snapshots.append(d)
    if snapshots:
        events = snapshot_events(pd.concat(snapshots, ignore_index=True))
        for key in remaining:
            d = events.loc[events["series"].eq(key)].copy()
            if not d.empty:
                ready[key] = d
    return ready, failures


def save_vintage(series_id, start, end, asof, out):
    target = Path(out)
    if target.exists():
        raise ValueError("输出已存在，未覆盖。")
    d = fred_vintage(series_id, start, end, asof)
    target.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(target, index=False, encoding="utf-8-sig")
    return {"rows": len(d), "vintage_asof": asof, "out": str(target.resolve())}


def snapshot_events(snapshot):
    """把已取得的 FRED 历史快照转成策略模块可读的事件，不猜首次公布日。"""
    from .catalog import FRED
    from .cleaning import EVENT_COLUMNS
    rows = []
    for (source_id, asof), group in snapshot.groupby(["series_id", "vintage_asof"]):
        selected = {key: info for key, info in FRED.items() if info["id"] == source_id and info["frequency"] == "monthly"}
        if not selected:
            raise ValueError("快照转换目前支持美国 CPI、失业率和非农月度序列。")
        d = group.copy()
        d["date"] = pd.to_datetime(d["date"].astype(str), format="mixed")
        if d["date"].duplicated().any():
            raise ValueError("同一 FRED 快照有重复月份。")
        values = pd.to_numeric(d.set_index("date")["value"].sort_index(), errors="coerce")
        values = values.replace([float("inf"), float("-inf")], float("nan"))
        if values.first_valid_index() is None:
            continue
        # 排除尚未公布的尾部空行，保留已公布区间内的缺报，供连续期检查。
        first, last = values.first_valid_index(), values.last_valid_index()
        values = values.reindex(pd.date_range(first, last, freq="MS"))
        label = PUBLIC_SNAPSHOT if "source" in group and group["source"].eq(PUBLIC_SNAPSHOT).all() else "fred_historical_snapshot"
        available = (pd.Timestamp(asof) + pd.Timedelta(days=1)).tz_localize("America/New_York").isoformat()
        for key, info in selected.items():
            transformed = values
            if info.get("transform") == "yoy":
                transformed = ((values / values.shift(12) - 1) * 100).iloc[12:]
            elif info.get("transform") == "diff":
                transformed = values.diff().iloc[1:]
            # 已公布当期的同比也可能因上年缺报而无法计算。保留该期，明确报告缺数。
            for date, value in transformed.items():
                rows.append({"series": key, "period": date.strftime("%Y-%m"), "value": float(value),
                             "unit": info["unit"], "release_at": None, "available_at": available,
                             "availability_rule": "snapshot_next_new_york_midnight", "source": label,
                             "source_url": "https://alfred.stlouisfed.org/series?seid=" + source_id,
                             "vintage": str(asof), "signal_eligible": True, "pit_verified": False})
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)
