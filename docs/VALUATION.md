# 估值、授权与复算

适用版本：0.4.0（Unreleased）。安装和研究启动见[使用指南](USAGE.md)。

本版保留 `finance → thesis → valuation → verification → report` 和价值、质量、变化三个研究框架。方法选择由提案和证据决定；没有框架到模型的一对一映射，也没有新增控制器或每方法智能体。

## 运行与验证

以下命令从仓库根目录执行。

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m examples.v022.synthetic
.venv/bin/python -m examples.v022.local_materials
```

真实材料验证需要现有 `公开财报及数据/` 文件。缺文件时应补充真实文件，不能用合成数据代替。示例不会发起收费模型调用。原始研究使用 `start --material ... --mode real-model`，读取用户明确配置的模型与检索服务。本版真实服务验证范围见[v0.3验收](validation/v0.3/README.md)。

## 方法与权威契约

`ValuationProposal.model` 和 `Assumptions.model` 支持 `dcf / pe / pb / ev_ebitda / ddm`，提案另可为 `not_applicable`。工具名仍为 `valuation`，请求包含 `model / parameters / assumptions_version`；程序注入当前授权 `basis`，不能让调用者替换依据。校验当前方法、参数、版本、任务快照和原始来源，人工模式也不豁免硬门槛。

新方法参数由 `yantra.schemas.MethodInputs` 定义；工具描述提供完整 JSON Schema。`metric`、净债务、少数股东权益、股数、基准及可比两端均保留原始 `FinancialRecord`。每条正式来源都需要当前运行的 `src-…:A…`、原始哈希、原文数值和日期；财务输入必须等于已接受的提取记录。校验证券代码、股本类别、币种、单位、期间、FY/TTM/forward、合并/归母口径和材料截止。来源定位证明引用可追踪，经济含义和原文件真实性仍属于独立审阅范围。

| 方法 | 硬条件和计算 | 区间与限制 |
|---|---|---|
| PE | 正 EPS、归母基本或稀释每股口径；同类同期间同日 PE 可比集；EPS×PE 中位数 | 同一 PE 可比集 min/max；单一可比为点值；非置信区间 |
| PB | 正 BVPS、归母每股净资产口径；同类 PB 可比集 | 同一 PB 可比区间；没有资产重估 NAV |
| EV/EBITDA | 正合并 EBITDA×同口径倍数；权益=EV−净债务−少数股东权益；除匹配总普通股数 | 缺桥接参数只保留企业价值，权益及每股值为 null；不以零补缺 |
| 单阶段 DDM | 年度每股 D0、股权成本 k、长期增长 g；D1=D0×(1+g)，价值=D1/(k−g)；0≤g<k<1 | k±1个百分点、g±0.5个百分点的9格敏感性，注明无效格；没有与PB自动取均值 |
| DCF | 原有增长、折现、预测年数、净债务、股数等参数；新研究增加明确 `minority_interest` | 原有三情景、9格敏感性继续沿用；新凭证为 `dcf_bridge` |

金额统一计算为**百万元**，股数为**百万股**，输入支持元/股、万元等显式单位转换；每股 EPS/BVPS/股利/价格不乘除百万，比例为 `ratio`。交付原型中的 `fcff_base / forecast_growth / wacc / forecast_years / value_per_share` 在主接口对应 `base_fcf / growth_rate / discount_rate / years / per_share_value`。本版使用主系统 Pydantic 契约，没有复制原型 dataclass。

财务记录可用 `information_kind` 区分实际信息类型；forward 每股/经营输入必须标注 `prediction`。旧财务快照未带该字段时按历史披露表呈现，新增可选字段不改变旧算法。

新建原始材料 DCF 的 FCFF 准入优先采用 `EBIT×(1−税率)+折旧摊销−资本开支−ΔNWC`，要求同公司、期间、币种、合并口径；资本开支为正支出数，ΔNWC 保留正负。税率须在[0,1]。不能以 `operating_profit` 的名称直接替代 EBIT 口径，也不能只填 `unlevered` 标签就批准 OCF 倒推。OCF−Capex 仍作为分析指标保存；未经核对的 OCF 倒推暂不开放给新研究，这是项目准入规则。旧快照及固定演示的明确旧算法仍可复现。

PE 的 `metric.share_scope` 必须明确为 `basic EPS` 或 `diluted EPS`，PB 必须为 `ending book value per share`；对应可比倍数的 `per_share_basis` 必须一致。由价格与财报重建倍数时，可比财报自身的分母口径也必须一致，不能混用基本与稀释EPS。

## 手动修改或切换方法

使用 `resume RUN_ID --decision modify --parameters JSON字符串`；`--parameters`接收JSON内容，不能直接传JSON文件路径。旧式 DCF 参数字典仍可做增量改参；新方法或方法切换使用完整对象：

```json
{"model":"pb","parameters":{"…":"完整MethodInputs，使用当前材料真实来源"},"basis":{"metric":"论点和参数依据","comparable_rule":"可比集筛选理由"}}
```

上例展示请求形状，不是可执行财务数据。实际请求须使用完整的当前原文记录；来源无效会被拒绝。运行中的修改通过已有 `parameters.request.json` 机制应用。换方法或改参增加版本、保存历史并使旧估值和核验失效；替换上游财务也撤销旧授权。独立核验否决不能由数学成功或主 Agent 覆盖。

## 市场基准和复算

基准分为 `current_price / reference_price / consensus_target / earnings_expectation`。参考价不能叫一致预期；一致基准还需原文提供机构及样本量。价格比较以每股同币种、证券类别和观察日期核对；经营预期比较需同指标、期间、盈利基础与单位，并保存本方预测及依据。`own_value` 使用该指标原始单位，程序统一转换后比较。差异=`本方/基准−1`，不是预期收益率。无可靠基准为 `unresolved`、差异为 null。

thesis 保存解释和假说；valuation 保存结构化基准及差异，程序拒绝两者采用不同基准，报告展示计算使用的同一基准。缺口不会填零，也不会因可选基准缺失伪造数学结论。

凭证继续使用主系统 `calc:<hash>` 索引，保存 `kind / inputs / formula / results / assumptions_version`，新方法另存 `model / basis / schema_version` 和输入内完整来源。`replay` 重新构建输入执行数学计算、比较结果和哈希，再执行程序核验；未知 kind 或方法明确失败，篡改结果后重新生成哈希也不能通过数学复算。旧 `dcf`、`raw_financial_metrics`、`unit_conversion` 仍可重算；旧记录没有方法时解释为 DCF。历史输入与当前提取不一致时，允许复现历史数学，但保留核验失败，不把旧问题修饰为通过。

业务范围通过`FinancialRecord.business_scope`分别记录；分部数据与集团数据分别计算，业务指标不能直接成为集团估值输入。历史快照的算法、引用与原核验限制保持原版本含义。

## 首轮模型选择与暂定情景

新研究在已记录的授权范围内，依据实际业务、盈利和现金流证据选择主模型。`ValuationProposal.selection_reason` 保存依据，`unused_models` 说明其他方法未采用原因；`auxiliary_models` 是可选的独立提案列表，不要求计算全部方法。正式计算时 `Assumptions.auxiliary_models` 按方法保存独立参数、依据、版本和状态；各自通过来源硬条件，各自持有凭证。辅助模型硬条件不足时保留缺口，不以主模型授权代替。结果分开保存数值、公式、参数和差异，不自动平均；差异不证明某个模型更正确。

长期授权或本次覆盖必须明确同意 `models` 及 `assumptions`，交付方式本身不授权。未授权或逐节点未回复时 `authorization_source=provisional`、`confirmed=false`；计算准入仅对这个明确标记的情景扩展，来源、FCFF／权益桥接、币种、期间、版本及独立核验均保留。报告首页不展示正式目标价或方向性倾向，具体值放在财务模块并标注参数、依据和待确认项。核验否决仍阻止正式结论。

暂定报告后 `resume RUN_ID --decision confirm` 只明确确认当前主模型，创建新版本、失效受影响成果、重算及核验，保留原文件。确认或改动辅助模型须在 `--decision modify` 的完整对象中另列 `auxiliary_models`，每项有 `model / parameters / basis`。单次修改不会更改长期设置。仅有研究、没有适用模型时仍交付实质分析；不补零或强迫估值。

用户可通过 modify 的 `choices` 对象选择 `perspective`（内置框架标识）、编辑 `risk`（按既有id只写user_level/user_opinion）、`disconfirmation`、`tracking`（完整结构化列表），或 `expectations`（有来源的own_forecasts/external_forecasts）。风险事实、来源、系统等级及独立否决不可替换；分歧并列保存。修改形成版本并重算、核验。运行中的请求在原调度边界处理，不新增自动订阅、提醒或数据采集。

## 财务指标与异常调查信号

`financial_analysis.FIELD_MAPPING` 给出精确科目名，公式见 `FORMULAS`。未知科目不以相近字段代替。金额归一为百万元，比例为ratio；证券、股类、币种、合并／归母、集团／业务及期间分别保存。归母净利润使用 `net_profit_attributable`，归母净资产使用 `equity_attributable`，集团净利润不能替代。

| 类别 | 公式和口径 |
|---|---|
| 盈利 | 毛利率=(收入−成本)/收入；归母净利率=归母净利润/收入；ROE=归母净利润/平均归母权益；销售、管理、财务费用分别/收入 |
| 营运 | 应收周转=收入/平均应收；存货周转=成本/平均存货；周转天数=本期天数/周转率 |
| 偿债 | 负债率=总负债/总资产；流动比率=流动资产/流动负债；速动比率=(流动资产−存货)/流动负债；有息债务=明确的短借+长借+债券 |
| 现金质量 | 净现比=经营现金流/归母净利润；OCF−Capex与EBIT桥接FCFF继续分别保存，不混用 |

平均余额=(期初+期末)/2。年度可复用可比前一年度期末；中期、单季、TTM须显式期初。FY采用365天约定，明确年度日期时按365/366天；中期、单季及TTM天数只用明确起止日。周转天数保留小数，避免中间舍入影响信号。FY、`2025H1/H2`、单季`2025Q1-Q4`及明确标记／起止日的TTM分别计算，不从累计中报推算单季。同比只比较同类同槽的前一年，环比费用变化和连续两期信号需要相邻可比期间；TTM未定义观察频率时不声称连续触发。

固定信号：连续两期净现比<0.5；应收或存货同比增速连续两期超过营收同比20个百分点；商誉/集团净资产>30%；销售或管理费用率相邻一期变化绝对值>5个百分点；年度负债率增加>10个百分点；单季归母利润同比变化绝对值>50%。阈值对所有实际业务相同，严格大于／小于，不由模型修改。大额减值和“无业务解释”只保留定性关注，无编造量化门槛。

缺失、零／负分母、缺期初、身份／口径冲突或不可比期间保存 `unavailable/gaps`，不补造。资产=负债+权益按归一金额的0.000001百万元精度核对；失败暂停资产／权益相关指标，保留可开展的其他分析。可显式提取 `net_profit_attributable_cross_statement / operating_cash_flow_cross_statement` 做同期间跨表核对；没有对应披露时不宣称跨表已核验。公式、原始输入、数据质量、来源和结果在 `raw_financial_metrics` 凭证中保存，历史凭证不带新筛查时仍复算旧算法。

`BusinessProfile` 描述产品／服务、收入构成、客户、销售结算及经营周期，并保留来源与未知项。异常解释逐项绑定 `signal_id`、可能原因、涉及业务、支持／反对来源和后续核查；不能删除触发信号。仅有集团数据时保持“无法定位到具体业务”，业务原因只是待验证解释，不分摊集团数值。

## 经营预测比较与清单

`compare_expectations` 是 thesis 的确定性工具。`own_forecasts` 保存本方数值、口径、预测期间、来源、假设依据和兑现条件；`external_forecasts` 保存已接受的原始预测记录、机构、日期、样本范围及 `consensus/research_sample`。核对同指标、证券／股类、期间、币种、单位、集团／分部和合并／归母口径；一致预期额外要求原文的机构和样本量，不因一份研报声称完整一致预期。绝对差=本方−外部，相对差=本方/外部−1（外部分母必须为正，否则为null）。

没有外部基准仍可通过工具保存有依据的本方预测及兑现条件，差异列表为空，并注明“本次未取得可比的外部预测基准”；没有依据的本方数值也不补造。新报告的估值相对价格比较在财务估值模块，经营预测比较在第八模块；不输出价格隐含假设或“市场已经定价”的断言。`expectation_comparison` 凭证支持离线复算及来源核验。

风险对象包含描述、分类、建议等级、来源、触发信号及依据，可另有user_level/user_opinion；反证包含对应论点、条件、频率及状态；跟踪包含名称、口径、频率、阈值、当前值及来源。已知状态／当前值须有来源，未知不伪造。旧式字符串清单兼容为结构化未知值，不能因此声称完成风险分级或监测。持续跟踪仅生成清单。
