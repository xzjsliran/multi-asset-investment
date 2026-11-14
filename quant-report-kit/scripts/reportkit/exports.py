"""同一份核验后的分析，输出给浏览器、人和宿主AI。"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re

import pandas as pd

from .inputs import write_json
from .reporting import client_text, validate_report
from .evidence import contains_number, render_commentary


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def add_commentary(data, value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError("解读文件需要JSON对象。")
    inputs = [] if value is None else value.get("sections", [])
    if not isinstance(inputs, list):
        raise ValueError("解读文件需要sections数组。")
    known = {f["id"] for f in data["facts"]}
    blocks = []
    for block in inputs:
        if not isinstance(block, dict) or not all(isinstance(block.get(k), str) and block[k].strip() for k in ("title", "text")):
            raise ValueError("每段解读需要title和text。")
        ids = block.get("evidence_ids", [])
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in known for i in ids):
            raise ValueError("每段解读的evidence_ids须引用本次facts中存在的事实编号。")
        client_text(block["title"], "解读标题", heading=True)
        if contains_number(block["title"]):
            raise ValueError("研究解读标题须使用定性表述，关键数字由 claims 引用。")
        client_text(block["text"], "解读正文")
        section = block.get("section", "comparison")
        if section not in {"summary", "comparison", "portfolio"}:
            raise ValueError("解读section须为summary/comparison/portfolio。")
        claims = block.get("claims", [])
        text = render_commentary(data, block["text"], claims, ids)
        blocks.append({"section": section, "title": block["title"], "text": text,
                       "interpretation": block["text"], "claims": claims, "evidence_ids": ids})
    data["commentary"] = blocks
    summary = [b for b in blocks if b["section"] == "summary"]
    if summary:
        data["summary"] = [*data.get("execution_findings", []), *summary]
        data["lead"] = data["summary"][0]["text"]
    return data


def make_snapshot(data):
    queries = {}
    files = [f"{s['label']} / {s['source']['daily_file']}" for s in data["strategies"]]

    def query(qid, rows, definition, source_files=None):
        queries[qid] = {"rows": rows, "source": {"label": "历史回测数据",
            "tables": source_files or files,
            "filters": [f"共同区间 {data['period']['start']} 至 {data['period']['end']}"],
            "metricDefinitions": [{"label": qid, "definition": definition}]}}

    query("metrics", [{"strategy": s["label"], **s["metrics"]} for s in data["strategies"]],
          data["period"]["anchor"] + "。累计收益为末日净值与起算基值之比减1。年化收益按日历天数折算，波动率按252日折算。")
    query("original_metrics", [{"strategy": s["label"], **s["original_metrics"]} for s in data["strategies"]], "按各自完整区间重新汇总；不参与共同区间排名。")
    queries["original_metrics"]["source"]["filters"] = ["各策略各自完整回测区间，日期在表格中逐项列出"]
    query("curves", data["curves"], data["period"]["anchor"] + "。回撤相对于此前峰值，含起算基值。")
    query("monthly", data["monthly"], "所选区间内日收益按月复利；首末月可能不完整。")
    query("annual", data["annual"], "所选区间内日收益按年复利；首末年可能不完整。")
    query("facts", data["facts"], "事实编号对应report_data.json中的计算结果和决策记录。")
    pair_rows = []
    for pair in data["pairs"]:
        pair_rows += [{"left_strategy": pair["left"], "right_strategy": pair["right"], **row} for row in pair["rule_differences"]]
    query("rule_differences", pair_rows, "逐字段比较实际策略配置；显示名称、回测日期、本金不当作策略规则差异。",
          [f"{s['label']} / strategy.json" for s in data["strategies"]])
    public_diffs = [{"left_strategy":p["left"], "right_strategy":p["right"], **r} for p in data["pairs"] for r in p["display_differences"]]
    query("display_differences", public_diffs, "对照策略配置的中文说明；完整字段差异见计算结果。")
    query("overview", data["overview"], "实际回测配置中的资产范围、基础比例、调仓和基准。")
    query("assumptions", data["assumptions"], "本报告历史模拟性质、比较区间、成本及统计口径。")
    query("execution_notes", [{"note":n} for n in data["execution_notes"]], "原始回测的执行假设及数据说明。", [f"{s['label']} / manifest.json" for s in data["strategies"]])
    delta_rows = [{"left":p["left"], "right":p["right"], "metric":label, "value":p["deltas"][key]} for p in data["pairs"] for key,label in [("total_return","累计收益差"),("annual_volatility","年化波动率差"),("max_drawdown","最大回撤差")]]
    query("pair_deltas", delta_rows, "共同区间后者指标减前者；小数存储，显示为百分点。最大回撤差为正表示后者回撤较浅。")
    query("disclosures", data["disclosures"], "根据实际输入和本次分析范围列示的研究局限与风险提示。")
    for s in data["strategies"]:
        source = s["label"]
        risk = s["detail"]["risk"]
        query(f"{s['id']}_risk", risk["rows"], risk.get("definition", risk.get("reason", "")), [f"{source}/{s['source']['daily_file']}"])
        query(f"{s['id']}_risk_correlations", risk["correlations"], risk.get("correlation_definition", "未提供"), [f"{source}/{s['source']['daily_file']}"])
        for key, definition, filename in [
            ("rules", "实际运行配置；内置对照显示其实际配权/静态规则。", "strategy.json"),
            ("contributions", "区间内各部分计入费用的货币盈亏之和除以起算权益，贡献相加等于区间收益。", s["source"]["daily_file"]),
            ("holdings", "共同末日收盘调仓后的持仓权重。", "weights.csv"),
            ("weight_history", "各部分每日收盘调仓后的持仓权重。", "weights.csv"),
            ("trades", "所选区间内执行的成交；正金额买入，负金额卖出。", "trades.csv"),
            ("selections", "所选区间执行的选股和配权记录。", "selections.csv"),
            ("regimes", "按执行日期筛选的宏观配置记录；同时保留信号日、可获得日、指标值和目标权重。", "regimes.json"),
            ("optimization", "按执行日期筛选的配权诊断，含样本区间和回退记录。", "optimization.json"),
            ("diagnostics", "原始完整回测的质量检查计数，未按共同区间重算。", "checks.json"),
        ]:
            query(f"{s['id']}_{key}", s["detail"][key], definition, [f"{source}/{filename}"])
            if key == "diagnostics":
                queries[f"{s['id']}_{key}"]["source"]["filters"] = ["原始完整回测"]
    # 对客户展示的快照只保留来源标识及指纹；机器复核资料另存原路径。
    public = json.loads(compact(data))
    for s in public["strategies"]:
        s["source"].pop("directory", None)
    review = review_markdown(data)
    if review:
        # 离线模板已有带证据的研究局限区，直接复用其正文渲染。
        public["disclosures"].append({"id": "review_suggestions", "title": "结果复查", "text": review})
        queries["disclosures"]["rows"] = public["disclosures"]
    return {"surface": "report", "title": data["title"], "status": "reviewed", "buildStatus": "complete",
            "id": "quant-report-" + hashlib.sha256(compact(data).encode()).hexdigest()[:20],
            "generatedAt": datetime.now(timezone.utc).isoformat(), "report": {"asOf": data["period"]["end"]},
            "filters": [], "queries": queries, "quantReport": public}


def render_html(snapshot, template_path):
    template = Path(template_path).read_text(encoding="utf-8")
    marker = "__QUANT_REPORT_SNAPSHOT__"
    if template.count(marker) != 1:
        raise ValueError("报告模板缺少唯一的数据占位符，请重新构建模板。")
    raw = compact(snapshot)
    payload = raw.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    rendered = template.replace(marker, payload)
    # 同一字体声明覆盖正文、控件和图表文字，中文字符回退到宋体。
    typography = (
        '<style>.qr-report, .qr-report button, .qr-report input, .qr-report select, '
        '.qr-report svg text {font-family: "Times New Roman", "SimSun", "宋体", serif;}</style>'
    )
    rendered = rendered.replace("</head>", typography + "</head>", 1)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    rendered = re.sub(r'(<meta\s+name="data-app-snapshot-sha256"\s+content=")[^"]*("\s*/?>)', lambda m: m[1]+digest+m[2], rendered)
    rendered = re.sub(r"<title>.*?</title>", lambda _: "<title>"+html.escape(snapshot["title"])+"</title>", rendered, count=1, flags=re.S)
    return rendered


def pct(v):
    return "—" if v is None else f"{v:.2%}"


def md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def review_markdown(data):
    blocks = []
    for item in data.get("review_suggestions", []):
        ids = "、".join(item["evidence_ids"])
        blocks.extend([f"**{item['title']}**", f"{item['observation']}（{ids}）", item["test"], item["decision"]])
    return "\n\n".join(blocks)


def markdown_report(data):
    p, meta, h = data["period"], data["report"], data["template"]["headings"]
    out = [f"# {data['title']}", "", meta["nature"], "",
        f"报告日期：{meta['report_date']}　版本：{meta['version']}　数据截至：{p['end']}", ""]
    for key,label in [("report_id","报告编号"),("issuer","出具机构"),("author","研究人员"),("reviewer","复核人员"),("distribution","分发范围")]:
        if meta.get(key): out += [f"{label}：{meta[key]}", ""]

    def heading(key): out.extend(["", "## "+h[key], ""])
    def table(rows, columns):
        out.append("|"+"|".join(x[1] for x in columns)+"|")
        out.append("|"+"|".join("---" for _ in columns)+"|")
        for row in rows:
            out.append("|"+"|".join(md_cell(fmt(row.get(key))) if fmt else md_cell(row.get(key,"—")) for key,_,fmt in columns)+"|")
        out.append("")
    def blocks(section):
        for block in data.get("commentary", []):
            if block["section"] == section:
                out.extend(["### "+block["title"], "", block["text"], ""])

    heading("summary")
    for block in data["summary"]:
        if block.get("title"): out.extend(["### "+block["title"], ""])
        out.extend([block["text"], ""])
    heading("mandate")
    table(data["overview"], [("strategy","策略",None),("allocation","基础配置",None),("rebalance","大类调仓",None),("benchmark","参照基准",None)])
    table(data["assumptions"], [("item","项目",None),("value","设定",None)])
    out += [n+"\n" for n in data["execution_notes"]]
    heading("performance")
    cols=[("strategy","策略",None),("total_return","累计收益",pct),("annual_return","年化收益",pct),("annual_volatility","年化波动率",pct),("max_drawdown","最大回撤",pct),("sharpe_zero_rf","夏普比率（无风险利率0）",lambda v:"—" if v is None else f"{v:.2f}")]
    table([{"strategy":s["label"],**s["metrics"]} for s in data["strategies"]], cols)
    table(data["annual"], [("strategy","策略",None),("period","年度",None),("return","区间收益",pct)])
    out += ["年度、月度指标仅覆盖所选区间；首末年度或月份可能不完整。", ""]
    table(data["monthly"], [("strategy","策略",None),("period","月份",None),("return","区间收益",pct)])
    if data["pairs"]:
        heading("comparison")
        blocks("comparison")
        labels={s["id"]:s["label"] for s in data["strategies"]}
        for pair in data["pairs"]:
            out += ["### "+labels[pair["left"]]+"与"+labels[pair["right"]], "", pair["comparison_type"]+"。"+pair["interpretation"], ""]
            d=pair["deltas"]
            out += [f"后者相对前者：累计收益差{d['total_return']*100:+.2f}个百分点，年化波动率差{d['annual_volatility']*100:+.2f}个百分点，最大回撤差{d['max_drawdown']*100:+.2f}个百分点（正值表示回撤较浅）。", ""]
            if pair["comparison_dimensions"]:
                table(pair["comparison_dimensions"], [("parameter","配置维度",None),("left",labels[pair["left"]],None),("right",labels[pair["right"]],None)])
    heading("portfolio")
    blocks("portfolio")
    for s in data["strategies"]:
        d,m=s["detail"],s["metrics"]
        out += ["### "+s["label"], ""]
        if m["max_drawdown"]<0:
            recovery=f"恢复日期为{m['recovery_date']}" if m["recovery_date"] else "截至区间末尚未恢复前期高点"
            out += [f"最大回撤{m['max_drawdown']:.2%}；前期高点日期{m['drawdown_peak']}，谷底日期{m['drawdown_trough']}，{recovery}。", ""]
        if d["contributions"]:
            table(d["contributions"], [("name","资产组合",None),("contribution","收益贡献（百分点）",lambda v:f"{v*100:+.2f}")])
        risk = d["risk"]
        out += ["#### 风险贡献分析", "", risk.get("definition", risk.get("reason", "")), ""]
        if risk["rows"]:
            table(risk["rows"], [("name","资产组合",None),("volatility_contribution","年化波动贡献（百分点）",lambda v:f"{v*100:+.2f}"),("risk_share","风险贡献占比",pct)])
            out += [risk["note"], ""]
            table(risk["correlations"], [("left_name","资产部分",None),("right_name","对照部分",None),("correlation","收益贡献相关系数",lambda v:"—" if v is None else f"{v:.3f}")])
            out += [risk["correlation_definition"], ""]
        if d["holdings"]:
            table(d["holdings"], [("sleeve_name","资产组合",None),("code","代码",None),("name","证券名称",None),("weight","期末权重",pct)])
        table(d["rules"], [("parameter","策略规则",None),("value","内容",None)])
        out += [n+"\n" for n in d["notices"]]
    heading("disclosures")
    for item in data["disclosures"]:
        out += ["### "+item["title"], "", item["text"], ""]
    out += [n+"\n" for n in data["scope_notes"]]
    review = review_markdown(data)
    if review:
        out += ["## 结果复查", "", review, ""]
    heading("appendix")
    table([{"strategy":s["label"],**s["original_metrics"]} for s in data["strategies"]], [("strategy","策略",None),("start","起始日",None),("end","结束日",None),*cols[1:]])
    out += [data["period"]["anchor"] + "。累计收益为期末与期初净值之比减1。年化收益按实际日历天数折算，年化波动率采用日收益样本标准差乘以√252。夏普比率采用零无风险利率。", "",
        "回撤相对于所选区间内此前最高净值计算。月度及年度收益为当期日收益的复合收益。资产组合收益贡献按区间货币盈亏除以期初权益计算，合计等于累计收益；该指标不构成经济因果归因。", "",
        "资料来源：所列策略的日度净值、配置参数及已提供的持仓和决策记录。各图表及数据表附来源标识；完整输入指纹保存在随附复核资料中。", ""]
    return "\n".join(out)


def export_report(data, config, out, template):
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"输出目录已有文件，请另选一个目录：{out}")
    # 先在内存完成渲染；缺模板、错误JSON等问题不会留下半套报告。
    publication_checks = validate_report(data)
    snapshot = make_snapshot(data)
    rendered = render_html(snapshot, template)
    narrative = markdown_report(data)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out/"report_data.json", data)
    write_json(out/"report_config.json", config)
    context = {k: data[k] for k in ["schema_version", "title", "period", "lead", "facts", "pairs", "scope_notes", "methods", "report", "template", "overview", "assumptions", "execution_notes", "disclosures"]}
    context["strategies"] = [{"id": s["id"], "label": s["label"], "metrics": s["metrics"], "rules": s["detail"]["rules"],
        "contributions": s["detail"]["contributions"], "risk":s["detail"]["risk"], "regimes": s["detail"]["regimes"], "diagnostics": s["detail"]["diagnostics"],
        "source_notes": s["detail"]["source_notes"], "notices": s["detail"]["notices"],
        "execution_review": s["execution_review"]} for s in data["strategies"]]
    context["evidence_values"] = data["evidence_values"]
    context["execution_findings"] = data["execution_findings"]
    context["review_suggestions"] = data.get("review_suggestions", [])
    context["writing_instructions"] = (
        "按本次事实撰写解读。每段提供 section、title、text、evidence_ids，可选 claims。"
        "正文 text 只写定性解释，数值以 evidence_values 的键写入 claims，由程序生成完整陈述。"
        "引用数值的 fact_id 必须在本段 evidence_ids 中。先解释执行限制，再讨论收益和风险。"
        "横向方案差异只说明整体差异。判断和机构发布仍需另行复核。"
    )
    write_json(out/"ai_context.json", context)
    for key in ["curves", "monthly", "annual"]:
        pd.DataFrame(data[key]).to_csv(out/f"{key}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"id": s["id"], "strategy": s["label"], **s["metrics"]} for s in data["strategies"]]).to_csv(out/"comparison.csv", index=False, encoding="utf-8-sig")
    contributions = [{"strategy_id": s["id"], "strategy": s["label"], **row} for s in data["strategies"] for row in s["detail"]["contributions"]]
    pd.DataFrame(contributions, columns=["strategy_id", "strategy", "sleeve", "name", "pnl", "contribution"]).to_csv(out/"contributions.csv", index=False, encoding="utf-8-sig")
    risks = [{"strategy_id":s["id"],"strategy":s["label"],**row} for s in data["strategies"] for row in s["detail"]["risk"]["rows"]]
    pd.DataFrame(risks, columns=["strategy_id","strategy","sleeve","name","volatility_contribution","risk_share"]).to_csv(out/"risk_contributions.csv", index=False, encoding="utf-8-sig")
    correlations = [{"strategy_id":s["id"],"strategy":s["label"],**row} for s in data["strategies"] for row in s["detail"]["risk"]["correlations"]]
    pd.DataFrame(correlations, columns=["strategy_id","strategy","left","right","left_name","right_name","correlation"]).to_csv(out/"risk_correlations.csv", index=False, encoding="utf-8-sig")
    # 避免左右策略ID与左右字段值重名。
    diffs = [{"left_strategy": p["left"], "right_strategy": p["right"], **r} for p in data["pairs"] for r in p["rule_differences"]]
    pd.DataFrame(diffs, columns=["left_strategy", "right_strategy", "parameter", "left", "right"]).to_csv(out/"rule_differences.csv", index=False, encoding="utf-8-sig")
    (out/"报告.html").write_text(rendered, encoding="utf-8")
    (out/"报告解读.md").write_text(narrative, encoding="utf-8")
    checks = {"same_currency": True, "same_comparison_dates": True, "positive_finite_nav": True,
              "daily_return_matches_nav_when_provided": True, "contribution_sum_matches_return_when_available": True,
              "strategy_count": len(data["strategies"]), "facts": len(data["facts"]), "commentary_sections": len(data.get("commentary", []))}
    checks["report_content"] = publication_checks
    checks["risk_contribution_sum_matches_volatility_when_available"] = True
    write_json(out/"publication_checks.json", publication_checks)
    write_json(out/"checks.json", checks)
    manifest = {"schema_version": 1, "producer": "quant-report-kit", "created_at": snapshot["generatedAt"],
                "inputs": data["input_fingerprints"], "template_sha256": hashlib.sha256(Path(template).read_bytes()).hexdigest(),
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}}
    write_json(out/"manifest.json", manifest)
    return {"report": str(out/"报告.html"), "interpretation": str(out/"报告解读.md"),
            "checks": checks, "review_suggestions": context["review_suggestions"]}
