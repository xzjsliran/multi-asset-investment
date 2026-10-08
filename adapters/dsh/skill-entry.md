## DSH 运行入口

本 Skill 由 DeepSeek Harness 插件提供，位于插件包内 `skills/multi-asset-investment`。宿主在 `<skill_resources>` 中给出该目录的绝对路径，以它定位本 Skill 及四个模块，不要假定插件安装位置或当前工作目录。先读取 `references/dsh-runtime.md`，再执行下文首次启动。

使用 DSH 的终端与文件工具调用随包 Python。四个模块位于本 Skill 目录内，按任务读取对应 SKILL.md。沿用用户已确认的需求；环境、行情、账户和报告都放在本次会话的研究工作目录，插件目录只读使用。
