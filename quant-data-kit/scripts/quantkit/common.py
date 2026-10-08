"""参数和本地缓存。缓存只在使用者运行时产生，不随 Skill 分发。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def token_value():
    # 只供本地进程读取；不经 MCP，也不写入请求配置和输出。
    from .credentials import get_credential
    return get_credential("tushare")


def normalize_asset(asset):
    import re
    a = dict(asset)
    code = str(a.get("code", "")).strip().upper()
    if not re.fullmatch(r"\d{6}\.(SH|SZ)", code):
        raise ValueError("代码请写成 510300.SH 或 000001.SZ，并明确 etf / stock；不要只填名称。")
    if a.get("kind") not in {"etf", "stock"}:
        raise ValueError("本版证券类型为 etf 或 stock。")
    prefix = code[:6]
    if a["kind"] == "stock":
        allowed_stock = prefix.startswith(("600", "601", "603", "605", "688", "689")) if code.endswith(".SH") else prefix.startswith(("000", "001", "002", "003", "300", "301"))
        if not allowed_stock:
            raise ValueError("本版股票范围为沪深 A 股；请核对代码、交易所和证券类型。")
    elif not ((code.endswith(".SH") and prefix.startswith("5")) or (code.endswith(".SZ") and prefix.startswith("159"))):
        raise ValueError("请核对沪深 ETF 代码和交易所，指数/LOF 请另选对应接口。")
    a.update(code=code, symbol=code[:6], exchange=code[-2:],
             ak_symbol=code[:6], tx_symbol=code[-2:].lower()+code[:6], ts_code=code,
             currency="CNY", timezone="Asia/Shanghai")
    # 上市时间可以由可靠基础资料补充；未知时保留未知。
    for key in ("list_date", "delist_date"):
        if a.get(key):
            if not a.get("metadata_source"):
                raise ValueError("填写上市/退市日期时也请填写 metadata_source。")
            a[key] = pd.Timestamp(a[key]).strftime("%Y-%m-%d")
    return a


def read_request(path):
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed_keys = {"start", "end", "lookback_days", "provider", "assets", "macro", "nbs_urls", "nbs_crawl_pages", "nbs_saved_pages"}
    if set(c) - allowed_keys:
        raise ValueError("配置中存在未识别字段；凭证应留在本地环境或 SDK 配置中。")
    start, end = pd.Timestamp(c["start"]), pd.Timestamp(c["end"])
    if start.tz is not None or end.tz is not None:
        raise ValueError("start/end 使用 YYYY-MM-DD 日期。")
    start, end = start.normalize(), end.normalize()
    if start > end:
        raise ValueError("开始日期不能晚于结束日期。")
    if c.get("provider", "free") not in {"free", "auto", "tushare"}:
        raise ValueError("provider 应为 free、auto 或 tushare。")
    c["provider"] = c.get("provider", "free")
    c["lookback_days"] = int(c.get("lookback_days", 120))
    if not 0 <= c["lookback_days"] <= 3650:
        raise ValueError("lookback_days 范围为 0—3650 个自然日。")
    c["assets"] = [normalize_asset(a) for a in c["assets"]]
    if not c["assets"] or len({a["code"] for a in c["assets"]}) != len(c["assets"]):
        raise ValueError("资产不能为空或重复。")
    from .catalog import FRED
    allowed = {"cn_pmi", "cn_cpi_yoy", "vix", "world_gdp_growth", *FRED}
    if set(c.get("macro", [])) - allowed:
        raise ValueError("未知宏观指标；先查看 references/数据接口与口径.md。")
    if len(c.get("macro", [])) != len(set(c.get("macro", []))):
        raise ValueError("宏观指标列表存在重复。")
    if not 0 <= int(c.get("nbs_crawl_pages", 0)) <= 100:
        raise ValueError("nbs_crawl_pages 范围为 0—100。")
    c["start"], c["end"] = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    return c


class Client:
    """每次网络任务单独运行，超时可终止；同参数缓存带原获取时间。"""
    def __init__(self, cache, refresh=False, timeout=75):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.refresh, self.timeout = refresh, timeout
        self.log = []

    def call(self, op, **kwargs):
        spec = {"op": op, "params": kwargs, "format": 1}
        key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        file = self.cache / (key + ".json")
        # 默认 24 小时缓存；--refresh 会重新取数。历史数据也可能被供应商修订。
        if file.exists() and not self.refresh and time.time() - file.stat().st_mtime < 86400:
            data = json.loads(file.read_text(encoding="utf-8"))
            self.log.append(dict(spec, status="cached", fetched_at=data["fetched_at"], cache_key=key))
            return data["result"]
        attempts = 2
        for attempt in range(attempts):
            try:
                p = subprocess.run([sys.executable, str(ROOT / "scripts/worker.py")],
                                   input=json.dumps(spec), text=True, capture_output=True, timeout=self.timeout)
                if not p.stdout.strip():
                    raise RuntimeError("数据子进程未返回结果，退出码 " + str(p.returncode))
                data = json.loads(p.stdout)
                if not data["ok"]:
                    if data.get("retryable") and attempt + 1 < attempts:
                        time.sleep(0.5)
                        continue
                    raise RuntimeError(data["error"])
                write_json(file, data)
                self.log.append(dict(spec, status="fetched", fetched_at=data["fetched_at"], cache_key=key))
                return data["result"]
            except subprocess.TimeoutExpired:
                # 单次请求达到超时上限后切换备用数据源。
                error = "接口超过 " + str(self.timeout) + " 秒未完成"
                break
            except Exception as exc:
                error = str(exc)
                break
        self.log.append(dict(spec, status="failed", error=error))
        raise RuntimeError(error)


def frame(result):
    return pd.DataFrame(result["data"], columns=result["columns"])


def year_chunks(start, end):
    cursor, end = pd.Timestamp(start), pd.Timestamp(end)
    while cursor <= end:
        last = min(pd.Timestamp(cursor.year, 12, 31), end)
        yield cursor.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")
        cursor = last + pd.Timedelta(days=1)
