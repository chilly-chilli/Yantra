# Yantra v0.2 实施记录

2026-10-13 在现有非 Git 工作目录上实施；修改前的源码与文档备份位于 `/tmp/yantra-v01-backup/`。保留 ADK 2.11.0、Pydantic 合约、确定性 DCF、会话隔离、原始计算凭证及 v0.1 演示路径，没有初始化 Git、发布 PR 或部署服务。

## 选型

选型以原始来源定位、可运行性及小依赖范围为依据。版本是本次实际安装版本；详细许可证以各项目发布包及上游 LICENSE 为准。

| 项目 | 本次采用方式／版本 | 评估与限制 |
|---|---|---|
| [Google ADK](https://github.com/google/adk-python) | 复用 2.11.0，Apache-2.0 | 核对本地 Runner、Context、Session、LlmAgent、LiteLlm 与回调源码；不升级；每个临时角色独立实例及会话 |
| [MarkItDown](https://github.com/microsoft/markitdown) | 设计参考，未依赖，MIT | 覆盖格式广，但统一 Markdown 不直接满足页码、单元格、表格边界原始定位；选择格式专用读取器 |
| [Docling](https://github.com/docling-project/docling) | 评估后未采用，MIT | 有布局和 OCR 能力，需要更重依赖及模型资源；当前使用可验证的 PDFium/Tesseract 组合，不宣称达到复杂版式完整理解 |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | 0.11.9，MIT | 本地 PDF 文本、表格与边界；原始文件保留；不是 OCR |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | 4.30.0 | PDFium 渲染扫描页；Python 绑定 Apache-2.0/BSD-3-Clause，含 PDFium 第三方许可证 |
| [pytesseract](https://github.com/madmaze/pytesseract)／[Tesseract](https://github.com/tesseract-ocr/tesseract) | 0.3.13 包及本机引擎，Apache-2.0 | 中英文字实际 OCR，保留图像区域；语言包单独安装；列关系与数值仍需核验 |
| [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | 3.1.5，MIT | 工作表及单元格定位；未缓存公式不虚构结果 |
| [python-docx](https://github.com/python-openxml/python-docx) | 1.2.0，MIT | 按原始块顺序读取段落与表格；图片图表明确缺口 |
| [python-pptx](https://github.com/scanny/python-pptx) | 1.0.2，MIT | 幻灯片、形状、表格定位；嵌入图表数字不推断 |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | 4.15.0，MIT | 网页正文、原始 HTML、片段定位及发布时间；纯 HTTP 无法访问的页面保留失败 |
| [DDGS](https://github.com/deedy5/ddgs) | 9.16.0，MIT | 默认公开搜索发现，实际读取结果 URL；失败时有限切换后端；不把摘要视为证据 |
| [primp](https://github.com/deedy5/primp) | 2.0.1，MIT | 普通 HTTP 受站点指纹阻拦时的一次替代获取，实际验证取得小米 IR 与上交所原始 PDF |
| [SearXNG](https://github.com/searxng/searxng) | 未采用 | 需要部署或可用实例；本轮没有外部服务部署 |
| [feedparser](https://github.com/kurtmckee/feedparser) | 未采用，BSD-2-Clause | RSS 可辅助发现，当前公开搜索足够；未增加未使用的生产依赖或持续订阅 |
| [AKShare](https://github.com/akfamily/akshare) | 评估后未采用，MIT | 数据口径及上游可用性需逐接口核验；本轮没有用库名代替已验证的行情或共识接入 |

公开资料获取无需资料密钥。私有资料服务字段保留在配置示例中，当前没有连通私有行情／共识服务，不声称已经支持。价格、汇率及预期基准没有可靠来源时明确缺口，不以模型记忆或财经站摘要代替。

## 实施与可观察检查

1. 增加原始 index、文件格式解析、真实 OCR 和定位 → 实际生成及解析办公文档、文本型 PDF、扫描 PDF、中英图片、HTML、CSV、JSON，检查数字及位置。
2. 增加预算内结构化提取、原文数字校验及部分指标 → 千分位／负数、缺失值、未来信息、冲突、OCF 与 FCFF 区别有检查；财务结果和指标不能改写工具凭证。
3. 增加四类主动独立检索 → 用户资料齐全时仍实际执行披露、反证、新变化、预期基准查询并记录原获取结果。
4. 增加框架和版本 → 内置三框架、自然语言解析、原文草稿、独立核验发布与固定快照；不是完整 memory 系统。
5. 增加动态第三层及协作 → 真实 ADK Session／工具事件执行两个不同临时角色；第四层、工具扩大及未授权读取被阻止；转派申请不会提前执行，主 Agent 批准后执行。
6. 增加自主／人工授权、人民币账本及总截止 → 并发原子预留、用量结算、缺失用量保守保留、等待超时、修改重算、取消及恢复保留同次预算。
7. 完整成果与复测 → 八部分报告、源文件、JSON、计算记录；固定字节复算和程序复核不冒充远程推理。

真实运行揭示并修正了 SOCKS 依赖缺失、站点 HTTP 指纹阻拦、提取输出截断、缺少带出处披露日期、重复默认选区、补查失败覆盖既有底表，以及市场预期结果形状无效导致报告收尾失败。保留此前失败／中断验证的记录，不将其当作验收成功。

## 验收

详细测试数量、真实样本运行编号、费用、耗时、引用及剩余限制统一维护在 [VALIDATION_V0_2.md](VALIDATION_V0_2.md)。测试模型、真实本地解析、真实联网及真实模型调用分别说明。没有市场行情、共识或可靠 FCFF 不代表解析模块为空，但真实流程是否通过必须满足可检查的财务记录、定性证据、独立研究与核验等要求。
