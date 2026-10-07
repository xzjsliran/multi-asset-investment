"""可选：用个人免费 FRED API Key 取得指定历史日期能看到的版本。"""
import os
from pathlib import Path
import re

import pandas as pd
import requests


def fred_vintage(series_id, start, end, asof):
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        raise ValueError("此选项需要个人 FRED_API_KEY；普通 FRED 下载无需 Key。")
    if not re.fullmatch(r"[A-Z0-9]+", series_id):
        raise ValueError("请使用 FRED 原始序列代码，例如 PAYEMS。")
    if pd.Timestamp(start) > pd.Timestamp(end) or pd.Timestamp(asof) > pd.Timestamp.now().normalize():
        raise ValueError("检查观察区间和历史版本日期。")
    url = "https://api.stlouisfed.org/fred/series/observations"
    try:
        r = requests.get(url, params={"api_key": key, "file_type": "json", "series_id": series_id,
                "observation_start": start, "observation_end": end,
                "realtime_start": asof, "realtime_end": asof, "limit": 100000}, timeout=(8, 30))
        r.raise_for_status()
        body = r.json()
        if int(body.get("count", 0)) > len(body.get("observations", [])):
            raise ValueError("历史版本结果超过单页，请缩短区间。")
    except Exception as exc:
        raise ValueError(str(exc).replace(key, "[FRED_API_KEY]")) from None
    d = pd.DataFrame(body.get("observations", []))
    if d.empty:
        raise ValueError("指定日期下没有可用观测。")
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d["series_id"] = series_id
    d["vintage_asof"] = asof
    # 这是“该日版本快照”，不能将其当作每条数值最初的发布时间。
    d["available_at"] = (pd.Timestamp(asof)+pd.Timedelta(days=1)).tz_localize("America/New_York").isoformat()
    d["availability_rule"] = "snapshot_next_new_york_midnight"
    return d


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
        d = d.set_index("date").sort_index()
        d = d.reindex(pd.date_range(d.index.min(), d.index.max(), freq="MS"))
        values = pd.to_numeric(d["value"], errors="coerce")
        available = (pd.Timestamp(asof) + pd.Timedelta(days=1)).tz_localize("America/New_York").isoformat()
        for key, info in selected.items():
            transformed = values
            if info.get("transform") == "yoy":
                transformed = (values / values.shift(12) - 1) * 100
            elif info.get("transform") == "diff":
                transformed = values.diff()
            for date, value in transformed.dropna().items():
                rows.append({"series": key, "period": date.strftime("%Y-%m"), "value": float(value),
                             "unit": info["unit"], "release_at": None, "available_at": available,
                             "availability_rule": "snapshot_next_new_york_midnight", "source": "fred_historical_snapshot",
                             "source_url": "https://fred.stlouisfed.org/series/" + source_id,
                             "vintage": str(asof), "signal_eligible": True, "pit_verified": False})
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)
