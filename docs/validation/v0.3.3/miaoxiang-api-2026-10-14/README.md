# 妙想直接 API 接入验证

日期：2026-10-14（Asia/Shanghai）。版本：0.3.3。最终范围按用户最新指令仅包含金融数据与资讯搜索。当前协议及配置见[信息来源说明](../../../INFORMATION_SOURCES.md#妙想直接-api-协议)，结构化结果见[summary.json](summary.json)。

## 接入结果

| 注册工具 | 直接 POST 端点 | JSON 参数 |
|---|---|---|
| `ext_eastmoney_data` | `https://mkapi2.dfcfs.com/finskillshub/api/claw/query` | `toolQuery` |
| `ext_eastmoney` | `https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search` | `query` |

协议核对依据为[官方安装指南](https://marketing.dfcfw.com/res/download/A620260623NIYC2U.md)链接的金融数据和资讯搜索包内 Python 客户端源码。下载归档仅用于阅读协议，没有安装技能、执行上游脚本或执行其清理／发布流程；官方 ZIP 的 SHA-256 记录在 summary 中，不把完整技能包加入源码。

本机根 `.env` 已写入用户提供的 `MX_APIKEY`，保持 `0600` 和 Git 忽略；旧 `EASTMONEY_APIKEY` 是后置的 `${MX_APIKEY}` 引用。新工具的 `env_refs` 只传递 MX 变量；旧搜索运行快照仍可传递旧引用。配置中仅保存变量名，日志、示例和研究快照不写入密钥。

金融数据和搜索 tools 在本机已启用，旧 `skills/eastmoney` 已停用。智能选股的试写代码、示例配置和本机注册已移除；未知 provider 在网络请求前被拒绝。新的研究快照包含两项妙想 tools，技能内容不再注入；已有运行保持自己的历史快照。

## 实际认证探测

使用项目适配器、根 `.env` 凭据执行各一次公开信息查询，没有打印或保存第三方全文及密钥：

- 金融数据：2026-10-14 00:31:09（Asia/Shanghai），3.270 秒；成功响应，返回一张 `dataTableDTOList` 数据表。
- 资讯搜索：2026-10-14 00:31:11（Asia/Shanghai），1.573 秒；成功认证与 JSON 响应，记录顶层结构，不把此结果视作独立事实核验。

最初用户选择含智能选股时，曾在 00:31:12 执行一次只读选股探测，返回 24 行；自然语言中的“最多5只”未成为服务端硬行数限制。用户随后排除此项，当前实现不包含该服务。此实际执行记录保留在 summary 的 `prior_scope_probe`，不能将其描述为当前已纳入能力，也未再次调用该接口。

调用成功只证明本次协议与凭据可用，不证明账户无限额度、服务免费或金融内容正确。账户费率未核实；模板每次 service 费用声明仍为 0，非供应商账单。没有执行完整投研、LLM 调用、账户变更或发布。

## 离线与配置检查

```sh
.venv/bin/python -m unittest discover -s tests -p test_information.py -v
.venv/bin/python -m unittest discover -s tests -p test_capabilities.py -v
.venv/bin/python -m compileall -q yantra
git diff --check
```

[信息专项日志](information-tests.log)：17 项通过；[既有能力回归日志](capabilities-tests.log)：17 项通过。耗时见 summary。新增检查覆盖金融数据 payload、原始单位／精度保留、错误状态、旧搜索引用兼容、真实子进程凭据传递与响应脱敏，并确认被排除的智能选股不会发出请求。这些测试使用受控模拟响应，不消耗真实 API 额度。

同时核对示例 JSON 与工具 schema、实际本机启用状态、根变量引用解析、技能快照排除、文档相对链接及标题锚点；根 `.env` 和本机配置保持不入 Git。未重新执行与此次 API 接入无关的完整投研回归。本轮为本地提交前检查，未推送、合并或发布。
