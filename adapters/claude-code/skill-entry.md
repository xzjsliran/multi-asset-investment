## Claude Code 运行入口

本 Skill 目录为 `${CLAUDE_PLUGIN_ROOT}/skills/multi-asset-investment`。先读取该目录下的 `references/claude-code-runtime.md`，再执行下文首次启动。若客户端未展开路径变量，以本次加载的 SKILL.md 实际位置定位。

使用 Claude Code 的文件读取和终端工具调用随包 Python。四个模块位于本 Skill 目录内，按任务读取对应 SKILL.md。沿用用户已确认的需求；环境、下载数据、账户和报告均放在用户研究目录。
