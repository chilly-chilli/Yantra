# 模型与扩展配置

适用版本：0.3.0。研究启动见[使用指南](USAGE.md)，embedding计费、索引与降级见[知识模块](KNOWLEDGE.md#模型费用和重建)。

这两个模块通过 CLI 与用户交互，并把已选择的能力接入 `RawEngine` 原始材料研究。运行配置位于 `.yantra/config.json`，可用环境变量 `YANTRA_CONFIG` 或命令前的 `--config /绝对路径/config.json` 指定另一个配置文件。

## 交互与命令

```sh
.venv/bin/python -m yantra configure --env-file /本地/credentials.env
.venv/bin/python -m yantra models
.venv/bin/python -m yantra extensions
```

`configure` 可在两个模块间切换。模型菜单支持 list／add／select，扩展菜单支持 list／add／enable／disable；输入 back 返回，exit 退出配置中心。添加模型会询问模型类型、适配器、端点、凭据变量名及计费依据。添加 skills 询问目录，MCP／tools 读取 JSON 描述文件，再询问授权角色。

脚本入口支持相同操作及 check：

```sh
.venv/bin/python -m yantra models add local --file examples/v021/llm.example.json
.venv/bin/python -m yantra models select llm local
.venv/bin/python -m yantra models --env-file /本地/credentials.env check local
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

`tesseract-auto` 与 `tesseract-eng` 可直接选择；自定义配置允许 `language` 及绝对 `data_dir`。原始材料中的扫描页及图片使用所选 OCR，普通文字型 PDF 继续直接提取。OCR 输出的数值及财务口径仍需后续核验，复杂表格关系不因更换模型而自动获得保证。

LLM／vision／embedding 配置含 `model_id`、`base_url`、`api_key_env`。只有环境变量名称写入配置；字面量 `api_key` 等未知字段会被拒绝。若本地兼容服务要求任意占位密钥，也通过对应环境变量提供。配置需符合兼容 API 及 JSON 输出要求；不是任意供应商原生 API 的通用连接器。

`models check` 对 API 配置请求 `/models`，返回可用模型 ID 和所选 ID 是否存在；不发起推理。部分服务不提供此接口，检查失败不能独自证明推理不可用。Tesseract 检查版本和语言包；command 只检查命令可执行文件存在，不验证 OCR 品质。`env` 可检查当前 `MODEL_*` 配置。

LLM 人民币单价可放在 `pricing`，包含输入／输出每百万 token 单价、来源和日期；支持有依据的零费用本地推理。启动时优先采用 `--pricing`，其次所选 LLM 的 pricing，最后沿用环境单价。视觉 OCR 必须单独声明 pricing 与 `max_image_tokens`，后者须覆盖供应商的单图计费 token 上界；文字、图片上界与输出上限一起预留，按返回 usage 结算。超过声明预留会停止后续模型调用；这依赖真实计费上界，不能从模板数值推断供应商保证。

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

角色为 `finance`、`thesis`、`valuation`、`verification`；skills 还可用于 `main` 的决策指令。默认启用范围是 finance／thesis／verification。MCP／tools 由专业角色调用，不扩展主 Agent 的行动合约；第三层仍限于父任务授权工具。技能不会改变核验否决、确定性计算或预算边界。

MCP 通过官方 Python SDK 1.30.0 连接 stdio 或 Streamable HTTP：

```sh
.venv/bin/python -m yantra extensions add mcp demo \
  --file examples/v021/mcp.example.json --roles finance
.venv/bin/python -m yantra extensions check mcp demo
```

注册时实际初始化服务并分页读取工具描述和 input schema；check 会重新发现并更新供新研究使用的描述。HTTP 配置形状如下，`headers_env` 引用完整请求头值（例如 `Bearer ...`）：

```json
{"transport":"streamable-http","url":"http://127.0.0.1:8000/mcp","headers_env":{"Authorization":"MCP_AUTHORIZATION"},"roles":["finance"]}
```

stdio 的 `command` 是参数数组，`env_refs` 可传入服务要求的变量。当前范围为 MCP tools 的发现与调用；未实现 MCP prompts／resources、OAuth 登录或常驻连接管理。

命令工具：

```sh
.venv/bin/python -m yantra extensions add tools echo \
  --file examples/v021/tool.example.json --roles finance
```

tool 描述包括 `description`、`command`、`input_schema`、roles 和可选 env_refs。输入经 JSON Schema 校验后以一个 JSON 对象写入 stdin；命令向 stdout 输出一个 JSON 对象，日志应写 stderr。以参数数组启动，不通过 shell 插值；不是对用户程序的进程沙箱。最多接收 1 MB 输出，超时会终止所启动的命令进程组。只导入你打算授权执行的程序和服务。

扩展 timeout 默认 30 秒，至多 120 秒，执行时还受任务及全局剩余时间约束。工具调用计入原有工具限额；`cost_kind=model` 的扩展额外计入模型调用次数及共享人民币费用账本。`cost_cny` 是每次调用声明上界，非零须有 cost_source／cost_as_of。`cost_kind=service` 的声明费用单独统计，不冒充供应商账单。即使调用失败，已预留的费用也保留。

成功的 MCP／tool JSON 响应经所引用凭据脱敏后保存为原始扩展来源，附哈希和引用位置；后续核验可检查返回内容。保存调用凭证不等于第三方数据已被独立证实，也不产生估值计算凭证。接口不覆盖已有财务计算工具，扩展名会加命名空间。

## 新建与恢复

```sh
.venv/bin/python -m yantra start --company 示例公司 --market CN --security-code 600000 \
  --material /绝对路径/原始报告.pdf --mode real-model \
  --llm local --ocr tesseract-auto --env-file /本地/credentials.env
```

启动参数只覆盖当前研究，默认选择保存在项目配置中。`models select embedding 配置名`设置默认向量模型；`start --embedding 配置名`覆盖本次选择。模型端点、标识、单价、环境变量名、扩展描述和角色固定在 `state.json` 的 capabilities 中；技能文件及资源固定到运行目录。恢复沿用快照，即使用户改变默认模型或停用扩展；密钥值仍从恢复进程环境取得。修改默认模型／启停扩展仅影响新研究。实际命令程序、远端服务代码及模型权重没有冻结，离线 replay 不重复调用扩展或 OCR 模型。

新建原始研究继续使用 2 小时总墙钟上限和 30 秒收尾预留；收费 OCR 与扩展模型不另起预算。v0.1 固定演示使用原工具集，新能力用于原始研究及 `YANTRA_RAW_MATERIALS` ADK 入口。

示例 LLM／vision 描述是待填写的本地端点模板，模型 ID、密钥、费用与图片上界必须按所用服务修改。示例 tool／MCP 描述使用当前工作目录 `/Users/chillychili/Yantra` 的绝对路径，移动项目后需调整。回传示例仅验证接入，不提供真实行情或研究数据。

实现协议依据：[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。历史接入验证见 [v0.2.1 记录](validation/v0.2.1/README.md)。

当前版本的验证范围见[v0.3验收](validation/v0.3/README.md)；模型列表探测和离线响应不等于真实推理验收。
