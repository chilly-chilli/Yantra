# Yantra

实证研策：Google ADK 驱动的证据核验型买方投研原型。当前工作区版本为 **0.4.2（Unreleased）**；发布与变更状态见[更新记录](CHANGELOG.md)。

研究可从公司名称开始，自动查找证券与公开资料，也接受明确证券身份及原始文件、目录或网页链接。沿用 `finance → thesis → valuation → verification → report`，输出买入判断、备忘录、结构化结果、计算凭证与来源快照。支持 DCF、PE、PB、EV/EBITDA、单阶段 DDM，以及跨 session 共享知识与混合检索。

## 快速开始

需要 Python 3.11+；Google ADK 固定为 2.11.0。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m yantra --help
test -f .env || cp .env.example .env
.venv/bin/python -m yantra configure --env-file .env
```

按[安装与配置说明](docs/USAGE.md#安装)准备 OCR、模型凭据和有来源的定价，然后启动研究：

首次研究可使用本次默认自主策略，无须先填写偏好问卷；它不创建长期授权。已有拒绝、撤销或本次手动模式仍以条件性情景交付，详见[偏好与授权](docs/KNOWLEDGE.md#首次设置与自主选择授权)。

```sh
.venv/bin/python -m yantra start --company 贵州茅台 --env-file .env
```

该命令会使用已配置的真实模型和信息来源；本轮未执行这个真实研究例子。`start` 默认为正式研究，材料可为空；身份或上市市场有多个合理候选时保存等待状态，通过 `resume --select-identity` 选择。显式 `--mode real-model` 和 `--material` 继续支持；旧 `start --mode offline-demo` 返回迁移提示，虚构演示改用 `demo --materials-dir`。完整命令见[使用指南](docs/USAGE.md#原始材料研究)。默认期限12个月、总墙钟上限2小时、共享费用上限49元；报告先给出“值得买／暂不值得买／目前无法判断”，再说明情景、安全边际、风险和缺口。

## 文档入口

| 需要了解 | 主要文档 |
|---|---|
| 安装、启动、预算、取消、恢复与复测 | [使用指南](docs/USAGE.md) |
| LLM、OCR、embedding、skills、MCP与工具配置 | [能力配置](docs/CAPABILITIES.md) |
| 信息工具、Tushare、Tavily 与 Yahoo Finance 接入 | [信息来源配置](docs/INFORMATION_SOURCES.md) |
| 估值参数、来源、授权与数学复算 | [估值说明](docs/VALUATION.md) |
| 共享知识、索引、编辑删除与长期要求 | [知识说明](docs/KNOWLEDGE.md) |
| 模块职责、权限和数据流 | [架构说明](docs/ARCHITECTURE.md) |
| agents-cli、本地验证与行为评估 | [开发与评估](docs/DEVELOPMENT.md) |
| 当前与历史验证、提案及开发提示词 | [文档索引](docs/README.md) |
| 开发、commit／push与文档维护规则 | [AGENTS.md](AGENTS.md) |

## 验证与能力边界

v0.4.2全量离线回归265项通过，末次授权语义调整后45项针对性检查通过；公司名启动、换源、身份恢复和三类判断使用受控响应验证。真实账户、联网资料覆盖、模型质量及judge未验证；见[v0.4.2证据与限制](docs/validation/v0.4.2/README.md)。

```sh
.venv/bin/python -m pip install 'reportlab==4.5.1'
.venv/bin/python scripts/run_offline_tests.py
```

CLI优化的完整回归记录为 **111项通过**，最终偏好补充测试3项通过，见[CLI验收证据](docs/validation/v0.3/cli-agent-2026-10-13/README.md)；0.3.1版本检查见[版本记录](docs/validation/v0.3.1/README.md)。

v0.3开发验收记录为 **95项测试通过**，其中65项既有回归、30项新增。见[验收记录及原始证据](docs/validation/v0.3/README.md)。记录中的实际ADK／SQLite执行与离线决策、embedding／联网响应分别说明；该轮未验证真实外部服务端到端研究。

统一信源管道的 RSS／专用官方披露渠道仍是预留接口，订阅、轮询、自动监控和通用一致预期服务留待后续版本。信息命令扩展可按需查询行情与资料。SOTP、正常化盈利仍是独立原型。旧研究和历史验收保留当时结果；新版不会自动导入 workspace 旧材料。

v0.4.2 新增 Tushare 直连、Tavily 搜索和通过 yfinance 访问 Yahoo Finance 的适配器；三个模板均停用且费用未核实，yfinance 是尚未在本轮安装或进行真实覆盖测试的可选依赖。CN/HK 证券身份研究与适用换源使用既有权限、预算和证据路径；单一可靠来源可支持初步判断，关键冲突、数学或独立核验问题仍阻止肯定结论。Yahoo 的 US 查询能力不代表完整美股研究已获支持。实际离线验证与未执行的真实服务范围见[验证索引](docs/validation/README.md)。

首轮投资逻辑整合增加实际业务识别、四类确定性财务指标与统一异常信号、可选辅助模型的独立授权和核验、结构化风险／反证／跟踪清单，以及一致预期／研报样本／无外部基准三类表达。跟踪仅生成清单。实际检查和未验证的模型质量范围见[本轮记录](docs/validation/v0.4.0/investment-logic-2026-10-14/README.md)。

v0.3.3 新增 OSC 7501 终端状态、四类信息命令扩展及 PaddleOCR 任务 API。版本检查与实际验证边界见[v0.3.3 记录](docs/validation/v0.3.3/README.md)。

v0.4.0 未发布：原生 App／插件、所属 Session 的持久轨迹、Worker 单次请求、MCP 连接复用、只读 Artifact 及可选 SkillToolset 试点；工程入口和行为评估配置已补齐。测量、完整回归和未执行的真实模型／judge范围见[v0.4.0记录](docs/validation/v0.4.0/README.md)。接入和本机收益不等同于研究质量或真实费用提高。
