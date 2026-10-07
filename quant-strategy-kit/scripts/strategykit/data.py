"""读取第一部分的标准数据；价格估值与可交易状态分别保留。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


def booleans(series):
    return series.astype(str).str.lower().isin(["true", "1", "1.0"])


def date_series(series):
    return pd.to_datetime(series.astype(str), format="mixed", errors="coerce").dt.normalize()


@dataclass
class Dataset:
    prices: pd.DataFrame
    tradable: pd.DataFrame
    calendar: pd.DatetimeIndex
    features: pd.DataFrame
    macro: pd.DataFrame
    provenance: dict

    @classmethod
    def load(cls, directory):
        import json
        root = Path(directory)
        raw = pd.read_csv(root / "prices.csv", dtype={"code": str}, low_memory=False)
        required = {"date", "code", "close_adjusted"}
        if not required.issubset(raw):
            raise ValueError("prices.csv 需要 date、code、close_adjusted。")
        raw["date"] = date_series(raw["date"])
        if raw["date"].isna().any() or raw.duplicated(["date", "code"]).any():
            raise ValueError("行情日期无效或存在重复的日期和代码。")
        if "currency" in raw and not raw["currency"].eq("CNY").all():
            raise ValueError("外币行情需要先换算为 CNY，不能混合美元价格直接相加。")
        raw["close_adjusted"] = pd.to_numeric(raw["close_adjusted"], errors="coerce")
        valid = np.isfinite(raw["close_adjusted"]) & raw["close_adjusted"].gt(0)
        if "price_valid" in raw:
            valid &= booleans(raw["price_valid"])
        if "price_available_at" in raw:
            known = pd.to_datetime(raw["price_available_at"], utc=True, format="mixed", errors="coerce")
            cutoff = (raw["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
            valid &= known.notna() & (known <= cutoff)
        raw.loc[~valid, "close_adjusted"] = np.nan
        raw["can_trade"] = valid
        if "tradable" in raw:
            raw["can_trade"] &= booleans(raw["tradable"])
        if "volume_native" in raw:
            raw["can_trade"] &= raw["volume_native"].isna() | raw["volume_native"].gt(0)
        cal = pd.read_csv(root / "calendar.csv")
        dates = pd.DatetimeIndex(date_series(cal["date"]).dropna()).unique().sort_values()
        px = raw.pivot(index="date", columns="code", values="close_adjusted").reindex(dates)
        tradable = raw.pivot(index="date", columns="code", values="can_trade").reindex(dates).fillna(False).astype(bool)
        features = pd.DataFrame()
        if (root / "stock_features.csv").exists():
            features = pd.read_csv(root / "stock_features.csv", dtype={"code": str})
            for col in ["snapshot_date", "ann_date", "annual_ann_date", "end_date", "annual_end_date", "list_date", "delist_date"]:
                features[col] = date_series(features[col])
            features["consecutive_years"] = booleans(features["consecutive_years"])
        macro = pd.read_csv(root / "macro_observations.csv") if (root / "macro_observations.csv").exists() else pd.DataFrame()
        if not macro.empty:
            macro["available_at"] = pd.to_datetime(macro["available_at"], utc=True, format="mixed", errors="coerce")
            macro["signal_eligible"] = booleans(macro["signal_eligible"])
            macro["value"] = pd.to_numeric(macro["value"], errors="coerce")
        meta_path = root / "strategy_data_manifest.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"source": "quant-data-kit", "directory": str(root.resolve())}
        return cls(px, tradable, dates, features, macro, meta)
