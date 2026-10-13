# Yantra v0.2.1

实证研策：Google ADK 驱动的证据核验型买方投研原型。

生产入口接收公司名称／证券代码及原始文件、目录或网页链接，主动独立检索，解析财务和经营证据，选择研究框架，形成论点、评估 DCF 适用性并独立核验。输出八部分 Markdown 备忘录、JSON、计算凭证、来源快照及运行记录。A 股、港股的研究范围与正稳定 FCFF DCF 的适用范围分别管理。

本版增加两个 CLI 模块：LLM／OCR 模型管理，以及 skills／MCP／tools 扩展管理。所选模型、技能指令与授权工具实际接入原始材料研究，并固定在每次运行中。保留 v0.1 固定材料演示与已有 ADK 入口。v0.2 的投研验收缺口继续见 [原验收记录](docs/VALIDATION_V0_2.md)；本次功能验证见 [v0.2.1 验证记录](docs/VALIDATION_V0_2_1.md)。

## 安装

```sh
python3 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python 'reportlab==4.5.1'  # 仅测试样本生成
```

Python 3.11+，本次使用 3.12；保持 `google-adk==2.11.0`。原始解析使用 pdfplumber、PDFium、Tesseract、openpyxl、python-docx、python-pptx 和 Beautiful Soup，保留页码、表格边界、工作表单元格、Word 文档块、幻灯片、图像区域及网页片段。CSV／JSON 保留行号／JSON Pointer。扫描 PDF 和图片执行真实 OCR；复杂表格、图表、嵌入图片及公式缺少缓存值会登记缺口。

OCR 需本机 Tesseract 可执行文件及 `chi_sim`、`eng` 语言包。当前 macOS 已验证以下安装位置，其他系统可设置 `TESSDATA_PREFIX` 指向语言目录：

```sh
mkdir -p .venv/share/tessdata
curl -fL https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata -o .venv/share/tessdata/chi_sim.traineddata
cp /opt/homebrew/share/tessdata/eng.traineddata .venv/share/tessdata/eng.traineddata
```

缺少中文语言包会登记缺口，不会声称中文 OCR 已完成。文件格式支持 `.pdf`、常见栅格图片、`.xlsx`、`.docx`、`.pptx`、HTML、纯文本、Markdown、CSV、JSON、XML；旧二进制 `.doc/.xls/.ppt` 不直接解析，须先转换。

## 模型与扩展配置

从项目根目录打开交互式配置中心，或分别进入两个模块：

```sh
.venv/bin/python -m yantra configure
.venv/bin/python -m yantra models
.venv/bin/python -m yantra extensions
```

模型模块支持注册、列出和选择 OpenAI 兼容 LLM、Tesseract OCR、视觉模型 OCR，以及自定义 OCR 命令适配器。默认 LLM 沿用 `MODEL_*` 环境配置，默认 OCR 为 `tesseract-auto`。选择会影响新建原始研究；启动时可用 `--llm 名称 --ocr 名称` 临时覆盖。

扩展模块支持导入 `SKILL.md` 及资源目录、连接 MCP 的 stdio／Streamable HTTP 服务、注册 JSON 命令工具，并按角色启停。技能指令传入匹配角色的模型请求，工具经现有 ADK 工具回调、角色授权与共享预算执行。配置保存在 `.yantra/config.json`，密钥填写环境变量名，实际值通过环境或显式 `--env-file` 提供。

```sh
.venv/bin/python -m yantra models select ocr tesseract-eng
.venv/bin/python -m yantra models check tesseract-auto
.venv/bin/python -m yantra extensions add skills cashflow --path examples/v021/cashflow-skill --roles finance,verification
.venv/bin/python -m yantra extensions add tools echo --file examples/v021/tool.example.json --roles finance
.venv/bin/python -m yantra extensions add mcp demo --file examples/v021/mcp.example.json --roles finance
.venv/bin/python -m yantra extensions disable tools echo
```

示例工具及 MCP 服务仅回传输入，用于确认接入。模型定价、协议、凭据、运行快照和适配范围详见 [v0.2.1 使用说明](docs/CAPABILITIES_V0_2_1.md)。

## 原始材料研究

将 `.env.example` 的模型配置填入本地环境文件。模型凭据与资料服务凭据分别管理。CLI 只加载显式指定的 `--env-file`，不输出密钥。真实研究启动前必须提供人民币输入／输出单价、价格来源和日期；其他计价币种可以通过 `--pricing` 提供带来源和日期的汇率。未知模型单价不使用固定每调用金额冒充费用控制。

```sh
.venv/bin/python -m yantra start \
  --company 小米集团 --market HK --security-code 01810 --exchange HKEX --trading-currency HKD \
  --material '/绝对路径/原始材料目录' --material 'https://披露机构/报告.pdf' \
  --mode real-model --env-file '/本地/模型配置.env' --runs-dir runs
```

`--material` 可重复。生产路径不要求人工 manifest、固定行号或字段名 JSON。仅提供名称时先从原始资料识别证券；证据不足或存在多个柜台、类别、跨市场证券时，停止正式任务调度，保存候选，并要求通过市场及证券代码消除歧义。显式参数是用户对目标证券的确认；仍需核验公司、财报、股数及行情对应关系。

默认研究期限 12 个月，资料截止采用启动时点并保存时区。历史研究可显式 `--cutoff '2025-12-31T23:59:59+08:00'`。报告期间与披露日期分别保存；日期晚于截止的材料排除，历史截止下未知披露日期的材料也排除。来源 URL 日期及 HTML 发布时间作为带依据的日期信息保存，仍需核验其准确性。

每次都主动检索官方披露、反对证据、新变化和市场预期基准，并实际获取原响应。DDGS 用于发现；HTTP 与浏览器兼容的 HTTP 客户端用于获取，保留查询、字节快照、哈希和获取时间。网站拒绝访问、资料缺少、未知日期均记录；搜索摘要不能替代原文证据。私有数据服务尚未连接，公开路径无需资料服务密钥。RSS、AKShare、Docling 等选型见 [实施说明](docs/IMPLEMENTATION.md)。

提取值须有原文数值、币种、单位、期间、合并口径、披露日期、来源定位及哈希；括号负数和千分位由程序验证。管理层陈述、预测、假设与历史事实分开。重复和冲突保留候选；不同口径不静默合并，缺失值不填零。

## 框架、估值与预算

`--framework auto` 为默认，由预算内模型从价值与安全边际（`value`）、经营质量与现金流（`quality`）、经营改善与预期差（`change`）选择一个主框架并记录理由。可显式选择；其他用于辅助反证。`--framework-file '/路径/自定义框架.md'` 接受自然语言／Markdown，保存原文与草稿。用户框架先经过独立核验，再固定用于当前研究并保存为以后可选择的版本。主 Agent 在运行中调整的草稿经核验后供以后选择；不会静默改动正在使用的框架。

默认 `--confirmation-mode autonomous`。控制器在完整参数、来源口径、证券股数、币种、正 FCFF 及依据检查后，记录 `authorization_source=controller`，再运行确定性计算；不会伪装为人工确认。`--confirmation-mode manual` 需用户明确确认当前版本。等待也消耗本次总墙钟时间，超时不会自动批准。

OCF−资本开支只是一项可计算指标；未调节融资利息和税盾时，不作为 FCFF。金融企业、持续亏损、强周期或股数／口径依据不足时可以完成研究并输出 `partial`，不会为了目标价强套 DCF。不扩展完整行业模型库。报表币种与交易币种分别记录，单位换算不会隐式换汇。缺少参考价格、共识或必要输入时保留缺口。

单次完整研究任务默认墙钟上限为 2 小时（7200 秒），其中预留 30 秒收尾；配置不得超过 7200 秒。新建研究使用此默认值，已保存运行沿用原截止时点。默认人民币模型费上限 49 元，必须低于 50 元。全部主 Agent、专业任务、临时角色、解析模型、框架操作、重试与修复共享持久化截止时点和费用账本。先按输入 UTF-8 字节的保守 token 上界及输出上限原子预留，再按返回用量和配置单价核算；没有用量时保留预留，不记零。账单不可得时明确标注，搜索／数据服务费单独统计。工具、模型、任务时间、失败重试及主循环另有限额。

## 进度、修改、取消与恢复

```sh
.venv/bin/python -m yantra status RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision confirm --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision modify --parameters '{"growth_rate":0.03}' --runs-dir runs
.venv/bin/python -m yantra cancel RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --runs-dir runs --env-file '/本地/模型配置.env'
```

活动原始研究的参数修改以请求文件提交，在调度边界应用新版本，失效受影响成果，再重算与核验。取消可由另一终端提交，随后在预算边界收束；正在进行的 HTTP 请求最多等待其局部超时。正常恢复沿用原预算及截止；`resume --continue-research` 可明确继续 partial/failed，仍不重置预算及截止；已中断操作保留预留，未完成请求不会自动重放。修改不重置预算；已取消研究不能恢复。拒绝估值仅用于人工等待时。

每个运行目录包括 `state.json`、`events.jsonl`、`materials/index.json`、原始字节文件、提取成果、检索记录、`report-vN.md/.json`、`calculations-vN.json`。框架在研究存储目录的 `frameworks/` 保存；无完整聊天历史共享。

## ADK 与固定复测

```sh
export YANTRA_RAW_MATERIALS='["/绝对路径/原始财报.pdf"]'
export YANTRA_MARKET=HK
export YANTRA_SECURITY_CODE=01810
export YANTRA_TRADING_CURRENCY=HKD
# 其他 MODEL_*、人民币单价与来源已在本地环境配置
.venv/bin/adk run yantra
.venv/bin/adk web --port 8000
```

标准 `yantra/agent.py:root_agent` 入口复用控制器。每个专业及临时任务有独立 ADK Session；动态第三层角色在父授权范围内实际运行，禁止第四层。获准成果可读取、消息可送达；消息不创建任务。跨 Agent 转派需主 Agent 内部批准、验证输入权限和预算后执行。核验 Agent 的否决不能被主 Agent 覆盖。

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m yantra replay RUN_ID --runs-dir runs
.venv/bin/python -m yantra compare FIRST_RUN_ID SECOND_RUN_ID --runs-dir runs
```

原始研究的 `replay` 固定原始字节、提取成果、框架和假设版本，重新检查字节哈希、复算指标及 DCF，并执行程序一致性复核；不联网、不重新执行远程经济判断，也不会撤销原独立核验的否决。复测范围与真实端到端研究分别报告。固定演示仍使用 `--mode offline-demo --materials-dir 含manifest的目录`，保持明确人工确认与虚构材料标记。

`completed` 只在当前独立核验及程序条件均满足时成立；`partial` 是有成果且有明确缺口；`failed` 是没有可用成果；`cancelled` 是取消。研究的 `partial` 与开发验收通过是不同判断，解析空接口、空报告或预算失控仍不能通过开发验收。
