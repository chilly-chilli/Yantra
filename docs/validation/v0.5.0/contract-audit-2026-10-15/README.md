# v0.5 宿主契约逐项审计与补充验证

日期：2026-10-15（Asia/Shanghai）。范围来源为用户授权的 `docs/prompts/v0.5-host-integration.md`；下面按其章节和最低场景核对。以 `e2f3868` 为本轮基线，保留[首次交付记录](../README.md)的原始结果，不将历史失败改成成功。

## 本轮发现与修正

- 查询原来只有业务状态，没有明确的阶段与可执行后续操作。现从持久任务／结果、in_flight 和执行锁观察投影 `progress`；没有运行中收据时，不把一条未完成任务称为正在执行。`next_actions` 给出回应、取消、恢复及当前 revision/pending_id，仍不是授权。
- 取消发生在首次state保存之前时，原预算检查只认运行目录的cancel.request，仍能预留准备费用。受控反例在旧实现预留0.25元；现提交目录的取消意图也进入原RunBudget的预留前检查，没有外部调用。没有执行者的已取消意图直接收束；未确认启动就已退出的执行者可显式恢复。
- 旧安装包中，只有定性证据的正式研究state为partial，写出的报告／不可变资格为failed。反例实际复现了两种状态；现先作最终状态判定，再统一写报告及固定资格，未改变估值或核验门槛。
- 宿主配置现在保留 `YANTRA_CONFIG`、`YANTRA_RUNS_DIR`、`YANTRA_KNOWLEDGE_DIR` 语义，显式参数优先；新增宿主 `--knowledge-dir`，执行者取得同一绝对路径，没有搬迁或合并凭据／知识。

## 要求与权威证据

“已验证”仅覆盖表中范围；受控数据、实际本机进程、真实协议和真实模型分开。代码链接是实现依据，行为结论以测试或已记录的实际调用佐证。

| 提示词要求 | 当前权威证据与结论 |
|---|---|
| §1、§2 CLI＋一个主要Skill＋本地MCP，同版、无GUI | [共享入口](../../../../yantra/host_service.py)、[CLI](../../../../yantra/cli.py)、[分发Skill](../../../../yantra/host_skill/yantra-research/SKILL.md)、[stdio服务](../../../../yantra/host_mcp.py)及安装闭环；没有GUI／远端平台。 |
| §1 研究提交、离开调用、查询、阅读、修改、差异闭环 | 实际CLI退出后，以仍存活的子进程PID和执行锁证明独立运行，再放行完成；真实stdio重连、两版读取及修改收据测试。Codex实际调用见阶段记录；最终OpenCode安装版仍待复验。 |
| §1、§5 保留模型、业务授权、独立核验与单一费用账本 | 原RawEngine／RunBudget及ADK工厂继续执行；新入口不结算费用。业务回归23项、正式研究7项、宿主未知结果／过期约束检查。没有复用宿主订阅。 |
| §4.1 命令、路径和目录写入检查 | 实际CLI、临时研究目录、wheel安装和持久文件；原工作区已有改动保留。宿主工具是否存在由调用者真实报告。 |
| §4.2 区分命令缺失／拒绝、入口缺失、依赖、路径及配置 | doctor路由、dependency_missing、execution_permission_denied、executable_missing等分类；非TTY、缺依赖进程、不可见材料、拒绝启动和脱敏检查。 |
| §4.3 本地诊断、网络及真实服务分别说明 | doctor无网络／模型调用；公网当前代理连接失败。真实账户、资料覆盖、研究模型和judge未执行，不能宣称就绪。 |
| §4.4 共享本地路径，不能假设远端路径可见 | 路径规范化及material_path_invisible；跨工作目录CLI和已安装包检查。容器／远端／自动上传未验证且未承诺。 |
| §4.5、§6 调用结束后执行者存活 | 强制挂起的实际worker在提交CLI已退出后仍有存活PID，再继续完成；stdio关闭不取消，SIGKILL后发现中断。 |
| §4.6 有CLI／只有MCP／两者皆无三种接入 | doctor route字段、Skill指引；Codex禁shell与OpenCode禁bash/edit的实际MCP证据。未提供工具时给接入步骤。 |
| §4.7 权限不是收费／发布授权 | 本轮遵守当前端口限制，没有尝试绕过保护；没有收费推理、push、合并、部署或发布。 |
| §5 同一schema、包装、错误及接口兼容 | [Pydantic输入](../../../../yantra/host_contract.py)、工具发现schema解析、真实协议业务错误、CLI兼容18项。新增输出字段与服务器路径选项，未改变包装版本1。 |
| §5 查询阶段、进度、缺口、预算、待办及后续动作 | 新增progress和受约束next_actions，观察字段不写业务状态、不产生百分比；集成测试等待阶段和运行中任务判定。 |
| §5 failed/partial查询仍成功，MCP业务错误分开 | 真实stdio创建实际failed合成研究后成功查询，isError=false；无run／无产物／校验失败为isError=true。 |
| §5、§9 版本／授权资格与正文一致 | 旧安装包定性partial反例及修复检查；不可变输出固定资格、假设版本、原文及hash。否决和暂定语义不被摘要隐藏。 |
| §6 先核对ADK 2.11.0，例外有理由与行为测试 | [ADR 0003](../../../decisions/0003-local-host-lifecycle.md)记录进程存活及Runner之前的准备费用检查缺口；复用原App／插件，无ADK升级。 |
| §6 接受、启动、运行、失败和取消分开 | 提交收据、execution字段、启动拒绝、启动确认前退出、挂起存活、SIGKILL／恢复与取消边界检查。 |
| §6 幂等作用域、摘要、保留；并发及丢响应 | SQLite研究目录作用域；规范化请求、配置／知识路径和环境文件位置摘要；顺序／6线程／重试／冲突测试。不宣称外部调用exactly-once。 |
| §6 持久记录和跨重启预算／未知操作约束 | 真实进程级中断、独立CLI／MCP重连和固定账本／截止检查；未知外部操作与不确定修改禁止自动重放。没有进行物理机器断电测试或开机自动恢复。 |
| §6 单一写入者、并发修改、重复响应 | 宿主执行锁＋原Engine锁，revision在业务锁内复查，operation_id持久去重，旧pending／revision明确拒绝。 |
| §6 原预算、原截止、已取消兼容 | 过期正式研究不重置预留账本，初次接受时间固定；取消后不能恢复；新增材料使用新的关联研究。 |
| §7 包、资源、干净环境及跨目录 | 独立venv安装wheel及实际CLI/MCP闭环；Skill随包导出。三项依赖来自已校验的安装快照，普通公网干净下载仍未验证。 |
| §7 配置、研究、知识、凭据位置和旧默认 | [宿主指南](../../../HOSTS.md)的显式参数／环境变量优先级；测试环境选择及跨目录固定路径。原直接CLI默认值保留，没有自动扫描迁移。 |
| §7 结构化配置、宿主引导、非TTY | 原models/extensions结构化入口与主要Skill；原CLI18项兼容，host输入不读菜单；本地凭据值不进入响应。 |
| §7 Yantra与宿主账单区分、已有授权复用 | Skill／doctor明确账单边界；原偏好快照及拒绝／撤销规则由原引擎读取。没有第二套永久授权。 |
| §8.1 主要Skill全流程及限制保留 | 同一Skill覆盖全部九类操作；静态验证、两宿主发现／OpenCode加载与阶段实际调用。真实自然语言模型是否遵守指令未评估。 |
| §8.1 不继承任意宿主扩展权限 | 本轮不改原角色／父权限／冻结扩展实现；Skill明确来源和工具结果不是授权。现有业务／ADK回归作为原门槛证据。 |
| §8.2 固定SDK stdio发现、输入、结果及错误 | SDK 1.30.0真实stdio初始化、九工具发现、校验、错误、重连；服务只适配共享业务入口。 |
| §8.2 断会话不取消、不隐式恢复、获准产物读取 | 实际断连和受控worker存活；读取只接受已索引产物，有限字节、哈希、缺失、篡改、路径和run symlink拒绝。 |
| §8.2 本地信任和所属ADK Session | 固定本地工作区；旧已绑定ADK记录没有host映射时拒绝；user/session字符串不是认证。 |
| §8.3 两宿主版本、安装、权限和两条路径 | 实际版本仍为Codex 0.162.0-alpha.17.2／OpenCode 1.18.33。旧记录包含Skill发现、命令权限及MCP降级；只改临时配置。最终OpenCode安装包serve复验受端口限制。 |
| §9 追问原文、推断和假设分别表达 | read按固定输出version定位原文evidence与解析anchors；Skill要求分开叙述；没有静默换源或把新搜索替换旧字节。 |
| §9 假设修改重算、失效、核验和差异收据 | 实际两代demo与CLI/MCP修改，参数前后差异、原／新假设和输出版本、重复操作、旧hash保留。真实模型重新核验质量未验证。 |
| §9 新财报及长期偏好 | parent_run_id和明确新提交流程已固定；resume不收材料；单次参数不写长期偏好。 |
| §10 场景1 | 顺序、并发、重试／丢响应及冲突检查已验证。 |
| §10 场景2 | CLI退出后worker存活、启动前死亡、SIGKILL、独立重连、预算／截止和未知操作检查已验证，物理断电未执行。 |
| §10 场景3 | 真实CLI/MCP交替操作，实际账本一致；参数锁和越界拒绝。 |
| §10 场景4 | 实际failed研究通过stdio成功查询，partial资格、bounded read和取消请求／实际停止分别检查。 |
| §10 场景5 | 过期、陈旧修订、重复操作、身份选择绑定pending及原预算；拒绝语义沿用业务回归，不由Skill授权。 |
| §10 场景6 | 指定版本、原／新hash、缺失／篡改／越界及分片完整性检查。 |
| §10 场景7 | 本地诊断分类、缺依赖、命令拒绝／无工具与MCP降级、不可见路径检查及阶段宿主实际调用。 |
| §10 场景8 | 两宿主阶段离线闭环已有证据；本次补充实现后的最终OpenCode安装包尚未复验，不能宣布全项验收。 |
| §10 分层证据及真实调用授权 | 确定性、子进程、真实stdio、实际宿主配离线替身分开记录。没有真实资料／收费模型／judge范围和总预算，均未执行。 |
| §11 文档、Unreleased、日志及提交范围 | 当前指南、ADR、Skill和本记录同步；保护其他任务的文档迁移／用户材料；本地提交前diff检查，未经授权不push。 |

## 本轮检查及限制

本轮末次宿主检查22项全部通过（含身份回应和实际failed研究查询）；正式研究7项、授权业务23项通过。完整回归286项，282通过，四项仍因localhost端口绑定被拒绝而报错。

本轮更新后的独立安装包通过CLI/MCP闭环；Codex实际app-server闭环及新版progress字段通过。OpenCode当前安装包的stdio MCP连接、主要Skill加载和bash拒绝检查通过；完整serve闭环仍受端口限制。另试原生debug/run入口：debug不能调用该MCP工具，run可到达本地替身provider，但进程内替身插件初始化未完成，尚未形成完整闭环；[探测摘要](opencode-native-entry.json)保留失败及超时边界。OpenCode探测脚本首次以大写Connected匹配实际小写connected而报断言错误，宿主连接命令当时已退出0；修正探测脚本后重验通过，原输出保留在opencode-probe-first.log。

本目录日志保留实际执行结果。宿主末次定向检查包含本轮新增身份回应和failed查询场景；完整回归在新增该身份用例前启动，因此不能将两者数量相加，也不能称其为当前全部用例通过。

- [audit-host.log](audit-host.log)：宿主确定性、实际进程及stdio。
- [audit-formal.log](audit-formal.log)、[audit-business.log](audit-business.log)：原正式研究及授权逻辑回归。
- [audit-full.log](audit-full.log)：完整离线回归，四个localhost HTTP fixture仍受当前sandbox的socket.bind限制。
- [codex.json](codex.json)：最终安装包的实际app-server／stdio闭环，禁用shell、未启动模型turn，固定版本1→2→1的原字节哈希验证通过。
- [opencode-local.json](opencode-local.json)、[原生入口探测](opencode-native-entry.json)：实际本地接入检查与未完成的完整闭环边界。
- [installation.json](installation.json)／[installation-provenance.json](installation-provenance.json)：本轮wheel和独立安装闭环，不改变原依赖来源限制。

当前仍无法完成：四项HTTP测试／最终OpenCode serve的本地端口复验，及公网索引的真正全新依赖下载。实际bind预检仍返回PermissionError，既有代理127.0.0.1:7890仍无法连接。没有通过其他执行表面绕过限制，goal不标complete。原始复现及临时驱动位于`/tmp/yantra-v05-*`，可能被系统清理；本目录只保留受控证据和摘要。
