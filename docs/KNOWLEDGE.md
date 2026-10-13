# 检索、共享知识与长期要求

适用版本：0.3.3。通用安装与研究操作见[使用指南](USAGE.md)，模型与扩展配置见[能力配置](CAPABILITIES.md)。ADK为2.11.0。既有估值方法、授权、否决、费用预留及恢复语义保留，历史运行不迁移、不扫描、不自动导入。新库只收集新版session明确输入和实际取得的资料。

## 正式数据与派生索引

CLI默认`.yantra/knowledge/`；`start --knowledge-dir PATH`可指定，API默认`<runs-dir>/knowledge/`。共享库不同于每次运行的`runs/run-ID/`工作档案。公司身份由证券身份或明确company ID关联，不能仅用同名公司合并；市场、交易所、证券代码和股份类别属于单独security ID。

```text
.yantra/knowledge/
  knowledge.sqlite       # 正式公司/证券、来源、修订、提议、核验、引用、快照及操作
  objects/<sha256>       # 原始字节，按内容共享；获取渠道和session引用分别登记
  reports/run-ID/        # 完整报告文件
  commit.lock            # 串行提交，等待仍消耗触发session的墙钟预算
  index-runs/index-ID/   # 独立语义查询/重建的显式预算账本
```

SQLite中的`fulltext`和`vectors`是派生数据。正式事实、修订或核验记录不能通过删除索引重建；正式数据库需按普通数据备份保留。向量保存模型、配置指纹、维度和index version；不同指纹不混用。FTS5索引采用英文词和中文单字／二字组，中文多字查询要求相关二字组共同命中。

文件提交先将原始字节与pending journal事务写入SQLite，再原子写文件并完成journal。中断后恢复相同字节；已确认的永久删除也登记待清理文件，重启继续完成授权清理。知识修订及引用在同一SQLite事务提交；派生索引失败记降级，正式修订仍保留，可重建。全库采用一把提交锁串行写入，包括同公司更新；基准版本CAS保证旧任务不能覆盖新修订。该保守方案适合本地首版，无独立向量数据库服务。

知识引用是entry ID＋revision ID；证据是source ID＋sha256＋anchor/locator。相同字节只占一个对象，渠道和获取记录并存，不因重复获取增加证据独立性。

## 研究加载、核验和历史比较

```sh
.venv/bin/python -m yantra start --company 虚构公司 --market CN \
  --security-code 600001 --exchange SSE --share-class A --trading-currency CNY \
  --material /absolute/path/new-report.pdf --mode real-model \
  --question '汽车业务与集团盈利是否一致' --topic 汽车 \
  --knowledge-dir .yantra/knowledge --env-file .env
```

跨市场／类别属于同一法律主体时，明确`--company-id`；未确认的不同证券不自动合并。`knowledge status`给出公司和证券目录。`--question`及`--topic`可重复，研究问题用于检索公司以外的相关知识。

启动上下文受字节预算限制，加载来源目录、适用事实、R1-R4重点历史论点及问题；原文通过`knowledge_read`按需读取。分类包括verified fact、pending/rejected候选、historical judgment、manual input以及单独的user requirements。没有命中重点规则的材料仍可归档和查询。

历史论点始终是参考，不能取得当前已确认结论资格。事实复用仍检查字节hash、期间、证券类别、财务币种／单位／合并口径、信息截止、更正、冲突和原核验缺口。同期间同口径冲突保留全部候选，不选择最新值。历史截止筛选事实的信息披露时间；论点与问题还检查实际记录时间，禁止把事后修订回填为当时观点。未知原始披露时间沿用既有历史截止排除规则。

相同hash及解析／OCR配置可复用保存的解析区域；模型仍可为新缺口提取或补查。每次研究继续主动查披露、反证、新变化及市场基准，历史命中不表示资料充分。

每次thesis结果先固定独立判断快照，然后比较历史重点论点与未关闭问题，记录支持、削弱、反驳、scope/假设变化或不足及后续核查任务。比较进入主Agent和后续Worker上下文，由Agent补查并提议修订；新thesis成果生成新快照，旧快照不覆盖。独立指证据和核验独立，模型已经接触历史观点。业务和集团scope不同不能直接支持／反驳同一论点。

程序工具：`knowledge_search`、`knowledge_read`、`knowledge_propose`；只有verification任务得到`knowledge_review`。控制器独立核验批次使用相同后端预算，并将结果绑定提议hash。投资判断状态与核验状态分离；核验问题只认证依据与核查价值，不认证未知答案。研究角色没有commit工具。新候选可归档为pending/rejected，既有正式条目的状态变更须经核验及CAS后提交。独立否决不能被后来的passed覆盖。

`completed/partial/failed/cancelled`均归档已经取得的字节及提取结果，运行状态不改变条目核验状态。测试注入后端若未实现逐条核验，候选保持pending，有明确记录。

## 模型、费用和重建

embedding注册沿用模型配置中心，密钥只填写环境变量名。示例为协议形状，模型／端点／价格必须由使用者提供实际有效值：

```json
{
  "kind": "embedding", "provider": "openai-compatible",
  "model_id": "YOUR_EMBEDDING_MODEL", "base_url": "https://YOUR_PROVIDER/v1",
  "api_key_env": "EMBEDDING_API_KEY", "dimensions": 1024,
  "pricing": {"input_per_million": 1, "output_per_million": 0,
              "source": "REPLACE_WITH_ACTUAL_PRICE_SOURCE", "as_of": "2026-10-13"}
}
```

```sh
.venv/bin/python -m yantra models add embedding-profile --file /absolute/path/embedding.json
.venv/bin/python -m yantra models select embedding embedding-profile
.venv/bin/python -m yantra knowledge rebuild --budget '{"max_cost_cny":1,"run_seconds":60,"max_models":1}'
.venv/bin/python -m yantra knowledge rebuild --vectors --embedding embedding-profile \
  --budget '{"max_cost_cny":2,"run_seconds":300,"max_models":20}'
.venv/bin/python -m yantra knowledge query '资本投入与盈利转化' \
  --budget '{"max_cost_cny":1,"run_seconds":60,"max_models":10}'
```

研究内部embedding费用、时间及请求计入触发session账本，沿用保守token预留和人民币上限。仅缺失或内容变化的向量生成；换模型生成对应指纹的新索引，旧模型索引不参与新模型检索。配置缺失、价格不明、错误或预算不足记录降级，继续元数据／关键词。缺用量保留预留，未知费用不记零。embedding重建不会重置已有session预算；独立CLI语义调用及所有索引重建均创建单独且明确限额的index session，必须提供金额、墙钟和请求数。全文重建消耗墙钟而不调用模型。CLI不带预算的query只执行元数据／关键词。`run_seconds`不得超过7200，金额必须低于50元。

## 查询、编辑和删除

```sh
.venv/bin/python -m yantra knowledge status
.venv/bin/python -m yantra --output json knowledge query '毛利率' --company-id COMPANY_ID --period FY2025 --kind fact --cutoff 2026-02-01 \
  --limit 12 --context-bytes 16000 --summary-only --topic 盈利质量
.venv/bin/python -m yantra knowledge view KNOWLEDGE_ID --revision-id REVISION_ID
.venv/bin/python -m yantra knowledge source SOURCE_ID
.venv/bin/python -m yantra knowledge edit-fact KNOWLEDGE_ID \
  --changes '{"value":12,"raw_value":"12"}' --reason '人工更正，需重新核验'
.venv/bin/python -m yantra knowledge delete-source SOURCE_ID
.venv/bin/python -m yantra knowledge delete-source SOURCE_ID --session RUN_ID
.venv/bin/python -m yantra knowledge delete-source SOURCE_ID --impact
.venv/bin/python -m yantra knowledge delete-source SOURCE_ID --permanent --confirm '影响清单中的完整confirmation token'
```

所有非交互命令支持[统一输出与错误契约](USAGE.md#agent非交互调用与输出契约)。默认text继续显示原有JSON形状；`--output json`增加统一响应包装，Agent须从data读取原结果。query透传已有 `KnowledgeQuery`：`--limit`默认12、范围1至100；`--context-bytes`默认16000、范围1至100000；`--summary-only`仅从来源条目省略原文text并保留固定版本读取提示；可重复 `--topic` 按任一主题过滤。字节上限约束服务的结果预览内容，不包含CLI响应包装，条目超过上限时可能返回空结果。CLI不另行实现检索或摘要。

`--changes-file`、`--budget-file`及preferences的 `--parameters-file`读取UTF-8 JSON对象，与各自内联选项互斥；缺文件、非法对象或非有限数值报错。重建仍必需显式预算；不带预算的query保持元数据／关键词路径。索引session、查询日志和固定版本pin仍按既有服务保存，查询不是完全无副作用的数据库只读操作。

view返回修订历史、原始证据及绑定hash的核验记录。编辑创建manual新版本并保留旧值、理由和出处，数值、单位、期间、币种、范围、主张和来源实质变化撤销旧核验资格。只改规范化空白不会创建实质修订；不能设置passed恢复资格。没有新的原文支持时人工输入仍是pending。禁止直接编辑数据库／文件并自动回写。

默认删除是退出后续检索和自动加载的软删除，旧session的固定读取仍能追溯。`--session`只移除该session引用，保留共享资料。永久删除先查看影响，确认token绑定当前影响范围；引用变化使旧token失效。删除对象、受影响结构化内容及索引，保留ID、删除审计和缺失来源关系；已登记session原始副本删除并写`source-missing.json`。其他hash的对象不删除。受影响运行status能看到无法完整复现，replay明确报错；不会重做远程判断假装复现成功。用户最初提供的文件不由共享库管理，永不代删。自动去重和索引重建不触发永久删除。

## 明确长期要求

```sh
.venv/bin/python -m yantra preferences remember '以后默认研究18个月' \
  --meaning '默认研究期限18个月' --parameters '{"horizon":"18个月"}' --scope global
.venv/bin/python -m yantra preferences list
.venv/bin/python -m yantra preferences list --history
.venv/bin/python -m yantra preferences remember '此公司改为24个月' --meaning '公司默认期限24个月' \
  --parameters '{"horizon":"24个月"}' --scope COMPANY_ID --id PREFERENCE_ID
.venv/bin/python -m yantra preferences forget PREFERENCE_ID
```

只保存显式remember指令，不推断普通任务偏好。保存原话、含义、来源、范围、时间与版本，并回显scope及application。结构化defaults支持horizon、已核验内置framework、questions、topics、notes；其他明确要求可放notes供本次Agent参考，不能覆盖程序硬限制或否决。特定公司使用明确company ID，不能从多个同名公司猜测。

优先级为本次显式参数＞公司要求＞全局默认。新默认在调度边界加载；显式参数保留。`remember/forget --apply-to RUN_ID --modify-this-task`明确允许修改本次对应字段，在边界应用并使相关论点、假设、估值和核验失效。撤销保留历史并重新计算默认值。保存和应用状态分别记录。CLI在remember/forget持久化之前预检目标是否为running/waiting的v0.3运行、知识库路径及公司范围。运行不存在为 `run_not_found`，其他目标约束为 `preference_target_invalid`（退出2），不修改长期要求。保存后再次检查，再写调度请求；成功只表示 `application=pending_at_scheduling_boundary`，不是研究已经应用。

跨SQLite修改与运行请求文件没有联合事务。保存后若运行状态变化或请求写入失败，返回退出1及 `error.code=preference_application_failed`；`error.details.completed`列出已完成的保存／撤销、要求ID及保存版本，`error.details.application`列出失败目标与安全错误。`retryable=false`，不要重新新增；先用preferences list/list --history检查，再使用 `--id` 明确更新已有要求或在有效目标上重试应用。forget的已完成结果含id及revoked，撤销历史仍保留。不存在的要求返回 `preference_not_found`。

ADK入口支持`/memories`、`/forget ID`及：

```text
/remember {"original":"记住期限18个月","meaning":"默认研究18个月","parameters":{"horizon":"18个月"},"scope":"global"}
```

未指定scope时已有明确session绑定公司，否则全局。可加`id`显式更新；`apply_to_current:true`申请当前任务应用，`modify_this_task:true`明确覆盖相应本次显式参数。普通聊天不自动保存，也不创建新聊天产品。

## v0.3.1 信源预留

`SourceAdapter.discover(DiscoveryRequest,cursor)`返回渠道中立Candidate；Candidate可用URL、external ID或locator，未知值保留None。发现输入含问题、目标、截止及原预算check/timeout上下文。`fetch(candidate,budget)`返回FetchResult原始字节、suffix、获取时间、状态及失败原因。SourcePipeline执行共同的截止、限长、取消／时间检查、归档、去重与解析入口。

元数据包括adapter/source ID、external ID、原URL／canonical URL、标题、发布时间及依据、内容类型、原内容引用、获取状态及失败。web_search的DDGS发现与HTTP/primp原文获取已接入；离线适配器测试同一入口。不透明cursor/checkpoint保存在SQLite，未来分页／RSS进度可复用。

此统一信源管道尚未接入 RSS/Atom 订阅、轮询、自动唤醒、专用官方披露或第三方数据服务；通用插件平台未实现。摘要及接口元数据不能取得原文证据资格，多渠道同一 hash 仍是同一份证据。

v0.3.3 的扶摇、东方财富、AKShare 与 98dou 信息工具通过[命令扩展](INFORMATION_SOURCES.md)接入，保存响应凭证；未接入此处预留的订阅／checkpoint 调度，也不因搜索摘要而获得原文证据资格。
