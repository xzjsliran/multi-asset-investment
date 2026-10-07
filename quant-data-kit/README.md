# 量化项目第一部分：数据接入套装

负责将投资研究的年份、资产和指标需求转换为可复核的数据。通过本地代码获取市场、财务及宏观数据，统一代码、日历、字段和信息可用时点。

给智能体的入口是[SKILL.md](SKILL.md)。学生开发过程另见[WorkBuddy开发指南](references/WorkBuddy操作指南.md)。

包内有获取方法、清理代码、配置示例和 Skill；没有真实行情、下载缓存、个人 token 或 Python 环境。用户运行时才下载数据并生成结果。

## 已包含什么

- 境内ETF（包含QDII）和A股日线：原始价格、复权研究序列、成交量/成交额及来源说明。513100/513500用同一境内ETF通道，见 [QDII选择说明](references/境内QDII与海外行情选择.md)。
- 代码映射、国内交易日历、周/月/季末和下一交易日。
- 缺失、重复、价格异常和不同来源的覆盖检查。
- 中国 PMI、CPI；美国政策利率、实际联邦基金利率、CPI、CPI 同比、10 年/30 年美债收益率、失业率、非农总就业及新增就业；VIX、全球年度 GDP 增长。
- 统计局原公告解析、可选的 FRED 历史版本获取、CME FedWatch 下载表导入。
- 可直接打开的 HTML 数据检查报告，以及 CSV 和 JSON 输出。

2026年10月7日增加美国ETF及USD/CNY行情、历史估值/财务/行业/名称截面、FRED历史快照到宏观事件的转换。方法仍归本数据模块，见 [海外行情与历史财务扩展](references/海外行情与历史财务扩展.md)；策略及回测位于相邻的 `quant-strategy-kit`。新增接口的实测情况见策略模块验证记录。

默认先使用免费来源，确有缺口且本机配置了凭证时才使用 Tushare。Tushare 由本地代码直接请求官方接口，不使用 MCP。

iFinD为可选补充来源，提供MCP工具发现、原始响应保存、缓存与调用预算。已用4次数据查询验证基金、财务和美国宏观接口；本次发现日期解析及字段口径问题，原始响应暂不自动进入回测。配置方法与具体发现见[iFinD可选数据源](references/iFinD可选数据源.md)。统一插件启动时询问可用凭证，Tushare/iFinD密钥只保存在用户私有目录，也支持环境变量。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `SKILL.md` | 给智能体看的调用说明 |
| `references/WorkBuddy操作指南.md` | 学生从理解需求到开发的步骤与指令 |
| `references/教师成品复核命令.md` | 老师重跑参考实现的指令 |
| `references/安装来源.md` | 上游 Skills 的 GitHub 地址和安装方法 |
| `references/数据接口与口径.md` | 数据来源、字段、清理和时间处理 |
| `references/美国宏观与利率预期.md` | 美国指标、历史版本和加息预期的具体办法 |
| `references/验证记录.md` | 此次实际跑通的部分与未完成的连接验证 |
| `assets/request.example.json` | 六类候选 ETF 与中美宏观请求示例，可改年份 |
| `scripts/run.py` | 数据入口 |
| `scripts/setup_env.py` | 创建独立环境并安装依赖 |
| `scripts/download_skills.py` | 获取上游 Skill，制作本地导入包 |
| `tests/test_data_rules.py` | 用人工小样本检验数据处理规则 |

## 运行方式

在这个文件所在目录执行。下面以 macOS 为例，Windows 的环境 Python 位于 `.venv\Scripts\python.exe`。

```bash
python3 scripts/setup_env.py --venv .venv
.venv/bin/python scripts/run.py doctor
.venv/bin/python scripts/run.py init --out request.json
.venv/bin/python scripts/run.py fetch --config request.json --out runs/first --cache runs/cache
```

`python3` 必须是 Python 3.11 或更高版本；首次建议使用 3.11/3.12。没有配置 token 也能试免费来源；`auto` 不会把“免费源缺少复权”当成“已经完整”。

这一步不需要 vn.py。AKShare、Tushare 负责取数，pandas/NumPy 负责整理，requests/Beautiful Soup 处理公开下载和公告。之后做回测时再选择计算方式。

## 参考实现

参考了 [Balanced Portfolio](https://github.com/hxlog/balanced-portfolio) 的数据适配和复权口径说明，本地参考版本为 `818c0966b65bb182f4b29b60bd8d02c7a32610bd`。保留了统一字段、检查备用来源、保留来源信息的做法；本包独立实现，不需要它的数据库、Redis、网页后端或前端。

本包按完整区间选择同一个行情来源，不拼接不同来源的复权绝对值。宏观按可用时点另行处理。各次运行保留来源与检查记录，供后续研究复核。
