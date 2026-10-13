# CLI Agent调用验收

日期：2026-10-13（Asia/Shanghai）；范围：0.3.0未发布CLI边界。基线在开始时存在大量前序改动；工作期间另一操作将其隔离提交为 `a8a1538`，本轮差异仅为CLI、相关测试和当前指南。本轮不push，不修改研究、预算、知识检索、持久化或重放算法。

## 证据与命令

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p test_cli_contract.py -v
.venv/bin/python -m compileall -q yantra/cli.py yantra/cli_contract.py yantra/knowledge/cli.py tests/test_cli_contract.py
.venv/bin/python -m yantra --output json --help
.venv/bin/python -m yantra --output json --version
git diff --check
```

最终数量、时间和检查状态见[摘要](summary.json)；完整回归日志见[测试日志](unittest.log)。最终审阅补充未知要求ID的错误优先级后，另执行3项偏好子进程测试，通过记录见[补充日志](preferences-unittest.log)。文档命令用现有argparse逐项解析，不执行真实研究、模型探测或删除；相对文件链接及标题锚点另外检查。当前说明见[使用指南](../../../USAGE.md#agent非交互调用与输出契约)与[知识说明](../../../KNOWLEDGE.md)。

## 实际覆盖

新增CLI契约使用真实Python子进程、临时目录及合成数据：成功与失败的单JSON响应、stderr进度、帮助／版本、非法参数／JSON／文件互斥、缺失运行、无TTY菜单、EOF与安全执行错误、所有既有运行状态、取消终态、偏好应用预检和保存后失败、自定义目录／配置／env-file恢复参数。真实离线ADK流程执行启动→waiting→显式改参→partial、compare、replay、预算failed、取消完成。completed状态以受控保存状态测试覆盖；固定演示材料保留业务缺口，不能把其partial写成completed。

知识参数通过真实查询结果及服务保存的search记录确认limit、context_bytes、summary_only与topics；事实文件改参、离线FTS重建、source读取及临时来源软删除通过真实服务执行。pricing-file向原始引擎传递使用受控创建／调度替身，未声称执行真实研究。保存后文件写入失败和JSON执行异常在子进程中注入，响应包含实际持久化要求ID，未泄露异常正文。终端菜单回归使用真实PTY执行模型选择、扩展导入和停用；EOF用子进程真实关闭stdin并模拟TTY检测，不等同真实终端断开事件。

保留既有95项回归，其ADK、SQLite、本地HTTP/MCP及OCR验证范围沿用原测试；所有模型决策／embedding使用离线合成替身或本地服务。未调用收费模型、外部研究／远程服务，未删除用户数据。依赖会输出既有ADK实验特性／用量缺失及Starlette弃用提示；它们不进入JSON stdout。

## 边界与后续建议

SQLite要求修改和运行请求文件无联合事务，仍可能部分成功；已显式返回完成修改与应用失败，禁止盲目新增重试。取消是边界请求，必须后续查询确认。next_actions是可执行参数数组，确认／继续动作仍需满足授权和原预算。通用服务异常只返回安全类别，必要的细节需在可信本地诊断中检查。

机器schema导出可在字段契约稳定后增加；JSONL事件流需要单独定义排序、断线和终态协议；启动幂等键需要持久化请求指纹、并发与恢复策略。三者本轮均未实现。没有后台任务服务、框架迁移或通用插件平台。
