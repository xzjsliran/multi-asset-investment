# 多资产投资研究智能体

本项目面向商业客户，提供数据接入、资产配置研究、策略回测、结果分析及专业报告。通过可调用的代码、Skill和模板接入WorkBuddy、DeepSeek Harness等宿主智能体。

目录用途与常用入口见[项目导航](docs/项目导航.md)。本地方案及申报文件集中在 `资料/`；历史运行结果集中在 `output/验证与测试/`；正式安装包在 `output/releases/`。这些本地资料和结果不随源码上传。

| 模块 | 工作内容 | 入口 |
|---|---|---|
| 数据接入与处理 | 市场、财务和宏观数据，代码与日历统一，历史信息时点及质量检查 | [quant-data-kit](quant-data-kit/README.md) |
| 策略与回测 | 自选资产、固定/宏观/风险平价配置、选股配权及低频回测，输出标准记录 | [quant-strategy-kit](quant-strategy-kit/README.md) |
| 结果分析与报告 | 绩效与风险、策略对照、收益与风险贡献、客户研究报告 | [quant-report-kit](quant-report-kit/README.md) |
| 组合管理与调仓计划 | 确认策略与实际持仓，判断调仓日期，计算目标配置、买卖差额和现金约束 | [quant-rebalance-kit](quant-rebalance-kit/README.md) |

统一入口见[多资产投资研究插件](multi-asset-investment/README.md)。首次启动确认研究需求及可选的Tushare/iFinD凭证；iFinD采用少量查询、缓存与调用预算。全部资产只做多，持仓与目标权重非负，现金不透支。历史回测与当前调仓共用策略决策逻辑。

## 安装与运行

从 [Releases](https://github.com/xzjsliran/multi-asset-investment/releases) 选择对应平台的 ZIP，完整解压后按包内 `INSTALL.md` 安装。GitHub 自动附带的 Source code 是源码快照。

| 平台 | 安装包 | 安装说明 |
|---|---|---|
| Codex | `multi-asset-investment-codex-v*.zip` | [安装说明](adapters/codex/INSTALL.md) |
| Claude Code | `multi-asset-investment-claude-code-v*.zip` | [安装说明](adapters/claude-code/INSTALL.md) |
| WorkBuddy | `multi-asset-investment-workbuddy-v*.zip` | [安装说明](adapters/workbuddy/INSTALL.md) |
| DeepSeek Harness | `multi-asset-investment-dsh-v*.zip` | [安装说明](adapters/dsh/INSTALL.md) |

开发环境使用 Python 3.11 或 3.12，在仓库根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python multi-asset-investment/scripts/run.py startup
```

Windows 使用 `py -3.11 -m venv .venv` 和 `.venv\Scripts\Activate.ps1`。直接海外证券或研究快照导入另装 `quant-data-kit/requirements-extended.txt`。启动检查不联网、不消耗数据额度，也不会输出密钥。

可以先让 Agent 读取 `multi-asset-investment/SKILL.md`，确认年份、资产、配权和调仓频率，再生成配置和数据计划。市场数据、账户及报告保存在使用者工作目录。仓库未包含真实数据、密钥、虚拟环境或历史报告。

## 开发、适配与发布

四个 kit 和统一入口各保留一份源码；平台差异放在 `adapters/`，安装包分别由 `scripts/build_codex_release.py`、`scripts/build_claude_code_release.py`、`scripts/build_workbuddy_release.py` 和 `scripts/build_dsh_release.py` 生成。当前提供 Codex、Claude Code、WorkBuddy 和 DeepSeek Harness（DSH）适配。

版本号在 `VERSION`。源码、适配说明和构建脚本进入 Git；生成目录与 ZIP 放在本地 `output/`，上传至对应版本的 GitHub Release。维护步骤见[项目维护与发布](docs/项目维护与发布.md)，平台安装方法见上表。四个平台的当前发行版本为 `0.3.0`，使用同一核心源码提交，安装包清单记录对应提交。

报告使用[投资回测报告规范](quant-report-kit/references/投资回测报告规范.md)；跨模块要求见[AGENTS.md](AGENTS.md)。各模块 references 仅包含投资研究所需说明，客户报告和产品界面采用投资研究表达。
