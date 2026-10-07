# Codex 运行约定

本文件随 Codex 安装包提供。以本次加载的 `SKILL.md` 所在目录作为 Skill 目录；不要假设插件的安装位置或当前工作目录。

## 环境与路径

1. 复用用户研究工作目录中的 Python 3.11/3.12 虚拟环境。没有环境时，在该工作目录创建 `.venv`，安装本 Skill 目录的 `requirements.txt`。始终用虚拟环境解释器执行命令；不要改系统 Python，也不要把环境写进插件缓存。
2. 启动命令是 `<环境解释器> <Skill目录>/scripts/run.py startup`。依赖缺失时先安装依赖；`startup` 返回的是检查结果，退出码为零不代表所有可选依赖均已安装。
3. 数据、账户和结果使用工作目录内的绝对路径，包含空格的路径需加引号。各模块位置为 `<Skill目录>/quant-*-kit/`，已包含在安装包中。
4. 需要直接海外行情时再安装 `quant-data-kit/requirements-extended.txt`。若用户使用境内 QDII，可先按境内 ETF 路线检查覆盖范围。

macOS/Linux 安装示例（替换两个占位路径）：

```bash
python3.11 -m venv "<研究工作目录>/.venv"
"<研究工作目录>/.venv/bin/python" -m pip install -r "<Skill目录>/requirements.txt"
"<研究工作目录>/.venv/bin/python" "<Skill目录>/scripts/run.py" startup
```

Windows 可使用 `py -3.11 -m venv`；解释器位于 `.venv\Scripts\python.exe`。

## 对话与调用

先读取统一 Skill，再按当前任务读取对应模块的 `SKILL.md`，不一次性加载所有指南。沿用用户已经确认的研究需求和授权，只补问影响本次计算的缺项。真实调仓仍需确认当前策略与持仓快照。

Tushare 和 iFinD 使用本地凭证配置命令；本插件不注册带有个人密钥的 MCP，也不要求 OpenAI API Key。Codex 沿用用户已有的模型设置。iFinD 查询额度规则和可选 MCP 来源说明保留在数据模块中。

启动、自检、策略配置和数据计划可离线执行；实际行情及宏观取数需要网络。使用各模块现有 CLI 和输出约定，不另写一套计算公式。客户报告按报告模块规范生成。
