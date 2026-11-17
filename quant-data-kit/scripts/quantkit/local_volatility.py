"""华创金工公开页的国内期权收盘波指。"""
import json
import re

import pandas as pd


HCVIX_URL = "https://service.hcquant.com/production/hcvix_public.php"
CN_VOLATILITY = {"cn_vix50": "HCVIX50", "cn_vix300": "HCVIX300"}


def volatility_closes(html):
    payload = re.search(r"\bvar\s+charts\s*=\s*", html)
    if not payload:
        raise ValueError("华创波指公开页未返回时序数据。")
    charts, _ = json.JSONDecoder().raw_decode(html[payload.end():])
    series = {s["name"]: s["data"] for chart in charts for s in chart["series"]}
    closes = []
    for key, display in CN_VOLATILITY.items():
        d = pd.DataFrame(series[display], columns=["Date", key]).drop_duplicates()
        d["Date"] = pd.to_datetime(d["Date"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        d[key] = pd.to_numeric(d[key], errors="coerce")
        # 同日若有互相矛盾的点位，保留缺口，避免选定未经核对的版本。
        d = d.loc[~d.duplicated("Date", keep=False) & d[key].gt(0)].set_index("Date")
        closes.append(d)
    return pd.concat(closes, axis=1).sort_index().reset_index()
