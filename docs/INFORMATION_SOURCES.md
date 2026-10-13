# 信息来源扩展

适用版本：0.3.3。四个来源使用现有 JSON 命令工具和角色授权；统一注册、预算、原始响应快照及凭据脱敏规则见[能力配置](CAPABILITIES.md#skillsmcp-与-tools)。新增来源只按专业角色的调用获取信息，不提供订阅、后台轮询或自动监控。

## 来源与人工准备

| 来源 | 实际接入 | 凭据变量及人工步骤 |
|---|---|---|
| [同花顺 Fuyao](https://fuyao.aicubes.cn/docs/quickstart/) | REST GET；`X-api-key` 请求头 | `FUYAO_API_KEY`：使用同花顺账号登录，在「API Key 管理」签发；权限不足需平台开通 |
| [东方财富妙想官方接入说明](https://marketing.dfcfw.com/res/download/A620260623NIYC2U.md) | 两个独立 POST API：金融数据、资讯搜索；`apikey` 请求头 | `MX_APIKEY`：在[妙想 Skills 页面](https://dl.dfcfs.com/m/itc4)领取 Key；本项目直接调用 API，不安装技能包 |
| [AKShare](https://akshare.akfamily.xyz/data/index.html) | Python SDK 白名单函数，访问各函数的上游站点 | 无 API Key；安装可选依赖 |
| [觅知 API 财联社电报](https://api.98dou.cn/doc/hotlist/cls/all.html) | GET `https://api.98dou.cn/api/hotlist/cls/all` | 文档标注免费，无参数、无需令牌；这是第三方聚合接口 |

妙想协议核实日期：2026-10-14，其他来源核实于 2026-10-13。Fuyao 全量定义见其[官方聚合文档](https://fuyao.aicubes.cn/llms-full.txt)；AKShare 函数与字段见[股票数据字典](https://akshare.akfamily.xyz/data/stock/stock.html)。提供者及上游网站可能调整权限、字段、频率限制或服务可用性。

在仓库根目录 `.env` 中填写真实值；只填写原始 key，无需添加 `Bearer`。这些真实凭据不写入 Git、扩展描述或研究快照：

```dotenv
FUYAO_API_KEY=<YOUR_FUYAO_API_KEY>
MX_APIKEY=YOUR_MX_APIKEY
```

占位符无法用于真实请求。为用户手动补全凭据保留的认证扩展默认为停用。官方资料未给出 Fuyao 和东方财富的每次调用人民币费用上界；模板 `cost_cny: 0` 仅为待确认声明值，不表示已确认服务免费。用户应在账户中核实费率：如果收费，填写每次调用的保守人民币上界、`cost_source` 和 `cost_as_of` 后重新注册，再启用。觅知免费及 AKShare SDK 免费不代表结果已获独立核验或无限访问权限。

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

`tests/test_information.py` 使用受控响应验证协议、白名单、参数、凭据拒绝、字段保留、截断、HTTP 错误和 JSON 命令注册；不访问真实信息接口。读取公开文档及安装 SDK 是实际联网操作，不能当作认证 API 成功或实时行情验证。妙想认证探测与独立真实只读探测的结果和限制见对应版本的验证记录；没有执行完整实时投研。
