"""可修改的投资研究配置。仅包含参数和代码清单，没有行情数据。"""
PRESET_NAMES = (
    "cross-border-allocation", "domestic-allocation",
    "vix-dynamic-allocation", "domestic-volatility-allocation", "regional-macro-allocation",
    "risk-parity-allocation", "csi800-factor-monthly",
)
PRESET_ALIASES = {"qdii-equity-allocation": "cross-border-allocation"}


def asset(code, name, kind="etf", **extra):
    return {"code": code, "name": name, "kind": kind, **extra}


def fixed(sid, name, weight, assets):
    return {"id": sid, "name": name, "weight": weight, "rebalance": "monthly", "assets": assets,
            "selection": {"method": "fixed"}, "weighting": {"method": "equal"}}


def csi800_stocks(weight):
    """内置主动股仓采用已验证的月度小市值多因子。"""
    return {
        "id": "stocks", "name": "主动A股", "weight": weight, "rebalance": "monthly", "assets": [],
        "risk_proxy": [asset("510300.SH", "沪深300ETF"), asset("510500.SH", "中证500ETF")],
        "selection": {"method": "quality_value_dividend", "universe": "csi800", "top_n": 20,
                      "buffer_rank": 40, "max_per_industry": 2, "small_cap_weight": .10},
        "weighting": {"method": "inverse_volatility", "max_weight": .08},
    }


def condition(series, op, threshold, periods=0, **extra):
    rule = {"series": series, "op": op, "threshold": threshold, **extra}
    if periods:
        rule.update(transform="change", periods=periods)
    return rule


def macro_settings(with_us):
    """四状态的资金方向是可改的研究假设，规则按地区独立求值。"""
    def states(activity, inflation, changes):
        names = ("景气偏强且通胀上行", "景气偏强且通胀持平或下行",
                 "景气偏弱且通胀上行", "景气偏弱且通胀持平或下行")
        return [{"name": name, "all": [activity[i // 2], condition(inflation, ">" if i % 2 == 0 else "<=", 0, 1)],
                 "changes": weights} for i, (name, weights) in enumerate(zip(names, changes))]

    china = states([condition("cn_pmi", ">=", 50), condition("cn_pmi", "<", 50)], "cn_cpi_yoy", [
        {"cn_equity": .02, "cn_bonds": -.03, "gold": .01},
        {"cn_equity": .04, "cn_bonds": -.04},
        {"cn_equity": -.05, "cn_bonds": -.025, "gold": .04},
        {"cn_equity": -.04, "cn_bonds": .04},
    ])
    regions = {"CN": {"rules": china, "modifiers": [
        {"name": "中国操作利率上行", "all": [condition("cn_policy_rate", ">", 0, 1)],
         "changes": {"cn_bonds": -.015, "gold": .015}},
        {"name": "中国城镇调查失业率上行", "all": [condition("cn_unemployment", ">", .2, 3)],
         "changes": {"cn_equity": -.01}},
    ]}}
    if with_us:
        america = states([condition("us_unemployment", "<=", 0, 3), condition("us_unemployment", ">", 0, 3)], "us_cpi_yoy", [
            {"us_equity": -.02, "usd_reserve": .02},
            {"us_equity": .04, "usd_reserve": -.04},
            {"us_equity": -.05, "cn_bonds": .025, "gold": .025},
            {"us_equity": -.04, "usd_reserve": .04},
        ])
        regions["US"] = {"rules": america, "modifiers": [
            {"name": "美联储政策利率近一月上行",
             "all": [condition("fed_upper", ">", 0, 30)],
             "changes": {"us_equity": -.015, "usd_reserve": .015}},
        ]}
    return {"enabled": False, "regions": regions}


def preset(name):
    name = PRESET_ALIASES.get(name, name)
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
        c["allocation"] = {"method": "risk_parity"}
        return c
    c = {"schema_version": 1, "name": "跨境多资产配置策略", "start": "2025-01-01", "end": "2025-12-31",
         "base_currency": "CNY", "initial_capital": 1_000_000, "position_policy": "long_only",
         "allocation_rebalance": "monthly", "max_valuation_gap": 10,
         "benchmark": asset("510300.SH", "沪深300ETF"), "macro": {"enabled": False}}
    cross = name in {"cross-border-allocation", "vix-dynamic-allocation", "regional-macro-allocation"}
    if cross:
        c["long_gap_policy"] = "carry_and_flag"
        c["sleeves"] = [
            fixed("bond", "美元活期存款", .25, [asset("USD_DEPOSIT", "美元活期存款", "usd_deposit")]),
            fixed("bond_cny", "人民币长债", .25, [asset("511260.SH", "上证10年期国债ETF")]),
            fixed("gold", "黄金", .10, [asset("518880.SH", "黄金ETF")]),
            fixed("equity", "A股宽基", .10, [asset("510300.SH", "沪深300ETF")]),
            # 海外20%平均分给标普500宽基和纳斯达克100成长仓位。
            fixed("overseas_broad", "标普500场外QDII", .10,
                  [asset("050025.OF", "博时标普500ETF联接A（人民币）", "qdii_nav", redemption_days=10)]),
            fixed("overseas_growth", "纳斯达克100场外QDII", .10,
                  [asset("160213.OF", "国泰纳斯达克100指数（QDII）", "qdii_nav", redemption_days=10)]),
            csi800_stocks(.10),
        ]
        groups = {"cn_equity": ["equity", "stocks"], "cn_bonds": ["bond_cny"], "us_equity": ["overseas_broad", "overseas_growth"],
                  "usd_reserve": ["bond"], "gold": ["gold"]}
        c["volatility"] = {"enabled": name == "vix-dynamic-allocation",
                           "signals": [{"series": "vix", "groups": ["us_equity"]}]}
        if name == "vix-dynamic-allocation":
            c["name"] = "跨境多资产配置（美股VIX调整）"
    elif name in {"domestic-allocation", "domestic-volatility-allocation"}:
        c["name"] = "境内多资产配置策略"
        c["sleeves"] = [fixed("bond", "境内国债", .4, [asset("511010.SH", "国债ETF")]),
                        fixed("equity", "股票宽基", .25, [asset("510300.SH", "沪深300ETF")]),
                        fixed("gold", "黄金", .15, [asset("518880.SH", "黄金ETF")]),
                        fixed("commodity", "商品", .1, [asset("159980.SZ", "有色ETF"), asset("159981.SZ", "能源化工ETF"), asset("159985.SZ", "豆粕ETF")]),
                        csi800_stocks(.10)]
        groups = {"cn_equity": ["equity", "stocks"], "cn_bonds": ["bond"], "gold": ["gold"], "commodity": ["commodity"]}
        c["volatility"] = {"enabled": name == "domestic-volatility-allocation", "signals": [
            {"series": "cn_vix50", "switch_to": "cn_vix300", "switch_from": "2020-01-01",
             "groups": ["cn_equity"], "enter": 25, "exit": 22}]}
        if name == "domestic-volatility-allocation":
            c["name"] = "境内多资产配置（境内波指调整）"
    else:
        raise ValueError("可选预设为 " + "、".join(PRESET_NAMES) + "。")
    c["allocation_limits"] = {"groups": groups, "max_shift": .05, "max_cash": .25}
    c["macro"] = macro_settings(cross)
    if name == "regional-macro-allocation":
        c["name"] = "跨境多资产配置（中美宏观调整）"
        c["macro"]["enabled"] = True
    return c


CATALOG = [
    {"类别": "境内股票宽基", "例子": "沪深300、中证500、创业板等ETF", "获取": "数据模块境内ETF接口"},
    {"类别": "红利、成长或行业股票", "例子": "红利低波、科创50、行业ETF，或自选A股", "获取": "ETF行情；个股可结合历史财务截面"},
    {"类别": "债券", "例子": "境内国债ETF、美国短债ETF", "获取": "境内行情；美国ETF另取汇率"},
    {"类别": "黄金", "例子": "黄金ETF", "获取": "境内ETF；矿业股票单列股票部分"},
    {"类别": "其他商品", "例子": "有色、能源化工、豆粕ETF", "获取": "境内ETF；上市前不造历史价格"},
    {"类别": "海外股票", "例子": "场外QDII：160213.OF（纳斯达克100）、050025.OF（标普500）", "获取": "人民币复权净值及公布日"},
    {"类别": "美元存款", "例子": "USD_DEPOSIT（美元活期存款）", "获取": "历史挂牌利率计息及可用汇率"},
    {"类别": "现金", "例子": "权重未分配部分", "获取": "默认零收益，无需下载"}]
