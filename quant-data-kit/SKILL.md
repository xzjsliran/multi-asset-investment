---
name: quant-data-kit
description: 为多资产投资研究按用户指定年份准备 ETF、A 股及中美宏观数据。调用本地 Python 获取、映射代码、整理交易日和缺失值，生成数据检查报告。适用于准备回测数据、改变历史区间、补充宏观变量或复现数据流程；本套装尚不运行策略、预测或下单。
---

# 量化项目：数据接入与清理

默认服务投资研究流程，输出使用专业资产和指标名称。

## 开始

1. 确认本 Skill 的实际文件夹位置和用户的工作目录。使用用户目录保存运行结果，不把结果写进 Skill 安装目录。
2. 操作步骤见[数据获取指南](references/使用指南.md)。新电脑用 `scripts/setup_env.py --venv <工作目录/.venv>` 安装依赖；Python要求3.11+，建议3.11/3.12。后续固定使用该环境的Python。
3. 运行 `scripts/run.py doctor`和`scripts/configure_credentials.py status`，只查看配置状态。首次启动询问用户是否有尚未配置的Tushare/iFinD密钥，也可只用免费来源；现有凭证直接复用。通过本地终端的`scripts/configure_credentials.py tushare`或`ifind`不回显输入，保存在项目外。Tushare继续由本地代码调用，不接Tushare MCP。
4. 用 `scripts/run.py init --out <工作目录/request.json>` 创建配置。根据用户修改日期、标的和宏观指标；代码写成 `510300.SH`，资产类型明确为 `etf` 或 `stock`。预热天数是自然日。月/季/周调仓都使用同一份日线。
5. 运行 `scripts/run.py fetch --config <request.json> --out <新结果目录> --cache <工作目录/cache>`。整个命令使用绝对路径；给命令足够时间，网络请求内部已有超时与少量重试。

路径示意中的 `<...>` 必须替换为真实路径，不能原样传给终端。`auto` 先试免费来源，本地有凭证才补用 Tushare；`free` 完全不使用 Tushare；`tushare` 优先用 Tushare 获取国内行情、日历及中国宏观，其他宏观仍用官方公开来源。

海外敞口可通过境内QDII取得。513100.SH、513500.SH按etf处理，人民币场内价格不重复乘汇率，按境内日历对齐；先核对所需历史覆盖和复权。资料与实测见 [QDII选择说明](references/境内QDII与海外行情选择.md)。接口失败可在同一证券上换来源；改成QQQ/SPY属于改变证券，应由需求和配置明确。

## 如何判断完成

读取 `quality.json`、`manifest.json` 和 `数据检查报告.md`，再向用户说明覆盖和问题。退出码 0 表示数据请求已完成且价格检查通过，但宏观历史版本仍可能需要补充；退出码 2 表示部分数据未齐；退出码 1 表示流程失败。

- `price_research_ready` 判断价格部分是否齐全。即使是 true，也不等于已能判断停牌、涨跌停和实际成交。
- `cross_asset_liquidity_ready` 判断成交额字段是否齐全；新浪备用源成交量保留原始单位，不能直接据此做跨资产成交量排行。
- `macro_signal_coverage`、`macro_asof_coverage` 分别显示可用观测数和覆盖的决策日数。不能只根据下载成功就宣称宏观回测已准备好。
- 国内 PMI/CPI 当前历史表、美国 CPI/失业率/非农、全球 GDP 的下载记录与历史可用版本分别处理。不要手动把 `signal_eligible` 改成 true，也不要用统计期第一天冒充公布日。
- VIX、政策利率及美债日收益率使用代码中写明的跨时区滞后假设；假设不等于已取得精确发布时间。
- 未知缺失保留为空；不得补造上市前价格或把接口漏数都叫停牌。复权价用于研究，原始价用于之后的交易金额计算。

用户只改年份：保存一份新配置后重新运行；同参数可以复用缓存，要重新下载用 `--refresh`。中断后用原配置、新输出目录及同一缓存重跑，成功请求可复用。

## 宏观扩展与资料

用户有iFinD密钥、要求金融资料补充或核对时，阅读[iFinD可选数据源](references/iFinD可选数据源.md)。入口`scripts/ifind.py`支持工具发现和带次数上限的查询，默认每个工作目录最多4次，不自动重试。此通道目前保留原始证据，尚未进入自动行情替换链；特别核对日期、代码、复权、宏观频率和历史版本。不能把`ok=true`当成回测数据合格。

策略模块需要美国ETF、汇率、历史估值或财务选股截面时，阅读 [海外行情与历史财务扩展](references/海外行情与历史财务扩展.md)。新增入口为 `scripts/extend.py`，依赖在 `requirements-extended.txt`；获取方法集中在本数据模块，策略计算留给 `quant-strategy-kit`。

用户的年份、资产和用途已在对话中明确时直接复用；缺项才补问。策略改变频率或指标后，接收它生成的新数据需求，不重新让用户填写一遍问题。

- 查看 [接口与口径](references/数据接口与口径.md) 选择指标，里面包含中国 PMI/CPI、美国政策利率/CPI/长债收益率/就业、VIX 和全球增长。
- 需要核对中国宏观公布时点：在配置中填写 `nbs_urls`，或使用 `nbs_crawl_pages` 小范围发现公告。官网连接不通时，允许用户保存原公告 HTML，并通过 `nbs_saved_pages` 提供 URL 和本地文件；不要猜日期。公告解析 CLI：`scripts/run.py nbs --url <官网URL> --out <检查结果.json>`，也可加 `--html <另存的原公告.html>`。
- 需要美国历史版本：`scripts/run.py fred-vintage --series PAYEMS --start YYYY-MM-DD --end YYYY-MM-DD --asof YYYY-MM-DD --out <文件.csv>`。这个额外方法需要用户自己的免费 `FRED_API_KEY`。
- 用户问市场加息预期：阅读 [美国宏观与利率预期](references/美国宏观与利率预期.md)。官方 FedWatch 下载表用 `scripts/run.py expectations --input <整理后的CSV> --out <新目录>`。实际政策利率、长债收益率、市场预期是不同变量。
- 上游 AKShare/Tushare Skills 的来源和下载办法见 [安装来源](references/安装来源.md)。它们可作为接口查询参考，本 Skill 的程序不依赖另一个 Skill 才能运行。

向用户解释时先说拿到了什么、还缺什么、可以继续做什么，然后给报告路径。不要把接口名和报错堆成结论。
