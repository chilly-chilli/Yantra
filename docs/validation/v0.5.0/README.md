# v0.5.0 宿主集成验证与交付

日期：2026-10-15（Asia/Shanghai）。范围：CLI＋主要 Skill＋本地 stdio MCP，固定 ADK 2.11.0／MCP SDK 1.30.0。版本为 0.5.0 Unreleased，没有 push、合并、部署或发布。操作契约见[宿主指南](../../HOSTS.md)，设计取舍见[ADR 0003](../../decisions/0003-local-host-lifecycle.md)。

最终安装包的OpenCode原生run闭环现已补充通过，见[后续宿主验收](final-host-2026-10-15/README.md)；下文首次阶段的serve限制仍保留为历史记录。

后续逐项契约审计及修正另见[2026-10-15补充记录](contract-audit-2026-10-15/README.md)。下表保留首次交付阶段的原始数量和限制，不把后续结果覆盖成此前全绿。

## 结果与验收边界

宿主实现、隔离安装运行与定向检查已完成；**最终全量回归没有全部通过**：278项中274项通过，4项因当前沙箱禁止 localhost 端口绑定而报 PermissionError。末次新增宿主检查为16项全部通过；不能将不同阶段的重叠测试数量相加成全量通过。

| 检查层 | 实际结果 | 证据 |
|---|---|---|
| 完整离线回归 | 278项，252.349秒；274通过、4个环境错误 | [完整日志](full-regression.log) |
| 最终宿主确定性／实际子进程／stdio | 16项，全部通过；包含真实 SIGKILL／恢复和跨连接执行 | [宿主日志](host-integration.log) |
| 直接CLI兼容 | 18项全部通过，71.059秒 | [CLI日志](cli-regression.log) |
| ADK发现／隔离结构 | 6项全部通过，1.967秒 | [ADK日志](adk-structure.log) |
| 投资授权及研究逻辑 | 23项全部通过，23.920秒 | [业务回归](investment-regression.log) |
| 估值及核验 | 16项全部通过，25.067秒 | [估值回归](valuation-regression.log) |
| 最终安装包 | 隔离venv、依赖检查、跨目录CLI＋实际stdio闭环及Skill导出通过 | [安装结果](installation.json)、[构建来源](installation-provenance.json) |
| Codex实际宿主 | 最终0.5.0安装包，真实app-server直接工具调用；禁用模型shell工具；闭环通过 | [Codex摘要](codex.json) |
| OpenCode实际宿主 | 实现阶段源码，真实serve＋localhost确定性模型；bash/edit拒绝；闭环通过 | [OpenCode摘要](opencode.json) |
| Skill静态检查 | quick_validate通过；两宿主发现，OpenCode实际加载 | [分发Skill](../../../yantra/host_skill/yantra-research/SKILL.md) |
| 自然语言研究／真实金融资料／收费模型／judge | **未执行** | 无相应凭据、服务范围及总费用授权；不得由离线结果推断通过 |

全量的四个受限项目：`test_http_redirect_cannot_forward_declared_credentials`、`test_http_reuses_session_and_closes_client_without_stopping_service`、`test_streamable_http_mcp`、`test_model_list_probe_has_no_inference_call`。均在 `socket.bind(127.0.0.1, ...)` 建测试服务时失败，没有进入待测HTTP业务。它们在先前276项运行阶段通过；那个阶段整体仍因新宿主测试继承旧环境变量而1失败9错误，不能称全绿。修复测试存储隔离后，当前完整运行只剩这四项环境错误。没有关闭保护或把它们改成skip。

待允许本地测试端口的执行环境中，重新运行完整离线入口，并复核最终安装包的 OpenCode serve 场景。本记录不把这些待复验项写成已完成；真实自然语言模型理解、概括和投资质量也仍未知。

## 可复验命令

从仓库根目录执行，不读取用户凭据、不启动真实研究：

```sh
.venv/bin/python scripts/run_offline_tests.py
.venv/bin/python scripts/run_offline_tests.py test_host_integration.py
.venv/bin/python scripts/run_offline_tests.py test_cli_contract.py
.venv/bin/python scripts/run_offline_tests.py test_adk_structure.py
.venv/bin/python scripts/run_offline_tests.py test_investment_logic.py
.venv/bin/python scripts/run_offline_tests.py test_valuation.py
git diff --check
```

离线入口阻断未模拟的外网；新增子进程测试另注入只允许loopback的测试sitecustomize，并检查没有外网尝试。stdio是真实协议和进程，不是直接调用函数替代；业务模型／资料仍为明确虚构fixture。旧CLI同步执行、查询失败研究成功、取消语义和ADK所属Session隔离保留。

## 生命周期、版本和错误行为

- 顺序／6线程并发／丢失响应重试只创建一个run；同键不同请求或配置位置拒绝。幂等只覆盖本地提交，不宣称收费操作exactly-once。
- CLI提交进程退出、MCP断开／重连后可查询；实际SIGKILL后执行锁释放，查询报告interrupted。显式恢复使用原run与限制，不把旧PID当成正在运行的证明。
- 原始研究继续保留截止和已预留费用；初次执行启动失败后，正式研究也使用最初接受时间计算截止。过期拒绝、未知in_flight/in_flight_tool不自动重放。
- CLI和MCP交替操作同一控制器，查询的预算一致。身份／参数响应绑定revision及pending_id；陈旧修订在控制器锁内再查，重复操作返回原收据。状态已写入后报错保留outcome_unknown，不伪称拒绝或安全可重试。
- 修改0.05→0.04形成新假设及报告代次，原代次哈希仍可核对。只读支持分片；篡改、缺失、任意路径、越界run symlink和未映射ADK Session拒绝。
- waiting任务取消先返回请求接受，执行者收束后查询为cancelled，旧取消任务不能resume。partial／暂定／核验限制通过结构化资格保留，查询错误与研究状态分开。
- 缺依赖启动返回安全dependency_missing；doctor不联网，命令权限是宿主报告值。无命令工具但配置MCP可降级，不要求复制凭据到对话。

## 安装验收

在 `/tmp/yantra-v05-install/env` 创建独立venv，以本地wheelhouse安装95项运行依赖及Yantra；没有system-site-packages，不向开发仓库设置PYTHONPATH。固定ADK、MCP、Pydantic、LiteLlm与SQLAlchemy版本见[结果](installation.json)。从 `/` 与 `/tmp` 调用已安装 `yantra`，完成schema发现、提交、跨调用查询、确认、读v1、MCP修改、断开后CLI查询、读v2及再次读v1；模型为固定离线演示策略。`pip check` 输出 `No broken requirements found.`，Skill从wheel内导出到新目录，既有目录被明确拒绝覆盖。

构建使用系统Python的既有setuptools（开发.venv缺构建后端）：

```sh
python3 -m pip wheel --no-index --no-deps --no-build-isolation --no-cache-dir . --wheel-dir /tmp/yantra-v05-install/wheels
```

**依赖来源限制**：离线缓存缺少SQLAlchemy 2.1.4、greenlet 3.5.6、jsonpath 0.82.2的原始发行包；公网请求受当前不可用代理限制。为验证隔离运行，这三项使用本机已安装内容，逐文件校验原RECORD哈希后重打包到临时wheelhouse，其余使用原缓存wheel或缓存中的已解压wheel。详细[快照来源](dependency-snapshots.json)明确标识，未升级／替换版本。这证明该隔离依赖组合中的安装及运行，不证明普通用户从公网全新下载完整固定依赖可行；联网干净安装仍需复验。重打包依赖、wheelhouse及环境均未提交到仓库。

## 两宿主实际操作

Codex 0.162.0-alpha.17.2：通过本机导出app-server schema核验，`.agents/skills`被发现，`features.shell_tool=false`时仍能发现并直接调用MCP。最终安装包实际完成submit→waiting→confirm→partial/report v1→modify→partial/report v2→再次读v1→list；旧版哈希保持不变。没有调用turn/start或宿主模型，因此不证明模型自主选工具或概括准确性。

OpenCode 1.18.33：隔离项目／XDG配置，实际发现并加载同一Skill；bash/edit=deny，MCP单独允许。真实serve的工具链由localhost OpenAI-compatible确定性替身驱动，完成同样研究、确认、两版读取及修改，30次本地替身请求，没有真实推理费用。结果保留partial/unverified及四项资料缺口。该执行发生在版本号尚为0.4.2的v0.5实现阶段；最终安装版在当前禁端口沙箱中未重新跑serve，版本字段和阶段不改写。

安装方式依据当时实际读取的[OpenAI MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Skills](https://learn.chatgpt.com/docs/build-skills)、[OpenCode MCP](https://opencode.ai/docs/mcp-servers/)、[Skills](https://opencode.ai/docs/skills/)、[Permissions](https://opencode.ai/docs/permissions/)，本机实际接口优先。只写临时配置，没有修改用户已有宿主设置，也不把user/session字段当作远程认证。

## 评估与本地证据

[十项只读追问题集](../../../tests/eval/host_readonly.xml)已解析并依据受控DEMO两版报告核对答案；没有运行真实模型／judge评分，不能作为质量分数。原有[真实评估审批入口](../../../tests/eval/README.md)仍要求明确模型、案例和总预算。

完整临时宿主trace、驱动、离线合成研究、依赖缓存和安装环境位于 `/tmp/yantra-host-acceptance/` 与 `/tmp/yantra-v05-install/`，本轮机器可读，可能被系统清理，不随克隆分发。仓库只保留经审阅的日志／摘要及原trace哈希；没有纳入用户财报、凭据、原工作区运行输出或其他任务文档迁移。最初工作区基线保存在 `/tmp/yantra-v05-baseline/`。
