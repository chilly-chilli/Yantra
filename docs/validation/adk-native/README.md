# ADK 原生接入验证（进行中）

日期：2026-10-14（Asia/Shanghai）。范围：0.3.3 基线上的 Unreleased 接入。固定 `google-adk==2.11.0`、`mcp==1.30.0`，本机 Python 3.12。任务仅授权本地开发和提交；未 push、部署或发布。

## 基线与第一阶段

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p 'test_adk_*.py' -v
.venv/bin/python -m pip check
git diff --check
```

- [原生基线日志](baseline-tests.log)：149 项，136.137 秒；147 通过、1 failure、1 error。两处知识复用测试截止固定2026-10-13，却按实际2026-10-14保存新知识。产品排除截止之后知识的行为正确；先保留失败，后续将在测试自身修复合成夹具时间，不修改历史证据或产品截止规则。
- [第一阶段 ADK 检查](adk-stage1-tests.log)：15 项通过。真实本地 ADK Runner、SQLite Session、HTTP TestClient；合成提供者、虚构材料，没有远程推理。[首次组合检查](adk-stage1-attempt.log) 有1项失败：恢复预检先用环境根目录定位，而既有测试工厂将研究放在下级目录。改为先由已有工厂加载、再以实际 engine.directory 检查 Session 绑定，避免推测存储路径，并在任何业务推进前拒绝跨 Session 恢复。
- `test_adk_native.py` 从 `/run_sse` 启动、等待和确认，随后实际 GET trace 读取内部角色／工具／结果哈希。验证重启读取、父 invocation、唯一事件 ID、分页、任务筛选、跨 Session 404及拒绝借用 run ID。
- 合成用量每决策10输入＋5输出 token，用量事件数量等于提供者请求数量；业务 model_calls 同样一致。观察不写第二套账本；等待不改变调用数或预算。6项实际本地工具调用保持原结果与独立核验。
- 验证轨迹不含材料正文、参数、state delta、thought 或凭据；崩溃未完成尾行不视为事件，下一写入删除尾部残片。
- `pip check` 与 `git diff --check` 通过。原生入口 Loader 现在返回 App，保留 root_agent 兼容符号。

## 目前可证实的变化

内部轨迹从临时消费变为可持久化并通过所属外部 Session 查询，所有自建 Runner 统一 App／插件。新增的是可追踪性与覆盖，没有证明模型费用或研究质量提升。trace 的额外本地 I/O 延迟、上下文和整个受影响路径的性能比较尚待测量，因此尚不宣称整体 Pareto 改进。

## 尚待完成

Worker 单生命周期试点及测量、agents-cli工程和行为评估配置、McpToolset连接复用／并发／清理、ArtifactService文件桥接、SkillToolset冻结目录及上下文测量、全回归与最终当前指南。Memory／cache／原生resume仅有接口调查；业务恢复仍保留。

真实模型／评估服务尚未执行：本任务没有批准具体新增模型或评估预算。现有凭据与运行默认预算不自动授权评估服务。离线结果不能充当真实行为评估。
