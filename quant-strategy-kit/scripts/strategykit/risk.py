"""大类风险平价：按信号日前的代表资产收益估计风险，输出可复核的目标。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize


def risk_contributions(covariance, weights):
    cov, w = np.asarray(covariance, dtype=float), np.asarray(weights, dtype=float)
    variance = float(w @ cov @ w)
    if not np.isfinite(variance) or variance <= 1e-20:
        raise ValueError("风险估计为零或无效，无法计算风险贡献比例。")
    parts = w * (cov @ w)
    return {"annual_volatility": float(np.sqrt(variance * 252)),
            "volatility_contributions": (parts / np.sqrt(variance) * np.sqrt(252)).tolist(),
            "risk_shares": (parts / variance).tolist()}


def equal_risk_weights(covariance):
    """求解凸的风险预算问题；归一化后各资产方差贡献相等。"""
    cov = np.asarray(covariance, dtype=float)
    n = len(cov)
    if cov.shape != (n, n) or not n or not np.isfinite(cov).all():
        raise ValueError("协方差矩阵无效。")
    cov = (cov + cov.T) / 2
    if np.diag(cov).min() <= 1e-20 or np.linalg.eigvalsh(cov).min() <= 0:
        raise ValueError("协方差须正定且各资产具有有效波动。")
    scaled = cov / np.diag(cov).mean()
    budget = np.full(n, 1. / n)
    def objective(x):
        return .5 * x @ scaled @ x - budget @ np.log(x)
    def gradient(x):
        return scaled @ x - budget / x
    result = minimize(objective, 1 / np.sqrt(np.diag(scaled) * n), jac=gradient,
                      method="L-BFGS-B", bounds=[(1e-12, None)] * n,
                      options={"ftol": 1e-15, "gtol": 1e-11, "maxiter": 2000})
    w = result.x / result.x.sum()
    shares = risk_contributions(cov, w)["risk_shares"]
    if not result.success or not np.isfinite(w).all() or np.min(w) < 0 or np.max(np.abs(np.array(shares)-budget)) > 1e-5:
        raise ValueError("风险平价求解未达到精度要求。")
    return w


def allocate_risk(config, prices, signal, previous):
    settings = config["allocation"]
    ids = [s["id"] for s in config["sleeves"]]
    base = {s["id"]: s["weight"] for s in config["sleeves"]}
    info = {"state": "大类资产风险平价", "method": "risk_parity", "inputs": [], "missing": False,
            "estimation": "signal_date_proxy_baskets", "proxies": settings["proxies"],
            "lookback": settings["lookback"], "shrinkage": settings["shrinkage"],
            "invested_weight": settings["invested_weight"], "observations": 0}
    try:
        # 每个代理篮子的成员事先配置；历史估计不用事后选中的股票冒充历史持仓。
        codes = list(dict.fromkeys(a["code"] for sid in ids for a in settings["proxies"][sid] if a.get("weight", 1) > 0))
        history = prices.loc[:signal].reindex(columns=codes).tail(settings["lookback"] + 1)
        if len(history) < 2 or not history.iloc[-1].notna().all():
            raise ValueError("信号日风险代理行情缺失。")
        returns = history.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()
        info["observations"] = len(returns)
        if len(returns) < settings["min_observations"]:
            raise ValueError("风险代理共同有效样本不足。")
        baskets = pd.DataFrame(index=returns.index)
        for sid in ids:
            assets = [a for a in settings["proxies"][sid] if a.get("weight", 1) > 0]
            total = sum(a.get("weight", 1.) for a in assets)
            baskets[sid] = sum(returns[a["code"]] * a.get("weight", 1.) / total for a in assets)
        raw = baskets.cov().to_numpy()
        if np.diag(raw).min() <= 1e-20:
            raise ValueError("风险代理缺乏有效价格波动。")
        shrink = settings["shrinkage"]
        cov = (1 - shrink) * raw + shrink * np.diag(np.diag(raw))
        w = equal_risk_weights(cov) * settings["invested_weight"]
        info.update(status="success", sample_start=str(returns.index[0].date()), sample_end=str(returns.index[-1].date()),
                    covariance=cov.tolist(), covariance_order=ids, **risk_contributions(cov, w))
        info["risk_rows"] = [{"sleeve": sid, "target_weight": float(w[i]),
                             "risk_share": info["risk_shares"][i],
                             "volatility_contribution": info["volatility_contributions"][i]} for i, sid in enumerate(ids)]
        return dict(zip(ids, map(float, w))), info
    except ValueError as exc:
        fallback = settings["fallback"]
        if fallback == "error":
            raise ValueError("风险平价无法计算：" + str(exc)) from exc
        chosen = previous if fallback == "hold" else base
        info.update(state="风险平价回退：" + ("上期配置" if fallback == "hold" else "基础比例"),
                    status="fallback_" + fallback, reason=str(exc), missing=True, risk_rows=[])
        return dict(chosen), info
