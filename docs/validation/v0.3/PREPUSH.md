# v0.3 Git提交前验证

日期：2026-10-13（Asia/Shanghai）。用户明确授权commit和push v0.3，目标为现有`origin/main`，提交标题为`v0.3`。推送不表示已创建tag、GitHub Release或部署服务。

完整回归 **95项通过，50.133秒**；`compileall`及差异空白检查通过。Git fetch确认提交前本地与远端基线一致，基线为`32008f5`。实际记录见[日志](prepush-unittest.log)及[摘要](prepush-summary.json)。

提交范围包含v0.3源码、必要测试、当前／历史文档和英文AGENTS.md。用户原始财报、材料、输出、临时目录、凭据及混合的未跟踪原型目录不纳入；实际.gitignore保持原样。

测试执行真实ADK、SQLite、FTS5、文件恢复和数学复算；模型决策、embedding及外部资料响应由离线夹具注入。没有新增真实LLM／embedding服务验收或供应商账单，既有[开发验收限制](README.md#真实服务验证与限制)仍有效。

本文件记录提交前检查；commit和push的最终结果以Git历史、远端分支及任务汇报为准。

隔离暂存快照另通过 **95项测试，56.780秒**，见[暂存版本日志](staged-unittest.log)。工作区并行产生的CLI契约修改与测试保留为未暂存工作，不混入此版本交付。
