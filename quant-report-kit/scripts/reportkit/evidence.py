"""为解读提供带策略、日期、单位和来源的结构化指标。"""
from __future__ import annotations

import re


METRICS = {
    "total_return": "累计收益",
    "annual_return": "年化收益",
    "annual_volatility": "年化波动率",
    "max_drawdown": "最大回撤",
    "sharpe_zero_rf": "夏普比率（无风险利率为零）",
}
CHINESE_QUANTITY = re.compile(
    r"百分之|千分之|万分之|[零〇一二两三四五六七八九十百千万亿点]+(?:%|％|个百分点|个基点|基点)"
)


def contains_number(text):
    return any(char.isdigit() for char in text) or CHINESE_QUANTITY.search(text) is not None


def add_value(data, key, metric, value, unit, strategies, subject, fact_id):
    """从本次计算生成完整的指标陈述，避免主体或单位被写错。"""
    if value is None:
        return
    start, end = data["period"]["start"], data["period"]["end"]
    if unit == "percent":
        displayed = f"{value:.2%}"
    elif unit == "percentage_point":
        scaled = value * 100
        displayed = "不足0.01个百分点" if 0 < abs(scaled) < .005 else f"{scaled:+.2f}个百分点"
    else:
        displayed = f"{value:.2f}"
    data["evidence_values"][key] = {
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": {"start": start, "end": end},
        "strategies": strategies,
        "fact_id": fact_id,
        "text": f"{start}至{end}，{subject}{metric}为{displayed}。",
    }


def render_commentary(data, interpretation, claims, evidence_ids):
    """由程序填写关键数字，保留独立的定性解读。"""
    if not isinstance(interpretation, str) or not interpretation.strip():
        raise ValueError("解读正文须为非空文本。")
    if contains_number(interpretation):
        raise ValueError("研究解读中的数字请使用 claims 引用，由程序填入本次计算值。")
    if not isinstance(claims, list) or any(not isinstance(key, str) for key in claims):
        raise ValueError("claims 须为结构化事实键的数组。")
    if len(set(claims)) != len(claims):
        raise ValueError("同一段的 claims 不能重复。")
    values = data["evidence_values"]
    sentences = []
    for key in claims:
        if key not in values or values[key]["fact_id"] not in evidence_ids:
            raise ValueError(f"数值引用 {key} 不在本次解读引用的事实中。")
        sentences.append(values[key]["text"])
    return "".join(sentences) + interpretation.strip()
