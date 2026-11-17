"""可修改的投资研究配置。仅包含参数和代码清单，没有行情数据。"""
import copy


PRESET_NAMES = (
    "cross-border-allocation", "qdii-equity-allocation", "domestic-allocation",
    "vix-dynamic-allocation", "risk-parity-allocation", "csi800-factor-monthly",
)


def asset(code, name, kind="etf", **extra):
    return {"code": code, "name": name, "kind": kind, **extra}


def fixed(sid, name, weight, assets):
    return {"id": sid, "name": name, "weight": weight, "rebalance": "monthly", "assets": assets,
            "selection": {"method": "fixed"}, "weighting": {"method": "equal"}}


def csi800_stocks(weight):
    """内置主动股仓采用已验证的月度小市值多因子。"""
    return {
        "id": "stocks", "name": "主动A股", "weight": weight, "rebalance": "monthly", "assets": [],
        "selection": {"method": "quality_value_dividend", "universe": "csi800", "top_n": 20,
                      "buffer_rank": 40, "max_per_industry": 2, "small_cap_weight": .10},
        "weighting": {"method": "inverse_volatility", "max_weight": .08},
    }


def preset(name):
    if name == "csi800-factor-monthly":
        return {
            "schema_version": 1, "name": "中证800质量价值红利低波（月度）",
            "start": "2021-11-01", "end": "2026-10-30", "base_currency": "CNY", "initial_capital": 1_000_000,
            "position_policy": "long_only", "execution_price": "open", "allocation_rebalance": "monthly",
            "long_gap_policy": "carry_and_flag",
            "benchmark": asset("H00300.CSI", "沪深300全收益指数", "index"),
            "transaction_costs": {"commission_rate": .00025, "slippage_rate": .00025, "stamp_duty_rate": .001},
            "sleeves": [csi800_stocks(1.)],
        }
    if name == "risk-parity-allocation":
        c = preset("domestic-allocation")
        c["name"] = "多资产风险平价策略"
        c["sleeves"] = c["sleeves"][:4]
        for sleeve, weight in zip(c["sleeves"], [.4, .3, .2, .1]):
            sleeve["weight"] = weight
        c["allocation"] = {"method":"risk_parity", "lookback":126, "min_observations":60,
                           "shrinkage":.1, "invested_weight":1., "fallback":"base_weights",
                           "proxies":{s["id"]:copy.deepcopy(s["assets"]) for s in c["sleeves"]}}
        return c
    c = {"schema_version": 1, "name": "多资产配置策略（海外ETF权益）", "start": "2025-01-01", "end": "2025-12-31",
         "base_currency": "CNY", "initial_capital": 1_000_000, "position_policy": "long_only",
         "allocation_rebalance": "monthly", "max_valuation_gap": 10,
         "benchmark": asset("510300.SH", "沪深300ETF"), "macro": {"enabled": False}}
    if name in {"cross-border-allocation", "qdii-equity-allocation"}:
        c["long_gap_policy"] = "carry_and_flag"
        c["sleeves"] = [
            fixed("bond", "美元短债", .25, [asset("SHY", "1—3年美国国债ETF", "us_etf")]),
            fixed("bond_cny", "人民币长债", .25, [asset("511260.SH", "上证10年期国债ETF")]),
            fixed("gold", "黄金", .10, [asset("518880.SH", "黄金ETF")]),
            fixed("equity", "A股宽基", .10, [asset("510300.SH", "沪深300ETF")]),
            # 海外20%平均分给标普500宽基和纳斯达克100成长仓位。
            fixed("overseas_broad", "海外股票宽基（标普500）", .10, [asset("SPY", "标普500ETF", "us_etf")]),
            fixed("overseas_growth", "海外股票成长（纳斯达克100）", .10, [asset("QQQ", "纳斯达克100ETF", "us_etf")]),
            csi800_stocks(.10),
        ]
        if name == "qdii-equity-allocation":
            c["name"] = "多资产配置策略（QDII权益）"
            # 只改变美股指数的持有工具，方便对照同一配置的两种实现。
            replacements = {
                "overseas_growth": asset("513100.SH", "国泰纳斯达克100ETF", exposure_market="US", listing_market="CN", currency="CNY"),
                "overseas_broad": asset("513500.SH", "博时标普500ETF", exposure_market="US", listing_market="CN", currency="CNY"),
            }
            for sleeve in c["sleeves"]:
                if sleeve["id"] in replacements:
                    sleeve["assets"] = [replacements[sleeve["id"]]]
    elif name in {"domestic-allocation", "vix-dynamic-allocation"}:
        c["name"] = "境内多资产配置策略" if name == "domestic-allocation" else "VIX动态资产配置策略"
        c["sleeves"] = [fixed("bond", "境内国债", .4, [asset("511010.SH", "国债ETF")]),
                        fixed("equity", "股票宽基", .25, [asset("510300.SH", "沪深300ETF")]),
                        fixed("gold", "黄金", .15, [asset("518880.SH", "黄金ETF")]),
                        fixed("commodity", "商品", .1, [asset("159980.SZ", "有色ETF"), asset("159981.SZ", "能源化工ETF"), asset("159985.SZ", "豆粕ETF")]),
                        csi800_stocks(.10)]
        if name == "vix-dynamic-allocation":
            c["macro"] = {"enabled": True, "rebalance": "monthly", "missing": "base_weights", "rules": [
                {"name": "波动偏高", "all": [{"series": "vix", "op": ">=", "threshold": 20, "max_age_days": 10}],
                 "weights": {"bond": .55, "equity": .15, "gold": .20, "commodity": .05, "stocks": .05}}]}
    else:
        raise ValueError("可选预设为 " + "、".join(PRESET_NAMES) + "。")
    return copy.deepcopy(c)


CATALOG = [
    {"类别": "境内股票宽基", "例子": "沪深300、中证500、创业板等ETF", "获取": "数据模块境内ETF接口"},
    {"类别": "红利、成长或行业股票", "例子": "红利低波、科创50、行业ETF，或自选A股", "获取": "ETF行情；个股可结合历史财务截面"},
    {"类别": "债券", "例子": "境内国债ETF、美国短债ETF", "获取": "境内行情；美国ETF另取汇率"},
    {"类别": "黄金", "例子": "黄金ETF", "获取": "境内ETF；矿业股票单列股票部分"},
    {"类别": "其他商品", "例子": "有色、能源化工、豆粕ETF", "获取": "境内ETF；上市前不造历史价格"},
    {"类别": "海外股票", "例子": "境内QDII：513100.SH（纳斯达克100）、513500.SH（标普500）；亦可直接选QQQ、SPY", "获取": "优先检查境内ETF行情；直接美国ETF另取汇率，替换需明确记录"},
    {"类别": "现金", "例子": "权重未分配部分", "获取": "默认零收益，无需下载"}]
