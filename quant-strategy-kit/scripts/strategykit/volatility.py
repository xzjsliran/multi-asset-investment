"""波指用各自的收盘序列预热。波动状态按日评分，实际按组合频率调仓。"""
import pandas as pd

from .regime import known_observations


def signal_history(events, series, signal, fast, slow):
    rows, cutoff = known_observations(events, series, signal)
    if rows.empty:
        return pd.DataFrame(), cutoff
    rows = rows.set_index(pd.to_datetime(rows["period"], errors="coerce"))
    rows = rows.loc[rows.index.notna() & (rows.index <= signal) & rows["value"].gt(0)].copy()
    rows["fast"] = rows["value"].rolling(fast).mean()
    rows["slow"] = rows["value"].rolling(slow).mean()
    return rows.dropna(subset=["slow"]), cutoff


def volatility_state(settings, events, signal):
    primary = settings["series"]
    fast, slow = settings["fast"], settings["slow"]
    history, cutoff = signal_history(events, primary, signal, fast, slow)
    switch_date = None
    if settings.get("switch_to"):
        following, _ = signal_history(events, settings["switch_to"], signal, fast, slow)
        if not following.empty:
            following = following.loc[following.index >= pd.Timestamp(settings["switch_from"])]
        if not following.empty:
            # 沪深300的均线只使用自身数据。预热完成前沿用上证50。
            switch_date = following.index[0]
            history = pd.concat([history.loc[history.index < switch_date], following]) if not history.empty else following
    info = {"series": settings["switch_to"] if switch_date is not None else primary,
            "groups": settings["groups"], "state": "指标不足", "missing": True}
    if history.empty:
        return info
    last = history.iloc[-1]
    entry = history["value"].ge(settings["enter"]) & history["fast"].gt(history["slow"])
    exit_ = history["value"].le(settings["exit"])
    transitions = entry.loc[entry | exit_]
    defensive = bool(transitions.iloc[-1]) if not transitions.empty else False
    stale = (cutoff - last["available_at"]).total_seconds() > settings["max_age_days"] * 86400
    return {**info, "state": "防守" if defensive else "正常", "defensive": defensive, "missing": stale,
            "reason": "最新收盘已过期，沿用最后可判断的状态" if stale else None,
            "period": str(last["period"]), "value": float(last["value"]), "available_at": last["available_at"].isoformat(),
            "fast_average": float(last["fast"]), "slow_average": float(last["slow"]),
            "switch_date": str(switch_date.date()) if switch_date is not None else None}
