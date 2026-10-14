# 使用指南

适用版本：0.4.0（Unreleased）。本文维护当前安装、启动、预算、恢复和复测操作。高级配置见[能力配置](CAPABILITIES.md)，估值契约见[估值说明](VALUATION.md)，知识与用户要求见[知识说明](KNOWLEDGE.md)。

## 安装

以下命令从仓库根目录执行，使用Python标准venv附带的pip。已有环境若缺少pip，可先运行`.venv/bin/python -m ensurepip --upgrade`；已使用uv管理的环境也可继续用其安装命令。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install 'reportlab==4.5.1'  # 仅测试样本生成
```

Python 3.11+；历史测试环境使用3.12，保持 `google-adk==2.11.0`。原始解析使用 pdfplumber、PDFium、Tesseract、openpyxl、python-docx、python-pptx 和 Beautiful Soup，保留页码、表格边界、工作表单元格、Word 文档块、幻灯片、图像区域及网页片段。CSV／JSON 保留行号／JSON Pointer。扫描 PDF 和图片执行真实 OCR；复杂表格、图表、嵌入图片及公式缺少缓存值会登记缺口。

默认本地 OCR 需本机 Tesseract 可执行文件及 `chi_sim`、`eng` 语言包；选择 PaddleOCR API 时扫描页上传至服务，不需要本机 Tesseract。API 配置见[能力配置](CAPABILITIES.md#paddleocr-任务-api)。当前 macOS 已验证以下本地安装位置，其他系统可设置 `TESSDATA_PREFIX` 指向语言目录：

```sh
mkdir -p .venv/share/tessdata
curl -fL https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata -o .venv/share/tessdata/chi_sim.traineddata
cp /opt/homebrew/share/tessdata/eng.traineddata .venv/share/tessdata/eng.traineddata
```

缺少中文语言包会登记缺口，不会声称中文 OCR 已完成。文件格式支持 `.pdf`、常见栅格图片、`.xlsx`、`.docx`、`.pptx`、HTML、纯文本、Markdown、CSV、JSON、XML；旧二进制 `.doc/.xls/.ppt` 不直接解析，须先转换。


## 配置入口

```sh
.venv/bin/python -m yantra configure --env-file .env
.venv/bin/python -m yantra models --env-file .env interactive
.venv/bin/python -m yantra extensions --env-file .env interactive
```

交互配置入口在 stdout 为 TTY 时显示一次实证研策 Unicode 块字符标志和版本号，仅显示大写 `YANTRA` 块字符字标与版本号并居中，读数点使用朱砂红。`NO_COLOR` 禁用颜色；`TERM=dumb` 使用纯文字标题。输出重定向、非交互命令及 JSON 模式不显示标志。

配置保存在`.yantra/config.json`；本项目所有本地模型、OCR 和资料服务凭据统一放在仓库根目录 `.env`。`.env.example` 仅为模板，新克隆且尚无 `.env` 时复制后填写，不覆盖已有配置。CLI 通过显式 `--env-file .env` 加载，其他工作目录可传根文件的绝对路径；不自动加载或合并其他文件。ADK 2.11.0 的本地 `run/web` 从 `yantra/` 向父目录查找，可直接加载根 `.env`。模型、OCR、embedding、skills 和 MCP 的完整命令见[能力配置](CAPABILITIES.md)。

## Agent非交互调用与输出契约

### 终端状态协议

CLI 按 [OSC 7501 规范 revision 0.4](https://www.superlogical.com/rex/docs/build/program-status)向 stderr 的 TTY 发送 `app=yantra` 根状态：执行为 `working`，菜单输入为 `blocked/kind=question`，研究确认等待为 `blocked/kind=permission`，成功操作为 `done`，执行错误或研究 partial/failed 为 `error`，中断／EOF／取消为 `idle`。`status` 成功查询到失败研究仍报告 `done`，CLI 退出码语义保持不变。

消息采用单行 UTF-8 的 base64，清理控制字符并限制在 2048 字节；不报告虚构进度百分比。stderr 重定向或 `TERM=dumb` 时不发送控制序列；JSON stdout 始终保持一个响应对象。无需特性探测，未知 OSC 应由终端忽略；具体界面提示取决于终端实现。协议会在进程退出／下一 shell 提示时清理 blocked，因此持久的确认问题仍须查看 `state.json` 或 `status`。

### 调用与响应

默认 `--output text` 保留运行摘要及终端菜单；所有命令及子命令均接受 `--output json`，选项可放在命令前或子命令后。Agent应使用明确子命令。`configure`、`models interactive`、`extensions interactive` 只接受文本模式和TTY；文本TTY中裸 `models/extensions` 仍兼容菜单。无TTY缺少子命令立即报错，不能靠输入管道隐式进入菜单。EOF中止未完成操作返回1，此前已明确保存的修改仍保留。

```sh
.venv/bin/python -m yantra --version
.venv/bin/python -m yantra --output json --version
.venv/bin/python -m yantra --output json models list
.venv/bin/python -m yantra --output json extensions list
.venv/bin/python -m yantra --output json start --company DEMO --mode offline-demo \
  --materials-dir /absolute/path/synthetic-manifest-directory --runs-dir /absolute/path/agent-runs
.venv/bin/python -m yantra --output json status RUN_ID --runs-dir /absolute/path/agent-runs
.venv/bin/python -m yantra --output json resume RUN_ID --runs-dir /absolute/path/agent-runs \
  --decision modify --parameters-file /absolute/path/parameters.json
```

`RUN_ID`和文件路径为待替换占位符；离线启动目录须含明确虚构的manifest与测试材料，原始研究仍需真实配置和授权。JSON模式stdout只输出一个UTF-8 JSON对象及换行；日志／进度写stderr，Agent应分别捕获两个流。`--help`和`--version`在JSON模式也使用相同包装。命令处理函数返回数据，公共输出层统一序列化；直接调用Python内部`execute()`不再负责打印。

响应最小契约（`schema_version`为字符串`"1"`）：

```json
{"schema_version":"1","command":"models list","data":{"models":{},"selected":{},"env_llm":"MODEL_*"}}
```

```json
{"schema_version":"1","command":"status","error":{"code":"run_not_found","message":"运行不存在，请检查run_id和 --runs-dir","retryable":false,"field":"run_id"}}
```

顶层只含 `schema_version`、`command` 和 `data/error` 之一；嵌套命令以空格连接，如 `knowledge query`。错误包含稳定 `code`、可读 `message` 和 `retryable`；可定位时有 `field`，字段校验的 `details.fields` 只列位置和校验类型，不输出输入值、凭据或任意服务异常正文。解析尚未识别命令时 `command` 为 `yantra`。参数名须完整拼写，不接受缩写；`resume --continue-research` 与 `--decision` 互斥。Agent应按code处理错误，不解析message；`retryable=true`仅表示可在条件改善后重试，不能推断重试安全或修改尚未发生。

| 退出码 | 含义 |
|---|---|
| 0 | CLI操作成功；研究结果或探测就绪性另看data |
| 2 | 参数解析、JSON输入、校验、对象缺失或状态约束失败；未执行所请求的研究操作 |
| 1 | 本地I/O、意外执行失败、EOF中止或要求保存后应用失败；可能已有持久化副作用 |
| 130 | 用户中断；检查已保存运行，不自动重放未完成请求 |

常见code：`argument_error`、`invalid_json`、`input_file_error`、`validation_error`、`invalid_run_id`、`run_not_found`、`invalid_run_state`、`missing_parameters`、`subcommand_required`、`interactive_required`、`input_aborted`、`io_error`、`execution_error`、`interrupted`。模型／扩展未注册为 `model_not_found/extension_not_found`；知识、来源、要求缺失为 `knowledge_not_found/source_not_found/preference_not_found`；缺失或哈希不匹配的来源为 `source_unavailable`；运行锁冲突为 `run_busy`（退出1、可重试）。偏好应用错误见[知识说明](KNOWLEDGE.md#明确长期要求)。通用执行错误故意不转发服务正文。

`start/resume/status/replay/cancel`的data包含 `run_id`、既有业务 `status`、`mode`、预算／限制、`stop_reasons`、`pending_user`、`hard_checks`、`issues`、报告路径及复现信息；未产生的字段为null。`status`查询到failed仍退出0，启动或恢复得到waiting/partial/failed/cancelled也不等于CLI失败。状态集合保持 `running/waiting/completed/partial/failed/cancelled`。`compare`返回比较结果，`replay`同时返回新运行与comparison。

`next_actions`提供 `{action, argv}`，argv使用当前Python可执行文件及绝对 `--config`、`--runs-dir`，并保留本次指定的 `--env-file` 路径。直接以参数数组启动子进程即可，无需猜测shell转义；文本确认提示也保留上述上下文。跨调用使用不同环境文件时须自行明确提供。partial/failed原始研究的继续动作仍受原预算／截止约束；waiting确认动作仍需检查pending_user并取得明确授权。建议动作不是自动批准。

`cancel`的 `data.cancellation` 为 `request_accepted`（请求已接受，调度边界才收束）、`already_terminal`（completed/partial/failed，无新请求）或 `cancelled`（已保存状态确认取消）。它们是操作结果，不是新增业务状态。请求接受后应查询status确认实际取消；状态响应是查询时的快照。`models/extensions check`的探测操作成功可返回 `ready:false`，不表示功能就绪；模型探测、MCP发现仍可能访问所配置服务，不是离线测试命令。

### JSON文件输入

以下选项继续支持内联JSON对象，并增加互斥的UTF-8文件方式：

| 内联选项 | 文件选项 | 使用位置 |
|---|---|---|
| `--limits` | `--limits-file` | start |
| `--pricing` | `--pricing-file` | start |
| `--parameters` | `--parameters-file` | resume、preferences remember |
| `--changes` | `--changes-file` | knowledge edit-fact |
| `--budget` | `--budget-file` | knowledge query/rebuild |

文件必须存在、可读，并包含JSON对象；数组、标量、非法JSON、NaN／Infinity及溢出数值均报错。两种输入不能同时指定；required选项可由任一种满足。文件方式不改变参数的业务校验、预算或权限。limits只覆盖已有默认值：原始研究默认7200秒/49元，固定演示沿用旧Limits默认值（900秒）。

本轮未提供机器schema导出、JSONL事件流或启动幂等键。启动失败或中断后，尤其已经创建运行时，应先检查运行记录，不能假定重试没有副作用。离线验证范围与证据见[CLI验收记录](validation/v0.3/cli-agent-2026-10-13/README.md)。

## 原始材料研究

将 `.env.example` 中需要的配置填入根 `.env`，模型与资料服务使用各自变量名。CLI 只加载显式指定的 `--env-file`，不输出密钥。真实研究启动前必须提供人民币输入／输出单价、价格来源和日期；其他计价币种可以通过 `--pricing` 提供带来源和日期的汇率。未知模型单价不使用固定每调用金额冒充费用控制。

```sh
.venv/bin/python -m yantra start \
  --company 小米集团 --market HK --security-code 01810 --exchange HKEX --trading-currency HKD \
  --material '/绝对路径/原始材料目录' --material 'https://披露机构/报告.pdf' \
  --mode real-model --env-file .env --runs-dir runs
```

`--material` 可重复。生产路径不要求人工 manifest、固定行号或字段名 JSON。仅提供名称时先从原始资料识别证券；证据不足或存在多个柜台、类别、跨市场证券时，停止正式任务调度，保存候选，并要求通过市场及证券代码消除歧义。显式参数是用户对目标证券的确认；仍需核验公司、财报、股数及行情对应关系。

默认研究期限 12 个月，资料截止采用启动时点并保存时区。历史研究可显式 `--cutoff '2025-12-31T23:59:59+08:00'`。报告期间与披露日期分别保存；日期晚于截止的材料排除，历史截止下未知披露日期的材料也排除。来源 URL 日期及 HTML 发布时间作为带依据的日期信息保存，仍需核验其准确性。

每次都主动检索官方披露、反对证据、新变化和市场预期基准，并实际获取原响应。DDGS 用于发现；HTTP 与浏览器兼容的 HTTP 客户端用于获取，保留查询、字节快照、哈希和获取时间。网站拒绝访问、资料缺少、未知日期均记录；搜索摘要不能替代原文证据。私有数据服务尚未连接，公开路径无需资料服务密钥。RSS、AKShare、Docling 等选型见 [实施说明](archive/v0.2/IMPLEMENTATION.md)。

提取值须有原文数值、币种、单位、期间、合并口径、披露日期、来源定位及哈希；括号负数和千分位由程序验证。管理层陈述、预测、假设与历史事实分开。重复和冲突保留候选；不同口径不静默合并，缺失值不填零。


## 框架、授权与预算

`--framework auto` 为默认，由预算内模型从价值与安全边际（`value`）、经营质量与现金流（`quality`）、经营改善与预期差（`change`）选择一个主框架并记录理由。可显式选择；其他用于辅助反证。`--framework-file '/路径/自定义框架.md'` 接受自然语言／Markdown，保存原文与草稿。用户框架先经过独立核验，再固定用于当前研究并保存为以后可选择的版本。主 Agent 在运行中调整的草稿经核验后供以后选择；不会静默改动正在使用的框架。

默认 `--confirmation-mode autonomous`。控制器按所选方法检查参数、原始来源、币种、证券类别、期间及口径，记录 `authorization_source=controller`，再运行确定性计算；不会伪装为人工确认。整体权益换算每股还需要匹配股数，EPS×PE/BVPS×PB 校验每股指标口径。`--confirmation-mode manual` 需用户明确确认当前方法和版本，不能绕过硬条件。等待也消耗本次总墙钟时间，超时不会自动批准。

单次完整研究任务默认墙钟上限为 2 小时（7200 秒），其中预留 30 秒收尾；配置不得超过 7200 秒。新建研究使用此默认值，已保存运行沿用原截止时点。默认人民币模型费上限 49 元，必须低于 50 元。全部主 Agent、专业任务、临时角色、解析模型、框架操作、重试与修复共享持久化截止时点和费用账本。先按输入 UTF-8 字节的保守 token 上界及输出上限原子预留，再按返回用量和配置单价核算；没有用量时保留预留，不记零。账单不可得时明确标注，搜索／数据服务费单独统计。工具、模型、任务时间、失败重试及主循环另有限额。

49 元是项目预算配置，附件通知摘录不能证明它是竞赛要求。


DCF、PE、PB、EV/EBITDA和DDM的公式、参数、单位与硬条件集中在[估值说明](VALUATION.md)。

## 进度、修改、取消与恢复

```sh
.venv/bin/python -m yantra status RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision confirm --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision modify --parameters '{"growth_rate":0.03}' --runs-dir runs
.venv/bin/python -m yantra cancel RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --runs-dir runs --env-file .env
```

活动原始研究的参数修改以请求文件提交，在调度边界应用新版本，失效受影响成果，再重算与核验。取消可由另一终端提交，随后在预算边界收束；正在进行的 HTTP 请求最多等待其局部超时。正常恢复沿用原预算及截止；`resume --continue-research` 可明确继续 partial/failed，仍不重置预算及截止；已中断操作保留预留，未完成请求不会自动重放。修改不重置预算；已取消研究不能恢复。拒绝估值仅用于人工等待时。

上面的 `growth_rate` 是DCF增量改参示例。切换方法时，`--parameters` 传入包含 `model`、完整 `parameters` 和 `basis` 的JSON对象；按新方法重新校验来源及硬条件、增加版本并使旧估值和核验失效。不能用旧参数授权另一种方法。完整请求形状见[手动修改说明](VALUATION.md#手动修改或切换方法)。

每个运行目录包括 `state.json`、`events.jsonl`、`materials/index.json`、原始字节文件、提取成果、检索记录、`report-vN.md/.json`、`calculations-vN.json`。框架在研究存储目录的 `frameworks/` 保存；无完整聊天历史共享。


## 共享知识与用户要求

研究先检索共享知识，再主动检查新披露、反证、变化和市场基准。CLI默认库为`.yantra/knowledge/`；历史论点是参考，事实复用仍需检查原文、期间、证券及口径。

```sh
.venv/bin/python -m yantra knowledge status
.venv/bin/python -m yantra knowledge query "汽车 毛利率"
.venv/bin/python -m yantra preferences list
```

索引显式预算、事实编辑、删除影响、长期要求与ADK记忆命令见[知识说明](KNOWLEDGE.md)。

## 本地验证与示例

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m examples.v022.synthetic
```

测试生成明确标注的临时夹具。`synthetic`使用脚本决策后端；需要用户财报的`local_materials`是单独的可选示例，使用方式与依赖见[示例目录](../examples/v022/README.md)。当前验收、历史记录和离线／真实服务边界见[验证索引](validation/README.md)。

## ADK 与固定复测

```sh
export YANTRA_RAW_MATERIALS='["/绝对路径/原始财报.pdf"]'
export YANTRA_MARKET=HK
export YANTRA_SECURITY_CODE=01810
export YANTRA_TRADING_CURRENCY=HKD
# 其他 MODEL_*、人民币单价与来源已在根 .env 配置
.venv/bin/adk run yantra
.venv/bin/adk web --port 8000
```

标准 `yantra/agent.py:app` 配置原生 App／插件，`root_agent` 保留为兼容导出。每个专业及临时任务有独立 ADK Session；动态第三层角色在父授权范围内实际运行，禁止第四层。获准成果可读取、消息可送达；消息不创建任务。跨 Agent 转派需主 Agent 内部批准、验证输入权限和预算后执行。核验 Agent 的否决不能被主 Agent 覆盖。

Unreleased 的本地 API 入口增加持久 Session 与脱敏研究轨迹：

```sh
.venv/bin/python -m uvicorn fast_api_app:app --env-file .env --host 127.0.0.1 --port 8000
```

使用标准 `/apps/yantra/users/USER_ID/sessions` 创建 Session，使用 `/run_sse` 推进。上述 ID 是占位符；所属研究的轨迹接口为 `GET /apps/yantra/users/USER_ID/sessions/SESSION_ID/research/trace`，接受 `after`（序号，默认0）、`limit`（1–500）及 `task_id`。返回研究与任务状态、角色、父任务、ADK Session／invocation、工具调用和结果哈希、错误类别及用量。`cursor` 用于下一页；序号为持久化顺序，因果关系看父 invocation，不能假设外层开始事件先于内部事件写入。

重启仍使用原 `YANTRA_RUNS_DIR`（默认 `runs`）及其 `.adk/sessions.db`，返回当前研究所属 Session。未绑定／不存在的 Session 返回404。API 沿用 ADK 本地 user/session 标识约定，无用户认证，仅按本机可信客户端使用；不提供远程多租户授权。轨迹不返回子任务材料正文或工具参数，不是证据全文接口。`controller`只读投影权威`state.json`预算、费用预留／用量收据、所属invocation及非ADK准备检索的目的／结果数；原生事件仍标明observation_only，不能与投影相加计费。服务没有实际账单，缺用量时保留预留。API关闭其自建SQLite服务，调用方传入的服务由调用方负责。普通 `adk web` 不包含此新增路由。验证范围见[接入记录](validation/v0.4.0/README.md)。

该本地入口还提供标准 ADK Artifact 路由：同一 Session 的 `GET .../artifacts` 列出 `report.json`、`report.md` 和 `calculations.json`；`GET .../artifacts/report.json/versions` 列出 ADK 版本（从0开始），`.../versions/0` 读取固定版本，`.../versions/0/metadata` 返回业务假设版本、run ID、sha256及控制器所有权。路径省略号代表同一 `/apps/yantra/users/USER_ID/sessions/SESSION_ID` 前缀，必须替换为实际标识。

内容来自已有 `report-vN.*`／`calculations-vN.json` 文件，ADK 只保存指针与哈希。POST／DELETE Artifact 返回403；修改／删除走既有业务操作。文件缺失、被改写或版本越界时读取404，元数据仍可定位原哈希。若控制器在同一业务版本再次覆盖源文件，旧 ADK 版本可能不可读取；服务不复制第二份字节来掩盖这种缺失。新版本业务文件存在时可跨重启读取；原始材料未纳入此服务。旧完成研究在其所属 Session 首次访问研究入口时建立文件索引，不重新模型推理。

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m yantra replay RUN_ID --runs-dir runs
.venv/bin/python -m yantra compare FIRST_RUN_ID SECOND_RUN_ID --runs-dir runs
```

原始研究的 `replay` 固定原始字节、提取成果、框架和方法/假设版本，重新检查字节哈希、复算指标及 DCF/PE/PB/EV-EBITDA/DDM，并执行程序一致性复核；未知方法明确失败，不联网、不重新执行远程经济判断，也不会撤销原独立核验的否决。旧 DCF 继续复现旧算法。复测范围与真实端到端研究分别报告。固定演示仍使用 `--mode offline-demo --materials-dir 含manifest的目录`，保持明确人工确认与虚构材料标记。

`completed` 只在当前独立核验及程序条件均满足时成立；`partial` 是有成果且有明确缺口；`failed` 是没有可用成果；`cancelled` 是取消。研究的 `partial` 与开发验收通过是不同判断，解析空接口、空报告或预算失控仍不能通过开发验收。
