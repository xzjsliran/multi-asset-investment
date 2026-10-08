# DeepSeek Harness 安装与首次使用

此包将 `v0.1.0` 的多资产投资研究核心接入 DeepSeek Harness（DSH），包含统一 Skill、数据、策略、报告及调仓四个模块。

## 下载与解压

从 [GitHub Release](https://github.com/xzjsliran/multi-asset-investment/releases/tag/v0.1.0) 下载 `multi-asset-investment-dsh-v0.1.0.zip`，完整解压得到 `multi-asset-investment-dsh` 文件夹，放在后续可以持续访问的位置。包内是一个 DSH 组合包：`package.json` 声明组合入口，`cordis.patch.yml` 登记一行插件，`index.js` 把包内统一 Skill 注册到会话技能目录，`skills/multi-asset-investment/` 包含统一 Skill、四个代码模块、报告模板和示例。不部署网页，也不注册 MCP 服务。

## 安装到 DSH

1. 打开侧栏「插件」页，点「添加插件」。
2. 「包名或地址」填解压文件夹的完整路径，例如 `/Users/你的用户名/工具/multi-asset-investment-dsh`。
3. 安装后确认该插件的组件显示为运行中；组合层写入当前 profile，所有会话共用。
4. 新建或继续对话，发送下方第一条需求。

插件在本机以使用者权限运行，请只安装自己解压的包。安装后暂不支持自动更新：升级时先在插件页卸载旧版本，再按上述步骤添加新目录。也可以让 Agent 使用插件管理安装这个本地目录，需要 Creator 模式并在对话中批准。界面文字与入口以本机 DSH 版本为准。

## 不使用插件时的替代方式

DSH 会扫描用户与项目的技能目录。把包内 `skills/multi-asset-investment` 整个文件夹复制到 `~/.dsh/skills/`（用户级）或研究项目的 `.dsh/skills/`（项目级），新会话的技能目录中即会出现“多资产投资研究”，不需要重启。这种方式不登记为插件，升级时自行替换文件夹。

## 第一条需求

> 使用多资产投资研究技能。先和我确认研究工作目录，在其中准备 Python 环境。随后确认研究年份、资产范围、配权方法和调仓频率，给出数据计划。Tushare 和 iFinD 是可选项，已有凭证请复用，缺少凭证时先用免费来源。

环境需要 Python 3.11 或 3.12，DSH 自带的受管解释器不作为本项目运行环境。让 Agent 读取 Skill 目录下的 `references/dsh-runtime.md`，按实际路径安装依赖。安装包包含方法、模板、示例及人工测试代码；真实行情、账户、研究结果、虚拟环境和密钥由使用者在插件外管理。DSH 默认按工作区写入权限运行，建议把会话工作区设为研究工作目录。

后续可以发送：

> 比较固定比例与风险平价资产配置，包含股票宽基、国债、黄金和商品 ETF。先让我确认具体资产和参数，再获取数据、回测并生成策略对比报告。

> 按我的当前策略和实际持仓估算下一次调仓。先核对可卖数量、现金和计划时间，再计算目标持仓与买卖差额。

## 版本与文件

`package.json` 记录包名与版本；`bundle-manifest.json` 记录核心源码提交、适配层提交和逐文件 SHA-256。Release 的 `SHA256SUMS-dsh.txt` 用于核对 ZIP 下载完整性。安装包不包含真实行情、账户、历史报告、环境或密钥。

遇到问题时反馈 DSH 版本、操作系统、使用的指令及去除密钥和账户信息后的报错。可在 [Issues](https://github.com/xzjsliran/multi-asset-investment/issues) 提交；下载和反馈需要仓库访问权限。
