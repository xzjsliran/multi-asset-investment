"""从数据kit标准表提取原始报价；复权序列只供策略信号使用。"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

from .inputs import timestamp, number, currency


def validate_quotes(document, as_of, max_age_days):
    if document.get("schema_version") != 1 or not isinstance(document.get("quotes"), list):
        raise ValueError("报价需要schema_version=1及quotes列表。")
    cutoff = timestamp(as_of, "数据截止时间")
    rates = {"CNY": 1.}
    fx_times = {"CNY": cutoff.isoformat()}
    for row in document.get("fx", []):
        cur = currency(row["currency"])
        if cur in rates and cur != "CNY":
            raise ValueError("汇率币种重复。")
        value = number(row["rate_to_cny"], "汇率")
        known = timestamp(row["available_at"], "汇率可用时间")
        if value <= 0 or known > cutoff or (cutoff-known).total_seconds() / 86400 > max_age_days:
            raise ValueError("汇率必须为正且在截止时间前有效。")
        if cur == "CNY" and value != 1:
            raise ValueError("人民币兑人民币汇率须为1。")
        rates[cur], fx_times[cur] = value, known.isoformat()
    quotes = {}
    for row in document.get("quotes", []):
        code = row["code"]
        if code in quotes:
            raise ValueError("证券报价重复："+code)
        if row.get("price_basis") != "raw":
            raise ValueError("买卖数量须用原始报价计算，不能用复权价："+code)
        px, cur = number(row["price"], "参考价格"), currency(row["currency"])
        if px <= 0 or cur not in rates:
            raise ValueError("报价须为正且提供已知汇率："+code)
        known = timestamp(row["available_at"], "报价可用时间")
        if known > cutoff:
            raise ValueError("报价晚于截止时间："+code)
        if not row.get("source"):
            raise ValueError("报价须注明来源："+code)
        for key in ["buy_lot", "sell_lot"]:
            if number(row.get(key), key) <= 0:
                raise ValueError("买卖最小数量单位须为正；由实际产品规则提供。")
        if not isinstance(row.get("tradable"), bool):
            raise ValueError("tradable须为布尔值。")
        age = (cutoff-known).total_seconds()/86400
        quotes[code] = {**row, "price_cny": px*rates[cur], "age_days": age,
                        "usable_for_orders": row["tradable"] and age <= max_age_days}
    return quotes, rates, fx_times


def extract_quotes(directory, codes, as_of, instrument_rules):
    """rules[code]含currency/buy_lot/sell_lot；不猜交易单位或用复权价代替。"""
    raw = pd.read_csv(Path(directory)/"prices.csv", dtype={"code": str})
    if not {"price_available_at", "close_raw"} <= set(raw):
        raise ValueError("价格表需有原始价和可用时间；请用数据模块补取。")
    cutoff = timestamp(as_of, "数据截止时间")
    known = pd.to_datetime(raw["price_available_at"], utc=True, format="mixed", errors="coerce")
    dates = pd.to_datetime(raw["date"], format="mixed").dt.normalize()
    mask = known.le(cutoff) & raw["close_raw"].gt(0) & dates.le(cutoff.tz_convert("Asia/Shanghai").tz_localize(None).normalize())
    if "price_valid" in raw:
        mask &= raw["price_valid"].astype(str).str.lower().isin(["true", "1", "1.0"])
    raw = raw.loc[mask].copy()
    raw["_known"] = known.loc[raw.index]
    quotes, fx = [], {}
    for code in sorted(set(codes)):
        if code not in instrument_rules:
            raise ValueError("请补充交易币种及买卖数量单位："+code)
        rule = instrument_rules[code]
        rows = raw.loc[raw.code.eq(code)].sort_values("_known")
        if rows.empty:
            raise ValueError("截止时间前无原始报价："+code)
        row = rows.iloc[-1]
        cur = rule["currency"]
        if pd.notna(row.get("close_usd")):
            if cur != "USD":
                raise ValueError("该行情对应美元证券，请按USD交易并提供外币现金："+code)
            px = float(row["close_usd"])
            fx_row = {"currency": "USD", "rate_to_cny": float(row["usd_cny"]), "available_at": str(row["fx_available_at"])}
            if "USD" not in fx or timestamp(fx_row["available_at"], "汇率时间") > timestamp(fx["USD"]["available_at"], "汇率时间"):
                fx["USD"] = fx_row
            available_at = str(row["quote_available_at"])
        else:
            if cur != "CNY":
                raise ValueError("标准表没有该证券的原币报价，请另行提供："+code)
            px, available_at = float(row["close_raw"]), row["_known"].isoformat()
        quotes.append({"code": code, "price": px, "currency": cur, "available_at": available_at,
                       "execution_session": rule.get("execution_session"),
                       "price_basis": "raw", "source": str(row.get("source", "quant-data-kit")),
                       "buy_lot": rule["buy_lot"], "sell_lot": rule["sell_lot"],
                       "tradable": str(row.get("tradable", True)).lower() in {"true", "1", "1.0"}})
    return {"schema_version": 1, "quotes": quotes, "fx": list(fx.values())}
