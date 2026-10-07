"""按实际逐日盈亏分解组合波动，比较报告总是重算共同区间。"""
import numpy as np
import pandas as pd


def analyse_risk(daily, names):
    cols = [c for c in daily if c.startswith("pnl_")]
    empty = {"status":"unavailable", "rows":[], "correlations":[], "annual_volatility":None,
             "reason":"需要各资产部分逐日盈亏与账户权益。"}
    if not cols or "equity" not in daily:
        return empty
    d = daily.reset_index(drop=True)
    if not np.isfinite(d[cols].to_numpy(dtype=float)).all():
        raise ValueError("风险贡献所需逐日盈亏包含空值或无效数值。")
    parts = d[cols].astype(float).div(d["equity"].shift(), axis=0)
    parts.iloc[0] = 0.  # 与报告共同起点的零收益口径一致。
    total = d["nav"].pct_change(fill_method=None).fillna(0).to_numpy()
    if not np.isfinite(parts.to_numpy()).all() or not np.allclose(parts.sum(axis=1), total, atol=1e-9, rtol=1e-7):
        raise ValueError("逐日资产盈亏无法还原组合收益，不能计算风险贡献。")
    cov = parts.cov().to_numpy()
    variance = float(np.var(total, ddof=1))
    vol = float(np.sqrt(max(variance, 0) * 252))
    component_cov = cov.sum(axis=1)
    rows = []
    for i, col in enumerate(cols):
        sid = col[4:]
        rows.append({"sleeve":sid, "name":names.get(sid, sid),
                     "volatility_contribution":float(component_cov[i] / np.sqrt(variance) * np.sqrt(252)) if variance > 1e-20 else 0.,
                     "risk_share":float(component_cov[i] / variance) if variance > 1e-20 else None})
    if not np.isclose(sum(r["volatility_contribution"] for r in rows), vol, rtol=1e-7, atol=1e-10):
        raise ValueError("风险贡献之和与组合年化波动不一致。")
    corr = parts.corr()
    correlations = [{"left":a[4:], "right":b[4:], "left_name":names.get(a[4:],a[4:]), "right_name":names.get(b[4:],b[4:]),
                     "correlation":float(corr.loc[a,b]) if pd.notna(corr.loc[a,b]) else None}
                    for i,a in enumerate(cols) for b in cols[i+1:]]
    return {"status":"ok" if variance > 1e-20 else "zero_volatility", "rows":rows, "correlations":correlations,
            "annual_volatility":vol, "observations":len(d), "return_intervals":len(d)-1,
            "start":str(pd.Timestamp(d.date.iloc[0]).date()), "end":str(pd.Timestamp(d.date.iloc[-1]).date()),
            "definition":"分部分日收益贡献=当日盈亏/上一日组合权益；波动贡献=Cov(该贡献,组合日收益)/组合日波动×√252。共同起点记零收益。",
            "correlation_definition":"各资产部分对组合的日收益贡献序列相关性，包含持仓变化影响；零波动序列不定义相关性。",
            "note":"波动贡献合计为组合年化波动率；风险占比合计为100%。负风险贡献表示分散效果，可能出现在只做多组合中，不表示空头。零波动时占比不定义。"}
