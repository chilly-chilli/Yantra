# Yantra v0.2 验收记录

日期：2026-10-13。结论：**主要实现与自动化边界测试已交付，但完整发布验收未通过。** 两家有真实原始解析、主动联网、真实模型调用和可定位成果；不能将测试通过或 partial 文件生成等同于全部验收完成。

后续配置调整：按用户要求，新建完整研究任务的默认时间上限与最大可配置值改为 7200 秒（2 小时），其中预留 30 秒收尾。下列真实运行数据记录的是调整前的预算和结果；已保存的截止时点保持其历史含义。

## 自动化验证

Python 3.12、ADK 2.11.0；31 项测试通过。验证命令：

```sh
.venv/bin/python -m unittest discover -s tests -v
uv pip check --python .venv/bin/python
```

现有 ADK 结构、原生 Runner／工具事件、回调、持久化 API 会话、并发隔离、原版本确认与修改复算继续通过。最初一个测试受用户应用 `.env` 覆盖测试路径影响而失败，测试现在隔离 ADK dotenv 加载；没有修改用户凭据文件。

新增验证包括：中英真实 Tesseract 图片及扫描 PDF 识别；文本 PDF、Word 段落／表格、Excel 单元格、PPT 幻灯片、HTML、CSV、JSON；原始数字、括号负数、未来日期拒绝；模型提前乘单位因子的透明纠正；财报标题及跨语言查询选区；两种授权与修改重算；原子人民币预留、用量核算及缺失用量保留；人工等待截止；动态第三层实际 ADK 执行、第四层与工具越权阻止；共享读取、消息及批准转派；核验否决；自定义框架核验并用于当前运行；失败补查保留底表；固定字节复算及草稿不可提前选用。

测试后端为显式测试替身，没有远程模型请求；格式测试执行实际解析及 OCR，不能作为所有复杂文件布局、财务口径或经济判断都准确的承诺。测试中的临时样本为虚构材料。OCR 语言包位于 `.venv/share/tessdata/`。依赖检查确认已安装包兼容。

## 真实联网与模型运行

模型使用用户本地配置的 DeepSeek Flash 及 Google ADK LiteLlm 路径；没有输出密钥。费用使用本次核对的[官方人民币高峰单价上限](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)（输入缓存未命中 2 元／百万 tokens、输出 8 元／百万 tokens）保守核算；缺少用量的中断请求保留预留。数值不是供应商最终账单。

| 样本 | 最新运行 | 终态 | 财务记录 / 研究证据 | 总墙钟 | 模型核算＋未结算预留 |
|---|---|---|---:|---:|---:|
| 小米集团，01810 HKD 柜台 | `run-f41fe7ee4a56` | partial | 11 / 7 | 3471.38 秒 | ¥47.256722 |
| 长鑫科技，688825 SSE | `run-2d56623aa316` | partial | 75 / 18 | 3099.67 秒 | ¥9.787362 |

两家保留了披露、反对证据、时点内变化与市场预期基准查询，以及每项实际获取的原响应、获取时间、哈希与定位。原始报告分别来自小米投资者关系网站 2025 年报和上交所 2025-12-30 招股说明书申报稿；长鑫较旧材料与后续来源的期间、公开时间和口径仍需调节，不能将预测当作历史年报数据。

```sh
.venv/bin/python -m yantra start --company 小米集团 --market HK --security-code 01810 --exchange HKEX --trading-currency HKD --mode real-model --env-file yantra/.env --material 'https://ir.mi.com/system/files-encrypted/nasdaq_kms/assets/2026/04/28/5-29-08/Xiaomi+2025+AR_EN.pdf' --framework quality --pricing '{"input_per_million":2,"output_per_million":8,"source":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing/","as_of":"2026-10-13","currency":"CNY"}' --runs-dir runs/v02-acceptance

.venv/bin/python -m yantra start --company 长鑫科技 --market CN --security-code 688825 --exchange SSE --trading-currency CNY --mode real-model --env-file yantra/.env --material 'https://static.sse.com.cn/stock/disclosure/announcement/c/202512/002170_20251230_B8QS.pdf' --framework change --pricing '{"input_per_million":2,"output_per_million":8,"source":"https://api-docs.deepseek.com/zh-cn/quick_start/pricing/","as_of":"2026-10-13","currency":"CNY"}' --runs-dir runs/v02-acceptance
```

执行过程中存在保留记录的早期失败和代码修正。最新两个运行通过原编号恢复，保留原截止、费用账本、工具与模型调用累计，没有将恢复作为新预算。长鑫的局部修复上限在修正任务输入缺失后，显式记录从 4 增至 8；人民币上限、总墙钟、既有预留及核验否决均未重置。

结果文件：

- [小米最终备忘录](../runs/v02-acceptance/run-f41fe7ee4a56/report-v2.md)、对应 JSON／计算凭证、`state.json`、`events.jsonl`、`materials/`。
- [长鑫最终备忘录](../runs/v02-acceptance/run-2d56623aa316/report-v5.md)、对应 JSON／计算凭证、`state.json`、`events.jsonl`、`materials/`。

## 未通过项及限制

1. **小米最低分析质量未满足。** 达到墙钟收尾边界后，最终有效论点为空；此前有研究分析，但财务修复使相关成果失效，未完成新一轮核验。最终表格不是一份通过最低研究质量验收的买方备忘录。此前的失效草稿单独保存在 `thesis-history.json`，仅供检查，不能充当当前已核验结论。
2. **两家独立业务核验未完整通过。** 长鑫有 17 条事实论点和 3 条假设，但模型超时导致核验结果未完整返回。程序检查及调用核验 Agent 不代替独立经济判断；保持否决与 partial，不改为 completed。
3. **估值提案与现金流口径仍不足。** 原始披露的 OCF−资本开支不直接视为 FCFF；股数、证券类别、融资利息和税盾、价格、汇率及市场基准缺口未补齐。没有完整 DCF、目标价或投资评级。未完整返回的估值提案也不能声称已通过适用性评估。
4. **语义和财务口径仍需更多真实样本校准。** 核验发现了期间／净利润类别混用、无出处的共识措辞和合并口径依据不足等问题。原文数值匹配和框架结构检查不能证明所有业务字段、日期或判断都正确。
5. **混合期间及币种适配仍有边界。** 原始币种、单位、期间及合并口径分组保存，但复杂财政年度、重述和不同证券股份类别的完整调节尚未用足量真实样本证明。私有价格、共识服务尚未连接，旧二进制办公格式、复杂 OCR 表格及图表数字不直接承诺支持。
6. **框架及协作的真实模型覆盖有限。** 用户框架当前选择、版本冻结及第三层／转派权限在测试中验证；两家真实样本不能证明所有自然语言框架都能正确解析，也不能保证每次研究都会动态创建同样的角色。

小米 3471.38 秒、长鑫 3099.67 秒均低于 3600 秒；最新运行的保守模型费用均低于人民币 50 元。达到边界后停止追加研究，不通过修改状态、删除必要条件或另起同次预算掩盖未通过项。

本轮交付为源码、配置示例、更新文档、可执行验证及上述真实结果。后续完整发布前，须在当前修正基础上重新验证稳定的论点保存、估值提案终止路径、业务核验完整返回及真实多格式／财务口径覆盖。
