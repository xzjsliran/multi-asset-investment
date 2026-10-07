"""美国 ETF 与汇率获取、按中国决策时点对齐。"""
from __future__ import annotations

import pandas as pd


def yahoo_history(code, start, end):
    import yfinance as yf
    # end 为开区间，多加一天覆盖用户要求的最后一天。
    d = yf.Ticker(code).history(start=start, end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                               auto_adjust=False, actions=True, timeout=25, raise_errors=True)
    if d.empty or "Close" not in d or (code != "CNY=X" and "Adj Close" not in d):
        raise ValueError(code + " 没有取得完整价格与复权列。")
    d = d.copy()
    d["date"] = d.index.tz_localize(None).normalize() if d.index.tz is not None else d.index.normalize()
    return d.reset_index(drop=True)


def normalize_history(d):
    d = d.copy()
    if "date" not in d:
        d["date"] = d.index.tz_localize(None).normalize() if getattr(d.index, "tz", None) else d.index
    d["date"] = pd.to_datetime(d["date"].astype(str), format="mixed").dt.normalize()
    return d.sort_values("date").drop_duplicates("date")


def align_us(code, history, fx, calendar):
    """纽约收盘在中国次日才已知；汇率按报价日期结束后可用处理。"""
    d, f = normalize_history(history), normalize_history(fx)
    d["quote_available_at"] = (d["date"] + pd.Timedelta(hours=16)).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    f["fx_available_at"] = (f["date"] + pd.Timedelta(days=1)).dt.tz_localize("UTC")
    f = f.rename(columns={"Close": "usd_cny", "date": "fx_date"})
    target = pd.DataFrame({"date": pd.to_datetime(calendar)})
    target["decision_at"] = (target["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
    d = d.rename(columns={"date": "us_quote_date", "Close": "close_usd", "Adj Close": "adjusted_usd", "Volume": "us_volume"})
    out = pd.merge_asof(target.sort_values("decision_at"), d[["quote_available_at", "us_quote_date", "close_usd", "adjusted_usd", "us_volume"]].sort_values("quote_available_at"),
                        left_on="decision_at", right_on="quote_available_at", direction="backward")
    out = pd.merge_asof(out, f[["fx_available_at", "fx_date", "usd_cny"]].sort_values("fx_available_at"),
                        left_on="decision_at", right_on="fx_available_at", direction="backward")
    valid = out[["adjusted_usd", "close_usd", "usd_cny"]].gt(0).all(axis=1)
    valid &= (out["decision_at"] - out["quote_available_at"]).dt.days.le(7)
    valid &= (out["decision_at"] - out["fx_available_at"]).dt.days.le(7)
    out["close_adjusted"] = (out["adjusted_usd"] * out["usd_cny"]).where(valid)
    out["close_raw"] = (out["close_usd"] * out["usd_cny"]).where(valid)
    out["tradable"] = valid & out["us_quote_date"].ne(out["us_quote_date"].shift()) & out["us_volume"].gt(0)
    out["price_valid"] = valid
    out["price_available_at"] = out[["quote_available_at", "fx_available_at"]].max(axis=1)
    out["currency"], out["code"] = "CNY", code
    out["source"] = "yahoo_adjusted_usd_times_known_fx"
    out["execution_note"] = "next_CN_decision_using_completed_US_close"
    return out
