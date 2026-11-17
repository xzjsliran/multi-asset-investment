"""场外净值与美元活期的人民币研究行情。"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import numpy as np
import pandas as pd

from .overseas import normalize_history


BOC_ARCHIVE = "https://www.boc.cn/fimarkets/lilv/fd33/"


def bank_rate(html):
    """读取中国银行境内外币挂牌表的美元活期年利率，单位为百分数。"""
    rows = BeautifulSoup(html, "html.parser").find_all("tr")
    for row in rows:
        cells = [re.sub(r"\s+", "", cell.get_text(strip=True)) for cell in row.find_all("td")]
        if len(cells) >= 2 and cells[0].strip("*＊") == "美元":
            return float(cells[1].rstrip("%"))
    raise ValueError("外汇存款利率表中未找到美元活期报价。")


def bank_rate_history(get, start, end):
    links = BeautifulSoup(get(BOC_ARCHIVE).content, "html.parser").find_all("a", href=True)
    dated = {}
    for link in links:
        match = re.fullmatch(r"外汇存款利率表\s*(\d{4}-\d{2}-\d{2})", link.get_text(strip=True))
        url = urljoin(BOC_ARCHIVE, link["href"])
        if match and url.startswith(BOC_ARCHIVE) and match[1] <= end:
            dated[match[1]] = url
    prior = max((day for day in dated if day <= start), default=None)
    if prior is None:
        raise ValueError("中行挂牌存款表未覆盖预热起点，请提供生效日利率表。")
    return pd.DataFrame([{"from": day, "rate_percent": bank_rate(get(url).content), "source": url}
                         for day, url in sorted(dated.items()) if day >= prior])


def calendar_marks(calendar):
    days = pd.DataFrame({"date": pd.DatetimeIndex(calendar).normalize().unique().sort_values()})
    days["decision_at"] = (days["date"] + pd.Timedelta(hours=16)).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
    return days


def nav_marks(code, raw, calendar):
    """以最近已公布净值估值。一次新净值对应一次研究用申赎机会。"""
    fields = ["nav_date", "ann_date", "unit_nav", "adj_nav"]
    if not set(fields) <= set(raw) or raw.empty:
        raise ValueError(code + " 没有场外单位净值、复权净值或公布日期。")
    d = raw[fields].drop_duplicates().copy()
    for col in ("nav_date", "ann_date"):
        d[col] = pd.to_datetime(d[col].astype(str), format="mixed", errors="coerce")
    for col in ("unit_nav", "adj_nav"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.loc[d["ann_date"].ge(d["nav_date"]) & d[["unit_nav", "adj_nav"]].gt(0).all(axis=1)
              & np.isfinite(d[["unit_nav", "adj_nav"]]).all(axis=1)].copy()
    # 数据源只给公布日。次日零点后使用，同一净值日采用首个能观察到的版本。
    d = d.loc[~d.duplicated(["nav_date", "ann_date"], keep=False)].sort_values(["ann_date", "nav_date"])
    d = d.drop_duplicates("nav_date")
    d = d.loc[d["nav_date"].eq(d["nav_date"].cummax())]
    if d.empty:
        raise ValueError(code + " 的场外净值没有可核验的公布日期及有效值。")
    d["price_available_at"] = (d["ann_date"] + pd.Timedelta(days=1)).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
    out = pd.merge_asof(calendar_marks(calendar), d, left_on="decision_at", right_on="price_available_at")
    valid = out["unit_nav"].notna() & (out["decision_at"] - out["price_available_at"]).dt.days.le(10)
    out["close_raw"], out["close_adjusted"] = out["unit_nav"].where(valid), out["adj_nav"].where(valid)
    out["price_valid"] = valid
    out["tradable"] = valid & out["nav_date"].ne(out["nav_date"].shift())
    out["currency"], out["code"], out["source"] = "CNY", code, "tushare_fund_nav_O"
    out["execution_note"] = "最新已公布场外净值模拟，实际按基金确认净值和渠道限额"
    return out


def deposit_marks(code, history, rates, calendar, source=None):
    """按生效日挂牌利率逐日累计美元利息，再使用可见汇率折人民币。"""
    fx = normalize_history(history).rename(columns={"date": "fx_date", "Close": "usd_cny"})
    fx["price_available_at"] = (fx["fx_date"] + pd.Timedelta(days=1)).dt.tz_localize("UTC")
    if source == "fred_DEXCHUS":
        from pandas.tseries.holiday import USFederalHolidayCalendar
        # H.10 通常在下一周第一个美国营业日发布上一周各日汇价。
        business_day = pd.offsets.CustomBusinessDay(calendar=USFederalHolidayCalendar())
        releases = (fx["fx_date"] + pd.offsets.Week(weekday=0)).map(business_day.rollforward)
        fx["price_available_at"] = (releases + pd.Timedelta(hours=16, minutes=15)).dt.tz_localize(
            "America/New_York").dt.tz_convert("UTC")
    fx["usd_cny"] = pd.to_numeric(fx["usd_cny"], errors="coerce")
    fx = fx.loc[fx["usd_cny"].gt(0) & np.isfinite(fx["usd_cny"])]
    out = pd.merge_asof(calendar_marks(calendar), fx[["price_available_at", "fx_date", "usd_cny"]],
                        left_on="decision_at", right_on="price_available_at")
    d = rates.copy()
    if not {"from", "rate_percent", "source"} <= set(d):
        raise ValueError("美元存款牌价需要 from、rate_percent 和 source。")
    d["from"] = pd.to_datetime(d["from"], errors="raise")
    d["rate_percent"] = pd.to_numeric(d["rate_percent"], errors="raise")
    if d.empty or d["from"].duplicated().any() or not d["rate_percent"].between(0, 100).all():
        raise ValueError("美元存款牌价须为按生效日给出的非负年利率百分数，日期不得重复。")
    duration = pd.DataFrame({"date": pd.date_range(out["date"].min(), out["date"].max())})
    duration = pd.merge_asof(duration, d.sort_values("from"), left_on="date", right_on="from")
    if duration["rate_percent"].isna().any():
        raise ValueError("美元活期牌价没有覆盖预热起点。")
    duration["balance_index"] = (1 + duration["rate_percent"] / (100 * 365)).cumprod()
    out = out.merge(duration[["date", "rate_percent", "balance_index", "source"]], on="date", validate="one_to_one")
    valid = out["usd_cny"].notna() & (out["decision_at"] - out["price_available_at"]).dt.days.le(7)
    out["close_raw"] = out["usd_cny"].where(valid)
    out["close_adjusted"] = (out["usd_cny"] * out["balance_index"]).where(valid)
    out["price_valid"], out["tradable"] = valid, valid
    out["currency"], out["code"] = "CNY", code
    out["execution_note"] = "美元活期牌价模拟及已知汇率"
    return out
