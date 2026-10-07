"""调仓计划的客户呈现；只消费计算结果，不重跑策略或改写交易数量。"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import html
import json
import math

import pandas as pd
from pathlib import Path

from .inputs import read_json, write_json
from .reporting import client_text, default_name, INTERNAL
from .analysis import selection_text, weighting_text, macro_text


def validate_plan(p):
    if p.get("document_type") != "rebalance_plan" or p.get("position_policy") != "long_only":
        raise ValueError("调仓报告需要只做多的调仓计划。")
    if p.get("mode") not in {"demo", "live"}:
        raise ValueError("计划缺少实际或模拟账户状态。")
    if p["mode"] == "live" and not p.get("confirmation", {}).get("actual_account_confirmed"):
        raise ValueError("实际计划尚未确认策略与持仓。")
    def nonnegative(value, label):
        if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError(label+"出现无效值或负持仓。")
    for row in p["holdings"]:
        for k in ["current_quantity", "projected_quantity", "current_value_cny", "projected_value_cny", "current_weight", "projected_weight", "target_weight"]:
            nonnegative(row[k], k)
    for row in p["targets"]:
        nonnegative(row["target_value_cny"], "目标金额")
        nonnegative(row["target_weight"], "目标权重")
    for row in p["cash_before"]+p["cash_after"]:
        nonnegative(row["amount"], "现金")
    for rate in p["fx"].values():
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("汇率无效。")
    equity = p["account"]["equity_cny"]
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("账户总资产无效。")
    current_cash = sum(x["amount"]*p["fx"][x["currency"]] for x in p["cash_before"])
    projected_cash = sum(x["amount"]*p["fx"][x["currency"]] for x in p["cash_after"])
    if abs(current_cash-p["account"]["cash_before_cny"]) > .01 or abs(projected_cash-p["account"]["cash_after_cny"]) > .01:
        raise ValueError("账户概要的现金与分币种现金不一致。")
    for value in [sum(x["current_value_cny"] for x in p["holdings"])+current_cash,
                  sum(x["projected_value_cny"] for x in p["holdings"])+projected_cash,
                  sum(x["target_value_cny"] for x in p["targets"])]:
        if abs(value-equity) > max(1., equity)*1e-8:
            raise ValueError("计划金额加总与账户总资产不一致。")
    if abs(sum(x["target_weight"] for x in p["targets"])-1) > 1e-8:
        raise ValueError("计划目标权重未加总为100%。")
    changes, orders, cash_changes = defaultdict(float), {}, defaultdict(float)
    for row in p["holdings"]:
        changes[row["code"]] += row["projected_quantity"]-row["current_quantity"]
    quotes = {row["code"]: row for row in p["quotes"]}
    for row in p["quotes"]:
        if row.get("price_basis") != "raw" or row["price"] <= 0 or not math.isfinite(row["price"]):
            raise ValueError("数量计算的原始参考价格无效。")
        if abs(row["price"]*p["fx"][row["currency"]]-row["price_cny"]) > 1e-7:
            raise ValueError("原币价格与人民币价格不一致。")
        if pd.Timestamp(row["available_at"]) > pd.Timestamp(p["timing"]["as_of"]):
            raise ValueError("参考报价晚于数据截止时间。")
    for row in p["holdings"]:
        for state in ["current", "projected"]:
            value = row[state+"_quantity"]*quotes[row["code"]]["price_cny"]
            if abs(value-row[state+"_value_cny"]) > .01 or abs(value/equity-row[state+"_weight"]) > 1e-8:
                raise ValueError("持仓数量、金额与权重不一致。")
        if "current_available_quantity" in row:
            nonnegative(row["current_available_quantity"], "当前可卖数量")
            if row["current_available_quantity"] > row["current_quantity"]+1e-9:
                raise ValueError("当前可卖数量大于持仓。")
    for row in p["targets"]:
        if abs(row["target_value_cny"]/equity-row["target_weight"]) > 1e-8:
            raise ValueError("目标金额与目标权重不一致。")
    for row in p["orders"]:
        nonnegative(row["quantity"], "买卖数量")
        if row["code"] in orders or row["side"] not in {"buy", "sell"}:
            raise ValueError("拟交易清单未按证券合并，或买卖方向无效。")
        signed = row["quantity"]*(1 if row["side"] == "buy" else -1)
        q = quotes[row["code"]]
        if row["currency"] != q["currency"] or abs(row["reference_price"]-q["price"]) > 1e-9 or abs(row["amount_native"]-abs(signed)*q["price"]) > .01:
            raise ValueError("拟交易清单的价格、币种或原币金额不一致。")
        if abs(signed-row["signed_quantity"]) > 1e-8 or abs(abs(signed)*q["price_cny"]-row["amount_cny"]) > .01:
            raise ValueError("买卖数量、方向和金额不一致。")
        orders[row["code"]] = signed
        held = [x for x in p["holdings"] if x["code"] == row["code"]]
        if signed < 0 and all("current_available_quantity" in x for x in held) and -signed > sum(x["current_available_quantity"] for x in held)+1e-8:
            raise ValueError("拟卖出数量超过可卖持仓。")
        cash_changes[row["currency"]] -= signed*q["price"]
    if any(abs(changes[code]-orders.get(code, 0.)) > 1e-7 for code in set(changes) | set(orders)):
        raise ValueError("拟交易清单不能还原调整后数量。")
    for cur in p["fx"]:
        delta = sum(x["amount"] for x in p["cash_after"] if x["currency"] == cur)-sum(x["amount"] for x in p["cash_before"] if x["currency"] == cur)
        if abs(delta-cash_changes[cur]) > .01:
            raise ValueError("拟交易清单与分币种现金变化不一致。")
    return {"content_checks": "passed", "position_policy": "long_only", "negative_positions": 0,
            "cash_conservation": "passed", "orders_reconcile_positions": True}


def load_plan(directory):
    root = Path(directory)
    h = read_json(root/"handoff.json")
    if h.get("document_type") != "rebalance_plan":
        raise ValueError("输入目录不是调仓计划。")
    for name, expected in h["files"].items():
        if Path(name).name != name or hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            raise ValueError("调仓数据校验失败："+name)
    p, c = read_json(root/"plan.json"), read_json(root/"strategy.json")
    validate_plan(p)
    return p, c


def export_rebalance(directory, out):
    p, c = load_plan(directory)
    template = read_json(Path(__file__).resolve().parents[2]/"assets/rebalance-report-template.json")
    title = template["title"]
    name = default_name(c) if INTERNAL.search(c["name"]) else client_text(c["name"], "策略名称", heading=True)
    names = {s["id"]: client_text(c.get("display", {}).get("sleeves", {}).get(s["id"], s.get("name", s["id"])), "资产部分名称") for s in c["sleeves"]}
    names["cash"] = "未分配现金"
    asset_names = {a["code"]: a.get("name", a["code"]) for s in c["sleeves"] for a in s.get("assets", [])}
    asset_names.update({x["code"]: x.get("name", x["code"]) for x in p["quotes"]})
    asset_names.update({x["code"]: x.get("name", x["code"]) for x in p["selections"]})
    fmt = lambda x: f"{0. if abs(x) < .005 else x:,.2f}"
    pct = lambda x: f"{x:.2%}"
    qty = lambda x: f"{x:,.6f}".rstrip('0').rstrip('.')
    display_time = lambda value: pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")
    timing = p["timing"]
    status = {"preliminary": "调仓预案 · 信号日更新", "constrained": "调仓计划 · 存在执行约束", "calculated": "调仓计划"}[p["status"]]
    nature = "历史模拟账户" if p["mode"] == "demo" else "已确认实际持仓"
    event_text = "初始配置" if p["action"] == "initial_allocation" else ("大类资产配置调整" if p["event"]["is_outer"] else ("、".join(names[sid] for sid in p["event"]["selection_sleeves"])+"内部调仓" if p["event"]["selection_sleeves"] else "本日未触发调仓，维持持仓"))
    summary = f"{timing['execution_date']}拟执行{event_text}。参考总资产{fmt(p['account']['equity_cny'])}元，调整后预计现金{fmt(p['account']['cash_after_cny'])}元。所有资产只做多。"
    overview = [["策略名称", name], ["账户性质", nature], ["持仓记录时间", display_time(timing["holdings_as_of"])],
                ["策略与持仓确认", "历史模拟账户" if p["mode"] == "demo" else display_time(p["confirmation"]["confirmed_at"])],
                ["数据截止时间", display_time(timing["as_of"])], ["本次采用的信号日", timing["signal_date"]],
                ["计划信号日", timing["scheduled_signal_date"]], ["拟执行日期", timing["execution_date"]],
                ["计划状态", status], ["交易成本率", "0%"], ["持仓方向", "全部资产只做多，现金不透支"]]
    allocations = []
    for sid in names:
        before = sum(x["current_value_cny"] for x in p["holdings"] if x["sleeve"] == sid)+sum(x["amount"]*p["fx"][x["currency"]] for x in p["cash_before"] if x["sleeve"] == sid)
        after = sum(x["projected_value_cny"] for x in p["holdings"] if x["sleeve"] == sid)+sum(x["amount"]*p["fx"][x["currency"]] for x in p["cash_after"] if x["sleeve"] == sid)
        target = sum(x["target_value_cny"] for x in p["targets"] if x["sleeve"] == sid)
        equity = p["account"]["equity_cny"]
        allocations.append([names[sid], pct(before/equity), pct(target/equity), pct(after/equity), fmt(after-target)])
    order_rows = [[asset_names.get(x["code"], x["code"]), x["code"], "买入" if x["side"] == "buy" else "卖出", qty(x["quantity"]),
                   f"{x['reference_price']:,.4f}", x["currency"], fmt(x["amount_native"]), fmt(x["amount_cny"]), display_time(x["price_available_at"])] for x in p["orders"]]
    holdings = [[names[x["sleeve"]], asset_names.get(x["code"], x["code"]), x["code"], qty(x["current_quantity"]), qty(x["projected_quantity"]),
                 pct(x["current_weight"]), pct(x["target_weight"]), pct(x["projected_weight"])] for x in p["holdings"]]
    reasons = [[names[s["id"]], selection_text(s["selection"]), weighting_text(s["weighting"])] for s in c["sleeves"]]
    audit_rows = [[names[x["sleeve"]], {"min_variance":"最小方差", "equal":"等权", "fixed":"指定比例"}.get(x["method"], x["method"]),
                   x.get("sample_start", "不适用"), x.get("sample_end", "不适用"), x.get("observations", "不适用"),
                   {"ok":"完成", "fallback_equal":"等权回退", "no_eligible_assets":"留存现金"}.get(x["status"], x["status"]),
                   "；".join(x.get("notes", [])) or "—"] for x in p["optimization"]]
    conditions = macro_text(c.get("macro", {}), names)
    if c.get("allocation",{}).get("method") == "risk_parity":
        conditions = "大类配权采用风险平价；组合内部继续执行已确认的选股与配权规则。"
    regime = p.get("regime")
    risk_rows = []
    risk_note = ""
    if regime:
        conditions = conditions.rstrip("。")+"。本次配置状态："+regime["state"]
        for x in regime.get("inputs", []):
            conditions += f"；{x['series']}："+(f"{x['value']}，统计期{x['period']}，可用时间{display_time(x['available_at'])}" if 'value' in x else x.get('reason', '未提供'))
        if regime.get("method") == "risk_parity":
            if regime.get("status") == "success":
                conditions += f"；风险代理收益样本{regime['sample_start']}至{regime['sample_end']}，共{regime['observations']}条，估计年化波动率{pct(regime['annual_volatility'])}。"
                risk_rows = [[names.get(x["sleeve"],x["sleeve"]),pct(x["target_weight"]),pct(x["risk_share"]),f"{x['volatility_contribution']*100:.3f}"] for x in regime["risk_rows"]]
                risk_note = "按风险代理及信号日前的收缩协方差估计；反映目标配置，未计整手、资金及未成交偏差。实际执行后的风险分布可能不同。"
            else:
                conditions += "；"+regime.get("reason", "风险估计未完成")
    issues = [[names.get(x["sleeve"], x["sleeve"]), x["code"], x["reason"]] for x in p["issues"]]
    funding_headers = ["资金范围", "币种", "拟买入所需资金", "可用于买入的资金", "资金缺口"]
    funding = [["整个组合" if x["scope"] == "portfolio" else names[x["scope"]], x["currency"], fmt(x["required_buy_cash_native"]),
                fmt(x["spendable_cash_native"]), fmt(x["shortfall_native"])] for x in p.get("funding_gaps", [])]
    cash = defaultdict(float)
    for row in p["cash_after"]:
        cash[row["currency"]] += row["amount"]
    calendars = [[x["signal_date"], x["execution_date"], "大类资产配置" if x["outer"] else "组合内部调整", "、".join(names[sid] for sid in x["sleeves"])] for x in p["next_events"]]
    esc = lambda x: html.escape(str(x), quote=True)
    def table(headers, rows, empty="无相关记录"):
        return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows) if rows else '<tr><td colspan="'+str(len(headers))+'">'+esc(empty)+'</td></tr>')+'</tbody></table></div>'
    sections = []
    def add(sid, content):
        heading = next(s["title"] for s in template["sections"] if s["id"] == sid)
        sections.append(f'<section id="{sid}"><h2>{esc(heading)}</h2>{content}</section>')
    add("summary", '<p>'+esc(summary)+'</p>'+table(["项目", "内容"], overview))
    add("allocation", table(["资产部分", "当前比例", "目标比例", "预计调整后比例", "偏离目标金额（元）"], allocations)+'<p class="note">资产部分比例包含该部分保留的现金；预计调整后比例已考虑可卖数量、交易单位和分币种资金约束。</p>')
    add("orders", table(["资产", "代码", "方向", "预计数量", "参考单价", "币种", "预计原币金额", "折合人民币（元）", "报价可用时间"], order_rows, "本次没有拟交易指令")+'<p class="note">先卖后买；同一证券按账户净额列示。所列数量和金额为参考价估算。</p>')
    add("holdings", table(["资产部分", "资产", "代码", "当前数量", "预计调整后数量", "当前权重", "目标权重", "预计调整后权重"], holdings))
    audit_headers = ["资产部分", "配权方法", "样本起始日", "样本截止日", "共同收益样本数", "本次计算状态", "说明"]
    risk_headers = ["资产部分","目标权重","估计风险贡献占比","估计年化波动贡献（百分点）"]
    add("rationale", '<p>'+esc(conditions)+'</p>'+table(["资产部分", "选择规则", "配权方法"], reasons)+table(audit_headers, audit_rows, "本次无需更新组合内部配权")+("<h3>目标配置风险估计</h3>"+table(risk_headers,risk_rows)+'<p class="note">'+esc(risk_note)+'</p>' if risk_rows else ''))
    add("constraints", table(["资产部分", "代码", "事项"], issues, "未识别到额外执行约束")+(table(funding_headers, funding) if funding else '')+table(["币种", "预计剩余现金"], [[cur, fmt(v)] for cur, v in sorted(cash.items())])+'<ul>'+''.join('<li>'+esc(n)+'</li>' for n in p["notes"])+'</ul>')
    add("calendar", table(["信号日", "拟执行日", "事件", "更新内部配置的资产部分"], calendars, "请补充后续交易日历"))
    style = '''*{box-sizing:border-box}body{margin:0;background:#f2f4f7;color:#172b44;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}main{max-width:1280px;margin:36px auto;padding:40px;background:white;border-top:6px solid #153e65}header{border-bottom:1px solid #ccd6e1;padding-bottom:22px}h1{font-size:32px;margin:4px 0}h2{font-size:21px;border-left:4px solid #b58c49;padding-left:12px;margin:32px 0 16px}.tag{font-size:13px;letter-spacing:1px;color:#63748a}.sub{font-size:17px;color:#425874}p{margin:10px 0}.table-wrap{overflow-x:auto;margin:12px 0}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}th{background:#edf2f7;color:#254262;text-align:left;font-weight:600}td,th{padding:10px 12px;border-bottom:1px solid #e0e6ed;vertical-align:top}td{white-space:nowrap}section#rationale td,section#constraints td,section#summary td{white-space:normal}section#rationale table{min-width:760px}section#rationale td:first-child,section#rationale th:first-child{min-width:100px;white-space:nowrap}tr:nth-child(even){background:#f8fafc}.note,footer{font-size:12px;color:#64748b}footer{border-top:1px solid #dce3eb;margin-top:32px;padding-top:16px}ul{padding-left:20px}@media(max-width:640px){main{margin:0;padding:20px 16px}h1{font-size:26px}h2{font-size:19px}td,th{padding:8px}}@media print{body{background:#fff}main{margin:0;max-width:none;padding:15px}table{font-size:10px}td,th{white-space:normal;padding:5px}section{break-inside:avoid}}'''
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="icon" href="data:,"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+style+'</style><main><header><div class="tag">组合管理 · '+esc(nature)+'</div><h1>'+esc(title)+'</h1><div class="sub">'+esc(name)+'</div><p>'+esc(status+' ｜ 拟执行日 '+timing['execution_date'])+'</p></header>'+''.join(sections)+'<footer>计价币种：人民币；证券参考价及现金按原币列示。时间统一按北京时间列示。报告据本次计算结果生成。</footer></main></html>'
    def mdtable(headers, rows):
        clean = lambda v: str(v).replace('|', '\\|').replace('\n', ' ')
        return '| '+' | '.join(headers)+' |\n| '+' | '.join('---' for _ in headers)+' |\n'+'\n'.join('| '+' | '.join(clean(v) for v in row)+' |' for row in rows)
    md = f"# {title}\n\n{name} · {nature} · {status}\n\n{summary}\n\n时间统一按北京时间列示。\n\n"
    for heading, headers, rows in [("计划概要",["项目","内容"],overview),("资产配置调整",["资产部分","当前比例","目标比例","预计调整后比例","偏离目标金额（元）"],allocations),("拟交易清单",["资产","代码","方向","预计数量","参考单价","币种","预计原币金额","折合人民币（元）","报价可用时间"],order_rows),("持仓调整明细",["资产部分","资产","代码","当前数量","预计调整后数量","当前权重","目标权重","预计调整后权重"],holdings),("配置依据",["资产部分","选择规则","配权方法"],reasons),("执行约束与待处理事项",["资产部分","代码","事项"],issues),("后续调仓日历",["信号日","拟执行日","事件","资产部分"],calendars)]:
        md += '## '+heading+'\n\n'+(mdtable(headers,rows) if rows else '无相关记录。')+'\n\n'
        if heading == "配置依据":
            md += conditions+'\n\n'+mdtable(audit_headers, audit_rows)+'\n\n'
            if risk_rows:
                md += '### 目标配置风险估计\n\n'+mdtable(risk_headers,risk_rows)+'\n\n'+risk_note+'\n\n'
        if heading == "执行约束与待处理事项":
            if funding:
                md += mdtable(funding_headers, funding)+'\n\n'
            md += mdtable(["币种", "预计剩余现金"], [[cur, fmt(v)] for cur, v in sorted(cash.items())])+'\n\n'
            md += '\n'.join('- '+n for n in p["notes"])+ '\n\n'
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    (out/"报告.html").write_text(document, encoding="utf-8")
    (out/"报告解读.md").write_text(md, encoding="utf-8")
    write_json(out/"report_data.json", p)
    write_json(out/"checks.json", validate_plan(p))
    write_json(out/"ai_context.json", {"title": title, "strategy_name": name, "template": template,
                "evidence_file": "report_data.json", "mode": p["mode"], "timing": timing, "position_policy": "long_only"})
    write_json(out/"manifest.json", {"template_id": template["id"], "files": {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir()) if f.is_file()}})
    return {"html": str((out/"报告.html").resolve()), "checks": validate_plan(p)}
