# 首轮投资逻辑整合验证

日期：2026-10-14（Asia/Shanghai）。版本：0.4.0（Unreleased）；固定ADK 2.11.0，Python 3.12。依据[已确认首轮范围](../../../prompts/investment-logic-integration.md)，原有[v0.4接入记录](../README.md)保持当时的结果。本轮只做本地开发、检查及提交，不push、合并、部署或发布。

## 实现与验收对应

| 首轮要求 | 可观察检查 |
|---|---|
| 首次设置、明确拒绝、长期版本、撤销及单次覆盖 | setup缺失在获取材料／模型前报research_setup_required；真实CLI子进程及API /setup；冻结实际偏好；旧授权版本不能批准新选择 |
| 未回复仍交付、暂定不冒充批准 | 原生ADK流程产生provisional、confirmed=false、确定性估值和独立核验；报告首页无正式每股价，情景值保留在财务模块 |
| 后续确认及新版 | API服务／SQLite Session重建后确认，版本增加、失效重算和重新核验，report-v1仍存在；CLI confirm只确认主模型 |
| 实际业务、统一信号及集团边界 | 业务字段和异常解释检查原文引用；同阈值对应不同有来源的可能原因，集团信号始终保留且不能分摊；确定性公式、平均余额、分母、可比期间及严格阈值边界 |
| 主／辅助模型独立保障 | PE与PB各自参数、状态和凭证；辅助来源错误只拒绝辅助，辅助授权撤销导致程序核验失败；差异列示公式、参数及方向，不平均 |
| 风险意见及核验否决 | 系统高风险和用户低等级并存，后续模型修改不能删除该重大风险；独立major否决仍阻止正式结论 |
| 三种预测表达 | 一致预期核对机构／样本，有限研报明确样本，无外部基准保留本方预测和兑现条件；币种／单位／期间／日期不匹配拒绝，不生成价格隐含假设 |
| 结构化清单 | 风险、反证、跟踪必要字段；当前状态／数值没有来源拒绝；monitoring=checklist_only，无自动监控 |
| 回归及恢复 | 既有估值／replay、预算、权限、MCP／OCR、ADK轨迹及Artifact；篡改指标信号或结构化来源导致离线核验失败 |

## 实际命令和记录

从仓库根运行，所有行为夹具为明确标注的虚构数据，注入的决策／提供者不使用远程推理。

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p 'test_investment_logic.py' -v
.venv/bin/python scripts/check_offline_trace.py --agents-cli /absolute/path/agents-cli --output tmp/investment-cli-trace.json
.venv/bin/python -m compileall -q yantra tests scripts/eval_behavior.py scripts/measure_skills.py
.venv/bin/python -m pip check
.venv/bin/python scripts/eval_behavior.py
git diff --check
```

- [完整回归日志](tests.log)：201项通过，241.954秒；结果汇总见[summary.json](summary.json)。
- [首轮新增检查日志](investment-tests.log)：23项通过，21.393秒，包含实际ASGI /run_sse、/setup、SQLite重建及Session所属检查。这里的HTTP是本机测试传输，判断由固定夹具提供，不是LLM业务质量评估。
- [早期完整检查](first-regression.log)：178项、195.691秒，1 failure、1 error。隔离评估脚本尚未记录首次设置，以及旧偏好列表断言漏算setup。随后在隔离库显式拒绝授权，修正记录数；不修改原失败日志。另一次199项回归通过，后续补上偏好应用、重大风险保留及篡改筛查凭证检查后，以最终日志为准。
- 最后修正内存中的report_history对象共享（磁盘历史报告已保持不可变），随后新增23项、[原始控制器19项](v2-final-tests.log)（11.918秒）、[既有估值16项](valuation-final-tests.log)（24.413秒）共58项通过。完整201项在该最后快照复制修正前执行，最终受影响路径以58项检查为准。
- [真实CLI子进程契约](cli-contract.json)：未设置时退出2／没有研究目录、拒绝授权设置保存版本1、start/setup/resume帮助均可解析；没有模型、外部抓取或删除操作。
- [原固定演示localhost／agents-cli轨迹](offline-cli-trace.json)及[执行日志](offline-cli-trace.log)：实际socket服务、SQLite重启、等待预算不变、确认、公开轨迹和三类只读Artifact，installed agents-cli 1.10.0的完整case ID及transport metric通过。该脚本验证固定演示的历史等待契约；新原始研究的暂定行为由上述新增API检查验证，不能混称同一行为。
- compileall、pip check及文档文件／锚点、JSON例子／help和差异检查见[summary.json](summary.json)。真实行为脚本仅打印not_executed；5个案例及judge没有执行。

## 原生边界与未验证范围

检查已安装的App、ToolContext及request_confirmation接口，继续复用原生App工厂、Runner、独立Session、工具回调／插件和只读Artifact。投资授权版本、撤销、暂定场景、证据依赖失效及独立否决仍由原业务控制器单独写入；原生暂停确认不能提供这些业务契约，未重建通用执行器或另建账本。

原始披露及来源哈希、FCFF／净债务／少数权益／股数、预算及总截止保持检查。旧运行和旧计算凭证不迁移；旧字符串清单转为明确未知状态不能当作新风险评级或监测结果。仅有数据且无业务证据时，业务字段保留未知／缺口；跨表缺对应披露时不宣称勾稽核验完整。提取有区域、字段、输出及总预算限制，不保证财报全部科目覆盖。

本轮没有调用真实LLM、embedding、远端OCR、外部金融服务或judge，没有运行用户私有财报端到端研究。业务识别、风险重要性、异常原因的模型质量和经济判断仍未知；性能、真实费用及模型质量改善没有测量。PS、SOTP、正常化盈利、行业阈值库、通用一致预期服务、自动采集／订阅／提醒、价格隐含假设分析均不在本轮。

所有新日志和JSON仅为受控夹具或脱敏观测；不提交Word设计原件、用户材料、凭据或运行库。当前指南和Unreleased记录同步维护；Git状态以实际提交历史为准。
