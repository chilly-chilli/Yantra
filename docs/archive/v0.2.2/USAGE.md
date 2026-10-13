# 估值集成运行说明

> 历史记录：保留该版本的功能、日期、结果和限制。当前入口见[文档索引](../../README.md)。

本版保留 `finance → thesis → valuation → verification → report` 和价值、质量、变化三个研究框架。方法选择由提案和证据决定；没有框架到模型的一对一映射，也没有新增控制器或每方法智能体。

## 运行与验证

```bash
cd /Users/chillychili/Yantra
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m examples.v022.synthetic
.venv/bin/python -m examples.v022.local_materials
```

真实材料验证需要现有 `公开财报及数据/` 文件。缺文件时应补充真实文件，不能用合成数据代替。示例不会发起收费模型调用。常规 `research` CLI 仍使用用户配置的真实模型与检索，具体启动方式沿用 README；本版没有验收真实 LLM 端到端经济判断。

## 方法与权威契约

`ValuationProposal.model` 和 `Assumptions.model` 支持 `dcf / pe / pb / ev_ebitda / ddm`，提案另可为 `not_applicable`。工具名仍为 `valuation`，请求包含 `model / parameters / assumptions_version`；程序注入当前授权 `basis`，不能让调用者替换依据。校验当前方法、参数、版本、任务快照和原始来源，人工模式也不豁免硬门槛。

新方法参数由 `yantra.schemas.MethodInputs` 定义；工具描述提供完整 JSON Schema。`metric`、净债务、少数股东权益、股数、基准及可比两端均保留原始 `FinancialRecord`。每条正式来源都需要当前运行的 `src-…:A…`、原始哈希、原文数值和日期；财务输入必须等于已接受的提取记录。校验证券代码、股本类别、币种、单位、期间、FY/TTM/forward、合并/归母口径和材料截止。来源定位证明引用可追踪，经济含义和原文件真实性仍属于独立审阅范围。

| 方法 | 硬条件和计算 | 区间与限制 |
|---|---|---|
| PE | 正 EPS、归母基本每股口径；同类同期间同日 PE 可比集；EPS×PE 中位数 | 同一 PE 可比集 min/max；单一可比为点值；非置信区间 |
| PB | 正 BVPS、归母每股净资产口径；同类 PB 可比集 | 同一 PB 可比区间；没有资产重估 NAV |
| EV/EBITDA | 正合并 EBITDA×同口径倍数；权益=EV−净债务−少数股东权益；除匹配总普通股数 | 缺桥接参数只保留企业价值，权益及每股值为 null；不以零补缺 |
| 单阶段 DDM | 年度每股 D0、股权成本 k、长期增长 g；D1=D0×(1+g)，价值=D1/(k−g)；0≤g<k<1 | k±1个百分点、g±0.5个百分点的9格敏感性，注明无效格；没有与PB自动取均值 |
| DCF | 原有增长、折现、预测年数、净债务、股数等参数；新研究增加明确 `minority_interest` | 原有三情景、9格敏感性继续沿用；新凭证为 `dcf_bridge` |

金额统一计算为**百万元**，股数为**百万股**，输入支持元/股、万元等显式单位转换；每股 EPS/BVPS/股利/价格不乘除百万，比例为 `ratio`。交付原型中的 `fcff_base / forecast_growth / wacc / forecast_years / value_per_share` 在主接口对应 `base_fcf / growth_rate / discount_rate / years / per_share_value`。本版使用主系统 Pydantic 契约，没有复制原型 dataclass。

财务记录可用 `information_kind` 区分实际信息类型；forward 每股/经营输入必须标注 `prediction`。旧财务快照未带该字段时按历史披露表呈现，新增可选字段不改变旧算法。

新建原始材料 DCF 的 FCFF 准入优先采用 `EBIT×(1−税率)+折旧摊销−资本开支−ΔNWC`，要求同公司、期间、币种、合并口径；资本开支为正支出数，ΔNWC 保留正负。税率须在[0,1]。不能以 `operating_profit` 的名称直接替代 EBIT 口径，也不能只填 `unlevered` 标签就批准 OCF 倒推。OCF−Capex 仍作为分析指标保存；未经核对的 OCF 倒推暂不开放给新研究，这是项目准入规则。旧快照及固定演示的明确旧算法仍可复现。

PE 的 `metric.share_scope` 必须明确为 `basic EPS` 或 `diluted EPS`，PB 必须为 `ending book value per share`；对应可比倍数的 `per_share_basis` 必须一致。由价格与财报重建倍数时，可比财报自身的分母口径也必须一致，不能混用基本与稀释EPS。

## 手动修改或切换方法

沿用 `respond --decision modify --parameters 参数JSON文件`。旧式 DCF 参数字典仍可做增量改参；新方法或方法切换使用完整对象：

```json
{"model":"pb","parameters":{"…":"完整MethodInputs，使用当前材料真实来源"},"basis":{"metric":"论点和参数依据","comparable_rule":"可比集筛选理由"}}
```

上例展示请求形状，不是可执行财务数据。实际请求须使用完整的当前原文记录；来源无效会被拒绝。运行中的修改通过已有 `parameters.request.json` 机制应用。换方法或改参增加版本、保存历史并使旧估值和核验失效；替换上游财务也撤销旧授权。独立核验否决不能由数学成功或主 Agent 覆盖。

## 市场基准和复算

基准分为 `current_price / reference_price / consensus_target / earnings_expectation`。参考价不能叫一致预期；一致基准还需原文提供机构及样本量。价格比较以每股同币种、证券类别和观察日期核对；经营预期比较需同指标、期间、盈利基础与单位，并保存本方预测及依据。`own_value` 使用该指标原始单位，程序统一转换后比较。差异=`本方/基准−1`，不是预期收益率。无可靠基准为 `unresolved`、差异为 null。

thesis 保存解释和假说；valuation 保存结构化基准及差异，程序拒绝两者采用不同基准，报告展示计算使用的同一基准。缺口不会填零，也不会因可选基准缺失伪造数学结论。

凭证继续使用主系统 `calc:<hash>` 索引，保存 `kind / inputs / formula / results / assumptions_version`，新方法另存 `model / basis / schema_version` 和输入内完整来源。`replay` 重新构建输入执行数学计算、比较结果和哈希，再执行程序核验；未知 kind 或方法明确失败，篡改结果后重新生成哈希也不能通过数学复算。旧 `dcf`、`raw_financial_metrics`、`unit_conversion` 仍可重算；旧记录没有方法时解释为 DCF。历史输入与当前提取不一致时，允许复现历史数学，但保留核验失败，不把旧问题修饰为通过。
