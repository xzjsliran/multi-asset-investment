"""历史财务、估值、行业和名称截面。仅整理输入，不在这里决定买哪些股票。"""
from __future__ import annotations

from pathlib import Path
import time

import pandas as pd

from .common import frame, write_json, now

FIELDS = "ts_code,ann_date,end_date,roe,roe_yearly,debt_to_assets,profit_dedt,ocfps,update_flag"


def paged_query(api, params):
    from .providers import ts_query
    allowed = {"stock_basic", "index_classify", "index_member_all", "daily_basic", "fina_indicator_vip", "fina_indicator", "namechange"}
    if api not in allowed:
        raise ValueError("未登记的历史财务扩展接口。")
    limit = 100 if api == "fina_indicator" else 1000
    chunks, fingerprints = [], set()
    for offset in range(0, 200_000, limit):
        page = ts_query(api, **params, limit=limit, offset=offset)
        digest = tuple(pd.util.hash_pandas_object(page, index=False).tolist())
        if len(page) and digest in fingerprints:
            raise ValueError(api + " 未响应翻页参数，需改用更小分段。")
        fingerprints.add(digest)
        chunks.append(page)
        if len(page) < limit:
            return pd.concat(chunks, ignore_index=True).drop_duplicates()
        time.sleep(.4)
    raise ValueError("翻页超过预定范围，未把截断数据当成完整结果。")


def concat_files(root, pattern):
    paths = sorted(Path(root).glob(pattern))
    if not paths:
        raise ValueError("缺少原始快照 " + pattern)
    return pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True).drop_duplicates()


def prepare_tables(basic, members, fina, names):
    basic = basic.drop_duplicates("ts_code").copy()
    members, fina, names = members.copy(), fina.copy(), names.copy()
    for d, columns in [(basic, ["list_date", "delist_date"]), (members, ["in_date", "out_date"]),
                       (fina, ["ann_date", "end_date"]), (names, ["start_date", "end_date", "ann_date"])]:
        for col in columns:
            d[col] = pd.to_datetime(d[col].astype(str), format="mixed", errors="coerce")
    fina = fina.loc[fina["ann_date"] > fina["end_date"]].copy()
    if "update_flag" not in fina:
        fina["update_flag"] = "0"
    fina["update_flag"] = fina["update_flag"].fillna("0").astype(str)
    fina = fina.sort_values(["ts_code", "end_date", "ann_date", "update_flag"]).drop_duplicates(["ts_code", "end_date", "ann_date"], keep="last")
    return basic, members, fina, names


def build_snapshot(signal, valuation, basic, members, fina, names):
    """保留未筛选股票；所有财报合并都先按公告日过滤。"""
    signal = pd.Timestamp(signal)
    v = valuation.loc[valuation["ts_code"].str.match(r"^(6(?:00|01|03|05|88|89)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)$")].copy()
    if v["ts_code"].duplicated().any():
        raise ValueError("估值截面同一股票重复。")
    if not pd.to_datetime(v["trade_date"].astype(str), format="mixed").eq(signal).all():
        raise ValueError("估值快照日期与信号日期不一致。")
    m = members.loc[(members["in_date"] <= signal) & (members["out_date"].isna() | (members["out_date"] > signal))]
    m = m.sort_values(["ts_code", "in_date", "l1_code"]).drop_duplicates("ts_code", keep="last")
    v = v.merge(m[["ts_code", "l1_name", "l3_name", "in_date", "out_date"]], on="ts_code", how="left", validate="one_to_one")
    v = v.merge(basic[["ts_code", "name", "list_date", "delist_date"]], on="ts_code", how="left", validate="one_to_one")
    n = names.loc[(names["start_date"] <= signal) & (names["end_date"].isna() | (names["end_date"] >= signal))
                  & (names["ann_date"].isna() | (names["ann_date"] <= signal))]
    n = n.sort_values(["ts_code", "start_date"]).drop_duplicates("ts_code", keep="last")
    v = v.merge(n[["ts_code", "name"]].rename(columns={"name": "historical_name"}), on="ts_code", how="left", validate="one_to_one")
    known = fina.loc[fina["ann_date"] < signal].sort_values(["ts_code", "end_date", "ann_date"])
    known = known.drop_duplicates(["ts_code", "end_date"], keep="last")
    latest = known.drop_duplicates("ts_code", keep="last")
    annual = known.loc[known["end_date"].dt.strftime("%m%d").eq("1231")]
    year = annual.groupby("ts_code").tail(1)
    pairs = annual.groupby("ts_code").tail(2)
    stats = pairs.groupby("ts_code").agg(annual_count=("end_date", "size"), min_two_year_profit=("profit_dedt", "min"),
                                          first_year=("end_date", "min"), last_year=("end_date", "max"))
    stats["consecutive_years"] = (stats["last_year"].dt.year - stats["first_year"].dt.year).eq(1)
    v = v.merge(latest[["ts_code", "ann_date", "end_date", "roe_yearly", "profit_dedt", "debt_to_assets"]], on="ts_code", how="left", validate="one_to_one")
    v = v.merge(year[["ts_code", "ann_date", "end_date", "roe", "ocfps"]].rename(columns={"ann_date": "annual_ann_date", "end_date": "annual_end_date", "roe": "annual_roe", "ocfps": "annual_ocfps"}), on="ts_code", how="left", validate="one_to_one")
    v = v.merge(stats[["annual_count", "min_two_year_profit", "consecutive_years"]], on="ts_code", how="left", validate="one_to_one")
    v["snapshot_date"] = signal
    v["financial_vintage"] = "vendor_history_filtered_by_announcement"
    return v.rename(columns={"ts_code": "code", "l1_name": "industry", "l3_name": "subindustry"})


def stock_features(dates, out, client=None, research=None):
    dates = sorted(set(pd.Timestamp(d).strftime("%Y%m%d") for d in dates))
    root = Path(research) / "raw" if research else None
    if research:
        tables = [concat_files(root, pattern) for pattern in ["stocks_*.parquet", "members_*.parquet", "fina_*.parquet", "name_history.parquet"]]
        valuations = {}
        for d in dates:
            p = root / f"valuation_{d}.parquet"
            if not p.exists():
                raise ValueError(f"research 没有 {d} 估值截面；请用联网 prepare 补取，或使用已有季度区间。")
            valuations[d] = pd.read_parquet(p)
    else:
        if client is None:
            raise ValueError("联网财务取数需要本地数据 Client。")
        def query(api, **params):
            result = frame(client.call("ts_table", api=api, params=params))
            time.sleep(.4)
            return result
        basic = pd.concat([query("stock_basic", list_status=s, fields="ts_code,name,list_date,delist_date,list_status") for s in ["L", "D", "P"]], ignore_index=True)
        industry_codes = set()
        for src in ["SW2014", "SW2021"]:
            industry_codes.update(query("index_classify", level="L1", src=src)["index_code"])
        members = pd.concat([query("index_member_all", l1_code=c, is_new=n) for c in sorted(industry_codes) for n in ["Y", "N"]], ignore_index=True)
        periods = pd.date_range(pd.Timestamp(dates[0]) - pd.DateOffset(years=3), pd.Timestamp(dates[-1]), freq="QE")
        fina = pd.concat([query("fina_indicator_vip", period=p.strftime("%Y%m%d"), fields=FIELDS) for p in periods], ignore_index=True)
        names = query("namechange")
        tables = [basic, members, fina, names]
        valuations = {d: query("daily_basic", trade_date=d, fields="ts_code,trade_date,pe,pe_ttm,pb,total_mv,dv_ttm") for d in dates}
    tables = prepare_tables(*tables)
    snapshots = [build_snapshot(d, valuations[d], *tables) for d in dates]
    result = pd.concat(snapshots, ignore_index=True)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    result.to_csv(out / "stock_features.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    write_json(out / "stock_features_manifest.json", {"dates": dates, "rows": len(result), "created_at": now(),
        "source": str(root.resolve()) if root else "Tushare local HTTPS",
        "historical_name_missing": int(result["historical_name"].isna().sum()),
        "industry_missing": int(result["industry"].isna().sum()),
        "financial_versions": "公告日期过滤供应商历史表；未逐份复原首次财报版本。"})
    return result
