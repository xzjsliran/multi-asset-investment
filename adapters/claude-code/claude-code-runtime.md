# Claude Code 运行约定

插件提供研究方法和 Python 代码，Claude Code 负责对话与工具调用。模型沿用用户的 Claude Code 配置。

## 路径与环境

从已加载的统一 SKILL.md 定位 Skill 目录，其中有 `scripts/run.py`、`requirements.txt` 和四个 `quant-*-kit`。不要把当前工作目录当作插件目录，也不要假定插件位于作者电脑上的路径。

1. 确认用户的研究工作目录，复用其中的 Python 3.11 或 3.12 虚拟环境。缺少环境时在研究目录创建 `.venv`，安装 Skill 目录的 `requirements.txt`。
2. 所有命令使用该虚拟环境的解释器，并为带空格的路径加引号。数据、策略配置、账户、缓存和报告使用研究目录内的绝对路径。
3. 执行 `<环境解释器> <Skill目录>/scripts/run.py startup`，读取返回的环境及可选凭证状态。该命令不联网、不返回密钥；退出码为零不表示每项可选依赖均可用。
4. 按需安装 `quant-data-kit/requirements-extended.txt` 中的海外行情依赖；境内 QDII 先按境内 ETF 数据流程检查覆盖。

macOS/Linux 示例，实际执行前替换占位路径：

```bash
python3.11 -m venv "<研究工作目录>/.venv"
"<研究工作目录>/.venv/bin/python" -m pip install -r "<Skill目录>/requirements.txt"
"<研究工作目录>/.venv/bin/python" "<Skill目录>/scripts/run.py" startup
```

Windows 可使用 `py -3.11 -m venv`，解释器位于 `.venv\Scripts\python.exe`。插件安装目录只存放分发文件，运行环境和研究结果留在插件外。

## 需求、凭证与执行

先确认研究年份、资产范围、配权规则与调仓频率，再生成数据计划。需要实际调仓时，确认当前策略、真实持仓、可卖数量、现金和计划时间。所有资产只做多，现金不透支。

Tushare 和 iFinD 为可选来源，已有凭证直接复用。需要新增凭证时，让用户在自己的交互终端执行 `<环境解释器> <Skill目录>/scripts/run.py credentials tushare` 或 `credentials ifind`，通过不回显输入保存。若 Agent 的终端不能接收交互输入，就把已替换路径的命令交给用户执行。不要让用户把密钥发进对话或写进插件。

使用宿主已有的工具权限；本包不自动安装环境，不注册 MCP、后台任务或启动钩子。iFinD 按数据模块的调用预算及缓存规则使用。每一步读取程序返回的状态，失败时说明具体缺项，不编造下载成功、回测结果或报告指标。

从统一入口按需读取模块说明，使用已有命令完成取数、回测、报告和调仓。模块内的历史本机路径只用于原作者复核，不能作为新用户输入路径。面向客户的报告遵循报告模块规范，历史表现与当前调仓计划分开说明。
