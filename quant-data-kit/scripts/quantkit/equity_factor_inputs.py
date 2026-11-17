"""多因子选股所需的时点数据和可续用的 Tushare 快照。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from threading import Lock, local
import time

import pandas as pd
import requests

from .common import token_value
from .providers import ts_query


STATEMENTS = {
    "income": "n_income_attr_p",
    "cashflow": "n_cashflow_act,c_pay_acq_const_fiolta",
    "balancesheet": "total_hldr_eqy_exc_min_int,goodwill",
}
MARKET_FIELDS = {
    "daily": "ts_code,trade_date,open,close,vol,amount",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "stk_limit": "ts_code,trade_date,up_limit,down_limit",
}
DIVIDEND_FIELDS = "ts_code,ann_date,end_date,div_proc,ex_date,imp_ann_date,cash_div_tax,base_share"
_http = local()
_rate_lock = Lock()
_next_request = {}


def query(api, **params):
    """同一接口错开并发请求，避免一批快照同时触发限流。"""
    if not hasattr(_http, "session"):
        _http.session = requests.Session()
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_request.get(api, now))
        _next_request[api] = slot + .15
    time.sleep(max(0., slot - time.monotonic()))
    return ts_query(api, session=_http.session, **params)


def cached_query(root, filename, api, *, page_size=0, **params):
    """按原始快照分区保存；翻页忽略参数时停止，防止把截断数据当完整数据。"""
    path = root / (filename + ".parquet")
    if path.exists():
        return pd.read_parquet(path)
    for attempt in range(4):
        try:
            if not page_size:
                table = query(api, **params)
            else:
                pages, seen = [], set()
                size = min(page_size, 500) if attempt >= 2 else page_size
                for offset in range(0, 60000, size):
                    page = query(api, **params, limit=size, offset=offset)
                    if page.empty:
                        if not pages:
                            pages.append(page)
                        break
                    fingerprint = tuple(pd.util.hash_pandas_object(page, index=False).to_numpy())
                    if fingerprint in seen:
                        raise ValueError(f"{api} 返回重复分页，无法确认该截面覆盖。")
                    seen.add(fingerprint)
                    pages.append(page)
                    if len(page) < size:
                        break
                else:
                    raise ValueError(f"{api} 超出单个截面的分页上限。")
                table = pd.concat(pages, ignore_index=True).drop_duplicates()
            with NamedTemporaryFile(dir=root, suffix=".partial", delete=False) as temp:
                pending = Path(temp.name)
            try:
                table.to_parquet(pending, index=False)
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
            return table
        except Exception as error:
            if attempt == 3 or "权限" in str(error):
                raise
            time.sleep(62 if any(word in str(error) for word in ("分钟", "限流", "频率超限")) else 2 ** attempt)


def batch(root, jobs, workers, label):
    jobs = [job for job in jobs if not (root / (job[0] + ".parquet")).exists()]
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(cached_query, root, name, api, **params): name for name, api, params in jobs}
        errors = []
        for i, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception as error:
                errors.append(f"{futures[future]}：{error}")
            if len(futures) >= 50 and (i % 100 == 0 or i == len(futures)):
                print(f"{label} {i}/{len(futures)}，失败 {len(errors)}", file=sys.stderr, flush=True)
        if errors:
            raise ValueError(f"{label}有 {len(errors)} 个快照待补。已保存成功分区。\n" + "\n".join(errors[:6]))


def index_members(root, pool, date):
    """月中没有新快照时，使用该信号日之前最近一次完整快照。"""
    day = date.strftime("%Y%m%d")
    name, code = pool["name"], pool["index_code"]
    params = {"index_code": code, "end_date": day, "fields": "index_code,trade_date,con_code,weight"}
    table = cached_query(root, f"{name}_{day}", "index_weight", start_date=day, **params)
    if table.empty:
        earliest = (date - pd.Timedelta(days=45)).strftime("%Y%m%d")
        table = cached_query(root, f"{name}_asof_{day}", "index_weight", start_date=earliest, **params)
        table = table.loc[table.trade_date.between(earliest, day)]
        table = table.loc[table.trade_date.eq(table.trade_date.max())]
    if (len(table) != pool["size"] or not table.con_code.is_unique
            or table.trade_date.nunique() != 1 or not table.trade_date.le(day).all()):
        raise ValueError(f"{date:%Y-%m-%d} 对应的 {name} 历史成分不完整，请核对指数历史覆盖或调整回测区间。")
    return table.con_code.to_list(), pd.Timestamp(table.trade_date.iloc[0])


def equity_inputs(root, signals, start, end, benchmark, pools, workers=8):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # 只在主线程读取本机凭证，避免并发请求重复调用凭证存储。
    credential = token_value()
    if credential:
        os.environ.setdefault("TUSHARE_TOKEN", credential)
    release_days = [pd.Timestamp(date).strftime("%Y%m%d") for date in sorted(set(signals))]
    start, end = (pd.Timestamp(day).strftime("%Y%m%d") for day in (start, end))
    jobs = [
        (f"valuation_{day}", "daily_basic", dict(trade_date=day, page_size=2000,
                                                fields="ts_code,trade_date,pe,pe_ttm,pb,total_mv,dv_ttm"))
        for day in release_days
    ]
    # 年度及同季度的上年累计报表，每个调仓日按实际披露日重建。
    periods = pd.date_range(pd.Timestamp(release_days[0]) - pd.DateOffset(years=2), pd.Timestamp(release_days[-1]), freq="QE")
    for period in periods.strftime("%Y%m%d"):
        for statement, fields in STATEMENTS.items():
            for report_type in ("1", "5"):
                jobs.append((f"{statement}_{period}_{report_type}", statement+"_vip", dict(
                    period=period, report_type=report_type, page_size=2000,
                    fields="ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"+fields)))
    for status in ("L", "D", "P"):
        jobs.append((f"stocks_{status}", "stock_basic", dict(
            list_status=status, page_size=1000, fields="ts_code,name,list_date,delist_date,list_status")))
    jobs.append(("name_history", "namechange", dict(page_size=1000)))
    for version in ("SW2014", "SW2021"):
        classes = cached_query(root, f"industry_{version}", "index_classify", src=version, level="L1")
        for industry in classes.index_code:
            for fresh in ("Y", "N"):
                jobs.append((f"members_{industry}_{fresh}", "index_member_all",
                             dict(l1_code=industry, is_new=fresh, page_size=1000)))
    endpoint = dict(start_date=start, end_date=end)
    if benchmark:
        jobs.append((f"index_{benchmark}_{start}_{end}", "index_daily",
                     dict(ts_code=benchmark, fields="ts_code,trade_date,open,close", **endpoint)))
    # 各池只请求策略在该信号日需要的成分，估值快照则按日期共用。
    for date, requests_for_date in pools.items():
        stamp = date.strftime("%Y%m%d")
        for pool in requests_for_date:
            if "index_code" in pool:
                jobs.append((f"{pool['name']}_{stamp}", "index_weight", dict(index_code=pool["index_code"],
                            start_date=stamp, end_date=stamp, fields="index_code,trade_date,con_code,weight")))
    batch(root, list({job[0]: job for job in jobs}.values()), workers, "财务及估值")


def opening_suspensions(root, start, end):
    """区分开盘即可交易与当日盘中复牌。日线首笔价本身不能区分两者。"""
    jobs = []
    for month in pd.period_range(start, end, freq="M"):
        first = max(pd.Timestamp(start), month.start_time)
        last = min(pd.Timestamp(end), month.end_time)
        name = f"suspend_{first:%Y%m%d}_{last:%Y%m%d}"
        jobs.append((name, "suspend_d", dict(start_date=f"{first:%Y%m%d}", end_date=f"{last:%Y%m%d}",
                     suspend_type="S", page_size=1000, fields="ts_code,trade_date,suspend_timing,suspend_type")))
    batch(root, jobs, 6, "历史停牌")
    frame = pd.concat([pd.read_parquet(root / (job[0]+".parquet")) for job in jobs],ignore_index=True)
    starts = frame.suspend_timing.astype("string").str.extract(r"^\s*(\d{2}:\d{2})", expand=False)
    ends = frame.suspend_timing.astype("string").str.extract(r"-\s*(\d{2}:\d{2})", expand=False)
    blocked = starts.isna() | (starts.le("09:30") & (ends.isna() | ends.gt("09:30")))
    return frame.loc[blocked,["ts_code","trade_date"]].drop_duplicates()
