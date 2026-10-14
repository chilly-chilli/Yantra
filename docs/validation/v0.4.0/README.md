# v0.4.0 ADK 原生接入验证（进行中）

日期：2026-10-14（Asia/Shanghai）。范围：0.3.3 基线上的 Unreleased 接入。固定 `google-adk==2.11.0`、`mcp==1.30.0`，本机 Python 3.12。任务仅授权本地开发和提交；未 push、部署或发布。

## 基线与第一阶段

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p 'test_adk_*.py' -v
.venv/bin/python -m pip check
git diff --check
```

- [原生基线日志](../adk-native/baseline-tests.log)：149 项，136.137 秒；147 通过、1 failure、1 error。两处知识复用测试截止固定2026-10-13，却按实际2026-10-14保存新知识。产品排除截止之后知识的行为正确；先保留失败，后续将在测试自身修复合成夹具时间，不修改历史证据或产品截止规则。
- [第一阶段 ADK 检查](../adk-native/adk-stage1-tests.log)：15 项通过。真实本地 ADK Runner、SQLite Session、HTTP TestClient；合成提供者、虚构材料，没有远程推理。[首次组合检查](../adk-native/adk-stage1-attempt.log) 有1项失败：恢复预检先用环境根目录定位，而既有测试工厂将研究放在下级目录。改为先由已有工厂加载、再以实际 engine.directory 检查 Session 绑定，避免推测存储路径，并在任何业务推进前拒绝跨 Session 恢复。
- `test_adk_native.py` 从 `/run_sse` 启动、等待和确认，随后实际 GET trace 读取内部角色／工具／结果哈希。验证重启读取、父 invocation、唯一事件 ID、分页、任务筛选、跨 Session 404及拒绝借用 run ID。
- 合成用量每决策10输入＋5输出 token，用量事件数量等于提供者请求数量；业务 model_calls 同样一致。观察不写第二套账本；等待不改变调用数或预算。6项实际本地工具调用保持原结果与独立核验。
- 验证轨迹不含材料正文、参数、state delta、thought 或凭据；崩溃未完成尾行不视为事件，下一写入删除尾部残片。
- `pip check` 与 `git diff --check` 通过。原生入口 Loader 现在返回 App，保留 root_agent 兼容符号。

## 第二阶段：Worker 与产物

- Worker 单次请求通过原 BaseLlm 接口在已有 Worker 生命周期内运行；不创建逐行动 Runner／Session。仍由 Engine／RunBudget 预留结算，保持 task＋local 输入、JSON模式与结构校验、工具前权限／预算检查、超时、取消与零自动重试。
- [第二阶段 ADK 检查](adk-stage2-tests.log)：17 项通过，含新增非法JSON／提供者错误／超时／取消单次请求及生成器清理测试；外部 API 检查用量仅出现一次，专业 Worker 不再产生内部决策 invocation。
- [能力回归](capabilities-inline-tests.log)：17 项通过；涉及冻结模型／skills、OCR价格、凭据脱敏、MCP／命令工具和预算。均为合成响应、实际本机命令或局部本机MCP，不是远端服务验证。
- [原始研究回归](v2-stage2-tests.log)：19 项通过，9.681秒；分别覆盖财务口径、授权、深度、协作、核验否决、总截止与replay。
- 原生 Artifact 外部HTTP测试：跨API重建读取报告／计算、ADK版本0/1关联业务版本1/2、源字节相同、旧版本保持、不同Session不可读、材料路径不可读、写入／删除403；缺失及哈希篡改404但版本元数据保留。没有重复权威产物或自动材料暴露。
- Artifact 首次检查修正测试的URL-safe base64解码；另发现上游ADK创建接口将服务的PermissionError包装为500，增加仅用于产物写入／删除的只读HTTP门槛后为403。没有改动报告字节或放开写权限。
- 当前指南、README／索引／ADR 的83个本地文件及锚点链接检查、compileall及差异检查通过；验证记录仍保留0.3.3基线，最终版本切换和完整回归尚待本任务后续阶段。

## 测量

可运行脚本：[measure_adk.py](../../../scripts/measure_adk.py)。从仓库根运行以下命令；checkout路径代表阶段一提交 `4f93afe` 的独立导出，必须自行准备，不是交付的用户材料。

```sh
.venv/bin/python scripts/measure_adk.py --checkout /absolute/path/stage1 --samples 5 --output tmp/measure-before.json
.venv/bin/python scripts/measure_adk.py --samples 5 --output tmp/measure-after.json
```

顺序执行的五次完整 ASGI HTTP 流程（启动→等待→确认，虚构资料／提供者，无网络推理），见[阶段一测量](measure-stage1-sequential.json)及[Worker接入测量](measure-inline-sequential.json)。忽略随机run/task ID及临时根目录后，报告、计算、核验问题及业务状态哈希均为 `b72cdde2df0f6fc4ff02b9067528827de50db26c3f7ebb6d4b14b770b739f3f1`。

| 维度 | 阶段一 | Worker接入后 | 边界 |
|---|---:|---:|---|
| 提供者／业务模型调用 | 18／18 | 18／18 | 合成请求，不证明真实模型效率 |
| 工具调用 | 6 | 6 | 核验及partial语义相同 |
| ADK invocation | 26 | 15 | 少11次内部Runner／Session生命周期，实质本地执行改善 |
| 决策Runner invocation | 18 | 7 | Worker行动不再另建Runner |
| 端到端中位秒数 | 0.4640 | 0.3870 | 本机离线5样本，含首轮启动，无统计置信区间；真实提供者延迟未知 |
| 轨迹文件字节 | 52480 | 36038 | 仍有Session／任务关联及一次用量事件 |
| 请求文本UTF-8字节 | 约677985 | 约677327 | 仅文本大小，真实输入token未知，减少不足0.1%不能作为上下文治理收益 |
| 费用 | 0 | 0 | 固定10输入/5输出合成token用于账本检查，不声称降低模型费用 |

隔离、恢复、费用次数、工具准入、核验否决与语义检查没有发现不可接受退化；维护方面减少11次Session／Runner生命周期，但增加约50行单请求桥接及错误归一化，仍有自定义行动适配，不宣称整体维护复杂度下降。最初并行采样因CPU竞争未用于上述延迟结论；真实网络、模型、费用与大上下文性能仍未知。完整研究质量及整体Pareto改进尚未证明。

## 目前可证实的变化

内部轨迹从临时消费变为可持久化并通过所属外部 Session 查询，所有自建 Runner 统一 App／插件，Worker少一层逐行动编排，标准Artifact读取现有产物。新增可追踪性、生命周期减少及产物生态接口；不等同于研究质量或模型费用提高。

## 尚待完成

agents-cli工程和行为评估配置、McpToolset连接复用／并发／清理、SkillToolset冻结目录及上下文测量、全回归与最终当前指南。Memory／cache／原生resume仅有接口调查；业务恢复仍保留。

真实模型／评估服务尚未执行：本任务没有批准具体新增模型或评估预算。现有凭据与运行默认预算不自动授权评估服务。离线结果不能充当真实行为评估。
