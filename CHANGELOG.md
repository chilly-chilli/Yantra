# 变更记录

功能记录与Git发布状态分别说明；本地实现和测试通过不表示已经推送、合并或发布。

## Unreleased — 0.3.0

- CLI增加统一text/JSON输出、schema_version=1响应、结构化解析／执行错误和`--version`；stdout与进度分流，处理函数返回数据。
- 明确无TTY菜单、EOF退出、业务状态与操作退出码；取消区分请求接受／终态／已取消，恢复参数数组保留运行目录与配置上下文。
- 长期要求应用前预检目标，保存后失败返回已完成修改与ID，避免盲目重试；查询暴露limit、context_bytes、summary_only及topics。
- limits、pricing、parameters、changes、budget增加互斥JSON文件输入；新增真实子进程离线契约测试并保留PTY交互回归。

CLI本轮验证与未执行范围见[验收记录](docs/validation/v0.3/cli-agent-2026-10-13/README.md)。CLI改动基于前序v0.3实现提交`a8a1538`；本轮单独提交，不push或发布。

- 新增原始文件＋SQLite正式知识存储、不可变修订、核验及固定session引用。
- 新增元数据、中文FTS5及embedding混合检索，本地向量索引、增量更新和预算降级。
- 接入Agent知识工具、独立核验、公司基准版本检查、判断快照及历史比较。
- 新增知识查询、重建、事实编辑、删除与明确长期要求的CLI／ADK入口。
- 为v0.3.1预留渠道中立的资料发现／获取、external ID、cursor及checkpoint接口。
- 整理当前使用、能力、估值、知识和架构指南，归档历史提案与验收；新增工程与文档规范。

开发验收：95项测试通过，真实embedding／LLM及外部获取服务未在该轮验证，详见[v0.3记录](docs/validation/v0.3/README.md)。本次用户已授权Git提交与远端同步，检查证据见[提交前验证](docs/validation/v0.3/PREPUSH.md)；实际commit／push状态以Git历史和远端分支为准。此前未提交的时点说明保留在[文档整理记录](docs/validation/documentation-2026-10-13/README.md)。

## 0.2.3

- 将估值集成版本标识调整为0.2.3，保持方法授权、单位转换及凭证算法。
- 保留65项回归与集成测试，以及本地真实材料的脚本研究与正确拒绝记录。

本地提交：`b824c4d`；历史验证和提交范围见[v0.2.3记录](docs/validation/v0.2.3/README.md)。

## 0.2.2

- 集成PE、PB、EV/EBITDA、单阶段DDM、EBIT→FCFF桥接及有来源的市场基准。
- 使用统一方法授权、Pydantic契约、计算凭证和离线数学复算。

历史限制与真实材料脚本验证见[v0.2.2记录](docs/validation/v0.2.2/README.md)。

## 0.2.1

- 新增模型／OCR与skills／MCP／命令工具配置模块，冻结运行能力快照。
- 接入共享预算、角色权限、响应脱敏及扩展凭证。

历史接入验证见[v0.2.1记录](docs/validation/v0.2.1/README.md)。

## 0.2

- 新增原始材料解析、主动检索、财务提取、框架、人民币预算及动态任务协作。
- 保留真实模型研究的partial结果和未通过项。

历史验收见[v0.2记录](docs/validation/v0.2/README.md)。
