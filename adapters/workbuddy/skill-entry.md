## WorkBuddy 运行入口

本 Skill 由 WorkBuddy 插件加载，目录为插件内 `skills/multi-asset-investment`。先读取该目录下的 `references/workbuddy-runtime.md`，再执行下文首次启动；若客户端未提供插件路径变量，以本次加载的 SKILL.md 实际位置定位 Skill 目录。

使用 WorkBuddy 的终端与文件工具调用随包 Python。四个模块位于本 Skill 目录内，按任务读取对应 SKILL.md。沿用用户已确认的需求；环境、下载数据、账户和报告均放在用户研究目录。
