# v0.4.2 正式公开资料研究验证

日期：2026-10-14（Asia/Shanghai）。状态：本地实现与离线验证完成，未发布、未推送、未合并、未部署。Python 3.12.6，Google ADK固定2.11.0，程序与pyproject版本0.4.2。原实施提示词暂定v0.5，本轮按用户指定文件名交付v0.4.2；原任务要求保留。

## 用户可见行为及范围

`start --company 公司名`进入正式研究，可无上传材料；`demo`显式使用虚构材料和固定离线策略。身份或市场歧义保存waiting候选，CLI `resume --select-identity`及同Session API `/select-identity`沿用原预算／截止。空偏好采用本次default_policy，授权快照unspecified、假设confirmed=false，不创建长期同意；明确拒绝／撤销／手动保持暂定。已有模型配置及可核实定价仍必需。

Tushare直连、Tavily和Yahoo Finance/yfinance使用已有扩展与材料路径，完整安全响应、请求、报价时间及哈希可回读；新模板停用且费用未知，凭据、依赖、角色及预算闸不能被回退绕过。搜索结果进入原文获取；搜索摘要和未定位原始披露的财务聚合值不能成为原始财务事实。失败、空身份结果及不适用行情继续适用换源，同一失败请求不自动重试。HTTP不等于浏览器，登录／付费／提交不会自动执行。

八部分报告及CLI/API首先给出三类判断，沿用主估值三情景或已有方法区间，确定性计算安全边际与阈值。关键冲突、来源／日期／证券／币种、数学及独立核验否决继续有效。等价单位和有原文、较晚披露日期的重述可解释；独立核验可标注不被估值或论点使用的非关键差异，候选原值保留，不选择未调节的计算输入。达到上限保留分析、收据和缺口，研究状态与投资结论分别呈现。

## 实际检查

命令从仓库根目录执行；外部调用由`run_offline_tests.py`及受控夹具显式阻断，允许localhost。测试使用合成材料、提供者响应及模型决策，没有真实账号数据或模型推理。

| 命令 | 实际结果 |
|---|---|
| `.venv/bin/python scripts/run_offline_tests.py` | 265项通过，222.350秒，未发生意外外部调用；[完整受控日志](regression.log) |
| `.venv/bin/python scripts/run_offline_tests.py test_information.py` | 最后Tushare合并参数默认及请求保存调整后17项通过，1.389秒 |
| `.venv/bin/python scripts/run_offline_tests.py test_source_fallback.py` | 19项通过，0.863秒，覆盖新服务、空身份、原文获取及访问限制 |
| `.venv/bin/python scripts/run_offline_tests.py test_evidence_acquisition.py` | 9项通过，包含独立非关键冲突分类与被引用输入禁止降级 |
| `.venv/bin/python scripts/run_offline_tests.py test_investment_logic.py` | 最后default_policy未确认语义调整后23项通过，23.333秒 |
| `.venv/bin/python scripts/run_offline_tests.py test_formal_research.py` | 最后准备任务收束修正后7项通过，17.602秒；实际ADK公司名流程、获取／核验及任务结果收束，零上传材料；[最终日志](delivery-formal-fixed.log) |
| `.venv/bin/python scripts/run_offline_tests.py test_judgment.py` | 同次最终调整后15项通过；价格阈值、全部否决及报告顺序 |
| `.venv/bin/python scripts/check_offline_trace.py --output /tmp/yantra-v042-final-trace.json` | 本机真实HTTP、SQLite跨重启、等待费用不变、历史demo恢复、只读Artifact及所属轨迹通过；[脱敏轨迹](offline-trace.json) |
| `.venv/bin/python scripts/eval_behavior.py` | 明确not_executed，打印5个已有真实行为评估案例的授权边界，未执行生成／judge |
| `.venv/bin/python -m compileall -q yantra tests scripts`及`git diff --check` | 通过 |

全量运行后只重跑末次修改影响的检查，以上针对性结果不是另一轮全量计数。其他已完成检查、命令和环境摘要见[verification.json](verification.json)。没有将旧版本结果改写成本次成功。

末次将服务费用合计闸限定于新研究，保留历史预算语义后，`test_v2.py`19项通过（12.036秒）、正式流程7项通过（17.712秒）。准备资料任务结果新增收束记录，避免已完成的无模型获取在业务轨迹被误标为中断；初次收束错误使用运行状态`completed`，而Result契约要求`complete`，导致7个场景中6次失败及4次子测试错误，保留[原始失败日志的无损gzip](delivery-formal-initial-failure.log.gz)，原字节哈希在verification.json中。修正后7项全部通过，最终结果见上表；没有收费调用或丢弃已有成果。

文档检查：`.venv/bin/python docs/validation/v0.4.2/check_documentation.py`核对当前指南、计划和记录的本地文件／标题链接；[结果](documentation-check.json)。首次扫描发现另一项本机宿主提示词迁移导致的旧链接，保留[首次失败记录](documentation-check-initial.json)，工作区导航已指向本机新文件。本次不提交其他人的迁移，也不提交依赖其新文件的两个导航链接片段；提交中的导航继续指向Git已有路径。克隆后的检查适用提交导航，本机检查适用工作区导航。

`start/demo/resume --help`在JSON模式全部通过，仅解析参数、不执行研究；当前指南的84条CLI例子及13条信息JSON请求在最终接口收紧前逐条通过解析／schema检查，新默认report_type仍保存实际发送参数。其他人的参考提议和迁移文件可以只存在本机；测试临时运行路径不属于正式文档附件。

## 未执行及实际限制

- 未调用真实Tushare／Tavily／Yahoo接口、付费服务、模型或judge；Tushare HTTPS可达性、账户接口权限、费用、数据覆盖和真实模型判断质量未验证。只读取公开上游说明及源码核对接口，不使用账户资料。
- yfinance 0.2.66为可选依赖声明，本环境未安装。SDK行为使用显式替身与受控curl_cffi响应检查，不能证明真实股票、港股柜台或美股研究全覆盖。正式身份及估值市场仍为CN/HK，Yahoo的US查询白名单不等于US完整研究支持。
- HTTP公开获取可用；没有内建可交互浏览器。密码登录页和访问拒绝形成缺口，未执行登录、付费或表单提交。
- 单一可比或点估值的三情景可能相同，报告说明区间规则，不编造三组独立经营预测。独立核验是现有隔离任务，受控决策通过不表示真实经济判断质量通过。
- 轨迹中的临时运行／报告路径属于合成测试，退出后已清理；版本化证据保留脱敏内容和哈希，不交付临时研究目录。既有用户材料、凭据、其他人的文档迁移与本地输出未纳入提交。

## 原生ADK与业务职责

检查了安装的App、Runner接口并复用现有共享App/plugin、专业Worker、Session及事件轨迹；没有升级ADK、替换模型或增加执行／费用账本。新选择、来源尝试、授权版本、安全边际与独立冲突分类由既有RawEngine及RunBudget写入，属于证券与证据业务边界，见[ADR 0002](../../decisions/0002-adk-native-boundaries.md)。源码及当前操作入口见[使用指南](../../USAGE.md)、[信息来源](../../INFORMATION_SOURCES.md)、[估值说明](../../VALUATION.md)。
