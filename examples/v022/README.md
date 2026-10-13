# 新方法验收示例

`synthetic.py` 中的公司、财务、倍数、价格、定价配置均为明确标注的合成测试数据。测试后端通过现有 ADK controller/worker 编排调用主系统工具；不调用外部模型。

```bash
cd /Users/chillychili/Yantra
.venv/bin/python -m examples.v022.synthetic
```

生成 PE、PB、EV/EBITDA、单阶段 DDM 的状态、报告和 `calc:<hash>` 凭证，保存在 `examples/v022/output/`。研究 `partial` 与方法计算成功是不同状态，合成结果不能当真实投资结论。DCF 原有固定演示及回归仍保留；新建原始材料 DCF 要求 EBIT 桥接和有来源的少数股东权益。

本地材料验证另行运行：

```bash
.venv/bin/python -m examples.v022.local_materials
```

它只读取 `公开财报及数据/` 中两份 FY2025 财报和 `东财口径.xlsx`，使用人工核对的原文 EPS、证券类别、封面日期和表内价格。金额来自文件，不补造数字；不联网、不调用收费模型。流程后端仍是脚本，财报与行情文件没有经过外部真实性认证。山西汾酒封面只有月份，按月末记录保守可获得日期上界，并保留该说明，不能冒充精确披露日。

成功样本使用山西汾酒单一可比，形成条件性 PE 点值。拒绝样本使用相同真实文件，但不提取必要的可比价格，故没有有效倍数；控制器输出 `not_applicable`，不产生估值。两者均复算并保留研究缺口。摘要位于 `output/local/validation.json`。

SOTP、正常化盈利候选仍在原始 `交付物/yantra_valuation/sotp.py`、`normalized.py` 及其合成 demo 中，作为**独立原型**保留，不注册为正式方法；其中债务重复扣减、口径和敏感性缺口未验收。`nav.py` 实际只有 PB/DDM，不是资产重估 NAV。无需运行或覆盖原始交付物来执行上述新示例。
