# Codex 安装与首次使用

从 GitHub Release 下载 `multi-asset-investment-codex-v*.zip`，解压得到 `multi-asset-investment-codex` 文件夹。保留整个文件夹，并放在后续可以持续访问的位置。

包内包含 Codex 插件清单、一个统一 Skill、四个代码模块、报告模板和本地插件目录清单。无需部署网页或 MCP 服务。实际取数使用 AKShare 等免费源，也可配置个人 Tushare/iFinD 凭证。

## 安装到 Codex

本包附有 `.agents/plugins/marketplace.json`。支持插件命令的 Codex CLI 可以直接注册解压目录：

```bash
codex plugin marketplace add "/完整路径/multi-asset-investment-codex"
codex plugin add multi-asset-investment@multi-asset-investment-local
```

如果当前客户端没有 `plugin add` 命令，先注册目录，再到桌面版插件目录选择“多资产投资研究”，安装该插件。必要时重启客户端并新建对话。插件目录名称为 `multi-asset-investment-local`。

支持项目本地插件目录的桌面客户端，也可以把解压文件夹作为一个项目打开，再从该项目的插件目录安装。不同版本的界面文字可能不同，以插件名称和目录来源为准。

更新时，把新版本解压到新的稳定目录，在插件目录卸载旧版，然后移除旧目录登记，再按上述步骤注册和安装新版本：

```bash
codex plugin marketplace remove multi-asset-investment-local
```

同名目录来源已经存在时，Codex 不允许直接换路径。运行环境、数据和凭证保留在研究工作目录及个人配置中。

## 首次使用

在新对话中选择“多资产投资研究”插件，或使用 `$multi-asset-investment`，然后发送：

> 使用多资产投资研究插件。先检查 Python 环境，在我指定的研究工作目录中配置依赖。再和我确认研究年份、资产范围、配权方法和调仓频率，列出所需数据及可用来源。Tushare 和 iFinD 都是可选项，先复用我已有的凭证。

Python 需要 3.11 或 3.12。让 Codex 根据 Skill 内的 `references/codex-runtime.md` 安装依赖。运行环境与研究文件应放在插件安装目录之外；启动检查不联网、不查询付费接口。

可以继续发送：

> 比较固定比例与风险平价资产配置，包含股票宽基、国债、黄金和商品 ETF。先让我确认资产与参数，再取数回测，生成策略对比报告。

> 根据我的当前策略和真实持仓，估算下次调仓。先核对持仓、可卖数量、现金和计划时间。

若客户端暂不支持插件安装，也可以让 Codex 直接读取解压目录中的 `skills/multi-asset-investment/SKILL.md`，使用相同代码。这种方式不会登记到插件列表。

## 版本与文件

插件版本在 `.codex-plugin/plugin.json`。`bundle-manifest.json` 记录源码提交和逐文件 SHA-256；Release 的 `SHA256SUMS` 用于检查 ZIP 下载完整性。安装包不包含真实行情、账户、历史报告、环境或密钥。

实时数据范围取决于使用者的数据来源及账户权限。

官方格式与安装说明：[OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)。
