# DSH 运行约定

本文件随 DeepSeek Harness（DSH）插件包提供。插件只登记研究方法和 Python 代码；对话、终端、文件与网页工具由 DSH 提供，模型沿用用户当前的 DSH 配置。

## 定位 Skill 目录

1. 本次加载的 `SKILL.md` 位于插件包内 `skills/multi-asset-investment`。宿主在技能资源区给出该目录的绝对路径（Base directory），以它作为 Skill 目录，不要假定 profile 位置、插件安装位置或当前工作目录。
2. 该目录包含 `scripts/run.py`、`requirements.txt` 和四个 `quant-*-kit`。路径带空格时加引号；插件目录只用于读取，不写入环境、数据或报告。

## 环境与路径

1. 本项目按 Python 3.11 或 3.12 验证。DSH 自带的受管解释器服务于它自己的内置技能，不作为本项目的运行环境；先依次检查 `python3.12 --version`、`python3.11 --version`、`python3 --version`，用满足要求的解释器创建环境。
2. 复用用户研究工作目录中的虚拟环境；没有环境时在该目录创建 `.venv`，安装本 Skill 目录的 `requirements.txt`。始终用虚拟环境解释器执行命令，不改系统 Python，也不把环境写进插件目录。
3. 启动命令是 `<环境解释器> <Skill目录>/scripts/run.py startup`。依赖缺失时先安装依赖；`startup` 返回检查结果，退出码为零不代表所有可选依赖均已安装。
4. 数据、账户、缓存和报告使用研究工作目录内的绝对路径。DSH 默认按工作区写入权限运行：把会话工作区设为该研究目录，写在工作区之外的命令会先请求授权；行情与报告不要放在插件目录或 DSH profile 目录。
5. 需要直接海外行情时再安装 `quant-data-kit/requirements-extended.txt`；用户使用境内 QDII 时，可先按境内 ETF 路线检查覆盖范围。

macOS/Linux 安装示例（替换占位路径）：

```bash
python3.12 -m venv "<研究工作目录>/.venv"
"<研究工作目录>/.venv/bin/python" -m pip install -r "<Skill目录>/requirements.txt"
"<研究工作目录>/.venv/bin/python" "<Skill目录>/scripts/run.py" startup
```

Windows 使用 `py -3.12 -m venv`；解释器位于 `.venv\Scripts\python.exe`。

## 对话与调用

先读取统一 Skill，再按当前任务读取对应模块的 `SKILL.md`，不一次性加载所有指南。沿用用户已经确认的研究需求和授权，只补问影响本次计算的缺项。真实调仓仍需确认当前策略与持仓快照。

本插件不注册 MCP 服务、不调用模型 API、不安装后台任务或启动钩子；取数、计算和报告一律走插件自带的模块。用户明确要求使用其他数据服务时另行确认。

Tushare 和 iFinD 使用本 Skill 的本地凭证命令；密钥不写入对话、插件目录、普通命令参数或报告。配置时让用户在能接收交互输入的终端执行 `<环境解释器> <Skill目录>/scripts/run.py credentials tushare` 或 `credentials ifind`，通过不回显输入保存；DSH 的终端工具不能接收交互输入时，把替换好路径的命令交给用户自行执行。iFinD 按数据模块的调用预算与缓存规则使用，先约定少量调用。

启动、自检、策略配置和数据计划可离线执行；实际行情及宏观取数需要网络。使用各模块现有 CLI 和输出约定，不另写一套计算公式。模块内记录的历史本机路径只用于原作者复核，不能当作新用户输入路径。客户报告按报告模块规范生成，历史表现与当前调仓计划分开说明。

## 工作目录

DSH 按工作区组织会话，技能目录随插件分发、内容只读。首次使用先与用户确认研究工作目录，后续会话沿用同一目录，避免环境与数据散落在多个临时位置。
