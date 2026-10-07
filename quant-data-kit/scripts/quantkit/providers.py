"""数据来源适配。全部为本地代码请求，不使用 MCP。"""
from __future__ import annotations

import io
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import pandas as pd
import requests

from .common import token_value
from .catalog import FRED

CN_COLUMNS = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
              "最低": "low", "成交量": "volume", "成交额": "amount"}


def pack(df, **meta):
    # 保留字段和精度，同时将 NaN 转为合法 JSON null。
    data = json.loads(df.to_json(orient="split", date_format="iso", double_precision=15))
    return {"columns": data["columns"], "data": data["data"], **meta}


def get(url, params=None):
    r = requests.get(url, params=params, timeout=(8, 25))
    r.raise_for_status()
    return r


def ts_query(api, **params):
    token = token_value()
    if not token:
        raise ValueError("本地尚未配置 Tushare token；可先使用 provider=free。")
    fields = params.pop("fields", "")
    r = requests.post("https://api.tushare.pro", json={"api_name": api, "token": token,
                      "params": params, "fields": fields}, timeout=(8, 25))
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise ValueError("Tushare " + api + ": " + str(body.get("msg", "接口失败")).replace(token, "[凭证]"))
    node = body.get("data") or {}
    return pd.DataFrame(node.get("items", []), columns=node.get("fields", []))


def market_em(p):
    import akshare as ak
    fn = ak.fund_etf_hist_em if p["kind"] == "etf" else ak.stock_zh_a_hist
    args = dict(symbol=p["symbol"], period="daily", start_date=p["start"].replace("-", ""),
                end_date=p["end"].replace("-", ""), adjust=p["adjust"])
    if p["kind"] == "stock":
        args["timeout"] = 20
    d = fn(**args).rename(columns=CN_COLUMNS)
    return pack(d, source="akshare_eastmoney", adjustment=p["adjust"] or "raw",
                volume_unit="lot_100", amount_unit="CNY")


def market_tx(p):
    # 腾讯公开日线下载：按年请求，避免一次请求被最大条数截断。
    # 从返回键检查复权类型，不能把 day 静默充当 hfqday。
    symbol = p["tx_symbol"]
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    body = get(url, {"param": f'{symbol},day,{p["start"]},{p["end"]},640,{p["adjust"]}'}).json()
    node = (body.get("data") or {}).get(symbol) or {}
    key = "hfqday" if p["adjust"] == "hfq" else "day"
    values = node.get(key)
    if values is None:
        raise ValueError("腾讯未返回所需 " + key + " 序列，保留为不可用，换其他来源。")
    d = pd.DataFrame([row[:6] for row in values], columns=["date", "open", "close", "high", "low", "volume"])
    # 公开下载端点不同品种的成交量单位未在同一文档明确，保留原单位。
    # 不用猜测的换算量进行跨资产流动性筛选。
    return pack(d, source="tencent_public", adjustment=p["adjust"] or "raw",
                volume_unit="vendor_native_unverified", amount_unit="unavailable")


def market_ts(p):
    api = "fund_daily" if p["kind"] == "etf" else "daily"
    d = ts_query(api, ts_code=p["ts_code"], start_date=p["start"].replace("-", ""),
                 end_date=p["end"].replace("-", ""))
    if len(d) >= 5000:
        raise ValueError("行情可能达到接口返回上限，请缩短请求分段。")
    d = d.rename(columns={"trade_date": "date", "vol": "volume"})
    if p["adjust"] == "hfq" and not d.empty:
        factor_api = "fund_adj" if p["kind"] == "etf" else "adj_factor"
        f = ts_query(factor_api, ts_code=p["ts_code"], start_date=p["start"].replace("-", ""),
                     end_date=p["end"].replace("-", ""))
        if f.empty:
            raise ValueError("复权因子为空。")
        f = f.rename(columns={"trade_date": "date"})
        if f.duplicated("date").any():
            raise ValueError("复权因子日期重复。")
        d = d.merge(f[["date", "adj_factor"]], on="date", how="left", validate="one_to_one")
        for col in ("open", "close", "high", "low"):
            d[col] = pd.to_numeric(d[col], errors="coerce") * pd.to_numeric(d["adj_factor"], errors="coerce")
    return pack(d, source="tushare", adjustment=p["adjust"] or "raw", volume_unit="lot_100", amount_unit="thousand_CNY")


def apply_sina_factors(data, events):
    """只采用明确的乘法因子；基金含累计现金项时另换来源。"""
    d = data.copy()
    d["date"] = pd.to_datetime(d["date"].astype(str), format="mixed")
    factors = pd.DataFrame(events).rename(columns={"d": "date", "f": "factor"})
    if factors.empty or not {"date", "factor"}.issubset(factors):
        raise ValueError("新浪没有返回可核对的复权因子。")
    factors["date"] = pd.to_datetime(factors["date"], errors="raise")
    factors = factors.loc[factors["date"] <= d["date"].max()].copy()
    if "u" in factors and pd.to_numeric(factors["u"], errors="raise").ne(0).any():
        raise ValueError("新浪该基金因子含累计现金项，不能当乘法复权；换用东方财富或 Tushare。")
    if "s" in factors and pd.to_numeric(factors["s"], errors="raise").ne(1).any():
        raise ValueError("新浪基金存在份额转换项，本版不混用其复权算法。")
    factors["factor"] = pd.to_numeric(factors["factor"], errors="raise")
    if factors.duplicated("date").any() or not factors["factor"].gt(0).all():
        raise ValueError("复权因子重复或无效。")
    out = pd.merge_asof(d.sort_values("date"), factors[["date", "factor"]].sort_values("date"), on="date", direction="backward")
    if out["factor"].isna().any():
        raise ValueError("复权因子未覆盖早期行情；不能反向填充未来因子。")
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce") * out["factor"]
    return out


def market_sina(p):
    import akshare as ak
    if p["kind"] == "etf":
        d = ak.fund_etf_hist_sina(symbol=p["tx_symbol"])
    else:
        d = ak.stock_zh_a_daily(symbol=p["tx_symbol"], start_date=p["start"].replace("-", ""),
                               end_date=p["end"].replace("-", ""), adjust="")
    if d.empty:
        raise ValueError("新浪原始行情为空。")
    d["date"] = pd.to_datetime(d["date"].astype(str), format="mixed")
    d = d.loc[d["date"].between(pd.Timestamp(p["start"]), pd.Timestamp(p["end"]))]
    meta = {}
    if p["adjust"] == "hfq":
        url = f'https://finance.sina.com.cn/realstock/company/{p["tx_symbol"]}/hfq.js'
        text = get(url).text
        # 仅解析 JSON，不执行网页中的 JavaScript。
        node, _ = json.JSONDecoder().raw_decode(text.split("=", 1)[1].lstrip())
        d = apply_sina_factors(d, node.get("data", []))
        meta = {"factor_source_url": url, "factor_events": node.get("data", [])}
    return pack(d, source="akshare_sina", adjustment=p["adjust"] or "raw",
                volume_unit="vendor_native_unverified", amount_unit="CNY", **meta)


def nbs_article(html, url):
    """从原公告提取当期值和公布时点，不把当前修订表回填到历史。"""
    if urlparse(url).scheme != "https" or urlparse(url).hostname != "www.stats.gov.cn":
        raise ValueError("只将国家统计局原公告解析为已核对的发布记录。")
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", "", soup.get_text(" ", strip=True)).replace("％", "%")
    title_node = soup.find("meta", attrs={"name": "ArticleTitle"})
    title = title_node.get("content", "") if title_node else (soup.title.get_text() if soup.title else text[:120])
    title = re.sub(r"\s+", "", title)
    if "解读" in title:
        raise ValueError("请选择指标发布原文，解读文章不作为原始发布时间。")
    period = re.search(r"(20\d{2})年(\d{1,2})月", title)
    if not period:
        raise ValueError("未从公告标题识别统计月份。")
    date_node = soup.find("meta", attrs={"name": "PubDate"})
    stamp = date_node.get("content", "") if date_node else ""
    date_match = re.search(r"20\d{2}[-/]\d{2}[-/]\d{2}(?:\s*\d{2}:\d{2}(?::\d{2})?)?", stamp)
    if not date_match or len(date_match.group()) == 10:
        precise = re.search(r"20\d{2}[-/]\d{2}[-/]\d{2}\d{2}:\d{2}(?::\d{2})?", text)
        date_match = precise or date_match
    if not date_match:
        raise ValueError("公告未找到可核对的发布时间。")
    s = date_match.group().replace("/", "-")
    if len(s) > 10 and s[10].isdigit():
        s = s[:10] + " " + s[10:]
    date_only = len(s) == 10
    released = pd.Timestamp(s).tz_localize("Asia/Shanghai")
    available = released + pd.Timedelta(days=1) if date_only else released
    if "采购经理" in title:
        m = re.search(r"(?<!非)制造业采购经理(?:人)?指数[（(]PMI[）)]为?(\d+(?:\.\d+)?)%?", text)
        if not m:
            raise ValueError("未识别制造业 PMI 当期值。")
        series, value, unit = "cn_pmi", float(m.group(1)), "index"
    elif "居民消费价格" in title:
        m = re.search(r"(?:全国)?居民消费价格同比(上涨|下降|持平)(\d+(?:\.\d+)?)?%?", text)
        if not m:
            raise ValueError("未识别全国 CPI 同比值。")
        series, unit = "cn_cpi_yoy", "percent_yoy"
        value = 0.0 if m.group(1) == "持平" else float(m.group(2)) * (-1 if m.group(1) == "下降" else 1)
    else:
        raise ValueError("本版公告解析支持 PMI 和 CPI。")
    return {"series": series, "period": f"{period[1]}-{int(period[2]):02d}", "value": value,
            "unit": unit, "release_at": released.isoformat(), "available_at": available.isoformat(),
            "availability_rule": "next_day_if_date_only" if date_only else "published_timestamp",
            "source": "nbs_release", "source_url": url, "vintage": "original_release_page",
            "signal_eligible": True, "pit_verified": True}


def nbs_url(p):
    url = p["url"]
    if urlparse(url).scheme != "https" or urlparse(url).hostname != "www.stats.gov.cn":
        raise ValueError("公告链接请使用 https://www.stats.gov.cn/ 下的原文。")
    r = get(url)
    r.encoding = "utf-8"
    return {"event": nbs_article(r.text, url)}


def nbs_index(p):
    # 小型按页爬虫；由调用方限定页数，遇到网络错误即报告。
    page = int(p["page"])
    url = "https://www.stats.gov.cn/sj/zxfb/" + (f"index_{page}.html" if page else "index.html")
    r = get(url)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        if "解读" not in title and re.search(r"20\d{2}年\d+月", title) and ("采购经理" in title or "居民消费价格" in title):
            target = urljoin(url, a["href"])
            if urlparse(target).hostname == "www.stats.gov.cn":
                urls.append(target)
    if not urls:
        raise ValueError("本页未识别 PMI/CPI 公告链接，请检查官网目录结构。")
    return {"urls": list(dict.fromkeys(urls)), "source_url": url}


def macro(p):
    import akshare as ak
    series = p["series"]
    if series in {"cn_pmi", "cn_cpi_yoy"}:
        d = ak.macro_china_pmi() if series == "cn_pmi" else ak.macro_china_cpi()
        return pack(d, source="akshare_eastmoney", vintage="latest_download")
    if series in FRED:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
        info = FRED[series]
        # 同比要读前 12 个月；新增就业要读前一期。下载后再裁剪输出统计期。
        first = (pd.Timestamp(p["start"]) - pd.Timedelta(days=400)).strftime("%Y-%m-%d") if info.get("transform") else p["start"]
        r = get(url, {"id": info["id"], "cosd": first, "coed": p["end"]})
        return pack(pd.read_csv(io.StringIO(r.text)), source="fred", source_url="https://fred.stlouisfed.org/series/" + info["id"], series_id=info["id"])
    if series == "vix":
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
        return pack(pd.read_csv(io.StringIO(get(url).text)), source="cboe", source_url=url)
    if series == "world_gdp_growth":
        url = "https://api.worldbank.org/v2/country/WLD/indicator/NY.GDP.MKTP.KD.ZG"
        b = get(url, {"format": "json", "date": p["start"][:4]+":"+p["end"][:4], "per_page": 1000}).json()
        if not isinstance(b, list) or len(b) != 2 or b[1] is None:
            raise ValueError("世界银行未返回指标记录。")
        return pack(pd.DataFrame(b[1])[["date", "value"]], source="worldbank",
                    source_url=url, last_updated=b[0].get("lastupdated"), vintage="latest_revised")
    raise ValueError("未知宏观序列。")


def fetch(op, p):
    if op == "ts_table":
        # 策略所需的历史财务扩展仍由本地代码请求；认证不进入参数和日志。
        from .fundamentals import paged_query
        return pack(paged_query(p["api"], p.get("params", {})), source="tushare", api=p["api"])
    if op == "us_history":
        from .overseas import yahoo_history
        return pack(yahoo_history(p["code"], p["start"], p["end"]), source="yahoo_finance", code=p["code"])
    if op == "market_em":
        return market_em(p)
    if op == "market_tx":
        return market_tx(p)
    if op == "market_ts":
        return market_ts(p)
    if op == "market_sina":
        return market_sina(p)
    if op == "calendar_ak":
        import akshare as ak
        return pack(ak.tool_trade_date_hist_sina(), source="akshare_sina_calendar")
    if op == "calendar_ts":
        return pack(ts_query("trade_cal", exchange="SSE", start_date=p["start"].replace("-", ""),
                            end_date=p["end"].replace("-", "")), source="tushare_trade_cal")
    if op == "macro":
        return macro(p)
    if op == "macro_ts":
        api = "cn_pmi" if p["series"] == "cn_pmi" else "cn_cpi"
        return pack(ts_query(api, start_m=p["start"][:7].replace("-", ""),
                            end_m=p["end"][:7].replace("-", "")), source="tushare", vintage="latest_download")
    if op == "nbs_url":
        return nbs_url(p)
    if op == "nbs_index":
        return nbs_index(p)
    if op == "probe_ts":
        return pack(ts_query("trade_cal", exchange="SSE", start_date="20250102", end_date="20250110"), source="tushare")
    raise ValueError("未知操作 " + op)
