# WorkBuddy 安装与首次使用

此包将 `v0.2.0` 的多资产投资研究核心接入 WorkBuddy，包含统一 Skill、数据、策略、报告及调仓四个模块。

## 下载与解压

从 [GitHub Release](https://github.com/xzjsliran/multi-asset-investment/releases/tag/v0.2.0) 下载 `multi-asset-investment-workbuddy-v0.2.0.zip`，完整解压得到 `multi-asset-investment-workbuddy` 文件夹。保留整个文件夹，包括 `.codebuddy-plugin` 隐藏目录，并放在后续可以持续访问的位置。

包内是一个本地插件市场：`.codebuddy-plugin/marketplace.json` 登记插件 `multi-asset-investment`，插件本体在 `plugins/multi-asset-investment/`，其中包含统一 Skill、四个代码模块、报告模板和示例。不部署网页，也不注册 MCP 服务。

## 安装到 WorkBuddy

1. 打开插件面板，选择“管理市场”，再选“添加市场”。
2. “市场源”填写解压文件夹的完整路径，例如 `/Users/你的用户名/工具/multi-asset-investment-workbuddy`。本地目录形式即 `./path/to/marketplace`。
3. 在市场中找到“多资产投资研究”，安装并启用，按用户或按项目启用均可。
4. 新建对话，发送下方第一条需求。

添加失败时，先确认填的是解压后的完整文件夹，且 `.codebuddy-plugin` 隐藏目录仍在；市场名称固定为 `multi-asset-investment-local`。不同版本的界面文字可能略有差异，以市场名称和目录来源为准。

## 第一条需求

> 使用多资产投资研究插件。先和我确认研究工作目录，在其中准备 Python 环境。随后确认研究年份、资产范围、配权方法和调仓频率，给出数据计划。Tushare 和 iFinD 是可选项，已有凭证请复用，缺少凭证时先用免费来源。

环境需要 Python 3.11 或 3.12。让 WorkBuddy 读取插件内的 `skills/multi-asset-investment/references/workbuddy-runtime.md`，按实际路径安装依赖。安装包包含方法、模板和示例；真实行情、账户、研究结果、虚拟环境和密钥由使用者在插件外管理。

后续可以发送：

> 比较固定比例与风险平价资产配置，包含股票宽基、国债、黄金和商品 ETF。先让我确认具体资产和参数，再获取数据、回测并生成策略对比报告。

> 按我的当前策略和实际持仓估算下一次调仓。先核对可卖数量、现金和计划时间，再计算目标持仓与买卖差额。

## 不使用插件市场时的替代方式

WorkBuddy 也支持导入本地技能文件夹。把 `plugins/multi-asset-investment/skills/multi-asset-investment` 整个文件夹加入技能目录，或让 WorkBuddy 直接读取该文件夹里的 `SKILL.md`，使用相同代码。这种方式不会登记到插件列表，升级时需要自行替换文件夹。

## 更新与卸载

更新时把新版本解压到新的稳定目录，在插件面板卸载旧版本，移除旧目录的市场登记，再按上述步骤添加新目录。运行环境、数据和凭证保留在研究工作目录及个人配置中，不随插件删除。

## 版本与文件

`.codebuddy-plugin/plugin.json` 记录插件名称与版本；`bundle-manifest.json` 记录核心源码提交、适配源码提交和逐文件 SHA-256。Release 的 `SHA256SUMS-workbuddy.txt` 用于核对 ZIP 下载完整性。安装包不包含真实行情、账户、历史报告、环境或密钥。

遇到问题时反馈 WorkBuddy 版本、操作系统、使用的指令及去除密钥和账户信息后的报错。可在 [Issues](https://github.com/xzjsliran/multi-asset-investment/issues) 提交；下载与反馈入口均为公开。
