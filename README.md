# Yantra v0.2.3

实证研策：Google ADK 驱动的证据核验型买方投研原型。

原始材料研究入口接收公司名称／证券代码及原始文件、目录或网页链接，主动独立检索，解析财务和经营证据，选择研究框架，形成论点，按方法判断适用性并独立核验。估值入口支持 DCF、PE、PB、EV/EBITDA 与单阶段 DDM；框架与估值方法是两个维度。输出八部分 Markdown 备忘录、JSON、统一计算凭证、来源快照及运行记录。

本版集成相对估值、PB/DDM、EBIT 的 FCFF 桥接、有来源的市场基准比较及信息类型呈现，核对“方法＋参数＋版本”并对新增方法执行真正的离线数学复算。原有 DCF、三情景、9 格敏感性和计算凭证继续保留；它们在 v0.2.1 已实现。LLM／OCR 管理及 skills／MCP／tools 扩展沿用 v0.2.1，固定演示与现有 ADK 编排保持兼容。

[v0.2.3 运行说明](docs/USAGE_V0_2_3.md) · [验证记录](docs/VALIDATION_V0_2_3.md) · [架构](docs/ARCHITECTURE.md) · [项目书素材状态与实际依赖登记](docs/DELIVERY_STATUS_V0_2_2.md)。主测试入口65项通过；本地真实材料的脚本流程已验证相对估值计算和资料不足时正确拒绝发布。没有调用收费模型，未验证真实 LLM 端到端投研或材料／行情的外部真实性。SOTP、正常化盈利仍为独立候选原型；本版没有资产重估 NAV、行情／一致预期服务、跨期记忆或自动交易。

研究沿用 `finance → thesis → valuation → verification → report`。投资逻辑由模型依据框架和材料生成；程序管理流程、权限、结构、来源、计算和核验。全部方法复用同一 `valuation` 角色与工具，没有第二套控制器或每方法一个智能体。

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

默认 `--confirmation-mode autonomous`。控制器按所选方法检查参数、原始来源、币种、证券类别、期间及口径，记录 `authorization_source=controller`，再运行确定性计算；不会伪装为人工确认。整体权益换算每股还需要匹配股数，EPS×PE/BVPS×PB 校验每股指标口径。`--confirmation-mode manual` 需用户明确确认当前方法和版本，不能绕过硬条件。等待也消耗本次总墙钟时间，超时不会自动批准。

| 方法 | 计算与准入条件 | 结果边界 |
|---|---|---|
| DCF | 新研究使用已核对的 EBIT→FCFF 桥接、折现及增长假设，并明确净债务、少数股东权益和匹配总股数 | 保留原有三情景和9格敏感性；旧快照复现旧算法 |
| PE | 正EPS×同类PE可比倍数中位数；期间、FY/TTM/forward、币种、归母及基本／稀释EPS口径匹配 | 区间来自同一可比集；非正EPS不发布有效价格结论 |
| PB | 正归母期末BVPS×同口径PB可比倍数中位数 | 净资产质量仍需业务核验；不是资产重估NAV |
| EV/EBITDA | 正合并EBITDA×同口径倍数，扣净债务与少数股东权益，再除匹配总普通股数 | 桥接缺失时仅保留企业价值，权益及每股值为null；明确零与净现金分别处理 |
| 单阶段DDM | 年度D0推导D1，价值=D1/(k−g)，要求0≤g<k<1及参数依据 | 显示9格敏感性及无效格；不与PB自动取均值 |

金额与股数分别统一为百万元、百万股；EPS、BVPS、每股股利和价格不按百万缩放，比例单独处理，不隐式换汇。来源必须解析到当前运行的原文锚点和哈希；仅存在证据对象不足以批准计算。可比区间与敏感性包络均说明形成规则，不称为统计置信区间。详细契约见[估值方法与历史映射](docs/USAGE_V0_2_2.md)。

新建原始材料 DCF 使用同口径 EBIT×(1−税率)+折旧摊销−资本开支−ΔNWC 桥接，并要求净债务、少数股东权益和股数有明确来源，未知值不补零。OCF−资本开支仍是分析指标，OCF 倒推尚未开放给新研究；旧快照及固定演示保留旧算法。不适用 DCF 的公司可提出其他资料完整的方法或保留 `partial`，不补造目标价。报表币种与交易币种分别记录，不隐式换汇。市场基准区分现价、参考价、一致目标价和经营盈利预期；缺可靠基准为 `unresolved`，差异不是预期收益率。

单次完整研究任务默认墙钟上限为 2 小时（7200 秒），其中预留 30 秒收尾；配置不得超过 7200 秒。新建研究使用此默认值，已保存运行沿用原截止时点。默认人民币模型费上限 49 元，必须低于 50 元。全部主 Agent、专业任务、临时角色、解析模型、框架操作、重试与修复共享持久化截止时点和费用账本。先按输入 UTF-8 字节的保守 token 上界及输出上限原子预留，再按返回用量和配置单价核算；没有用量时保留预留，不记零。账单不可得时明确标注，搜索／数据服务费单独统计。工具、模型、任务时间、失败重试及主循环另有限额。

49 元是项目预算配置，附件通知摘录不能证明它是竞赛要求。

## 进度、修改、取消与恢复

```sh
.venv/bin/python -m yantra status RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision confirm --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --decision modify --parameters '{"growth_rate":0.03}' --runs-dir runs
.venv/bin/python -m yantra cancel RUN_ID --runs-dir runs
.venv/bin/python -m yantra resume RUN_ID --runs-dir runs --env-file '/本地/模型配置.env'
```

活动原始研究的参数修改以请求文件提交，在调度边界应用新版本，失效受影响成果，再重算与核验。取消可由另一终端提交，随后在预算边界收束；正在进行的 HTTP 请求最多等待其局部超时。正常恢复沿用原预算及截止；`resume --continue-research` 可明确继续 partial/failed，仍不重置预算及截止；已中断操作保留预留，未完成请求不会自动重放。修改不重置预算；已取消研究不能恢复。拒绝估值仅用于人工等待时。

上面的 `growth_rate` 是DCF增量改参示例。切换方法时，`--parameters` 传入包含 `model`、完整 `parameters` 和 `basis` 的JSON对象；按新方法重新校验来源及硬条件、增加版本并使旧估值和核验失效。不能用旧参数授权另一种方法。完整请求形状见[手动修改说明](docs/USAGE_V0_2_2.md#手动修改或切换方法)。

每个运行目录包括 `state.json`、`events.jsonl`、`materials/index.json`、原始字节文件、提取成果、检索记录、`report-vN.md/.json`、`calculations-vN.json`。框架在研究存储目录的 `frameworks/` 保存；无完整聊天历史共享。

## 本地验证与示例

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m examples.v022.synthetic
.venv/bin/python -m examples.v022.local_materials
```

v0.2.3主入口65项测试通过，包含保留的49项回归及新增16项集成测试；不把交付原型自有47项普通函数测试相加。覆盖方法授权、改参／换方法失效、来源与非法输入拒绝、单位转换、真正离线复算、结果篡改发现、历史DCF兼容、缺市场基准和独立核验否决。

`synthetic` 使用明确标注的合成数据验证四种新增方法的完整控制流程。`local_materials` 使用本地五粮液／山西汾酒财报和带日期的价格表，验证PE计算及缺必要可比价格时拒绝发布；需要用户目录中的原始文件，不能用合成材料替代。二者使用注入脚本后端，不调用外部模型，研究保留 `partial`，不等同真实LLM投研验收。

`examples/v022/`保留集成阶段的目录名，新运行保存实际程序版本0.2.3；历史真实材料快照保持0.2.2，不改写版本。原始交付物、财报和运行输出未纳入版本提交。详见[示例说明](examples/v022/README.md)、[v0.2.3验证记录](docs/VALIDATION_V0_2_3.md)及[原始材料验收](docs/VALIDATION_V0_2_2.md)。

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

原始研究的 `replay` 固定原始字节、提取成果、框架和方法/假设版本，重新检查字节哈希、复算指标及 DCF/PE/PB/EV-EBITDA/DDM，并执行程序一致性复核；未知方法明确失败，不联网、不重新执行远程经济判断，也不会撤销原独立核验的否决。旧 DCF 继续复现旧算法。复测范围与真实端到端研究分别报告。固定演示仍使用 `--mode offline-demo --materials-dir 含manifest的目录`，保持明确人工确认与虚构材料标记。

`completed` 只在当前独立核验及程序条件均满足时成立；`partial` 是有成果且有明确缺口；`failed` 是没有可用成果；`cancelled` 是取消。研究的 `partial` 与开发验收通过是不同判断，解析空接口、空报告或预算失控仍不能通过开发验收。
