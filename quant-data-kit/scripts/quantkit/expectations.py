"""市场利率预期：校验 FedWatch 下载表，再计算加息/降息概率。"""
from pathlib import Path
import numpy as np
import pandas as pd


def clean_fedwatch(data):
    required = ["observation_at", "meeting_date", "target_lower", "target_upper", "probability",
                "current_target_upper", "source_url", "provenance_note"]
    if set(required) - set(data):
        raise ValueError("缺少字段：" + ", ".join(sorted(set(required) - set(data))))
    d = data[required].copy()
    # 不接受无时区的观察时刻，避免把美国盘后信息提前放入国内同一天。
    if not d["observation_at"].astype(str).str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", regex=True).all():
        raise ValueError("observation_at 必须带时区。")
    d["observation_at"] = pd.to_datetime(d["observation_at"], utc=True, errors="raise", format="mixed")
    d["meeting_date"] = pd.to_datetime(d["meeting_date"], errors="raise").dt.normalize()
    for col in ["target_lower", "target_upper", "probability", "current_target_upper"]:
        d[col] = pd.to_numeric(d[col], errors="raise")
    if not np.isfinite(d[["target_lower", "target_upper", "probability", "current_target_upper"]]).all().all():
        raise ValueError("利率和概率必须是有限数值。")
    if not d["probability"].between(0, 1).all() or not d["target_upper"].gt(d["target_lower"]).all():
        raise ValueError("概率应为 0—1，目标区间上下限应正确。")
    if not d["source_url"].fillna("").str.startswith("https://").all() or d["provenance_note"].fillna("").str.strip().eq("").any():
        raise ValueError("请保留来源链接和观察时间说明。")
    keys = ["observation_at", "meeting_date"]
    if d.duplicated(keys + ["target_lower", "target_upper"]).any():
        raise ValueError("同一观察时点、会议和目标区间重复。")
    rows = []
    for (obs, meeting), group in d.groupby(keys):
        if abs(group["probability"].sum() - 1) > 0.002:
            raise ValueError("同一会议各档概率合计应为 1；请检查百分数转换或漏掉的档位。")
        if group["current_target_upper"].nunique() != 1:
            raise ValueError("同一观察时点的当前政策利率不一致。")
        if obs >= meeting.tz_localize("America/New_York").tz_convert("UTC"):
            raise ValueError("本版按会议日期处理，只接收会议日前的明确快照；会议当天数据需另核对决议时刻。")
        ordered = group.sort_values("target_lower")
        if (ordered["target_lower"].iloc[1:].to_numpy() < ordered["target_upper"].iloc[:-1].to_numpy()).any():
            raise ValueError("目标区间互相重叠。")
        current = group["current_target_upper"].iloc[0]
        # 概率总和容许官网四舍五入的小误差，但不改动原始概率。
        rows.append({"observation_at": obs, "meeting_date": meeting,
            "hike_probability": group.loc[group["target_upper"].gt(current), "probability"].sum(),
            "cut_probability": group.loc[group["target_upper"].lt(current), "probability"].sum(),
            "hold_probability": group.loc[group["target_upper"].eq(current), "probability"].sum(),
            "expected_target_midpoint": (((group["target_lower"]+group["target_upper"])/2)*group["probability"]).sum(),
            "source_url": group["source_url"].iloc[0], "provenance_note": group["provenance_note"].iloc[0]})
    if not rows:
        raise ValueError("导入表为空。")
    return d.sort_values(keys), pd.DataFrame(rows)


def import_fedwatch(input_path, out_path):
    d, summary = clean_fedwatch(pd.read_csv(input_path))
    out = Path(out_path)
    if out.exists() and any(out.iterdir()):
        raise ValueError("输出目录已有内容，请换一个新目录。")
    out.mkdir(parents=True, exist_ok=True)
    d.to_csv(out / "fedwatch_buckets.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out / "fedwatch_summary.csv", index=False, encoding="utf-8-sig")
    return {"snapshots": len(summary), "rows": len(d), "out": str(out.resolve())}
