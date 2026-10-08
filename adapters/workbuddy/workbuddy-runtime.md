# WorkBuddy 运行约定

本文件随 WorkBuddy 安装包提供。以本次加载的 `SKILL.md` 所在目录作为 Skill 目录；不要假定插件的安装位置或当前工作目录。

## 环境与路径

1. 本项目按 Python 3.11 或 3.12 验证。WorkBuddy 自带的受管解释器版本可能与该要求不同，先依次检查 `python3.12 --version`、`python3.11 --version`、`python3 --version`，用满足要求的解释器创建环境；不要直接用受管解释器充当运行环境。
2. 复用用户研究工作目录中的虚拟环境；没有环境时在该工作目录创建 `.venv`，安装本 Skill 目录的 `requirements.txt`。始终用虚拟环境解释器执行命令，不改系统 Python，也不把环境写进插件目录。
3. 启动命令是 `<环境解释器> <Skill目录>/scripts/run.py startup`。依赖缺失时先安装依赖；`startup` 返回检查结果，退出码为零不代表所有可选依赖均已安装。
4. 数据、账户和结果使用工作目录内的绝对路径，包含空格的路径需加引号。各模块位置为 `<Skill目录>/quant-*-kit/`，已包含在安装包中。
5. 需要直接海外行情时再安装 `quant-data-kit/requirements-extended.txt`。用户使用境内 QDII 时，可先按境内 ETF 路线检查覆盖范围。

macOS/Linux 安装示例（替换占位路径）：

```bash
python3.12 -m venv "<研究工作目录>/.venv"
"<研究工作目录>/.venv/bin/python" -m pip install -r "<Skill目录>/requirements.txt"
"<研究工作目录>/.venv/bin/python" "<Skill目录>/scripts/run.py" startup
```

Windows 可使用 `py -3.12 -m venv`；解释器位于 `.venv\Scripts\python.exe`。

## 对话与调用

先读取统一 Skill，再按当前任务读取对应模块的 `SKILL.md`，不一次性加载所有指南。沿用用户已经确认的研究需求和授权，只补问影响本次计算的缺项。真实调仓仍需确认当前策略与持仓快照。

本插件不注册 MCP 服务，不使用 WorkBuddy 内置金融技能或连接器取数，也不要求额外的模型 API Key；取数、计算和报告一律走本插件自带的模块。用户明确要求使用其他数据服务时另行确认。

Tushare 和 iFinD 使用本 Skill 的本地凭证命令；密钥不写入对话、插件目录、普通命令参数或报告。iFinD 按数据模块的调用预算与缓存规则使用，先约定少量调用。

启动、自检、策略配置和数据计划可离线执行；实际行情及宏观取数需要网络。使用各模块现有 CLI 和输出约定，不另写一套计算公式。客户报告按报告模块规范生成，历史表现与当前调仓计划分开说明。

## 工作目录

WorkBuddy 按任务组织会话。研究年份、资产范围、配权规则和调仓频率可以在对话中确认，但数据、策略配置、账户快照和报告要保存到用户指定的研究目录，后续任务沿用同一目录，避免环境与数据分散在多个临时位置。
