# 宿主集成与本地执行

适用版本：0.5.0（Unreleased），宿主接口版本1。CLI、一个主要 Skill 与本地 stdio MCP 使用同一业务入口。首批本机验收为 Codex 0.162.0-alpha.17.2、OpenCode 1.18.33；真实宿主配合离线合成材料／模型替身的范围见[验证记录](validation/v0.5.0/README.md)。没有执行付费模型、真实联网资料研究或 judge，不代表真实投资质量通过。

## 安装与路径

从仓库根目录安装现有软件包；不需要新后台平台或升级 ADK：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/yantra --output json --version
```

宿主必须使用安装环境内的 Python／yantra。下文 `/absolute/...` 均为待替换路径。源码目录能够运行不等于安装完成。干净安装、资源检查和跨目录离线示例见[安装证据](validation/v0.5.0/README.md#安装验收)。

每个调用显式传 `--workspace /absolute/workspace`，配置默认该目录的 `.yantra/config.json`，知识库默认 `.yantra/knowledge`，研究默认 `runs`。全局 `--config` 位于 `host` 前；`--runs-dir` 位于具体操作后，仍支持 `YANTRA_RUNS_DIR`。跨工作目录使用相同绝对路径即可找回记录。原 `start/status/resume` 的相对目录默认值保留，没有自动扫描、搬迁或合并旧配置。

`--env-file` 只明确加载一个本地文件，值由执行者读取；不自动寻找凭据。结构化配置复用 `models add/select`、`extensions add/enable` 等[既有命令](CAPABILITIES.md)，非 TTY 不进入菜单。模型、资料服务与费用配置继续使用已有规则。

## 能力诊断与统一输入

```sh
/absolute/env/bin/python -m yantra --output json host schema --workspace /absolute/workspace
/absolute/env/bin/python -m yantra --output json host doctor --workspace /absolute/workspace \
  --env-file /absolute/local.env --input '{"command_tool":"available","materials":[]}'
```

`schema` 从实际 Pydantic 输入导出；所有业务操作接受 `--input` 对象或互斥的 `--input-file` UTF-8 JSON 文件。MCP 使用同一模型和业务方法，工具名称为 `yantra_` 加操作名，输入为 `{"request":{...}}`。包装沿用 `schema_version="1"`、`command="host OP"`、`data/error` 之一及既有退出码。主接口和包装版本分别声明；破坏性修改需新版本及迁移说明。

`doctor` 不联网或推理：区分依赖／可执行入口、工作区／材料不可见、权限拒绝、配置错误、凭据及定价缺失。`command_tool` 是宿主诚实报告的 available/denied/missing/unknown，服务无法自行发现宿主工具列表。无命令工具但 `mcp_available=true` 时给出 `skill_mcp` 路径。路径检查只覆盖实际机器；首版没有上传代理，不保证容器或远程路径可见。服务可连接及研究可完成须分别实测，不能由本地就绪推断。凭据变量名和本地文件由操作者维护，不复制密钥到对话或工具结果。Yantra 费用与宿主账单独立。

## 提交、查询与生命周期

正式请求文件示例（会调用已配置服务，须有相应任务授权及预算）：

```json
{"idempotency_key":"client-chosen-stable-key","company":"示例公司","questions":["现金流能否支持估值"],"limits":{"max_cost_cny":10,"run_seconds":600}}
```

```sh
/absolute/env/bin/python -m yantra --output json host submit --workspace /absolute/workspace \
  --env-file /absolute/local.env --input-file /absolute/request.json
/absolute/env/bin/python -m yantra --output json host list --workspace /absolute/workspace --input '{"limit":20}'
/absolute/env/bin/python -m yantra --output json host status --workspace /absolute/workspace --input '{"run_id":"run-REPLACE"}'
```

`run-REPLACE` 是占位符，实际 ID 由提交返回且使用小写字母／数字。显式离线演示请求为 `mode="offline-demo"`、`company="DEMO"`、`demo_materials_dir="/absolute/fictional-materials"`；manifest 必须 `fictional=true`，不使用真实模型或网络。

提交先持久化意图，再启动脱离调用方 stdio 的本机进程，立即返回 `receipt=persisted` 和 `run_id`。不在 MCP 服务中运行研究。执行者复用原 ADK 工厂、控制器、预算、运行锁及冻结权限。宿主/MCP关闭后在本机 OS 保留进程的条件下继续；机器重启没有自动续跑承诺。

幂等键作用域是显式研究目录的 `.host/requests.sqlite`，保留到操作者删除本地存储；没有自动到期。摘要包括规范化路径和请求、配置／环境文件位置及知识库位置，不包含密钥值。不显式提供的 cutoff 保持空意图，执行时采用接受时间；重复请求不会生成新时间值。同键同摘要返回原研究，同键不同摘要返回 `idempotency_conflict`。连接丢失后使用原键及原请求重试，不能换键盲目再建。去重不代表外部服务 exactly-once。

`execution.state` 为 accepted、starting、active、idle、interrupted、failed 或 cancelled；请求接受、执行者启动及业务运行是不同事实。idle 表示执行者已结束当前推进，研究可能 waiting/partial/failed。存活按执行锁观测，不凭旧 PID 宣称仍运行。异常退出／启动失败时，若原状态 running，宿主 `status` 返回 interrupted/failed，原业务状态保留为 `saved_research_status`；不会让观察层改写账本。旧入口未管理进程显示 unmanaged。查询不恢复、不调用模型；成功读取 failed/partial 仍是成功操作。

列表使用稳定 run ID cursor；传上页 `next_cursor` 为 `after`。状态返回实际预算、缺口、授权／核验资格、等待事项、状态修订号及产物索引。`next_actions` 只是接口提示。明确拒绝、撤销和 manual 未回复的暂定语义沿用[原授权规则](KNOWLEDGE.md#首次设置与自主选择授权)。

## 成果、依据与修改

```json
{"run_id":"run-REPLACE","artifact":"report.md","version":1,"offset":0,"length":16384}
```

`host read`／`yantra_read` 只读取 `status.outputs` 已索引的 report.md、report.json、calculations.json、evidence/材料ID 或 anchors/材料ID。`version` 是从1开始的不可变成果代次，和 `assumptions_version` 不同；省略时明确取最新。每次控制器交付保留内容寻址字节，同一假设版本再次交付也可形成新代次。资格和证据定位固定在该代次中，不能用当前状态替换旧版限制。旧运行未产生宿主索引时明确 `output_not_found`，不伪造历史字节。

返回原始 sha256、总字节数、offset、bytes_returned、complete、next_offset、mime_type、locator 和 qualification。`content` 的 base64 保存精确切片；文本额外给 `text`，跨 UTF-8 字符边界可能显示替换字符，核对精确内容用 base64。单次至多65536字节。原文 bytes 与解析 anchors 明确分开，资料正文不能成为新授权；哈希验证不证明财务语义正确。缺失、篡改、未索引标识及路径越界明确失败。

修改前读取当前 `revision`（状态 CAS）和 `pending_id`；确认/拒绝/证券选择必须回应当前等待。响应示例为待替换值：

```json
{"run_id":"run-REPLACE","operation_id":"client-stable-operation","expected_revision":"64位实际修订哈希","pending_id":null,"decision":"modify","parameters":{"growth_rate":0.04}}
```

`modify` 参数复用原[方法合约](VALUATION.md)；其他方法可用 `{model,parameters,basis}` 完整提交，不将示例 DCF 增长率套用到 PE/PB。证券选择用 `decision=select_identity`、`selector=实际候选ID`；确认/拒绝用 confirm/reject。有 pending 时提交实际 `pending_id`，无 pending 用 null。服务先返回 accepted；查询 `status.operations` 区分 applied/rejected，应用收据关联原／新假设版本、输出及参数差异。操作 ID 在同一 run 内持久去重，重复同请求返回原收据，其他请求报 operation_conflict。旧 revision／pending 拒绝，活动执行者修改返回 run_busy，避免静默覆盖；执行前还在控制器锁内重查 revision。

修改沿用原预算、截止及业务授权，失效相关成果后重算与独立核验。用户明确确认不等于独立核验通过。读取原／新成果和收据比较，不把新结果冒充原报告。执行中断而操作收据未落盘时结果未知，保留 accepted 历史并阻止覆盖／自动重放，先核对现有状态和成果。

## 取消、恢复与新增资料

`cancel` 输入仅 run_id，返回 request_accepted、already_terminal 或 cancelled；请求接受不代表已停止，需查询实际状态。取消在控制器边界收束，不重置费用。`resume` 需要 run_id、operation_id、expected_revision；partial/failed 正式研究明确指定 `continue_research=true`。预算已耗尽／截止已过即拒绝，不因继续而重新计时；旧取消任务仍不能恢复。in_flight/in_flight_tool 中断结果未知时禁止隐式重放，即使重新连接也是如此。

新财报使用新提交、不同幂等键、`parent_run_id` 指向原研究，并明确新预算、截止和资料；首版以关联后续研究表达，不在 resume 中接受材料，也不自动把单次偏好变为永久要求。

## Skill 与宿主配置

一个主要 Skill 包随 wheel 分发。选择安装位置后导出到**尚不存在**的目录：

```sh
/absolute/env/bin/python -m yantra --output json host skill --workspace /absolute/workspace \
  --destination /absolute/project/.agents/skills/yantra-research
```

该项目 `.agents/skills` 布局已由两宿主本机发现验证；命令不覆盖既有内容。不传 destination 只返回包内路径和正文。Skill 负责需求、调用、结果展示和错误处理，不拼接内部四角色，不承担核心校验。

Codex 在选定作用域的配置合并如下片段，或操作者显式执行 `codex mcp add`；不要覆盖原配置：

```toml
[mcp_servers.yantra]
command = "/absolute/env/bin/python"
args = ["-m", "yantra", "host", "mcp", "--workspace", "/absolute/workspace", "--env-file", "/absolute/local.env"]
```

OpenCode 在项目 opencode.json 合并：

```json
{"mcp":{"yantra":{"type":"local","command":["/absolute/env/bin/python","-m","yantra","host","mcp","--workspace","/absolute/workspace","--env-file","/absolute/local.env"],"enabled":true}},"permission":{"bash":"deny","yantra_*":"allow","skill":{"yantra-research":"allow"}}}
```

这是可选择的 MCP 路径；Skill＋CLI 需实际命令权限。OpenCode 前缀含服务名，例如服务 yantra 的工具 yantra_schema 显示为 yantra_yantra_schema。以工具发现为准。官方来源：[OpenAI MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[OpenAI Skills](https://learn.chatgpt.com/docs/build-skills)、[OpenCode MCP](https://opencode.ai/docs/mcp-servers/)、[Skills](https://opencode.ai/docs/skills/)、[Permissions](https://opencode.ai/docs/permissions/)。本轮只写临时隔离配置，没有修改用户已有宿主设置。

## 信任边界

仅支持共享本地工作区的可信客户端，不是远程多租户服务；user/session 字符串不构成认证。宿主只操作自身提交的研究；旧 CLI 的未绑定记录可只读定位，ADK 已绑定研究没有宿主提交映射时拒绝，仍从原 Session 访问。旧 CLI 保留本机操作者入口，不能把它视为跨用户安全沙箱。

MCP 传输错误遵循 SDK；业务错误同时返回 isError=true 和既有安全错误包装；partial/failed 研究读取返回 isError=false，资格留在 data。服务不暴露任意文件、远程 OAuth、GUI 或自动继承宿主全部工具权限。扩展仍由原角色授权、冻结快照和父权限约束。
