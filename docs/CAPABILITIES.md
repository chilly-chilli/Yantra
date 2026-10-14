# 模型与扩展配置

适用版本：0.4.0（Unreleased）。研究启动见[使用指南](USAGE.md)，embedding计费、索引与降级见[知识模块](KNOWLEDGE.md#模型费用和重建)。

这两个模块通过 CLI 与用户交互，并把已选择的能力接入 `RawEngine` 原始材料研究。运行配置位于 `.yantra/config.json`，可用环境变量 `YANTRA_CONFIG` 或命令前的 `--config /绝对路径/config.json` 指定另一个配置文件。

## 交互与命令

```sh
.venv/bin/python -m yantra configure --env-file .env
.venv/bin/python -m yantra models
.venv/bin/python -m yantra extensions
```

`configure` 可在两个模块间切换。模型菜单支持 list／add／select，扩展菜单支持 list／add／enable／disable；输入 back 返回，exit 退出配置中心。添加模型会询问模型类型、适配器、端点、凭据变量名及计费依据。添加 skills 询问目录，MCP／tools 读取 JSON 描述文件，再询问授权角色。

脚本入口支持相同操作及 check：

```sh
.venv/bin/python -m yantra models add local --file examples/v021/llm.example.json
.venv/bin/python -m yantra models select llm local
.venv/bin/python -m yantra models --env-file .env check local
.venv/bin/python -m yantra models select llm env
.venv/bin/python -m yantra models select ocr tesseract-auto
.venv/bin/python -m yantra models list
.venv/bin/python -m yantra extensions list
.venv/bin/python -m yantra extensions check mcp demo
.venv/bin/python -m yantra extensions disable mcp demo
.venv/bin/python -m yantra extensions enable mcp demo
```

管理命令的 `--env-file` 放在 `models`／`extensions` 后、子命令前。配置名称为小写字母、数字、下划线或连字符，最多 64 个字符；内置 OCR 和 `env` 名称保留。

## 模型适配范围

| 类型 | provider | 实际行为 |
|---|---|---|
| LLM | `openai-compatible` | 通过现有 LiteLlm／ADK 结构化决策路径请求用户端点 |
| embedding | `openai-compatible` | 调用兼容embedding接口，向量本地持久化；费用、时间和请求数受显式预算约束 |
| OCR | `tesseract` | 本地 Tesseract 实际识别；自动选择已安装的中英语言包 |
| OCR | `vision` | 向兼容 API 视觉模型发送 PNG，要求逐行文本及原图像素坐标 |
| OCR | `command` | 向用户命令发送图像 JSON，读取规范化 OCR JSON |
| OCR | `paddleocr-api` | 上传原图 PNG 至 PaddleOCR jobs API，轮询并读取 JSONL，保留原图版面框 |

`tesseract-auto` 与 `tesseract-eng` 可直接选择；自定义配置允许 `language` 及绝对 `data_dir`。原始材料中的扫描页及图片使用所选 OCR，普通文字型 PDF 继续直接提取。OCR 输出的数值及财务口径仍需后续核验，复杂表格关系不因更换模型而自动获得保证。

LLM／vision／embedding 和 PaddleOCR API 配置含 `model_id`、`base_url`、`api_key_env`。只有环境变量名称写入配置；字面量 `api_key` 等未知字段会被拒绝。若本地兼容服务要求任意占位密钥，也通过对应环境变量提供。LLM／vision／embedding 需符合兼容 API 及 JSON 输出要求；PaddleOCR 使用下述供应商任务 API。

`models check` 对 LLM／vision／embedding API 配置请求 `/models`，返回可用模型 ID 和所选 ID 是否存在；不发起推理。部分服务不提供此接口，检查失败不能独自证明推理不可用。Tesseract 检查版本和语言包；command 只检查命令可执行文件存在，不验证 OCR 品质。`env` 可检查当前 `MODEL_*` 配置；PaddleOCR 的本地凭据检查见下节。

LLM 人民币单价可放在 `pricing`，包含输入／输出每百万 token 单价、来源和日期；支持有依据的零费用本地推理。启动时优先采用 `--pricing`，其次所选 LLM 的 pricing，最后沿用环境单价。视觉 OCR 必须单独声明 pricing 与 `max_image_tokens`，后者须覆盖供应商的单图计费 token 上界；文字、图片上界与输出上限一起预留，按返回 usage 结算。超过声明预留会停止后续模型调用；这依赖真实计费上界，不能从模板数值推断供应商保证。

### PaddleOCR 任务 API

v0.3.3 增加 `provider=paddleocr-api`，使用 [AI Studio 官方 jobs 协议](https://ai.baidu.com/ai-doc/AISTUDIO/Mml7n69e7)，默认模型 `PaddleOCR-VL-1.6`。本地令牌与 LLM、资料服务配置统一填入仓库根目录 `.env`，Paddle 使用 `PADDLEOCR_API_TOKEN`，填原始令牌，不加 `Bearer`。CLI 须显式 `--env-file .env` 加载；统一凭据文件与 ADK 查找规则见[配置入口](USAGE.md#配置入口)。旧 MCP 变量按原名保留以支持旧快照，不自动替代新的 API 令牌变量。

```sh
.venv/bin/python -m yantra models add paddleocr-api --file examples/v033/paddleocr-api.example.json
.venv/bin/python -m yantra models select ocr paddleocr-api
.venv/bin/python -m yantra models --env-file .env check paddleocr-api
# 已注册旧 MCP 时停用，避免同时暴露旧 OCR 工具：
.venv/bin/python -m yantra extensions disable mcp paddleocr-vl-16
```

`check` 仅检查本地配置及令牌是否已填写，返回 `live_api_verified:false`；不会创建 OCR 任务，也不证明令牌有效。模板令牌不能执行真实 OCR。配置的 `base_url` 是完整 jobs 地址，`api_key_env` 引用变量名。`call_cost_cny` 是每张上传图像的声明费用上界，必须有 `cost_source/cost_as_of`，零费用也须说明免费配额依据。[官方配额说明](https://ai.baidu.com/ai-doc/AISTUDIO/Xmjclapam)截至核对日为每用户／模型每天 20,000 页，超额拒绝请求；示例按该免费配额声明 0 元，若账户条款变化，应先更新配置。

解析器把扫描 PDF 逐页、栅格图片作为 PNG 上传，URL 材料先在本地保存来源快照再解析。提交 multipart → 有限时轮询 pending/running → 下载签名 JSONL（单图结果上限 16 MiB）；整体受 OCR 和研究剩余时间约束，取消在请求边界检查。failed、未知状态、无效结果或超时保留资料缺口，不回退为成功。供应商任务提交后，本地停止不会自动删除或取消远端任务。

关闭方向分类、去畸变和图表识别，以保留原图坐标。`prunedResult.parsing_res_list` 的版面框会校验并转为原图 anchor；仅返回 Markdown 时只定位到整张原图并登记警告，不能声称逐行坐标或表格关系已核验。结果下载不带令牌，不额外下载派生图片。既有运行恢复仍沿用旧能力快照，API 默认只影响新运行。

### 命令 OCR

command OCR 的一次 stdin／stdout 协议：

```json
{"image_base64":"PNG 的 base64 内容","mime_type":"image/png","width":1000,"height":600,"language":"auto"}
```

```json
{"words":[{"text":"逐行原文","bbox":[10,20,400,50]}],"warnings":[]}
```

坐标为原图像素 `[左,上,右,下]`，控制器验证边界。收费适配器声明每次调用费用上界 `call_cost_cny`、`cost_source`、`cost_as_of`，无用量或账单时保留预留。`env_refs` 将命令需要的变量名映射到本地凭据变量名。

## Skills、MCP 与 tools

导入技能：

```sh
.venv/bin/python -m yantra extensions add skills cashflow \
  --path examples/v021/cashflow-skill --roles finance,verification
```

导入目录须含 `SKILL.md`，也可指定该文件；同目录资源一并复制到配置文件旁的 skills 目录。拒绝指向目录外的软链接，跳过 `.git`、`.venv`、`__pycache__` 和 `.env`。新运行再复制资源、固定内容及哈希。匹配角色的模型请求实际包含技能文本；专业角色可通过 `read_skill_resource` 读取获准的 UTF-8 文本资源。资源读取有 128 KB 上限，SKILL.md 也限 128 KB。技能脚本不会因导入而自动执行，需要独立注册为 tool。

Unreleased 支持显式只读 SkillToolset 试点；默认 `--loading eager` 保持完整指令注入。若目录较大，可导入时选择：

```sh
.venv/bin/python -m yantra extensions add skills cashflow_lazy \
  --path examples/v021/cashflow-skill --roles finance,verification --loading native
```

原生模式仅改变新运行的专业 Worker：初始输入包含获准目录、稳定技能别名和资源哈希，`list_skills`、`load_skill`、`load_skill_resource` 经现有授权和工具预算执行。加载后才加入该任务指令；资源每次核对冻结字节，目录外路径、变更后的文件及未获父权限的工具被拒绝。`scripts/` 映射为只读 `references/scripts/`，没有原生脚本执行器、动态 additional tools 或状态变量插值。主协调／提取／审查仍保持既有完整技能输入；旧运行的快照及权限不变。

原生目录给出实际资源别名：全部原始路径前置`references/`，例如原`references/a.txt`成为`references/references/a.txt`。这样根目录`a.txt`与子目录同名文件保持不同，原始路径和哈希同时保留；以目录返回的file_path读取，勿自行猜测。

新运行的 Worker 输入保留首个完整工具收据，完全相同的后续收据以首项索引／哈希引用；权威工具日志和原文读取保持完整。它不是模型生成摘要或 Session 压缩。小目录的原生加载实测增加输入及调用，使用大目录时也可能多一次决策；因此默认不切换。字节上界、调用数及本机延迟比较见[技能测量](validation/v0.4.0/README.md#第四阶段技能与上下文试点)，真实输入 token、费用和模型行为尚未测量。

角色为 `finance`、`thesis`、`valuation`、`verification`；skills 还可用于 `main` 的决策指令。默认启用范围是 finance／thesis／verification。MCP／tools 由专业角色调用，不扩展主 Agent 的行动合约；第三层仍限于父任务授权工具。技能不会改变核验否决、确定性计算或预算边界。

MCP 通过固定 ADK 2.11.0 的 `McpToolset` 生命周期和官方 Python SDK 1.30.0 连接 stdio 或 Streamable HTTP：

```sh
.venv/bin/python -m yantra extensions add mcp demo \
  --file examples/v021/mcp.example.json --roles finance
.venv/bin/python -m yantra extensions check mcp demo
```

注册时实际初始化服务并分页读取工具描述和 input schema；check 会重新发现并更新供新研究使用的描述。HTTP 配置形状如下，`headers_env` 引用完整请求头值（例如 `Bearer ...`）：

```json
{"transport":"streamable-http","url":"http://127.0.0.1:8000/mcp","headers_env":{"Authorization":"MCP_AUTHORIZATION"},"roles":["finance"]}
```

stdio 的 `command` 是参数数组，`env_refs` 可传入服务要求的变量。当前范围为 MCP tools 的发现与调用；未实现 MCP prompts／resources、OAuth 登录或后台常驻服务。Unreleased 在同一专业任务内复用原生连接，任务完成／失败或 Runner 关闭时释放；第三层不会关闭父任务的连接。HTTP 仅关闭客户端及 MCP Session，不停止远端服务。直接使用 `ExtensionRuntime` 时应 `async with` 或显式 `await runtime.close()`。

运行仍使用冻结的名字、描述及 input schema；每次调用分页读取定义并比对，新工具被过滤、旧定义改变则拒绝，不更新旧运行权限。工具使用安全别名，不把同名 RPC 当作 ADK Agent 转派。MCP UI 元数据仅作为数据，不能修改 ADK 事件动作；无脚本、资源或 OAuth 权限的隐式扩展。HTTP 不跟随重定向，不自动探测 ADC；凭据只来自 `env_refs`／`headers_env`。原生连接／工具错误日志在此运行上下文中抑制服务详情，响应按已引用凭据脱敏且限制1 MB，业务错误保留安全类别。

命令工具：

```sh
.venv/bin/python -m yantra extensions add tools echo \
  --file examples/v021/tool.example.json --roles finance
```

tool 描述包括 `description`、`command`、`input_schema`、roles 和可选 env_refs。输入经 JSON Schema 校验后以一个 JSON 对象写入 stdin；命令向 stdout 输出一个 JSON 对象，日志应写 stderr。以参数数组启动，不通过 shell 插值；不是对用户程序的进程沙箱。最多接收 1 MB 输出，超时会终止所启动的命令进程组。只导入你打算授权执行的程序和服务。

扩展 timeout 默认 30 秒，至多 120 秒，执行时还受任务及全局剩余时间约束。工具调用计入原有工具限额；`cost_kind=model` 的扩展额外计入模型调用次数及共享人民币费用账本。`cost_cny` 是每次调用声明上界，非零须有 cost_source／cost_as_of。`cost_kind=service` 的声明费用单独统计，不冒充供应商账单。即使调用失败，已预留的费用也保留。

成功的 MCP／tool JSON 响应经所引用凭据脱敏后保存为原始扩展来源，附哈希和引用位置；后续核验可检查返回内容。这里固定的是SDK响应结构的脱敏JSON字节，不是完整网络帧。保存调用凭证不等于第三方数据已被独立证实，也不产生估值计算凭证。接口不覆盖已有财务计算工具，扩展名会加命名空间。

外部调用开始前持久化 `in_flight_tool`，已记录结果后才清除。若进程中断或调用取消，恢复会保留预留费用、调用次数及原截止并停止，不自动重放结果未知的外部操作；轨迹标为 interrupted。这是现有业务恢复的补强，不启用原生 at-least-once resume。旧版本未记录的中断不能事后证明幂等性。每次实时定义核对增加metadata请求，实际服务若对发现计费，应将其包含在声明费用上界；真实网络费用和延迟尚未测量。验收见[v0.4.0记录](validation/v0.4.0/README.md)。

## 信息获取扩展

v0.3.3 提供扶摇 REST、东方财富妙想金融数据／资讯搜索直接 API、AKShare 查询和 98dou 财联社聚合工具，沿用 tools 的角色授权、超时和原始响应凭证。接口、Key 申请、可移植注册命令及费用声明见[信息来源配置](INFORMATION_SOURCES.md)。这些工具是命令扩展，妙想无需技能包；订阅／轮询仍未实现，第三方结果仍须独立核验。

## 新建与恢复

```sh
.venv/bin/python -m yantra start --company 示例公司 --market CN --security-code 600000 \
  --material /绝对路径/原始报告.pdf --mode real-model \
  --llm local --ocr tesseract-auto --env-file .env
```

启动参数只覆盖当前研究，默认选择保存在项目配置中。`models select embedding 配置名`设置默认向量模型；`start --embedding 配置名`覆盖本次选择。模型端点、标识、单价、环境变量名、扩展描述和角色固定在 `state.json` 的 capabilities 中；技能文件及资源固定到运行目录。恢复沿用快照，即使用户改变默认模型或停用扩展；密钥值仍从恢复进程环境取得。修改默认模型／启停扩展仅影响新研究。实际命令程序、远端服务代码及模型权重没有冻结，离线 replay 不重复调用扩展或 OCR 模型。

新建原始研究继续使用 2 小时总墙钟上限和 30 秒收尾预留；收费 OCR 与扩展模型不另起预算。v0.1 固定演示使用原工具集，新能力用于原始研究及 `YANTRA_RAW_MATERIALS` ADK 入口。

示例 LLM／vision 描述是待填写的本地端点模板，模型 ID、密钥、费用与图片上界必须按所用服务修改。示例 tool／MCP 描述使用当前工作目录 `/Users/chillychili/Yantra` 的绝对路径，移动项目后需调整。回传示例仅验证接入，不提供真实行情或研究数据。

实现协议依据：[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。历史接入验证见 [v0.2.1 记录](validation/v0.2.1/README.md)。

当前版本的验证范围见[v0.3验收](validation/v0.3/README.md)；模型列表探测和离线响应不等于真实推理验收。

首轮投资逻辑整合在thesis角色增加只读确定性 `compare_expectations`，与已有valuation价格基准分开；输入、来源和预测差异保存计算凭证。没有新增收费服务、模型、PS、订阅或自动监控。权限、公式及报告契约见[估值说明](VALUATION.md#经营预测比较与清单)。
