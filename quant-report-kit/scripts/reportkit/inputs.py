"""读取结果目录；不重新取行情或运行策略。"""
from __future__ import annotations
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

SCENARIOS = {"strategy": "daily.csv", "equal_weight": "equal_weight_daily.csv",
             "static": "static_daily.csv", "benchmark": "benchmark_daily.csv"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding="utf-8")


def records(frame):
    # pandas统一处理时间戳、空值与numpy数值；JSON不输出NaN/Infinity。
    return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False, double_precision=15))


def validate_daily(frame, name):
    if not {"date", "nav"}.issubset(frame.columns):
        raise ValueError(f"{name}缺少date/nav列。")
    d = frame.copy()
    d["date"] = pd.to_datetime(d["date"], errors="raise")
    if d["date"].isna().any() or d["date"].dt.tz is not None or not d["date"].eq(d["date"].dt.normalize()).all():
        raise ValueError(f"{name}请使用不带时区的日历日期YYYY-MM-DD。")
    if d["date"].duplicated().any():
        raise ValueError(f"{name}有重复日期，请先修复结果。")
    d = d.sort_values("date").reset_index(drop=True)
    if len(d) < 2:
        raise ValueError(f"{name}至少需要两个日期。")
    d["nav"] = pd.to_numeric(d["nav"], errors="raise")
    if not np.isfinite(d["nav"]).all() or not d["nav"].gt(0).all():
        raise ValueError(f"{name}净值须为正数且不能缺失。")
    expected = d["nav"].pct_change(fill_method=None)
    if "return" in d:
        d["return"] = pd.to_numeric(d["return"], errors="raise")
        if not np.isfinite(d["return"]).all() or not np.allclose(d["return"].iloc[1:], expected.iloc[1:], rtol=1e-7, atol=1e-9):
            raise ValueError(f"{name}的日收益与净值变化不一致，请检查是否混入资金流或错误记录。")
    return d


def effective_config(config, scenario):
    c = copy.deepcopy(config)
    if scenario == "equal_weight":
        for s in c.get("sleeves", []):
            if s.get("weighting", {}).get("method") == "min_variance":
                s["weighting"]["method"] = "equal"
    elif scenario == "static":
        if c.get("allocation", {}).get("method") == "risk_parity":
            c["allocation"] = {"method": "fixed"}
        base = {s["id"]: s["weight"] for s in c.get("sleeves", [])}
        for rule in c.get("macro", {}).get("rules", []):
            rule["weights"] = dict(base)
    elif scenario == "benchmark":
        c = {"name": c.get("benchmark", {}).get("code", "基准") + "买入持有",
             "base_currency": c.get("base_currency", "CNY"), "initial_capital": c.get("initial_capital"),
             "cost_rate": c.get("cost_rate", 0), "sleeves": [], "benchmark_only": c.get("benchmark", {})}
    return c


@dataclass
class Run:
    id: str
    label: str
    path: Path
    scenario: str
    daily: pd.DataFrame
    config: dict
    handoff: dict
    tables: dict
    documents: dict
    hashes: dict
    notices: list

    @property
    def currency(self):
        return self.handoff.get("base_currency", self.config.get("base_currency", "CNY"))


def load_run(spec, base):
    sid = spec.get("id", "")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", sid):
        raise ValueError("每个策略id使用字母开头的英文、数字、短横线或下划线，最多64字符。")
    path = (Path(base) / spec["result_dir"]).resolve()
    scenario = spec.get("scenario", "strategy")
    if scenario not in SCENARIOS:
        raise ValueError(f"不支持的scenario：{scenario}。")
    handoff = read_json(path / "handoff.json")
    if handoff.get("schema_version") != 1:
        raise ValueError(f"{path.name}的handoff版本不支持。")
    label = str(spec.get("label") or handoff.get("strategy_name") or sid)
    if not label.strip():
        raise ValueError("策略名称不能为空。")
    if scenario not in {s["id"] for s in handoff.get("scenarios", [])}:
        raise ValueError(f"{label}没有导出{scenario}对照。")
    cfg = read_json(path / "strategy.json")
    currency = handoff.get("base_currency", cfg.get("base_currency"))
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"{label}需要明确的三字母基础币种，例如CNY。")
    if cfg.get("base_currency") and cfg["base_currency"] != currency:
        raise ValueError(f"{label}的配置与交接清单币种不一致。")
    d = validate_daily(pd.read_csv(path / SCENARIOS[scenario]), label)
    if "equity" in d:
        d["equity"] = pd.to_numeric(d["equity"], errors="raise")
        if not np.isfinite(d["equity"]).all() or not d["equity"].gt(0).all():
            raise ValueError(f"{label}的权益必须为正数且不能缺失。")
    capital = handoff.get("initial_capital", cfg.get("initial_capital"))
    if capital is not None and (not np.isfinite(float(capital)) or float(capital) <= 0):
        raise ValueError(f"{label}本金应为正数。")
    if capital is not None and "equity" in d and not np.allclose(d["nav"] * float(capital), d["equity"], rtol=1e-7, atol=.01):
        raise ValueError(f"{label}本金、净值与权益不一致。")
    tables, docs, hashes, notices = {}, {}, {}, []
    wanted = ["handoff.json", "strategy.json", SCENARIOS[scenario], "manifest.json"]
    if scenario == "strategy":
        for key in ["weights", "trades", "selections", "targets", "blocked", "valuation_gaps"]:
            file = path / f"{key}.csv"
            if file.exists():
                tables[key] = pd.read_csv(file, dtype={"code": str})
                wanted.append(file.name)
        for key in ["regimes", "optimization", "checks", "unfilled_at_end", "summary", "contributions", "assets"]:
            file = path / f"{key}.json"
            if file.exists():
                docs[key] = read_json(file)
                wanted.append(file.name)
    else:
        notices.append("该内置对照导出了账户日线；持仓、交易和决策明细未单独导出。")
    if (path / "manifest.json").exists():
        docs["manifest"] = read_json(path / "manifest.json")
    # 清单中的行数和字段可验证输入是否被意外改写。
    entries = {f["path"]: f for f in handoff.get("files", [])}
    for name in wanted:
        f = path / name
        if f.exists():
            hashes[name] = hashlib.sha256(f.read_bytes()).hexdigest()
            if name in entries and name.endswith(".csv"):
                df = d if name == SCENARIOS[scenario] else tables[Path(name).stem]
                if len(df) != entries[name].get("rows", len(df)) or list(df.columns) != entries[name].get("columns", list(df.columns)):
                    raise ValueError(f"{label}/{name}与输出清单不一致。")
    return Run(sid, label, path, scenario, d, effective_config(cfg, scenario), handoff, tables, docs, hashes, notices)


def load_request(file):
    p = Path(file).resolve()
    c = read_json(p)
    if c.get("schema_version") != 1 or not isinstance(c.get("strategies"), list) or not c["strategies"]:
        raise ValueError("比较配置需要schema_version=1和非空strategies数组。")
    runs = [load_run(s, p.parent) for s in c["strategies"]]
    if len({s.id for s in runs}) != len(runs) or len({s.label for s in runs}) != len(runs):
        raise ValueError("策略id和显示名称都应唯一。")
    if len({s.currency for s in runs}) != 1:
        raise ValueError("各策略基础币种不同，请先在数据/回测模块换算成同一币种。")
    if c.get("alignment", "common") != "common":
        raise ValueError("当前采用共同日期比较，alignment请使用common。")
    return c, runs
