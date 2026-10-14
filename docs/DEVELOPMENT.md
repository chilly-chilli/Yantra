# 本地开发与评估

适用0.4.0（Unreleased）；固定Google ADK2.11.0，工具环境agents-cli1.10.0。安装仍见[使用指南](USAGE.md#安装)，模型和凭据引用见[能力配置](CAPABILITIES.md)。

`agents-cli-manifest.yaml`让CLI识别现有工程，`yantra.fast_api_app:app`提供其package入口。使用已安装固定.venv启动；不运行完整enhance覆盖工程，不引入云部署、A2A或默认评估模型。架构职责及例外见[ADR0002](decisions/0002-adk-native-boundaries.md)。

先运行确定性检查，再运行外部接口的离线轨迹检查。真实模型行为评估和judge各需具体批准凭据／预算，不能用已有研究默认预算隐式启动；运行命令、合成案例、预算模板和评分门槛集中在[评估说明](../tests/eval/README.md)。该说明也记录安装CLI的grade需要ADC、转换轨迹丢失内部关联等实际限制。

[CI配置](../.github/workflows/checks.yml)分别报告确定性、离线轨迹及真实行为；真实行为目前明确未执行。CI配置存在不代表远端CI已成功运行。可重复本机测量脚本位于`scripts/measure_adk.py`、`scripts/measure_mcp.py`和`scripts/measure_skills.py`，方法和结果集中在[v0.4.0验证](validation/v0.4.0/README.md)。

提交前只stage当前任务，检查`git diff --cached`和`git diff --check`，完成与改动相关的验证。运行机制提交说明应包含复用的ADK能力、自定义缺口和证据。local commit不等于push、合并或部署；完整规则见[AGENTS.md](../AGENTS.md)。

首轮投资逻辑整合的确定性检查包括 `tests/test_investment_logic.py`：明确setup、拒绝及撤销、暂定报告、主／辅助模型、固定财务信号、结构化风险意见和经营预测；真实本机ASGI请求验证 `/setup`、首次启动拒绝、暂定交付、SQLite Session重启确认及所属Session轨迹。合成行为案例的逐节点要求已调整为“未回复仍交付，确认后新版”，真实生成和judge仍需原显式审批。新状态的来源／数学／恢复测试不能证明LLM业务识别、异常归因或研究质量提高。验证见[本轮记录](validation/v0.4.0/investment-logic-2026-10-14/README.md)。
