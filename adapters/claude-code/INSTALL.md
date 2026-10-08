# Claude Code 安装与使用

此包将 `v0.2.0` 的多资产投资研究核心接入 Claude Code，包含统一 Skill、数据、策略、报告及调仓四个模块。

## 下载与安装

从 [GitHub Release](https://github.com/xzjsliran/multi-asset-investment/releases/tag/v0.2.0) 下载 `multi-asset-investment-claude-code-v0.2.0.zip`，完整解压得到 `multi-asset-investment-claude-code` 文件夹。将其放到固定位置，保留 `.claude-plugin` 隐藏目录。

在终端执行，替换为解压文件夹的实际完整路径：

```bash
claude plugin marketplace add "/完整路径/multi-asset-investment-claude-code"
claude plugin install multi-asset-investment@multi-asset-investment-local
```

在研究工作目录启动新的 Claude Code 会话，输入：

```text
/multi-asset-investment:multi-asset-investment
```

也可先临时加载，不登记插件目录。在研究工作目录的终端执行：

```bash
claude --plugin-dir "/完整路径/multi-asset-investment-claude-code"
```

然后使用同一条 Skill 指令。这两种方式任选一种。专用安装包包含完整核心代码；仓库根目录是开发源码布局，不能用 `adapters/claude-code` 子目录代替完整插件安装目录。

## 第一条需求

> 使用多资产投资研究插件。先和我确认研究工作目录，在其中准备 Python 环境。随后确认研究年份、资产范围、配权方法和调仓频率，给出数据计划。Tushare 和 iFinD 是可选项，已有凭证请复用，缺少凭证时先用免费来源。

环境需要 Python 3.11 或 3.12。让 Claude Code 读取包内 `skills/multi-asset-investment/references/claude-code-runtime.md`，按实际路径安装依赖。安装包包含方法、模板和示例，真实行情、账户、研究结果、虚拟环境和密钥由使用者在插件外管理。

后续可以发送：

> 比较固定比例与风险平价资产配置，包含股票宽基、国债、黄金和商品 ETF。先让我确认具体资产和参数，再获取数据、回测并生成策略对比报告。

> 按我的当前策略和实际持仓估算下一次调仓。先核对可卖数量、现金和计划时间，再计算目标持仓与买卖差额。

## 版本与反馈

`bundle-manifest.json` 分别记录核心源码提交、适配源码提交和文件指纹。`SHA256SUMS-claude-code.txt` 用于核对 ZIP 下载完整性，随 Release 单独提供。

遇到问题时反馈 Claude Code 版本、操作系统、使用的指令及去除密钥和账户信息后的报错。可在 [Issues](https://github.com/xzjsliran/multi-asset-investment/issues) 提交；下载与反馈入口均为公开。

适配依据：[插件清单](https://code.claude.com/docs/en/plugins-reference)、[本地插件目录与安装](https://code.claude.com/docs/en/plugin-marketplaces)、[Skill 命名与路径变量](https://code.claude.com/docs/en/skills)。
