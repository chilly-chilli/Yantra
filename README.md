# Yantra

实证研策：Google ADK 驱动的证据核验型买方投研原型。当前工作区版本为 **0.3.0**；发布与变更状态见[更新记录](CHANGELOG.md)。

研究接受公司／证券身份及原始文件、目录或网页链接，沿用 `finance → thesis → valuation → verification → report`，输出备忘录、结构化结果、计算凭证与原始来源快照。支持 DCF、PE、PB、EV/EBITDA、单阶段 DDM，以及跨 session 共享知识与混合检索。

## 快速开始

需要 Python 3.11+；Google ADK 固定为 2.11.0。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m yantra --help
.venv/bin/python -m yantra configure
```

按[安装与配置说明](docs/USAGE.md#安装)准备 OCR、模型凭据和有来源的定价，然后启动研究：

```sh
.venv/bin/python -m yantra start \
  --company 示例公司 --market CN --security-code 600001 --exchange SSE \
  --share-class A --trading-currency CNY \
  --material /absolute/path/new-report.pdf --mode real-model \
  --env-file /absolute/path/model.env
```

上述公司、代码和路径为待替换示例。原始材料研究要求明确选择 `real-model`；固定虚构演示使用 `offline-demo`。默认研究期限12个月、总墙钟上限2小时、模型费上限49元；核验否决与资料缺口始终保留。

## 文档入口

| 需要了解 | 主要文档 |
|---|---|
| 安装、启动、预算、取消、恢复与复测 | [使用指南](docs/USAGE.md) |
| LLM、OCR、embedding、skills、MCP与工具配置 | [能力配置](docs/CAPABILITIES.md) |
| 估值参数、来源、授权与数学复算 | [估值说明](docs/VALUATION.md) |
| 共享知识、索引、编辑删除与长期要求 | [知识说明](docs/KNOWLEDGE.md) |
| 模块职责、权限和数据流 | [架构说明](docs/ARCHITECTURE.md) |
| 当前与历史验证、提案及开发提示词 | [文档索引](docs/README.md) |
| 开发、commit／push与文档维护规则 | [AGENTS.md](AGENTS.md) |

## 验证与能力边界

```sh
.venv/bin/python -m pip install 'reportlab==4.5.1'
.venv/bin/python -m unittest discover -s tests -v
```

v0.3开发验收记录为 **95项测试通过**，其中65项既有回归、30项新增。见[验收记录及原始证据](docs/validation/v0.3/README.md)。记录中的实际ADK／SQLite执行与离线决策、embedding／联网响应分别说明；该轮未验证真实外部服务端到端研究。

RSS／官方数据渠道只预留统一发现与获取接口，订阅、轮询、自动监控、新行情／一致预期服务留待后续版本。SOTP、正常化盈利仍是独立原型。旧研究和历史验收保留当时结果；新版不会自动导入workspace旧材料。
