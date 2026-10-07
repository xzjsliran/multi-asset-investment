import React, { useState } from "react";
import { DataComponent, EvidenceChart, ReportSection, RichNarrative, useDataApp } from "../../data-app-public.jsx";

const percent = (v) => v == null ? "—" : `${(v * 100).toFixed(2)}%`;
const dateText = v => v == null ? "—" : String(v).slice(0, 10);
const dateTime = v => {
  if (!v) return "未提供";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  return new Intl.DateTimeFormat("zh-CN", { timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false }).format(d) + "（北京时间）";
};
const seriesLabels = { vix:"VIX", cn_pmi:"中国制造业PMI", cn_cpi_yoy:"中国CPI同比", fed_upper:"联邦基金目标利率上限", fed_effective:"有效联邦基金利率", us_cpi:"美国CPI", us_cpi_yoy:"美国CPI同比", us_10y:"美国10年期国债收益率", us_30y:"美国30年期国债收益率", us_unemployment:"美国失业率", us_nonfarm:"美国非农就业人数", us_nonfarm_change:"美国非农新增就业", world_gdp_growth:"全球GDP增速" };
const number = (v) => v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const text = (v) => v == null ? "未设置" : typeof v === "object" ? JSON.stringify(v) : typeof v === "boolean" ? (v ? "启用" : "关闭") : String(v);
const PALETTE = ["#2563a6", "#d18b25", "#168273", "#8256a7", "#c46170", "#5f7684", "#95634a", "#649849"];
const metricColumns = [["strategy", "策略"], ["total_return", "累计收益", percent], ["annual_return", "年化收益", percent],
  ["annual_volatility", "年化波动", percent], ["max_drawdown", "最大回撤", percent], ["sharpe_zero_rf", "夏普比率（无风险利率0）", v => v == null ? "—" : v.toFixed(2)]];

function Table({ rows, columns, limit = 20 }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(rows.length / limit));
  const current = Math.min(page, pages - 1);
  if (!rows.length) return <p className="qr-empty">未提供该项独立明细。</p>;
  return <div className="qr-table-group" data-reviewed-rows>
    <div className="qr-table-scroll"><table className="qr-table"><thead><tr>{columns.map(([key, name, fmt]) => <th key={key} className={fmt ? "qr-number" : ""}>{name}</th>)}</tr></thead>
      <tbody>{rows.slice(current * limit, (current + 1) * limit).map((row, i) => <tr key={i}>{columns.map(([key, , fmt]) => <td key={key} className={fmt ? "qr-number" : ""}>{fmt ? fmt(row[key]) : text(row[key])}</td>)}</tr>)}</tbody></table></div>
    {pages > 1 && <div className="qr-pager"><button onClick={() => setPage(Math.max(0, current - 1))} disabled={!current}>上一页</button><span>第 {current + 1} / {pages} 页 · 共 {rows.length} 行</span><button onClick={() => setPage(Math.min(pages - 1, current + 1))} disabled={current === pages - 1}>下一页</button></div>}
  </div>;
}

function EvidenceTable({ id, title, queryId, rows, columns, limit = 20, description }) {
  const { visible } = useDataApp();
  return visible(id) && <DataComponent id={id} kind="table" title={title} queryId={queryId} displayRows={rows} sourceRows={rows} description={description}>
    <Table rows={rows} columns={columns} limit={limit} />
  </DataComponent>;
}

function StrategyDetail({ strategy, report }) {
  const { visible } = useDataApp();
  const s = strategy, d = s.detail, id = s.id;
  const q = (k) => `${id}_${k}`;
  const contrib = d.contributions.map(r => ({ ...r, "贡献(百分点)": r.contribution * 100 }));
  const risk = d.risk || {rows:[],correlations:[],reason:"未提供风险分解数据。"};
  const riskBars = risk.rows.map(r => ({...r,"波动贡献(百分点)":r.volatility_contribution*100}));
  const weights = d.weight_history.map(r => ({ ...r, date: r.date.slice(0, 10), "持仓(%)": r.weight * 100 }));
  const sleeveColors = Object.fromEntries([...new Set([...weights.map(x => x.name), ...contrib.map(x => x.name)])].map((name,i) => [name,PALETTE[i % PALETTE.length]]));
  const facts = report.facts.filter(f => f.strategies.length === 1 && f.strategies[0] === id);
  const recovery = s.metrics.recovery_date ? `于${s.metrics.recovery_date}恢复前期高点` : "截至区间末尚未恢复前期高点";
  const drawdownText = s.metrics.max_drawdown < 0 ? `最大回撤${percent(s.metrics.max_drawdown)}；前期高点日期${s.metrics.drawdown_peak}，谷底日期${s.metrics.drawdown_trough}，${recovery}。` : "区间内没有出现净值回撤。";
  const regimeRows = d.regimes.map(r => ({ ...r,
    observation: r.method === "risk_parity" ? (r.status === "success" ? `代理收益样本 ${r.sample_start} 至 ${r.sample_end}，${r.observations}条；估计年化波动 ${percent(r.annual_volatility)}` : r.reason) : (r.inputs || []).map(x => `${seriesLabels[x.series] || x.series} ${number(x.value)}；观测日期 ${dateText(x.period)}；信息可用时点 ${dateTime(x.available_at)}`).join(" / "),
    allocation: Object.entries(r.weights || {}).map(([k,v]) => `${s.config.sleeves?.find(z => z.id === k)?.name || k} ${percent(v)}`).join(" · ") }));
  return <div className="qr-detail" key={id}>
    <ReportSection id={`${id}-readout`} queryId="facts" sourceRows={facts} title={`${s.label}：回撤特征`} showHeading={false}>
      <RichNarrative id={`${id}:readout`} value={`### ${s.label}\n\n${drawdownText}`} />
    </ReportSection>
    {contrib.length > 0 && visible(`${id}-contribution`) && <EvidenceChart id={`${id}-contribution`} queryId={q("contributions")} title={report.template.headings.contribution} description="单位为百分点；各部分相加等于共同区间累计收益。"
      spec={{ type: "horizontalBar", x: "name", y: "贡献(百分点)", valueDecimals: 2, stackable: false, colors:sleeveColors }} rows={contrib} sourceRows={d.contributions} height={Math.max(300, contrib.length * 42)}>
      <Table rows={d.contributions} columns={[["name","资产组合"],["contribution","收益贡献 · 百分点",v=>(v*100).toFixed(2)],["pnl",`区间盈亏 · ${report.period.currency}`,number]]} />
    </EvidenceChart>}
    {riskBars.length > 0 && visible(`${id}-risk`) && <EvidenceChart id={`${id}-risk`} queryId={q("risk")} title="资产组合风险贡献" description={risk.note}
      spec={{type:"horizontalBar",x:"name",y:"波动贡献(百分点)",valueDecimals:2,stackable:false,colors:sleeveColors}} rows={riskBars} sourceRows={risk.rows} height={Math.max(280,riskBars.length*42)}>
      <Table rows={risk.rows} columns={[["name","资产组合"],["volatility_contribution","年化波动贡献 · 百分点",v=>(v*100).toFixed(2)],["risk_share","风险贡献占比",percent]]} />
    </EvidenceChart>}
    {risk.rows.length > 0 ? <details className="qr-disclosure"><summary>风险分解方法与相关性</summary>
      <p className="qr-muted">{risk.definition}</p>
      <EvidenceTable id={`${id}-risk-correlations`} title="资产部分收益贡献相关性" queryId={q("risk_correlations")} rows={risk.correlations} description={risk.correlation_definition}
        columns={[["left_name","资产部分"],["right_name","对照部分"],["correlation","相关系数",v=>v == null ? "—" : v.toFixed(3)]]} />
    </details> : <p className="qr-muted">风险贡献分析：{risk.reason}</p>}
    {weights.length > 0 && visible(`${id}-weights`) && <EvidenceChart id={`${id}-weights`} queryId={q("weight_history")} title={report.template.headings.weights} description="每日收盘调仓后的大类权重；包括行情涨跌造成的自然漂移。"
      spec={{ type: "line", x: "date", y: "持仓(%)", series: "name", colors:sleeveColors, valueDecimals: 2 }} rows={weights} sourceRows={d.weight_history} height={330} />}
    <EvidenceTable key={`${id}-holdings`} id={`${id}-holdings`} title={`${report.template.headings.holdings}（${report.period.end}）`} queryId={q("holdings")} rows={d.holdings}
      columns={[["sleeve_name", "资产组合"], ["code", "代码"], ["name", "名称"], ["weight", "权重", percent], ["value", `金额 · ${report.period.currency}`, number]]} />
    {d.regimes.length > 0 && <EvidenceTable key={`${id}-regimes`} id={`${id}-regimes`} title={s.config.macro?.enabled ? "宏观条件与配置决策" : "大类配置记录"} queryId={q("regimes")} rows={regimeRows} limit={12}
      description={s.config.allocation?.method === "risk_parity" ? "风险代理在信号日前估计协方差；目标风险均衡与本区间实际风险贡献分别列示。" : s.config.macro?.enabled ? "信号日使用当时可获得的指标；执行日期与目标权重来自实际决策记录。" : "按照固定基础比例调仓；本策略未启用宏观条件。"}
      columns={[["signal_date", "信号日", dateText], ["execution_date", "执行日", dateText], ["state", "配置状态"], ["observation", "指标记录"], ["allocation", "大类目标权重"]]} />}
    <details className="qr-disclosure"><summary>策略规则明细</summary>
      <EvidenceTable id={`${id}-rules`} title="策略参数" queryId={q("rules")} rows={d.rules} limit={50} columns={[["parameter", "设置"], ["value", "内容"]]} />
    </details>
    <details className="qr-disclosure"><summary>成交与组合构建记录</summary>
      <EvidenceTable id={`${id}-trades`} title="成交记录" queryId={q("trades")} rows={d.trades} columns={[["date", "执行日", dateText], ["signal_date", "信号日", dateText], ["sleeve_name", "资产组合"], ["code", "代码"], ["side_label", "方向"], ["amount", "买卖金额", number]]} />
      <EvidenceTable id={`${id}-selections`} title="选股与配权" queryId={q("selections")} rows={d.selections} columns={[["signal_date", "信号日", dateText], ["execution_date", "执行日", dateText], ["code", "代码"], ["name", "名称"], ["within_weight", "组合内权重", percent]]} />
      <EvidenceTable id={`${id}-optimization`} title="配权诊断" queryId={q("optimization")} rows={d.optimization} columns={[["signal_date", "信号日", dateText], ["method_label", "方法"], ["status_label", "状态"], ["observations", "样本数", number], ["sample_start", "样本起点", dateText], ["sample_end", "样本终点", dateText], ["notes", "说明"]]} />
    </details>
    <details className="qr-disclosure"><summary>数据质量与运行诊断</summary>
      <EvidenceTable id={`${id}-diagnostics`} title="原始完整区间诊断" queryId={q("diagnostics")} rows={d.diagnostics} columns={[["item", "检查项"], ["count", "数量", number], ["scope", "范围"]]} />
      <RichNarrative id={`${id}:notes`} value={[...d.source_notes, ...d.notices].map(x => `- ${x}`).join("\n") || "无补充说明。"} />
    </details>
    {d.notices.length > 0 && <RichNarrative id={`${id}:available-detail`} className="qr-muted" value={d.notices.join("\n\n")} />}
  </div>;
}

function NarrativeBlocks({ report, section }) {
  return (report.commentary || []).filter(b => b.section === section).map((b,i) =>
    <ReportSection key={`${section}-${i}`} id={`qr-${section}-commentary-${i}`} title={b.title} queryId="facts" sourceRows={report.facts.filter(f => b.evidence_ids.includes(f.id))}>
      <RichNarrative id={`qr:${section}-commentary-${i}`} value={b.text} />
    </ReportSection>);
}

export function ReportContent() {
  const { snapshot, appTitle, setAppTitle, canEdit, mode, visible } = useDataApp();
  const r = snapshot.quantReport;
  const [selected, setSelected] = useState(r?.strategies?.[0]?.id);
  const [pairIndex, setPairIndex] = useState(0);
  if (!r?.strategies?.length) return <article className="report-content"><p>请选择回测结果生成报告。</p></article>;
  const h = r.template.headings, meta = r.report;
  const active = r.strategies.find(s => s.id === selected) || r.strategies[0];
  const colors = Object.fromEntries(r.strategies.map((s,i) => [s.label, PALETTE[i % PALETTE.length]]));
  const metrics = r.strategies.map(s => ({ strategy:s.label, ...s.metrics }));
  const original = r.strategies.map(s => ({ strategy:s.label, ...s.original_metrics }));
  const ddRows = r.curves.map(x => ({ ...x, "回撤(%)": x.drawdown * 100 }));
  const annual = r.annual.map(x => ({ ...x, "收益(%)": x.return * 100 }));
  const monthly = [...new Set(r.monthly.map(x => x.period))].map(period => ({ period, ...Object.fromEntries(r.strategies.map(s => [s.id,r.monthly.find(x => x.period === period && x.strategy_id === s.id)?.return ?? null])) }));
  const pair = r.pairs[pairIndex];
  const pairLabel = p => `${r.strategies.find(s => s.id === p.left).label} / ${r.strategies.find(s => s.id === p.right).label}`;
  const pairRows = pair ? pair.display_differences.map(x => ({ left_strategy:pair.left, right_strategy:pair.right, ...x })) : [];
  const deltaRows = pair ? [{ metric:"累计收益差", value:pair.deltas.total_return }, { metric:"年化波动率差", value:pair.deltas.annual_volatility }, { metric:"最大回撤差", value:pair.deltas.max_drawdown }] : [];
  return <article className="report-content qr-report" aria-label="投资策略回测研究报告">
    <header className="report-hero">
      <div className="qr-eyebrow">{meta.nature}</div>
      <h1 data-data-app-title contentEditable={canEdit && mode === "edit"} suppressContentEditableWarning
        onBlur={canEdit && mode === "edit" ? e => setAppTitle(e.currentTarget.textContent.trim() || appTitle) : undefined}>{appTitle}</h1>
      <div className="qr-meta" data-reviewed-rows><span>回测区间 {r.period.start} — {r.period.end}</span><span>计价币种 {r.period.currency}</span></div>
      <div className="qr-meta qr-document-meta" data-reviewed-rows><span>报告日期 {meta.report_date}</span><span>版本 {meta.version}</span>
        {[["report_id","报告编号"],["issuer","出具机构"],["author","研究人员"],["reviewer","复核人员"],["distribution","分发范围"]].filter(([k]) => meta[k]).map(([k,label]) => <span key={k}>{label} {meta[k]}</span>)}
      </div>
    </header>
    <ReportSection id="qr-summary" title={h.summary} queryId="facts" sourceRows={r.facts.filter(f => r.summary.some(b => b.evidence_ids.includes(f.id)))}>
      <RichNarrative id="qr:summary" className="qr-summary" value={r.summary.map(b => `${b.title ? `### ${b.title}\n\n` : ""}${b.text}`).join("\n\n")} />
    </ReportSection>
    <section className="qr-block" aria-label={h.mandate}>
      <h2 className="qr-section-title">{h.mandate}</h2>
      <EvidenceTable id="qr-overview" title={h.overview} queryId="overview" rows={r.overview} columns={[["strategy","策略"],["allocation","基础配置"],["rebalance","大类调仓"],["benchmark","参照基准"]]} limit={50} />
      <details className="qr-disclosure"><summary>{h.assumptions}</summary>
        <EvidenceTable id="qr-assumptions" title={h.assumptions} queryId="assumptions" rows={r.assumptions} columns={[["item","项目"],["value","设定"]]} limit={50} />
        <ReportSection id="qr-execution-notes" title="数据与执行规则" queryId="execution_notes" sourceRows={r.execution_notes.map(note=>({note}))}>
          <RichNarrative id="qr:execution-notes" value={r.execution_notes.join("\n\n") || "输入仅提供净值；执行规则及历史数据版本未提供。"} />
        </ReportSection>
      </details>
    </section>
    <section className="qr-block" aria-label={h.performance}>
      <h2 className="qr-section-title">{h.performance}</h2>
      <EvidenceTable id="qr-metrics" title={h.metrics} queryId="metrics" rows={metrics} columns={metricColumns} limit={50} description="历史模拟结果。交易成本口径见回测设定；未计成本的结果不代表实际可实现收益。" />
      {visible("qr-nav") && <EvidenceChart id="qr-nav" queryId="curves" title={h.nav} description="净值指数：共同起始日收盘值为1。"
        spec={{ type:"line", x:"date", y:"nav", series:"strategy", colors, stackable:false, startAtZero:false, valueDecimals:4 }} rows={r.curves} sourceRows={r.curves} height={365} />}
      {visible("qr-drawdown") && <EvidenceChart id="qr-drawdown" queryId="curves" title={h.drawdown} description="相对所选区间内此前最高净值的跌幅。"
        spec={{ type:"line", x:"date", y:"回撤(%)", series:"strategy", colors, stackable:false, valueDecimals:2 }} rows={ddRows} sourceRows={r.curves} height={310} />}
      {visible("qr-annual") && <EvidenceChart id="qr-annual" queryId="annual" title={h.annual} description="按共同区间统计；首末年度可能不完整。"
        spec={{ type:"bar", x:"period", y:"收益(%)", series:"strategy", colors, stackable:false, valueDecimals:2 }} rows={annual} sourceRows={r.annual} height={300} />}
      <DataComponent id="qr-monthly" title={h.monthly} kind="table" queryId="monthly" displayRows={monthly} sourceRows={r.monthly} description="按共同区间内日收益复合计算；首末月份可能不完整。">
        <Table rows={monthly} limit={12} columns={[["period", "月份"], ...r.strategies.map(s => [s.id,s.label,percent])]} />
      </DataComponent>
    </section>
    {pair && <section className="qr-rules-section qr-block" aria-label={h.comparison}>
      <h2 className="qr-section-title">{h.comparison}</h2>
      <NarrativeBlocks report={r} section="comparison" />
      <div className="qr-section-heading"><label className="qr-control">对照组合<select aria-label="对照组合" value={pairIndex} onChange={e => setPairIndex(Number(e.target.value))}>{r.pairs.map((p,i) => <option key={i} value={i}>{pairLabel(p)}</option>)}</select></label></div>
      <ReportSection id={`qr-comparison-${pair.left}-${pair.right}`} title={pair.comparison_type} queryId="facts" sourceRows={r.facts.filter(f => f.strategies.length===2 && f.strategies.includes(pair.left) && f.strategies.includes(pair.right))}>
        <RichNarrative id={`qr:comparison-${pair.left}-${pair.right}`} value={pair.interpretation} />
      </ReportSection>
      <EvidenceTable id={`qr-delta-${pair.left}-${pair.right}`} title="绩效差异" queryId="pair_deltas" rows={deltaRows.map(row=>({...row,left:pair.left,right:pair.right}))} description="后者减前者，单位为百分点；最大回撤差为正表示后者回撤较浅。" columns={[["metric","指标"],["value","差值（百分点）",v=>`${v>0?"+":""}${(v*100).toFixed(2)}`]]} />
      {pairRows.length > 0 && <details className="qr-disclosure"><summary>{h.differences}</summary><EvidenceTable key={`${pair.left}-${pair.right}`} id={`qr-rules-${pair.left}-${pair.right}`} title={h.differences} queryId="display_differences" rows={pairRows} limit={12} columns={[["parameter", "配置项"], ["left", r.strategies.find(s=>s.id===pair.left).label], ["right", r.strategies.find(s=>s.id===pair.right).label]]} /></details>}
    </section>}
    <section className="qr-strategy-section qr-block" aria-label={h.portfolio}>
      <div className="qr-section-heading"><h2 className="qr-section-title">{h.portfolio}</h2><label className="qr-control">策略选择<select aria-label="策略选择" value={active.id} onChange={e => setSelected(e.target.value)}>{r.strategies.map(s => <option value={s.id} key={s.id}>{s.label}</option>)}</select></label></div>
      <NarrativeBlocks report={r} section="portfolio" />
      <StrategyDetail key={active.id} strategy={active} report={r} />
    </section>
    <ReportSection id="qr-disclosures" title={h.disclosures} queryId="disclosures" sourceRows={r.disclosures}>
      <RichNarrative id="qr:disclosures" value={r.disclosures.map(d=>`### ${d.title}\n\n${d.text}`).join("\n\n")+ (r.scope_notes.length ? "\n\n"+r.scope_notes.join("\n\n") : "")} />
    </ReportSection>
    <details className="qr-disclosure"><summary>{h.appendix}</summary>
      <EvidenceTable id="qr-original" title={h.original} queryId="original_metrics" rows={original} limit={50} columns={[["strategy", "策略"], ["start", "起始日", dateText], ["end", "结束日", dateText], ...metricColumns.slice(1)]} />
      <ReportSection id="qr-methods" title="指标定义与资料来源" queryId="metrics" sourceRows={metrics}>
        <RichNarrative id="qr:methods" value={`累计收益为期末与期初净值之比减1。年化收益按实际日历天数折算；年化波动率采用日收益样本标准差乘以√252。夏普比率采用零无风险利率，以日收益均值×252除以年化波动率。首日收益记0。\n\n回撤相对于所选区间内此前最高净值计算。月度及年度收益按当期日收益复合计算。资产组合收益贡献为区间货币盈亏除以期初权益，合计等于累计收益；该指标不构成经济因果归因。\n\n资料来源：所列策略的日度净值、配置参数及已提供的持仓和决策记录。各图表附来源标识；完整输入指纹保存在随附复核资料中。`} />
      </ReportSection>
    </details>
  </article>;
}
