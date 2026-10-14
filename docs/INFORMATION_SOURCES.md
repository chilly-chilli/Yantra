# 信息来源扩展

适用版本：0.4.2（Unreleased）。信息来源使用现有JSON命令工具和角色授权；统一注册、预算、原始响应快照及凭据脱敏规则见[能力配置](CAPABILITIES.md#skillsmcp-与-tools)。正式研究按资料缺口调用已就绪来源，不提供订阅、后台轮询或自动监控。

## 来源与人工准备

| 来源 | 实际接入 | 凭据变量及人工步骤 |
|---|---|---|
| [同花顺 Fuyao](https://fuyao.aicubes.cn/docs/quickstart/) | REST GET；`X-api-key` 请求头 | `FUYAO_API_KEY`：使用同花顺账号登录，在「API Key 管理」签发；权限不足需平台开通 |
| [东方财富妙想官方接入说明](https://marketing.dfcfw.com/res/download/A620260623NIYC2U.md) | 两个独立 POST API：金融数据、资讯搜索；`apikey` 请求头 | `MX_APIKEY`：在[妙想 Skills 页面](https://dl.dfcfs.com/m/itc4)领取 Key；本项目直接调用 API，不安装技能包 |
| [AKShare](https://akshare.akfamily.xyz/data/index.html) | Python SDK 白名单函数，访问各函数的上游站点 | 无 API Key；安装可选依赖 |
| [觅知 API 财联社电报](https://api.98dou.cn/doc/hotlist/cls/all.html) | GET `https://api.98dou.cn/api/hotlist/cls/all` | 文档标注免费，无参数、无需令牌；这是第三方聚合接口 |
| [Tushare Pro](https://tushare.pro/document/2?doc_id=290) | 仅HTTPS直连用户账户，POST白名单接口 | `TUSHARE_TOKEN`；按stock_basic、daily及财务各接口核对账号权限与费用 |
| [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) | basic网页搜索，POST `/search` | `TAVILY_API_KEY`；按账户额度核实每次调用上界 |
| [Yahoo Finance／yfinance](https://github.com/ranaroussi/yfinance) | 非官方Python访问工具，受限只读Yahoo请求 | 无API Key；可选yfinance依赖，遵守数据使用条款；本轮未安装或真实覆盖测试 |

妙想协议核实日期：2026-10-14；原有Fuyao、AKShare和觅知说明核实于2026-10-13。新增服务的实现及未验证范围见[本轮新增服务](#本轮新增服务)。Fuyao全量定义见其[官方聚合文档](https://fuyao.aicubes.cn/llms-full.txt)；AKShare函数与字段见[股票数据字典](https://akshare.akfamily.xyz/data/stock/stock.html)。提供者及上游网站可能调整权限、字段、频率限制或服务可用性。

在仓库根目录 `.env` 中填写真实值；只填写原始 key，无需添加 `Bearer`。这些真实凭据不写入 Git、扩展描述或研究快照：

```dotenv
FUYAO_API_KEY=<YOUR_FUYAO_API_KEY>
MX_APIKEY=YOUR_MX_APIKEY
```

占位符无法用于真实请求。为用户手动补全凭据保留的认证扩展默认为停用。官方资料未给出 Fuyao 和东方财富的每次调用人民币费用上界；模板 `cost_cny: 0` 仅为待确认声明值，不表示已确认服务免费。用户应在账户中核实费率：如果收费，填写每次调用的保守人民币上界、`cost_source` 和 `cost_as_of` 后重新注册，再启用。觅知免费及 AKShare SDK 免费不代表结果已获独立核验或无限访问权限。

## 本轮新增服务

三个模板位于 `examples/v042/information/`，均为 `enabled=false/cost_status=unknown/cost_cny=0`。零仅是未核实占位值；未就绪工具不会调用。新模板须按实际账户或数据使用条款设置 `cost_status=known`、保守的单次 `cost_cny` 上界及 `cost_source/cost_as_of`，**零费用同样需要依据和日期**，再重新注册并显式启用。不能从没有API Key、成功响应或SDK免费推断调用无费用。

在根 `.env` 填写用户自己的 `TUSHARE_TOKEN/TAVILY_API_KEY` 原始值；不复用托管连接、不开户或购买服务。Yahoo Finance是来源，yfinance是访问工具，不算两个独立来源。新增依赖 `yfinance==0.2.66` 放在可选 `requirements-information.txt`，主安装不要求它；本轮未安装yfinance，也未调用三个真实服务。

```sh
# 安装可选依赖是独立环境操作；不是服务验证：
.venv/bin/python -m pip install -r requirements-information.txt
.venv/bin/python -m yantra extensions add tools tushare --file examples/v042/information/tushare.example.json
.venv/bin/python -m yantra extensions add tools tavily --file examples/v042/information/tavily.example.json
.venv/bin/python -m yantra extensions add tools yahoo --file examples/v042/information/yahoo.example.json
```

以上注册保持模板停用。完成凭据、费用、依赖和具体权限核对后，修改自己的描述文件、重新执行add，再使用 `extensions enable tools 名称`。研究运行的就绪检查区分 `cost_unknown`、`credential_missing:变量名`、`dependency_missing:yfinance`；账号实际接口权限只在调用返回中确定，不能由注册或 `extensions check tools` 推断。单项缺失不阻塞其他可用来源。

按内容和访问方式分别记录能力，一项服务可覆盖多类资料：

| 工具／实际上游 | 内容 | 访问方式与边界 |
|---|---|---|
| `ext_tushare`／Tushare Pro | CN证券身份、未复权日线、财务三表 | HTTPS API，完整 `fields/items` 响应定位；聚合财务须再定位原始披露 |
| `ext_tavily`／公开网页 | 公告、财报地址、新闻线索 | 网页搜索；完整结果及URL进入既有网页获取流程，搜索摘要不作数值依据 |
| `ext_yahoo`／Yahoo Finance | 指定证券行情、历史行情及财务三表 | 非官方yfinance，保存SDK结果和原始JSON响应；聚合财务须再定位原始披露 |

Tushare只访问 `https://api.tushare.pro`，不跟随重定向，也不因TLS失败回退至明文HTTP传递token。官方接口说明中的HTTP地址不改变此实现边界；真实HTTPS可达性及各账户权限尚未验证。白名单为 `stock_basic/daily/income/balancesheet/cashflow`：身份查询必须给name或ts_code，不遍历全市场；日线给证券及明确交易日或有限日期范围；财务给期间或有限公告日期范围，`report_type=1`限定合并报表。日线价格是交易日期的收盘，不能称为实时行情。

Tavily本轮只接入这一家新增搜索服务，Bocha为未实施备选，FMP留待后续。搜索固定 `search_depth=basic/auto_parameters=false`，禁用answer和raw_content生成，保留usage；可指定general或news、最多10条结果和域名过滤。搜索返回地址由现有管道实际获取原文，单纯命中或摘要不能获得证据资格。

Yahoo白名单为 `quote/history/income-statement/balance-sheet/cash-flow`，均须显式symbol。历史start包含、end不包含，interval仅1d/1wk/1mo，固定 `auto_adjust=false/back_adjust=false/repair=false`；财务保留yearly或quarterly原期间列。报价核对证券、币种及原始 `regularMarketTime`，不以本地获取时间替代。访问会话只允许获准Yahoo主机的HTTPS GET，不执行同意表单POST或失败重试；cookie／crumb相关参数脱敏保存。覆盖按具体股票核实：`.SS/.SZ/.HK`及US符号访问能力不证明全市场覆盖，Yantra当前正式证券身份仍限CN/HK，US查询不是完整美股研究支持。

以下为受控证券的请求形状，须替换实际目标；它们不是已执行的真实查询：

| 工具 | 请求示例 |
|---|---|
| `ext_tushare` | `{"operation":"stock_basic","parameters":{"name":"示例公司","list_status":"L"}}` |
| `ext_tushare` | `{"operation":"daily","parameters":{"ts_code":"600001.SH","trade_date":"20261013"}}` |
| `ext_tushare` | `{"operation":"income","parameters":{"ts_code":"600001.SH","period":"20251231","report_type":"1"}}` |
| `ext_tavily` | `{"query":"示例公司 最新官方年报","max_results":5,"search_depth":"basic","include_domains":["sse.com.cn"]}` |
| `ext_yahoo` | `{"operation":"quote","parameters":{"symbol":"00001.HK"}}` |
| `ext_yahoo` | `{"operation":"history","parameters":{"symbol":"600001.SS","start":"2026-10-01","end":"2026-10-14","auto_adjust":false,"back_adjust":false}}` |

正式准备过程依据同一数据问题换源：身份、行情、财务和网页搜索不能任意互换。失败、空结果、不适用行情或未就绪原因保存到 `information_attempts`；缺关键财务原文或合适报价时继续公开路径并保留缺口。每次调用继续受冻结角色、最小凭据、原工具／时间预算和费用预留约束，相同失败请求不自动重试。两个原始来源冲突时保留记录和解释，无法解释的关键冲突阻止肯定买入判断，见[估值说明](VALUATION.md#方法与权威契约)。

## 安装与注册

以下命令均在仓库根目录执行。模板使用 `.venv/bin/python` 与 `-m yantra.information`，运行命令时仍需从仓库根目录启动；移动项目后可重新注册。实际本机配置可使用绝对解释器及适配器路径。

```sh
.venv/bin/python -m pip install -r requirements-information.txt
.venv/bin/python -m yantra extensions add tools fuyao --file examples/v033/information/fuyao.example.json
.venv/bin/python -m yantra extensions add tools eastmoney --file examples/v033/information/eastmoney.example.json
.venv/bin/python -m yantra extensions add tools eastmoney_data --file examples/v033/information/eastmoney_data.example.json
.venv/bin/python -m yantra extensions add tools akshare --file examples/v033/information/akshare.example.json
.venv/bin/python -m yantra extensions add tools cls --file examples/v033/information/cls.example.json
```

妙想 API 工具直接使用项目的 HTTP 客户端，不运行上游脚本、不依赖技能指令。此前注册的 `skills/eastmoney` 已在本机停用；已有该技能的其他环境可执行 `extensions disable skills eastmoney`，只保留 tools。旧技能目录仅作为先前实现的参考，不能代表当前接入流程。

完成 key 和费率配置后启用认证工具：

```sh
.venv/bin/python -m yantra extensions enable tools fuyao
.venv/bin/python -m yantra extensions enable tools eastmoney
.venv/bin/python -m yantra extensions enable tools eastmoney_data
.venv/bin/python -m yantra extensions list
```

研究启动或恢复时通过 `--env-file .env` 加载凭据。工具命令仅收到 `env_refs` 指定的凭据，不会继承全部 `.env` 内容。默认角色为 finance／thesis／verification；工具名称和 input schema 由现有扩展模块注入模型。新妙想工具只引用 `MX_APIKEY`；旧资讯工具快照仍可使用 `EASTMONEY_APIKEY`。根文件将旧变量定义为 `${MX_APIKEY}` 引用，并放在 `MX_APIKEY` 定义之后，避免重复保存密钥和前向引用为空。

`.env` 是持久配置，CLI／ADK 启动后读取；`process.env.MX_APIKEY` 是 Node.js 进程内的访问方式，本项目 Python 工具读取同名环境变量。无需修改系统 shell 启动文件。

`extensions check tools 名称` 仅检查命令的可执行文件存在，不调用适配器、不检查 key、网络或 SDK 是否就绪，也不验证实时数据。实际只读接口探测必须独立记录。来源工具没有接入预留的 `SourcePipeline` 订阅接口，不会因注册而后台拉取信息。

## 查询范围

| 工具 | 输入示例（证券为受控示例，运行前替换） |
|---|---|
| `ext_fuyao` | `{"operation":"ticker-search","parameters":{"q":"示例公司","limit":10}}` |
| `ext_fuyao` | `{"operation":"prices-snapshot","parameters":{"thscodes":"600001.SH"}}` |
| `ext_fuyao` | `{"operation":"income-statements","parameters":{"thscode":"600001.SH","period":"annual","limit":4}}` |
| `ext_eastmoney` | `{"query":"示例公司 最近一期公告"}` |
| `ext_eastmoney_data` | `{"toolQuery":"示例公司 最近三年营业收入、净利润，注明期间和单位"}` |
| `ext_akshare` | `{"function":"stock_zh_a_hist","parameters":{"symbol":"600001","start_date":"20260101","end_date":"20260131","adjust":""},"limit":200}` |
| `ext_cls` | `{}` |

Fuyao 还支持 `valuations-snapshot`、`balance-sheets` 和 `cash-flow-statements`；财务报表支持 annual／quarterly 的最近 1–20 期模式。快照只查询显式指定的最多 20 个证券；不自动遍历全市场。

### 妙想直接 API 协议

统一基址为 `https://mkapi2.dfcfs.com/finskillshub/api/claw`，两个接口均为 POST JSON，请求头 `apikey` 为原始 `MX_APIKEY` 值，HTTP 客户端自动设置 `Content-Type: application/json`。

| 工具 | 路径 | JSON 请求字段 | 核对依据 |
|---|---|---|---|
| `ext_eastmoney_data` | `/query` | `toolQuery` | [官方金融数据包](https://marketing.dfcfw.com/res/download/A620260331IHX67H.zip)中的 `mx_data.py` |
| `ext_eastmoney` | `/news-search` | `query` | [官方搜索包](https://marketing.dfcfw.com/res/download/A620260331K5WDTK.zip)中的 `mx_search.py` |

两个工具保持原始 JSON 和来源端点，不生成上游脚本的 Excel／CSV／文本派生文件。行情、财务期间、单位、证券身份须按原始字段核验；自然语言查询和供应商解释不等于确定性财务计算。请求失败不会自动重试并重复消耗额度。官方说明中的 113 为每日次数上限、114–116 为密钥或鉴权错误；显式非零 `status/code` 返回会终止调用，供应商错误正文不向日志暴露。具体账户费率尚需按账户条款核实，不能由成功请求推断免费。

按本次用户最终选择，只接入金融数据和资讯搜索；智能选股、自选股管理、模拟组合及 AI 社区接口不注册。

AKShare 还支持 `stock_individual_info_em`、`stock_news_em`、`stock_financial_abstract`，通过 `parameters.symbol` 指定股票代码；新闻函数也接受关键词。仅允许这四个函数，不能执行任意 SDK 函数、代码或自选 URL。AKShare 输出保持中文字段和原始上游单位，缺失数值为 `null`；默认返回前 200 行，最多 1000 行，并用 `total_rows`／`truncated` 显示截断。历史行情须保留 `adjust`（不复权／qfq／hfq）及时间范围，不能将复权价格直接当作当时成交价。财务摘要仍须按上游文档核对单位和财务口径。

REST 响应包含来源标识、端点和 UTC 获取时间；AKShare 记录函数、参数、字段和 SDK 文档地址，该文档地址不是原始上游请求 URL。获取时间不代替公告发布时间或行情时间；只有上游提供的字段可作后两者的证据。REST 不跟随重定向；HTTP／JSON 错误、Fuyao 非零业务码、妙想显式非零状态和觅知显式失败状态会终止调用，远程错误正文不输出。输出大小和请求超时受现有扩展控制器限制。认证失败、反爬验证及服务限流须明确保留为资料缺口。

## 验证边界

`tests/test_information.py` 使用受控响应验证协议、白名单、参数、凭据拒绝、字段保留、截断、HTTP错误和JSON命令注册；`test_source_fallback.py` 及正式入口测试验证适用换源、搜索结果原文获取、报价与费用闸。离线测试显式阻断未模拟外部访问，不调用真实信息接口。读取公开文档或安装SDK不能当作认证API成功或实时行情验证；本轮三个新服务未进行真实探测，yfinance未安装。现有妙想及其他历史探测仍保留当时范围，实际记录见[验证索引](validation/README.md)，没有据离线夹具宣称真实端到端投研通过。
