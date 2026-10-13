# Yantra v0.3 验证记录

> 历史记录：保留该版本的功能、日期、结果和限制。当前入口见[文档索引](../../README.md)。

日期：2026-10-13。在实际v0.2.3基线上实现0.3.0，保持Google ADK 2.11.0及现有估值、权限、预算、取消、恢复和离线复算。workspace旧财报、材料、输出及交付物未扫描或导入。本版夹具均在临时目录新建并标记fictional／虚构。

## 执行证据

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q yantra tests
.venv/bin/python -m yantra --help
git diff --check
```

完整测试日志见[本版日志](../v03-unittest.log)，数量、耗时、服务验证范围及检查结果见[机器摘要](../v03-summary.json)，CLI入口见[帮助输出](../v03-cli-help.txt)。完整入口 **95项通过，59.717秒**：原有65项回归保留，新增30项知识／信源／两期ADK测试。编译、CLI与差异空白检查通过。信源目标隔离和最终元数据调整后，另执行相关新增测试；结果记录在机器摘要。

测试比较来源、权限、状态、版本关系及计算，不比较模型逐字输出。SQLite、FTS5、本地文件事务、恢复、锁等待与实际ADK Runner／Session运行是真实代码；研究决策、独立知识核验和embedding HTTP响应使用明确离线夹具。

## 提示词17项验收对应

| # | 场景与观察结果 | 新增或保留的主要证据 |
|---|---|---|
| 1 | 两次实际ADK session关联同一公司，第二次采用合格事实及原文；仍主动执行四类查询 | `RuntimeKnowledgeTests.test_two_actual_adk_sessions_reuse_compare_and_replay_fixed_revisions` |
| 2 | 历史论点保留状态与证据，formal_current_conclusion恒为false，不能按事实adopt | `test_two_sessions_reuse_verified_fact_not_historical_conclusion` |
| 3 | 判断快照先保存，再比较；新修订保留supersedes与旧值，旧快照不变 | `test_snapshot_precedes_comparison_and_revision_preserves_history`及两期ADK测试 |
| 4 | supported/weakened/refuted/unresolved/closed状态变更逐条核验；否决保留且阻止提交 | `test_thesis_states_and_rejected_proposal_keep_review_records` |
| 5 | 集团与业务利润率分别计算，不错误覆盖；业务scope不能直接证实集团论点 | `test_business_and_group_metrics_remain_distinct`、`test_business_scope_cannot_directly_confirm_group_thesis` |
| 6 | 不同期间与口径并存，同期同口径不同数值均保留且要求重新核验 | `test_cross_session_conflict_and_distinct_period_scope`、`test_correction_relation_and_independent_index_failure` |
| 7 | 未来及未知披露资料排除；事后修订不进入历史截止，新历史研究与replay分离 | `test_historical_cutoff_excludes_future_evidence_and_posthoc_thesis` |
| 8 | 中文毛利率／汽车及单字命中原line/hash；不同表述的语义查询返回原定位 | `test_chinese_short_words_return_original_locator_and_hard_filters`、`test_semantic_retrieval_incremental_switch_and_budget` |
| 9 | 增量索引不重复调用，纯优先级变化复用向量；模型切换隔离；价格不明／费用不足／调用错误／请求耗尽降级 | `EmbeddingTests`四项与预算回归 |
| 10 | 自动归档pending不自动passed；partial的逐条合格事实可复用，cancelled仍保留原文 | `test_archive_pending_and_partial_fact_qualification_are_separate`及取消集成测试 |
| 11 | 人工实质编辑生成manual/pending新修订，旧pin仍可读；排版不撤销；passed不能人工设置 | `test_manual_edit_revokes_verification_and_old_pin_survives` |
| 12 | session unlink与共享删除区分，软删除保留旧引用，永久删除确认当前影响；无关对象保留，受影响replay明确失败 | `test_deletion_soft_pin_session_unlink_and_permanent_impact`、两期ADK测试 |
| 13 | 显式12个月优先于公司／全局默认，撤销重新计算，明确本次修改在边界失效成果并增版；CLI／ADK回显scope | `test_requirements_priority_versions_revoke_and_hard_constraints`、`test_requirements_boundary_revoke_and_explicit_parameters`、CLI／ADK测试 |
| 14 | 两个同基准并发提交仅一个成功，另一个VersionConflict；锁等待消耗原截止 | `test_concurrent_company_commit_stale_base_cannot_overwrite`、`test_lock_wait_consumes_original_deadline` |
| 15 | 字节提交中断后恢复；索引失败不损坏正式记录并可重建；授权永久删除中断后继续完成清理 | `test_interrupted_file_commit_recovers_and_indexes_rebuild`、`test_correction_relation_and_independent_index_failure`、`test_permanent_delete_interruption_recovers_authorized_cleanup` |
| 16 | resume保持deadline／ledger，cancel归档，replay固定修订且零远程判断；原估值、原始来源、否决与改参回归保留 | 两期ADK测试、取消测试，以及原65项回归 |
| 17 | 实际web_search发现路径与离线外部ID适配器使用同一个获取、去重、解析、截止及checkpoint流程 | `test_web_and_offline_adapter_share_fetch_archive_and_checkpoint` |

测试定义位于[知识边界测试](../../../tests/test_knowledge.py)和[控制器／ADK集成测试](../../../tests/test_knowledge_runtime.py)。既有回归位于原tests目录；没有运行依赖workspace旧财报的local_materials示例。

## 真实服务验证与限制

- 本轮未调用真实embedding、LLM、DDGS搜索或外部披露HTTP服务，无真实供应商账单。HTTP embedding响应、发现候选及原文响应由离线夹具注入；实际请求协议、凭据、供应商限额及模型效果需使用真实配置另行验证。
- 两期研究使用真实ADK编排与脚本判断后端，保留partial和财务／市场缺口，不能称为真实金融研究端到端成功。不同论点的经济判断仍由独立核验模型负责，程序检查不能证明投资判断正确。
- 语义排序采用筛选后候选的本地cosine与RRF60；优先级作平分排序依据。相似度不代表正确性、核验资格或独立证据。首版逐条新增向量，规模增长时受时间／请求预算限制并降级，没有独立向量服务。
- 公司关系需要可靠证券身份或明确company ID；不同类别不凭名称自动合并。日期依据、未核验候选、人工内容及冲突均保留限制，不静默补齐。
- 共享写入采用保守的全库串行锁，确保同公司串行但不追求多公司最大并发。索引可重建，正式SQLite及原始字节需备份。永久删除明确破坏相关历史可复现性；只有程序登记的库及session副本属于删除范围，外部原始输入文件不代删。
- v0.3.1只预留统一发现／获取、外部ID、cursor及checkpoint合约；未实现RSS／Atom订阅、轮询、自动唤醒或新数据服务。没有Obsidian、人工编辑UI、通用经验学习或新的聊天产品。

实现与原提案差异见[提案第12节](../../proposals/v0.3-knowledge.md#12-v03-实施采纳与差异2026-10-13)，实际存储、CLI及接入契约见[本版使用说明](../../archive/v0.3/USAGE.md)。本任务不创建PR、推送或部署。

## 后续Git交付检查

2026-10-13，在用户另行明确授权commit和push后，执行[提交前验证](PREPUSH.md)。这项后续检查不改写上述首次开发验收的时间、结果或服务验证范围。
