# 变更记录

功能记录与Git发布状态分别说明；本地实现和测试通过不表示已经推送、合并或发布。

## Unreleased — 0.3.3

- ADK 接入前两阶段：所有自建 Runner 显式采用原生 App／生命周期插件；新增所属 Session 的持久脱敏轨迹接口；Worker 在自身生命周期完成单次请求及工具执行，减少内部 Runner；只读 ArtifactService 桥接已有报告和计算文件，关联版本／哈希。阶段证据见[ADK 接入验证](docs/validation/v0.4.0/README.md)；其余接入仍在实施，不代表完整任务交付。

- 妙想改为金融数据和资讯搜索两个直接 HTTP API 工具，使用 `MX_APIKEY`，停用本机东方财富技能；保留旧资讯凭据引用兼容，并拒绝显式 API 业务错误。
- 本地凭据统一为根目录 `.env`，保留模板 `.env.example`；合并原模型、资料服务与旧 MCP 变量，移除重复文件，更新 CLI／ADK 配置说明。
- CLI 支持 OSC 7501 revision 0.4，报告运行、等待输入／确认、完成、错误及取消；状态仅发往 stderr TTY，不改变 JSON 输出和退出码。
- 新增扶摇 REST、东方财富妙想金融数据／资讯搜索、AKShare 白名单查询、98dou 财联社聚合命令扩展；提供凭据占位符、角色范围和配置说明。
- 新增 PaddleOCR-VL-1.6 jobs API OCR 适配器，有限时轮询、JSONL 下载及原图区域定位；配额、费用上界、缺失版面坐标和本地凭据检查均明确记录。
- 修正导入停用扩展时 CLI 返回的 enabled 状态，使其与保存配置一致。

本地实现与检查完成日期：2026-10-14；本轮未推送、打 tag、发布 Release 或部署。实际验证范围及未执行的认证／OCR 服务调用见[v0.3.3 记录](docs/validation/v0.3.3/README.md)。

## 0.3.1

- CLI增加统一text/JSON输出、schema_version=1响应、结构化解析／执行错误和`--version`；stdout与进度分流，处理函数返回数据。
- 明确无TTY菜单、EOF退出、业务状态与操作退出码；取消区分请求接受／终态／已取消，恢复参数数组保留运行目录与配置上下文。
- 长期要求应用前预检目标，保存后失败返回已完成修改与ID，避免盲目重试；查询暴露limit、context_bytes、summary_only及topics。
- limits、pricing、parameters、changes、budget增加互斥JSON文件输入；新增真实子进程离线契约测试并保留PTY交互回归。

CLI实现验证见[验收记录](docs/validation/v0.3/cli-agent-2026-10-13/README.md)，0.3.1版本与推送前检查见[版本记录](docs/validation/v0.3.1/README.md)。CLI实现提交为`cd73029`，基于v0.3提交`a8a1538`；版本提交及推送按用户本轮指令单独执行，GitHub Release、合并与部署尚未执行。RSS／轮询等信源能力仍为预留，不因版本号更新而成为已实现功能。

## 0.3.0

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
