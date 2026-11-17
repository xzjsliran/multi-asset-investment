# 资产配置策略与回测

将客户的资产范围、选股、配权和调仓要求转换为可复现的低频策略，保存账户、持仓及决策记录，供报告模块形成投资研究分析。

给智能体的入口是[SKILL.md](SKILL.md)。操作步骤见[使用指南](references/使用指南.md)。

- 自主增减股票、债券、黄金、商品、海外ETF和现金。
- 固定配置及可选的地区宏观、波动率和风险平价调整。
- 手动选股、动量选股、质量估值选股、自定义基本面条件。
- 主动 A 股多因子选股默认使用中证 800，可切换历史股票池。支持倒数波动率、等权、指定比例和最小方差配权。
- 全部资产只做多，证券及资产部分权重非负，现金不透支。
- 周、月、季度调仓可按不同资产部分设置。
- 回测按已确认的佣金、滑点和卖出印花税估算费用，提供等权对照、市场基准、持仓及调仓记录，统一导出 CSV/JSON。

默认采用跨境多资产配置，以下配置文件与同名 `--preset` 参数对应：

| 配置 | 预设标识与文件 | 配置范围 |
|---|---|---|
| 跨境多资产配置 | [`cross-border-allocation`](assets/cross-border-allocation.json) | 美元活期 25%、人民币长债 25%、黄金 10%、A 股宽基 10%、主动 A 股 10%、标普 500 与纳斯达克 100 场外 QDII 各 10% |
| 境内多资产配置 | [`domestic-allocation`](assets/domestic-allocation.json) | 境内股票、国债、黄金与商品 |
| 跨境波动率调整 | [`vix-dynamic-allocation`](assets/vix-dynamic-allocation.json) | 美股 VIX 默认调整标普和纳指 |
| 境内波动率调整 | [`domestic-volatility-allocation`](assets/domestic-volatility-allocation.json) | 上证 50ETF 波指随沪深 300 波指预热完成切换 |
| 中美宏观调整 | [`regional-macro-allocation`](assets/macro-cycle.example.json) | 各地区用本地数据判断四状态，资金可在已配置的大类间流动 |
| 风险平价配置 | [`risk-parity-allocation`](assets/risk-parity-allocation.json) | 将境内基础组合的可选资产部分交给风险平价 |

风险平价使用当前策略实际选用资产的风险代理，可以单独运行或与新版宏观、波动率调整叠加。代理篮子、窗口及缺数处理均可配置。 [风险平价与风险贡献](references/风险平价与风险贡献.md)

本模块在结果数据处完成交接：`handoff.json` 列清文件、单位和计算口径，`curves.csv`、`comparison.json`等供报告模块使用。图表、报告正文和页面由相邻的 [quant-report-kit](../quant-report-kit/README.md) 完成，支持单次分析及多份策略比较；接口详见 [回测结果与报告接口](references/回测结果与报告接口.md)。

市场、汇率和历史财务获取都在相邻的 `quant-data-kit`。本文件夹包含代码、Skill、指南和配置；真实下载数据与运行结果放在使用者工作目录中。

详细内容：[跨境资产配置](references/跨境资产配置说明.md) · [配置与计算](references/配置与计算说明.md)。

当前持仓的后续调仓由相邻[quant-rebalance-kit](../quant-rebalance-kit/README.md)负责。共用决策计算位于`scripts/strategykit/decision.py`，回测成交模拟与实际计划估算各自使用明确时间的报价。
