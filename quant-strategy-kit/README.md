# 资产配置策略与回测

将客户的资产范围、选股、配权和调仓要求转换为可复现的低频策略，保存账户、持仓及决策记录，供报告模块形成投资研究分析。

给智能体的入口是[SKILL.md](SKILL.md)。操作步骤见[使用指南](references/使用指南.md)。

- 自主增减股票、债券、黄金、商品、海外ETF和现金。
- 固定配置、按已公布指标进行条件调整，或大类资产风险平价。
- 手动选股、动量选股、质量估值选股、自定义基本面条件。
- 股票等权、指定比例或最小方差配权。
- 全部资产只做多，证券及资产部分权重非负，现金不透支。
- 周、月、季度调仓可按不同资产部分设置。
- 零成本回测、等权对照、基准、持仓和调仓记录；统一导出CSV/JSON。

默认采用跨境多资产配置，以下配置文件与同名 `--preset` 参数对应：

| 配置 | 预设标识与文件 | 配置范围 |
|---|---|---|
| 跨境多资产配置 | [`cross-border-allocation`](assets/cross-border-allocation.json) | 美元短债、红利、黄金、境内外权益与精选股票 |
| QDII权益配置 | [`qdii-equity-allocation`](assets/qdii-equity-allocation.json) | 海外权益改用境内QDII，美元短债仍使用SHY |
| 境内多资产配置 | [`domestic-allocation`](assets/domestic-allocation.json) | 境内股票、国债、黄金与商品 |
| VIX动态配置 | [`vix-dynamic-allocation`](assets/vix-dynamic-allocation.json) | 按VIX阈值调整大类比例 |
| 风险平价配置 | [`risk-parity-allocation`](assets/risk-parity-allocation.json) | 按股票、债券、黄金与商品的估计风险贡献配权 |

另有[增长与通胀四状态配置](assets/macro-cycle.example.json)，可复制后修改PMI与CPI条件。

风险平价使用信号日前的风险代理协方差，在只做多且不借款的条件下均衡各大类的估计风险。代理篮子、窗口、收缩系数、现金比例和缺数处理均可配置；股票内部仍可使用最小方差。当前宏观配额与风险平价分别作为方案运行，详见[风险平价与风险贡献](references/风险平价与风险贡献.md)。

本模块在结果数据处完成交接：`handoff.json` 列清文件、单位和计算口径，`curves.csv`、`comparison.json`等供报告模块使用。图表、报告正文和页面由相邻的 [quant-report-kit](../quant-report-kit/README.md) 完成，支持单次分析及多份策略比较；接口详见 [回测结果与报告接口](references/回测结果与报告接口.md)。

市场、汇率和历史财务获取都在相邻的 `quant-data-kit`。本文件夹包含代码、Skill、指南和配置；真实下载数据与运行结果放在使用者工作目录中。

详细内容：[跨境资产配置](references/跨境资产配置说明.md) · [配置与计算](references/配置与计算说明.md)。

当前持仓的后续调仓由相邻[quant-rebalance-kit](../quant-rebalance-kit/README.md)负责。共用决策计算位于`scripts/strategykit/decision.py`，回测成交模拟与实际计划估算各自使用明确时间的报价。
