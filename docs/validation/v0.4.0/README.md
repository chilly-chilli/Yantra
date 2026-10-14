# v0.4.0 ADK 原生接入验证

日期：2026-10-14（Asia/Shanghai）。范围：0.3.3基线上的0.4.0（Unreleased）接入。固定 `google-adk==2.11.0`、`mcp==1.30.0`，本机Python3.12。任务仅授权本地开发和提交；未push、部署或发布。以下阶段日志保留当时版本／结果，最终检查另列。

## 基线与第一阶段

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p 'test_adk_*.py' -v
.venv/bin/python -m pip check
git diff --check
```

- [原生基线日志](../adk-native/baseline-tests.log)：149 项，136.137 秒；147 通过、1 failure、1 error。两处知识复用测试截止固定2026-10-13，却按实际2026-10-14保存新知识。产品排除截止之后知识的行为正确；先保留失败，后续将在测试自身修复合成夹具时间，不修改历史证据或产品截止规则。
- [第一阶段 ADK 检查](../adk-native/adk-stage1-tests.log)：15 项通过。真实本地 ADK Runner、SQLite Session、HTTP TestClient；合成提供者、虚构材料，没有远程推理。[首次组合检查](../adk-native/adk-stage1-attempt.log) 有1项失败：恢复预检先用环境根目录定位，而既有测试工厂将研究放在下级目录。改为先由已有工厂加载、再以实际 engine.directory 检查 Session 绑定，避免推测存储路径，并在任何业务推进前拒绝跨 Session 恢复。
- `test_adk_native.py` 从 `/run_sse` 启动、等待和确认，随后实际 GET trace 读取内部角色／工具／结果哈希。验证重启读取、父 invocation、唯一事件 ID、分页、任务筛选、跨 Session 404及拒绝借用 run ID。
- 合成用量每决策10输入＋5输出 token，用量事件数量等于提供者请求数量；业务 model_calls 同样一致。观察不写第二套账本；等待不改变调用数或预算。6项实际本地工具调用保持原结果与独立核验。
- 验证轨迹不含材料正文、参数、state delta、thought 或凭据；崩溃未完成尾行不视为事件，下一写入删除尾部残片。
- `pip check` 与 `git diff --check` 通过。原生入口 Loader 现在返回 App，保留 root_agent 兼容符号。

## 第二阶段：Worker 与产物

- Worker 单次请求通过原 BaseLlm 接口在已有 Worker 生命周期内运行；不创建逐行动 Runner／Session。仍由 Engine／RunBudget 预留结算，保持 task＋local 输入、JSON模式与结构校验、工具前权限／预算检查、超时、取消与零自动重试。
- [第二阶段 ADK 检查](adk-stage2-tests.log)：17 项通过，含新增非法JSON／提供者错误／超时／取消单次请求及生成器清理测试；外部 API 检查用量仅出现一次，专业 Worker 不再产生内部决策 invocation。
- [能力回归](capabilities-inline-tests.log)：17 项通过；涉及冻结模型／skills、OCR价格、凭据脱敏、MCP／命令工具和预算。均为合成响应、实际本机命令或局部本机MCP，不是远端服务验证。
- [原始研究回归](v2-stage2-tests.log)：19 项通过，9.681秒；分别覆盖财务口径、授权、深度、协作、核验否决、总截止与replay。
- 原生 Artifact 外部HTTP测试：跨API重建读取报告／计算、ADK版本0/1关联业务版本1/2、源字节相同、旧版本保持、不同Session不可读、材料路径不可读、写入／删除403；缺失及哈希篡改404但版本元数据保留。没有重复权威产物或自动材料暴露。
- Artifact 首次检查修正测试的URL-safe base64解码；另发现上游ADK创建接口将服务的PermissionError包装为500，增加仅用于产物写入／删除的只读HTTP门槛后为403。没有改动报告字节或放开写权限。
- 当前指南、README／索引／ADR 的83个本地文件及锚点链接检查、compileall及差异检查通过；验证记录仍保留0.3.3基线，最终版本切换和完整回归尚待本任务后续阶段。

## 测量

可运行脚本：[measure_adk.py](../../../scripts/measure_adk.py)。从仓库根运行以下命令；checkout路径代表阶段一提交 `4f93afe` 的独立导出，必须自行准备，不是交付的用户材料。

```sh
.venv/bin/python scripts/measure_adk.py --checkout /absolute/path/stage1 --samples 5 --output tmp/measure-before.json
.venv/bin/python scripts/measure_adk.py --samples 5 --output tmp/measure-after.json
```

顺序执行的五次完整 ASGI HTTP 流程（启动→等待→确认，虚构资料／提供者，无网络推理），见[阶段一测量](measure-stage1-sequential.json)及[Worker接入测量](measure-inline-sequential.json)。忽略随机run/task ID及临时根目录后，报告、计算、核验问题及业务状态哈希均为 `b72cdde2df0f6fc4ff02b9067528827de50db26c3f7ebb6d4b14b770b739f3f1`。

| 维度 | 阶段一 | Worker接入后 | 边界 |
|---|---:|---:|---|
| 提供者／业务模型调用 | 18／18 | 18／18 | 合成请求，不证明真实模型效率 |
| 工具调用 | 6 | 6 | 核验及partial语义相同 |
| ADK invocation | 26 | 15 | 少11次内部Runner／Session生命周期，实质本地执行改善 |
| 决策Runner invocation | 18 | 7 | Worker行动不再另建Runner |
| 端到端中位秒数 | 0.4640 | 0.3870 | 本机离线5样本，含首轮启动，无统计置信区间；真实提供者延迟未知 |
| 轨迹文件字节 | 52480 | 36038 | 仍有Session／任务关联及一次用量事件 |
| 请求文本UTF-8字节 | 约677985 | 约677327 | 仅文本大小，真实输入token未知，减少不足0.1%不能作为上下文治理收益 |
| 费用 | 0 | 0 | 固定10输入/5输出合成token用于账本检查，不声称降低模型费用 |

隔离、恢复、费用次数、工具准入、核验否决与语义检查没有发现不可接受退化；维护方面减少11次Session／Runner生命周期，但增加约50行单请求桥接及错误归一化，仍有自定义行动适配，不宣称整体维护复杂度下降。最初并行采样因CPU竞争未用于上述延迟结论；真实网络、模型、费用与大上下文性能仍未知。完整研究质量及整体Pareto改进尚未证明。

## 目前可证实的变化

内部轨迹从临时消费变为可持久化并通过所属外部 Session 查询，所有自建 Runner 统一 App／插件，Worker少一层逐行动编排，标准Artifact读取现有产物。新增可追踪性、生命周期减少及产物生态接口；不等同于研究质量或模型费用提高。

## 第三阶段：MCP 生命周期与中断

以固定 ADK 2.11.0 的 McpToolset、McpTool、MCPSessionManager 替换逐调用 SDK 连接栈。同一专业任务内复用连接；每次分页读取工具定义并按冻结名字、描述、schema核对。保留业务别名／角色／父任务预算，响应脱敏后的SDK JSON仍作为来源凭证，不变成估值计算凭证。

固定版本的原生发现只读取一页，并拒绝框架保留名；拥有的适配器补分页并以安全ADK名字发送原RPC名。HTTP默认ADC探测导致首次本地HTTP回归超时；拥有的会话管理器禁止未声明ADC，后续HTTP检查断言未调用google.auth.default。不改环境变量或凭据。UI元数据仅作为数据，禁止事件动作；HTTP沿用原不跟随重定向的凭据边界。

- [第三阶段 ADK 检查](adk-stage3-tests.log)：24项通过。7项新增MCP测试使用实际本地stdio／Streamable HTTP，验证重复／四路并发、超时／取消后可用、schema改变阻止执行、新工具不扩权、分页／同名RPC、1MB限制、错误日志脱敏、无ADC／无重定向凭据、HTTP Session关闭404且服务仍运行、stdio实际PID全部退出。
- 正常Worker插件after_run关闭连接；取消首次测试留下连接，补Runner.close对应插件close后，实际进程退出验证通过。实际父调用→第三层调用→父再次调用三次RPC使用同一PID，第三层只拥有父给定的一个工具，结束后由父关闭。控制器仍串行，连接层并发检查不支持业务并行状态写入声明。
- 外部调用前持久`in_flight_tool`，已记录结果后清除。实际取消声明0.2元的合成MCP调用后重新加载：不调用main／worker、不重放RPC、工具／模型次数与费用预留及deadline不变，状态failed／partial。经外部HTTP trace读取任务为interrupted，观测有invocation_interrupted。0.2元是受控夹具预留，不是远端账单。
- [能力检查](capabilities-stage3-tests.log)：17项通过；[原始研究检查](v2-stage3-tests.log)：19项通过。没有启用或调用停用的真实服务、真实模型或OCR API。
- 最后把构造器日志也纳入当前MCP脱敏上下文，避免DEBUG级别输出连接参数；[最终7项MCP复测](native-mcp-final-tests.log)通过。compileall、测量脚本help和差异检查通过。

### MCP 测量

第三阶段后续覆盖复核：初始Vision OCR在线程池启动自己的决策Runner，之前丢失ContextVar而无持久关联。增加线程上下文传递及独立OCR scope，并保存OCR收据task_id。[OCR关联及能力复测](ocr-scope-tests.log)：18项通过，8.447秒；新增测试实际原生Runner＋合成图像提供者，验证run／finance／Session／invocation／父invocation、一次用量和凭据不外泄。没有真实模型请求或新增费用账本。

[measure_mcp.py](../../../scripts/measure_mcp.py)对阶段二提交`96d7e95`的导出和本次实现顺序各采样5次，每次用同一冻结定义执行two echo calls。仅测试虚构本地stdio；计时包含调用连接创建／schema核对／退出，不含catalog发现。见[之前](mcp-before.json)与[之后](mcp-after.json)。

| 维度 | 逐调用SDK | 原生工具集 | 证据与限制 |
|---|---:|---:|---|
| 两次RPC的运行进程／连接数 | 2 | 1 | PID及session一致，实际复用；发现额外进程两边相同 |
| 工具执行次数 | 2 | 2 | 文本结果均one/two，不增加执行或模型调用 |
| 中位耗时秒 | 0.6555 | 0.3422 | 本机5样本，无置信区间；真实网络延迟未知 |
| 退出后的进程 | 全部关闭 | 全部关闭 | OS实际检查，不以close返回成功代替 |
| 模型调用／付费服务 | 0／0 | 0／0 | 真实费用未知，定义核对metadata请求须纳入声明上界 |

实质改善为连接复用及本机启动开销；正确性／冻结权限／预算／中断／清理未发现不可接受退化。删除约45行自建连接栈，新增约150行固定版本适配与保护，维护代码量增加，不宣称总复杂度下降。需在未来ADK升级时复核受保护接口。旧版本未记录的中断不能事后证明幂等性；没有启用原生resume、常驻后台服务或MCP资源权限。

## 第四阶段：技能与上下文试点

固定 ADK 2.11.0 的 SkillToolset 负责 list／load／resource，业务包装负责冻结内容哈希、角色、父权限、预算和128 KB限制。每任务独立模型，安全别名稳定；脚本只能读作 references，无 executor、动态工具或状态插值。默认 eager 完整注入保持，`extensions add skills ... --loading native` 明确选择新运行 Worker 的按需试点；旧运行不自动增加工具权限或改变输入。

- [6项技能检查](native-skills-tests.log)通过，2.696秒：实际 Worker＋原生 SkillToolset、加载前后正文、一次用量计费、指令不重复输入、字节变化拒绝、路径／角色／空父权限拒绝、无脚本执行器、旧快照及CLI默认／显式模式。首次CLI夹具误读JSON中的`ok`，按已有`data`合约修复后通过；未改产品输出。
- Native `tool_filter=[]` 被ADK视为未过滤；包装改用显式谓词，实际空父授权的第三层 Worker 没有技能目录、正文或工具。原始 metadata 中的 additional tools／inject state 未传给原生Frontmatter。
- 新运行的 task＋local 投影只替换完全相同的重复收据；保留首项原文／索引／哈希和全部权威日志。原文可经 read_materials／read_skill_resource 读取，不产生模型摘要或新证据。已加载技能正文进入该任务指令，收据输入保留固定哈希。没有启用 Session 压缩。
- [CLI合约回归](cli-stage4-tests.log)：18项通过，102.616秒；包含同期其他任务的CLI品牌修改，未把这些修改纳入ADK提交。
- [第四阶段全部ADK检查](adk-stage4-tests.log)：30项通过，12.252秒。compileall和git diff --check通过；真实模型／网络服务未调用。

### 测量方法与取舍

运行 `.venv/bin/python scripts/measure_skills.py --samples 5 --output tmp/skills.json`。受控合成 Worker，每组顺序5样本，小目录1技能约128字节／256字节资源，大目录10技能各约8 KB／16 KB资源；比较旧输入、仅完全重复收据引用、原生加载＋引用。原生“使用”场景多一步load；“不使用”场景仍读两次相同资源。提供者返回结构化固定行动，结果资源哈希相同，不评判真实模型选择。证据见[全部样本](skills-context.json)。

| 目录／使用技能 | 旧输入文本字节 | 仅引用字节 | 原生＋引用字节 | 模型调用旧→原生 | 本机中位秒旧→原生 |
|---|---:|---:|---:|---:|---:|
| 小／不使用 | 74972 | 74863 | 78340 | 3→3 | .0436→.0445 |
| 小／使用 | 49392 | 49392 | 79203 | 2→3 | .0313→.0455 |
| 大／不使用 | 373619 | 357382 | 119911 | 3→3 | .0502→.0516 |
| 大／使用 | 232362 | 232362 | 120774 | 2→3 | .0355→.0523 |

请求文本是实际发送的system＋payload UTF-8字节。账本保守`input_token_bound`也是字节估计：大／不使用467453→206215，大／使用294918→207540；不是提供者输入token。仅引用在大重复收据场景减少16237字节（4.35%），调用数不变；无重复时输入不变。它仍有少量投影耗时。本机没有统计置信区间、网络延迟或真实计费，真实token／费用均未知。

大型技能目录文本减少约48%至68%，但使用时多一次模型决策，且小目录输入和延迟增加，不能认定整体Pareto改善。因此原生模式保留小范围显式试点，默认不推广；未声称研究质量或模型费用提高。后续真实行为评估应检查模型能否正确加载／引用，不能从固定行动夹具推导此结论。

## 第五阶段：工程与行为评估配置

最小manifest被实际安装agents-cli1.10.0识别为google-adk工程；package FastAPI入口已补齐。[本机配置探测](eval-config-probe.log)确认路径、无部署目标及两份metric配置由CLI实际parser解析。完整enhance dry-run会添加.env／云设施／A2A／默认Gemini judge，未采纳；依赖、模型、凭据未修改。

[固定工具调查](agents-cli-investigation.md)保留当时的安装接口、dry-run差异、grade失败和日期。[工程检查](eval-engineering-tests.log)：5项通过，2.452秒；含审批拒绝、有限总预算、实际ASGI合成生成、原配置不改及无秘密输出。注入固定后端验证的是评估驱动，不是模型行为分数。

- [真实localhost检查](engineering-trace.log)及[完整轨迹](engineering-trace.json)：固定虚构策略和材料，通过socket服务器启动→waiting→继续不计费→进程退出／重启→明确确认→partial；四角色及6项专业工具保持，标准Artifact可读，跨用户scope404。没有远程推理。
- 同一脚本实际运行`agents-cli eval generate --url ... --concurrency 1`，预期case ID不丢失，响应waiting及直接transport metric满足；CLI转换3事件，公开轨迹22事件，角色finance／thesis／valuation。直接函数只是离线门槛，不当作CLI grade或真实模型分数。
- 5类[合成行为案例](../../../tests/eval/datasets/behavior.json)与criteria覆盖缺数字、角色和工具／进展、越权与估值授权、独立否决、多轮等待确认恢复；已有确定性测试继续验证程序门槛。真实生成脚本先要求具体已注册模型、价格、案例ID及总模型／费用／时间审批，均分费用和调用上限，共享总截止。固定SourceAdapter保留四类发现记录但拒绝公网资料，不开启用户扩展或知识库。每轮调用实际ASGI入口并另附公开轨迹／原账本，失败run仍保留。
- 首次合成生成器检查触发默认DDGS发现，44秒后中止；无真实模型／judge调用。改由已有SourceAdapter绑定固定空发现，随后测试通过。另一处夹具误期望空tools为list，按现有dict快照修正。未修改业务检索合约。
- 分层CI配置分别报告确定性、离线轨迹和真实模型；真实项明确NOT EXECUTED，不自动读取用户凭据。远端CI尚未执行。本机pip check／compileall／diff check通过。
- agents-cli的history播种不是实际多轮执行，且转换丢失关联／usage；没有用它证明恢复。先前实际grade（仅纯本地函数）因SDK2.4.0云client初始化要求ADC失败；没有修改凭据、没有分数。此次完成parser及直接函数门槛；真实行为／托管judge预算未批准，均未执行。具体命令及评分要求见[评估说明](../../../tests/eval/README.md)。

## 完整回归的夹具修复

[首次完整检查](full-first-attempt.log)：176项，145.565秒，5个failure／27个error（含subtest）。估值集成16个测试方法共用的旧合成文本写`disclosure`，原解析器仅从`published`／披露定位日期，未知日期材料在UTC进入2026-10-14后按历史截止被正确排除；并非新增上下文投影丢字段。给受控示例添加明确的合成发布日2026-10-12，[16项估值复测](valuation-fixture-fixed.log)全部通过，17.976秒。未扩大解析器推断或截止权限，旧失败日志保留。

原基线两项知识夹具仅固定其自身knowledge记录时间为2026-10-13；研究／费用／墙钟时钟仍真实。[30项知识检查](knowledge-fixture-tests.log)通过，10.989秒。行为材料也改用明确“披露日期”并新增真实解析检查；[6项评估驱动检查](eval-disclosure-fixed.log)通过，2.440秒。

## 最终检查与交付范围

- [最终完整回归](full-verified-tests.log)：**178项通过，160.661秒**，运行及pyproject版本0.4.0。涵盖原研究阶段／财务口径／证券／费用预留／总截止／CAS修订／深度／父权限／估值授权／独立否决／partial及离线replay，历史失败没有改写。
- [34项ADK覆盖复核](adk-scope-final.log)及[18项能力检查](capabilities-scope-final.log)通过。最后增加费用收据owner_invocation_id后，完整回归包含实际ASGI读取的关联断言。公开controller投影只读state.json：数值预算、费用／用量收据及准备检索目的／结果数，不暴露查询、材料、定价来源或提供者详情，不二次结算。没有Runner的程序操作不会伪造ADK事件。
- 自建DatabaseSessionService通过原生close纳入API生命周期；调用方传入的服务不越权关闭。OCR命令调用前持久化调用计数和收据关联。原生工具错误代码为固定白名单；未知错误仅保留安全类别／哈希。
- 7项技能路径／权限检查通过，2.885秒（包含于最终完整回归）：保留完整原路径，根文件与references同名文件的实际读取结果／哈希不同，脚本仍只读。首次新增测试误放restored断言导致NameError，已修正测试位置；不是产品授权放宽。
- [最终外部入口](engineering-delivery-trace.log)及[完整输出](engineering-delivery-trace.json)再次通过实际localhost／SQLite进程重启／agents-cli／Artifact／跨scope检查。CLI转换3事件而公开轨迹22事件，不能用前者冒充全部研究事件。
- pip check、compileall、CLI版本／parser和本地文档文件／锚点检查通过；未执行远端CI。

### 七项接入验收

| 项目 | 接入前→后 | 可观察证据／限制 |
|---|---|---|
| 完整轨迹／Session | 内部消费→持久脱敏投影＋所属Session接口 | 真实HTTP读取、分页／任务过滤、父关联、重启、404；仍隔离子Session及私有内容 |
| App／插件 | 分散Runner→统一工厂及覆盖 | entry／worker／decision／vision OCR均关联；MCP完成／取消关闭，SQLite按所有权关闭；账本仍单写 |
| 嵌套Runner | Worker逐行动Runner→同一Worker单请求及原生工具执行 | invocation26→15，提供者18→18、工具6→6、同一语义哈希；保留结构化业务行动适配及主／提取隔离Runner |
| 评估／工程 | 无完整识别／行为案例→manifest／package入口／5案例／总预算驱动／分层CI | 实际CLI与parser、审批及生成器合成检查通过；真实模型／judge未执行，grade受ADC限制 |
| MCP | 每调用重连→原生连接池／冻结工具集 | 两次RPC进程2→1，实际并发／超时／取消／schema改变拒绝／PID退出；分页及ADC等适配增加代码 |
| Artifact | 仅业务文件→原生只读标准接口 | 版本／业务版本／sha256、原字节一致、重启、scope、缺失及写403；源覆盖可使旧版本不可读，无复制权威 |
| Skills／上下文 | 全量指令→显式原生只读试点；重复收据→固定引用 | 大目录文本减少，小目录／额外调用退化；默认eager保留。全文仍可读，真实token／费用／行为未知 |

### 最终测量解释

[最终顺序5样本](measure-delivery-sequential.json)再次确认所有语义哈希为`b72cdde2df0f6fc4ff02b9067528827de50db26c3f7ebb6d4b14b770b739f3f1`，提供者／模型18、工具6、invocation15、决策invocation7、轨迹36038字节及请求约677327字节；本机中位0.4480秒。之前阶段中位0.3870秒仍是当时实测；最终与基线0.4640秒的差距很小，样本少、时段不同且没有置信区间，**不认定整体延迟稳定下降**。首次最终采样与CLI并发，未用作延迟结论，改为上述单独顺序复测。

实质改善是外部可查的内部轨迹、Worker少11个生命周期、MCP同任务连接2→1及标准产物读取。其他维度的确定性检查未发现不可接受退化；真实行为、网络延迟、提供者token与费用仍未知，维护适配代码增加，不能证明全面Pareto或研究质量提高。Skills明显退化的配置保持显式试点，不默认推广；不存在“必然降低模型费用”的结论。

### 未执行与保留范围

真实模型／评估服务未执行：[计划输出](live-eval-not-executed.json)明确not_executed。没有批准具体新增模型或judge预算；现有凭据及研究默认预算不自动授权评估。本机MCP／HTTP为实际受控服务；最初合成检查曾误触默认公开搜索并已中止，不算真实外部研究成功。没有启用停用服务，未知公网及服务结果不充当验收。

MemoryService未新增适配：原生通用Session搜索没有截止／证券／固定修订／核验许可，已有read_knowledge提供这些业务能力；没有已测收益，不增加第二套存储或自动长期要求。ContextCacheConfig／Session压缩未启用：LiteLlm有提示接口，但当前Worker单请求没有cache_config，提供者支持／命中／费用／延迟未知；压缩Session也不会缩小显式task＋local，且摘要增加模型费用。保留固定引用和全文读取作为已测方案。

原生resume／工具确认未替代业务恢复：固定版本声明at-least-once、临时状态会丢失，尚无跨费用／知识／外部RPC幂等事务证明。现有显式确认和恢复检查、MCP中断停止保证保留；旧未记录中断不能事后证明幂等性。整体主编排不强制迁移Workflow，OpenAPI／A2A／云部署／后台触发未纳入范围。

### Git与复现

本任务已验证阶段提交：`4f93afe`、`96d7e95`、`ff1fef0`、`28cfcef`、`26396a8`、`9757422`、`cc7c05a`；最终收尾提交的实际hash在交付消息及automation memory中记录。同期品牌／CLI提交属于其他任务并原样保留，未混入本任务提交。用户材料、凭据、my_agent、output、tmp、交付物及公开财报目录未stage。未push、merge、打tag、部署或发布。

当前指南、ADR0002、AGENTS及Unreleased已同步。版本日志与JSON为明确的受控夹具证据；运行／资料文件仍在本机临时或独立目录，不随源码提供。测量脚本可生成新证据，旧checkout须按记录中的真实提交自行导出。完整阶段日志、首次失败及最终通过分别保留。
