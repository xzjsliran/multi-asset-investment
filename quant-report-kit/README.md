# 投资研究结果分析与报告

面向商业客户，将已有回测结果生成专业投资研究报告。支持单策略、多策略版本、动态与固定权重、组合内等权与最小方差、QDII与海外ETF权益方案比较，策略数量和年份均由输入决定。

全部资产只做多，程序检查已提供的配置、持仓及目标是否非负；仅净值输入注明无法验证持仓方向。调仓结果使用`rebalance --plan <调仓数据目录> --out <新报告目录>`生成《组合调仓计划》，遵循[调仓报告模板](assets/rebalance-report-template.json)，计算由相邻的[调仓kit](../quant-rebalance-kit/README.md)完成。

[报告规范](references/投资回测报告规范.md)规定表达逻辑；[正文模板](templates/投资回测报告模板.md)供写作参考；[SKILL.md](SKILL.md)和[结构化模板](assets/investment-report-template.json)供其他agent调用。

## 内容与交付

报告包括研究摘要、策略与回测设定、绩效风险、策略差异、资产配置与贡献、研究局限和指标附录。风险贡献从实际逐日盈亏重新计算，支持共同区间比较、负风险贡献及分部分相关性；与风险平价的目标配置估计分别展示。图表名称采用投资研究用语。客户名称与原结果目录分开管理，既有历史输入可直接接入。

生成单个离线HTML、Markdown正文、CSV指标表，以及供agent读取的事实和复核JSON。Python负责计算，宿主AI据此补充研究判断。模块不依赖额外模型API、MCP或网页开发环境。

## 运行

复用前两部分的Python环境，或安装本目录requirements.txt。

```bash
"研究目录/.venv/bin/python" quant-report-kit/scripts/run.py single \
  --result "研究目录/results/dynamic" \
  --name "VIX动态资产配置策略" --title "动态配置策略回测分析" \
  --out "研究目录/reports/dynamic"
```

多策略使用[配置样例](examples/compare.json)，修改结果目录后执行：

```bash
"研究目录/.venv/bin/python" quant-report-kit/scripts/run.py build \
  --config "研究目录/compare.json" --out "研究目录/报告版本1"
"研究目录/.venv/bin/python" quant-report-kit/scripts/run.py check \
  --report-dir "研究目录/报告版本1"
```

详细步骤见[使用指南](references/使用指南.md)。当前报告结构为2.1版，包含实际风险贡献及组合调仓的目标风险估计，继续检查全部资产只做多。

## 模块衔接

数据模块提供市场和宏观输入，策略模块保存规则、账户、持仓和决策，报告模块重算所选区间并形成研究结论。需要修改投资规则时，先保存新配置并重新回测。报告中的期末持仓带有历史日期。

套装仅包含代码、空模板、Schema、Skill和说明；真实市场数据、客户结果及凭证由使用者本地保存。

[接口与计算口径](references/接口与计算口径.md) · [监管依据与适用范围](references/监管依据与适用范围.md)
