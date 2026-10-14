# ADR 0002：ADK 原生机制与研究业务边界

日期：2026-10-14。状态：已采纳（Unreleased，固定 Google ADK 2.11.0）。范围：执行、Session／事件、工具和产物生命周期；各后续试点的状态以[验证记录](../validation/v0.4.0/README.md)为准。

## 决策

ADK 提供通用执行、编排接口、Session、事件及工具／插件生命周期。Yantra 负责 `finance → thesis → valuation → verification → report`、专业上下文隔离、父权限和第三层上限、证据字节和不可变修订/CAS、截止及财务口径、费用预留结算和总截止、估值版本授权、确定性计算、独立核验否决及固定离线 replay。

每个独立 Runner 使用同一 App／插件工厂，观察不成为第二套账本。独立任务继续拥有独立 Session；内部事件保存为脱敏投影并由所属外部 Session 读取，不复制到外层事件流。业务 `state.json`、预算账本和知识修订分别由原控制器／存储接口写入，ADK 状态仅承载入口关联和隔离任务结果。

新增自建通用机制前先检查固定版本源码及现有能力。例外需记录具体缺口、业务必要性与验证方式；不得以引入类的数量验收。涉及运行机制的提交描述复用能力、自定义理由和验证结果。

## 当前例外与取舍

- 自定义控制器保留阶段、审批、核验、预算和恢复，ADK Workflow 无法自动提供这些业务门槛。
- 事件投影不是完整内容复制：原生 Session 中可能包含专业材料、工具参数或提供者错误。只持久化可定位标识、工具名称、内容哈希、状态及用量，既补齐轨迹查询，也保留上下文边界。
- 专业任务保留独立 Session；Worker 决策改用同一 Worker 内的单次 BaseLlm 请求，消除逐行动的内部 Runner。主协调、提取及独立审查仍有各自隔离决策 Session。公共适配层已完成四角色离线回归，真实提供者行为尚待预算授权。
- ArtifactService 只读索引已有报告／计算文件，以哈希和业务版本定位，不复制权威字节。文件缺失或同版本覆盖则明确不可读；原始证据仍属于证据体系。业务恢复而非 Artifact 版本控制决定重新研究与改写。
- MCP 复用原生 McpToolset、McpTool 和会话池。固定2.11.0的发现只读一页，且HTTP默认探测ADC；因此保留约150行适配：分页读取、冻结过滤／定义核对、安全别名到原RPC映射、拥有的MCPSessionManager禁止未声明ADC、只读数据工具抑制UI动作、禁止重定向凭据扩散及上下文错误脱敏。涉及受保护接口的依赖明确由版本固定及分页／RPC／无ADC／重定向／实际进程退出测试覆盖；不是全局monkeypatch。
- 原生取消可能跳过 after_run；资源清理还绑定 Runner.close 的插件 close。外部调用结果未知时由控制器持久标记并停止恢复，不把连接重建或原生resume当作RPC幂等性证明。
- SkillToolset 只在显式 `loading=native` 的新运行 Worker 试点；冻结目录／内容哈希、角色和父权限仍由业务边界检查。原生模型复用发现／加载／资源读取，脚本作为文本引用，忽略动态工具和状态插值元数据。当前单请求适配仍显式投影 task＋local；加载正文进入当前任务指令，不复制到收据输入。默认 eager 保留，因为小目录输入增加且加载多一次决策；大目录文本减少不证明真实模型费用下降。完全相同收据的固定引用保留首项全文，不做自由摘要或开启 Session 压缩。
- 原生 resumability 明确为 at-least-once；尚无跨账本／知识／外部工具事务幂等性证明，保留业务恢复，不自动重放中断工具。
- ContextCacheConfig 在当前 LiteLlm 有适配代码，但提供者命中、费用和延迟尚未测量；不更换模型满足缓存。Session 压缩不会自动减少显式 task＋local 输入。
- MemoryService 尚无本次截止、证券口径和不可变修订的直接保障；不得用普通对话自动生成长期要求。

## 验证与限制

通过真实本地 HTTP 入口读取内部轨迹、跨重启、分页、任务筛选和所属 Session 拒绝；原生 ADK 执行配合合成提供者确认等待不计费、每次请求只计一次及无事件复制。这些不证明真实模型行为或研究质量提高。

查阅固定版本安装源码及官方样例 `9252494a7aaafca8fe4d81a04f15392345a5779b`：[Horizon 隔离](https://github.com/google/adk-samples/blob/9252494a7aaafca8fe4d81a04f15392345a5779b/core/python/long-horizon-harness/horizon/subagents/delegate_runner.py)、[插件样例](https://github.com/google/adk-samples/tree/9252494a7aaafca8fe4d81a04f15392345a5779b/core/python/safety-plugins)。不复制共享可变技能列表，不引入样例云设施。
