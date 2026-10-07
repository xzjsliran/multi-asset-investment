# 量化项目第二部分：策略与回测

将客户的资产范围、选股、配权和调仓要求转换为可复现的低频策略，保存账户、持仓及决策记录，供报告模块形成投资研究分析。

给智能体的入口是[SKILL.md](SKILL.md)。学生开发过程另见[WorkBuddy开发指南](references/WorkBuddy操作指南.md)。

- 自主增减股票、债券、黄金、商品、海外ETF和现金。
- 固定配置、按已公布指标进行条件调整，或大类资产风险平价。
- 手动选股、动量选股、原示例质量估值选股、自定义基本面条件。
- 股票等权、指定比例或最小方差配权。
- 全部资产只做多，证券及资产部分权重非负，现金不透支。
- 周、月、季度调仓可按不同资产部分设置。
- 零成本回测、等权对照、基准、持仓和调仓记录；统一导出CSV/JSON。

默认 `friend-demo` 来自 `research/friend_portfolio_2015_2026/research_protocol.json`，保留原大类配置与选股逻辑，改用可配置的股票配权。`friend-qdii-demo` 把其中QQQ/SPY换成境内513100/513500，SHY保持美元短债；另有 `classroom-demo`、`macro-demo` 和 `risk-parity-demo`。

风险平价使用信号日前的风险代理协方差，在只做多且不借款的条件下均衡各大类的估计风险。代理篮子、窗口、收缩系数、现金比例和缺数处理均可配置；股票内部仍可使用最小方差。当前宏观配额与风险平价分别作为方案运行，详见[风险平价与风险贡献](references/风险平价与风险贡献.md)。

本模块在结果数据处完成交接：`handoff.json` 列清文件、单位和计算口径，`curves.csv`、`comparison.json`等供报告组使用。图表、报告正文和页面由相邻的 [quant-report-kit](../quant-report-kit/README.md) 完成，支持单次分析及多份策略比较；接口详见 [交给报告组的数据说明](references/交给报告组的数据说明.md)。历史报告保留在原输出目录；新回测由报告模块统一呈现。

市场、汇率和历史财务获取都在相邻的 `quant-data-kit`。本文件夹包含代码、Skill、指南、配置和人工测试样例；真实下载数据与运行结果放在使用者工作目录中。本轮不生成ZIP。

详细内容：[默认示例](references/默认示例说明.md) · [配置与计算](references/配置与计算说明.md) · [验证记录](references/验证记录.md)。

当前持仓的后续调仓由相邻[quant-rebalance-kit](../quant-rebalance-kit/README.md)负责。共用决策计算位于`scripts/strategykit/decision.py`，回测成交模拟与实际计划估算各自使用明确时间的报价。
