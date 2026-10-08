"""客户报告的名称、章节、披露及内容检查；与绩效计算分开。"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import re

TEMPLATE = Path(__file__).resolve().parents[2] / "assets/investment-report-template.json"
INTERNAL = re.compile(r"朋友示例|朋友组合|朋友原例|教学|课堂|练习|老师|学生|试跑|(?<![a-z])(?:demo|friend|classroom|teacher|student)(?![a-z])", re.I)
PROMISE = re.compile(r"稳赚|保本保收益|保证盈利|保证收益|必然盈利|无风险获利|零风险投资|稳赚不赔")
QUESTION = re.compile(r"[?？]|怎样|怎么|多少|先看|看它|看清|发现什么")


def client_text(value, field, heading=False):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}须为非空文本。")
    if INTERNAL.search(value):
        raise ValueError(f"{field}含内部代称，请通过label或display指定客户使用的专业名称。")
    for match in PROMISE.finditer(value):
        # 正常风险提示中的否定句不当作收益承诺；仍需另行进行语义复核。
        if not re.search(r"不|不得|禁止|避免|未", value[max(0,match.start()-6):match.start()]):
            raise ValueError(f"{field}含收益保证或误导性表述。")
    if heading and (QUESTION.search(value) or re.match(r"^[一二三四五六七八九十百\d]+套", value)):
        raise ValueError(f"{field}应使用研究主题或分析结论，避免设问和策略数量前缀。")
    return value.strip()


def default_name(config):
    if config.get("allocation", {}).get("method") == "risk_parity":
        return "多资产风险平价策略"
    codes = {a["code"] for s in config.get("sleeves", []) for a in s.get("assets", [])}
    series = {x.get("series") for r in config.get("macro", {}).get("rules", []) for k in ("all", "any") for x in r.get(k, [])}
    if "vix" in series and config.get("macro", {}).get("enabled"):
        return "VIX动态资产配置策略"
    if {"513100.SH", "513500.SH"} & codes:
        return "多资产配置策略（QDII权益）"
    if {"QQQ", "SPY"} & codes:
        return "多资产配置策略（海外ETF权益）"
    return "多资产配置策略"


def public_runs(config, runs):
    """仅复制显示属性，原始配置、数据、路径和指纹保持可复核。"""
    specs = {s["id"]: s for s in config.get("strategies", [])}
    result = []
    for run in runs:
        spec = specs.get(run.id, {})
        c = copy.deepcopy(run.config)
        label = spec.get("label") or run.label
        if INTERNAL.search(label) and not spec.get("label"):
            label = default_name(c)
        label = client_text(label, "策略名称", heading=True)
        c["name"] = label
        display = spec.get("display", {})
        if not isinstance(display, dict):
            raise ValueError("display需要对象。")
        names = display.get("sleeves", {})
        states = display.get("states", {})
        if not isinstance(names, dict) or not isinstance(states, dict):
            raise ValueError("display.sleeves/states需要名称映射对象。")
        for s in c.get("sleeves", []):
            name = names.get(s["id"], s.get("name", s["id"]))
            if name == "股票练习":
                method = s.get("selection", {}).get("method")
                name = {"momentum": "A股动量组合", "quality_value": "A股质量价值组合"}.get(method, "A股组合")
            s["name"] = client_text(name, "资产组合名称")
        docs = copy.deepcopy(run.documents)
        for row in docs.get("regimes", []):
            row["state"] = states.get(row.get("state"), row.get("state", "未标注"))
        for rule in c.get("macro", {}).get("rules", []):
            rule["name"] = states.get(rule.get("name"), rule.get("name", "条件配置"))
        result.append(replace(run, label=label, config=c, documents=docs))
    if len({r.label for r in result}) != len(result):
        raise ValueError("客户显示名称重复，请为各策略指定独立label。")
    return result


def paragraph(text, evidence_ids):
    return {"text": text, "evidence_ids": evidence_ids}


def decorate(data, config):
    """生成共享的章节契约、研究摘要、设定表和披露内容，供HTML/Markdown/AI复用。"""
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    metadata = config.get("report", {})
    if not isinstance(metadata, dict):
        raise ValueError("report需要对象。")
    allowed = set(template["metadata_fields"])
    if set(metadata)-allowed:
        raise ValueError("未知report字段："+"、".join(sorted(set(metadata)-allowed)))
    for key, value in metadata.items():
        client_text(value, "report."+key)
    meta = {"document_type": "investment_backtest", "version": "2.1", "report_date": date.today().isoformat(), **metadata}
    if meta["document_type"] != "investment_backtest":
        raise ValueError("当前模板用于投资策略历史回测研究；证券研究报告、基金营销材料须使用出具机构相应制度及模板。")
    try:
        date.fromisoformat(meta["report_date"])
    except ValueError as exc:
        raise ValueError("报告日期应为YYYY-MM-DD。") from exc
    data["title"] = client_text(data["title"], "报告标题", heading=True)
    data["report"] = meta
    data["template"] = {k: template[k] for k in ("id", "version", "sections", "headings")}
    data["report"]["nature"] = "投资策略研究 · 历史模拟回测"
    data["overview"] = []
    for s in data["strategies"]:
        c = s["config"]
        rules = {x["parameter"]: x["value"] for x in s["detail"]["rules"]}
        allocation = "；".join(f"{x.get('name', x['id'])} {x.get('weight',0)*100:g}%" for x in c.get("sleeves", []))
        if c.get("allocation", {}).get("method") == "risk_parity":
            allocation = "风险平价动态配权；回退基础比例：" + allocation
        if c.get("benchmark_only"):
            allocation = "单一证券买入持有："+c["benchmark_only"].get("name", c["benchmark_only"].get("code", "未提供"))
        data["overview"].append({"strategy": s["label"], "allocation": allocation or "净值输入未提供资产配置",
            "rebalance": rules.get("大类调仓", "未提供"), "macro": rules.get("宏观规则", "未提供"),
            "cost": "未提供" if c.get("cost_rate") is None else f"{c['cost_rate']:.4%}",
            "benchmark": c.get("benchmark", c.get("benchmark_only", {})).get("name", c.get("benchmark", c.get("benchmark_only", {})).get("code", "未提供"))})
        sleeve_names = {x["id"]:x.get("name", x["id"]) for x in c.get("sleeves", [])}
        for row in s["detail"]["trades"]:
            row["sleeve_name"] = sleeve_names.get(row.get("sleeve"), row.get("sleeve"))
            row["side_label"] = {"buy":"买入", "sell":"卖出"}.get(row.get("side"), row.get("side"))
        for row in s["detail"]["optimization"]:
            row["method_label"] = {"min_variance":"最小方差", "equal":"等权"}.get(row.get("method"), row.get("method"))
            row["status_label"] = {"success":"求解成功", "ok":"求解成功", "fallback_equal":"回退等权", "equal":"等权配置"}.get(row.get("status"), row.get("status"))

    for pair in data["pairs"]:
        a, b = [next(s for s in data["strategies"] if s["id"] == sid) for sid in (pair["left"], pair["right"])]
        ar, br = [{r["parameter"]: r["value"] for r in s["detail"]["rules"]} for s in (a,b)]
        pair["display_differences"] = [{"parameter": k, "left": ar.get(k, "不适用"), "right": br.get(k, "不适用")} for k in dict.fromkeys([*ar,*br]) if ar.get(k)!=br.get(k)]
        # 正文按投资决策维度对照；完整参数行保留在展开明细及复核资料。
        def dimensions(s):
            rules = s["detail"]["rules"]
            return {"基础配置": next(x["allocation"] for x in data["overview"] if x["strategy"]==s["label"]),
                **{title:"；".join(f"{r['parameter'].split('·')[0]}：{r['value']}" for r in rules if r["parameter"].endswith(suffix)) or "不适用" for title,suffix in [("投资标的","·证券"),("选股规则","·筛选"),("配权方法","·配权"),("组合内调仓","·内部调仓")]},
                **{title:next((r["value"] for r in rules if r["parameter"]==key),"未提供") for title,key in [("大类配权","大类配权"),("大类调仓","大类调仓"),("条件配置","宏观规则"),("成本设置","交易成本率")]}}
        ad, bd = dimensions(a), dimensions(b)
        pair["comparison_dimensions"] = [{"parameter":k, "left":ad[k], "right":bd[k]} for k in ad if ad[k]!=bd[k]]
        # 仅同一回测导出的配套场景具有已知的受控变更。价格指纹相同本身不足以识别因果。
        same_run = a["source"]["directory"] == b["source"]["directory"]
        scenarios = {a["scenario"], b["scenario"]}
        if same_run and pair["same_price_inputs"] and scenarios == {"strategy", "static"}:
            pair["comparison_type"] = "动态权重对照"
            pair["interpretation"] = "同一回测输入及调仓日历下，将动态大类配权替换为基础权重。差异用于评价本区间大类配权方法的回测影响。"
        elif same_run and pair["same_price_inputs"] and scenarios == {"strategy", "equal_weight"}:
            pair["comparison_type"] = "组合内配权对照"
            pair["interpretation"] = "同一回测输入下，将原采用最小方差的资产组合改为等权。其他资产组合维持原配权规则。"
        elif "benchmark" in scenarios:
            pair["comparison_type"] = "市场参照"
            pair["interpretation"] = "单一证券买入持有用于提供市场参照。其风险暴露与多资产组合不同，收益差异不直接等同于风险调整后的超额收益。"
        else:
            pair["comparison_type"] = "策略横向比较"
            pair["interpretation"] = "比较反映所列资产、权重、选股和调仓等全部差异的综合结果；单项规则的影响需通过其余条件相同的对照评价。"

    findings = []
    if len(data["strategies"]) == 1:
        findings.append(paragraph(data["facts"][0]["text"], [data["facts"][0]["id"]]))
    else:
        # 摘要涵盖比较集合和风险，不将历史排序改写为投资推荐。
        summary = "；".join(f"{s['label']}累计收益{s['metrics']['total_return']:.2%}、最大回撤{s['metrics']['max_drawdown']:.2%}" for s in data["strategies"])
        ids = [f["id"] for f in data["facts"] if f["evidence"] == ["metrics", "curves"]]
        findings.append(paragraph(f"{data['period']['start']}至{data['period']['end']}，{summary}。", ids))
    data["summary"] = findings
    data["lead"] = findings[0]["text"]
    data["assumptions"] = [{"item":"绩效性质", "value":"历史模拟回测；非实际账户业绩"},
        {"item":"比较区间", "value":f"{data['period']['start']}至{data['period']['end']}，{data['period']['observations']}个估值日"},
        {"item":"计价币种", "value":data["period"]["currency"]},
        {"item":"持仓方向", "value":"全部资产只做多，目标权重及持仓非负，现金不透支。仅提供净值的输入无法验证实际持仓方向"},
        {"item":"净值基点", "value":"共同起始日收盘净值指数设为1；延续各自原有持仓路径，首日收益记0"},
        {"item":"成本口径", "value":"；".join(f"{x['strategy']}：交易成本率{x['cost']}" for x in data["overview"])},
        {"item":"统计口径", "value":"年化收益按日历天数/365.25折算；波动率及夏普比率按252日折算；夏普计算无风险利率取0"}]
    notes = list(dict.fromkeys(n for s in data["strategies"] for n in s["detail"]["source_notes"]))
    for n in notes:
        client_text(n, "数据与执行说明")
    data["execution_notes"] = notes
    data["disclosures"] = [{"id":"historical", "title":"历史模拟与前瞻风险", "text":"报告展示给定规则在历史数据上的模拟表现。历史收益、风险和持仓结果不代表未来表现，也不构成收益承诺或当前交易指令。"}]
    costs = [s["config"].get("cost_rate") for s in data["strategies"]]
    if all(x == 0 for x in costs):
        cost_text = "回测交易成本率设为0。实际佣金、税费、冲击成本和交易滑点会影响可实现收益；现金收益及成交简化以本报告回测设定为准。"
    elif any(x is None for x in costs):
        cost_text = "部分输入未提供交易成本设定，相关净值的费前或费后口径尚待核实。各策略成本设置见回测设定。"
    else:
        cost_text = "各策略按列示的交易成本率计提。该设定不自动覆盖税费、冲击成本、滑点和管理费用；实际交易结果可能与模拟值存在差异。"
    data["disclosures"].append({"id":"costs", "title":"交易成本与实现条件", "text":cost_text})
    data["disclosures"].append({"id":"model", "title":"样本与模型局限", "text":"本次仅评价所列历史区间。未提供独立样本外检验、滚动前推及参数敏感性检验，现有结果不能据此证明跨周期稳定性。"})
    if any(s["config"].get("macro",{}).get("enabled") for s in data["strategies"]):
        data["disclosures"].append({"id":"macro", "title":"指标解释", "text":"条件规则按照配置指标及阈值触发。VIX等市场指标反映特定市场状态，其触发记录本身不足以确认经济周期阶段。"})
    data["disclosures"].append({"id":"holdings", "title":"持仓与适用范围", "text":f"持仓表为{data['period']['end']}的回测截面。具体产品选择和配置比例应结合投资目标、期限、流动性需求及风险承受能力另行评估。"})
    data["disclosures"].append({"id":"long_only", "title":"持仓方向与资金约束", "text":"本项目全部资产采用只做多配置；卖出交易表示减少已有持仓。已提供的配置、持仓及目标权重按非负要求检查；只有净值的外部输入不能据此确认持仓方向。"})
    if meta.get("conflicts_disclosure"):
        data["disclosures"].append({"id":"conflicts", "title":"利益冲突披露", "text":meta["conflicts_disclosure"]})
    data["scope_notes"] = [x for x in data["scope_notes"] if not x.startswith("收益最高")]
    return data


def validate_report(data):
    """有限、可复核的内容检查；不把字符串检查标注为监管合规认定。"""
    headings = [data["title"], *data["template"]["headings"].values(), *(b["title"] for b in data.get("commentary", []))]
    for h in headings:
        client_text(h, "标题", heading=True)
    for s in data["strategies"]:
        client_text(s["label"], "策略名称", heading=True)
        for row in s["detail"]["rules"]:
            client_text(row["parameter"]+row["value"], "策略规则")
    for row in data["facts"]:
        client_text(row["text"], "研究事实")
    for b in data.get("commentary", []):
        client_text(b["text"], "研究解读")
        if not b.get("evidence_ids") or not set(b["evidence_ids"]) <= {f["id"] for f in data["facts"]}:
            raise ValueError("研究解读引用了不存在的事实编号。")
    required = {"historical", "costs", "model", "holdings", "long_only"}
    if not required <= {x["id"] for x in data["disclosures"]}:
        raise ValueError("报告缺少必需的回测说明。")
    missing = [k for k in ("issuer", "author", "reviewer", "conflicts_disclosure") if not data["report"].get(k)]
    return {"template_id":data["template"]["id"], "content_checks":"passed", "required_disclosures":sorted(required),
        "internal_names_in_client_text":False, "commentary_evidence_ids":"checked",
        "commentary_semantics":"agent_review_required" if data.get("commentary") else "deterministic_facts",
        "unprovided_institutional_fields":missing,
        "institutional_review":"not_recorded", "legal_compliance_certification":False,
        "scope":"计算、名称、必需披露与事实引用检查；出具机构资质、利益冲突、适用渠道及发布审查由实际机构核实。"}


def validate_artifact(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if Path(name).name != name or hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"报告文件校验失败：{name}")
    data = json.loads((root/"report_data.json").read_text(encoding="utf-8"))
    if data.get("document_type") == "rebalance_plan":
        from .rebalance import validate_plan
        return {**validate_plan(data), "files_verified": len(manifest["files"])}
    result = validate_report(data)
    result["files_verified"] = len(manifest["files"])
    result["note"] = "文件指纹与有限内容检查通过；不等同于机构发布审查。"
    return result
