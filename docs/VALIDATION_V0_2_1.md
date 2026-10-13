# v0.2.1 功能验证

2026-10-13，在 `/Users/chillychili/Yantra` 的 Python 3.12 虚拟环境验证。发布标识为 `0.2.1`，Google ADK 保持 2.11.0；新增 MCP SDK 1.30.0 和 JSON Schema 校验依赖。

## 结果

```text
.venv/bin/python -m unittest discover -s tests -v
Ran 49 tests in 13.528s
OK

uv pip check --python .venv/bin/python
Checked 98 packages
All installed packages are compatible

.venv/bin/python -m compileall -q yantra tests examples/v021
通过
```

全套包含原有 32 项回归测试和 `tests/test_capabilities.py` 的 17 项新增测试。旧 ADK 会话、核验否决、确定性计算、角色与第三层授权、原始解析、真实中英 Tesseract OCR、预算、取消和恢复等测试继续通过。

## 新增能力检查

| 检查 | 执行方式与证据 |
|---|---|
| 两个 CLI 交互模块 | 实际 CLI 子进程注册／选择模型，以及导入／列出／停用工具；验证持久化选择与角色 |
| API 模型探测 | 真实本地 HTTP 服务返回模型列表，仅收到 `/v1/models`；没有推理请求 |
| 模型选择及恢复 | 测试后端工厂捕获所选配置，改变默认模型后恢复仍使用第一份快照和价格 |
| 技能指令与图像 | 原生 ADK 模型请求由本地 BaseLlm 测试模型捕获；匹配角色收到技能指令，图片以 image part 送达 |
| 技能资源 | 实际目录复制及哈希核对；默认文件后续改变不修改运行快照；越权角色、越界路径和外部软链接被拒绝 |
| stdio MCP | 实际启动官方 FastMCP 本地服务，完成初始化、发现与调用；错误参数在调用前被拒绝，凭据回显脱敏 |
| Streamable HTTP MCP | 实际启动本地 HTTP MCP 服务，用官方 SDK 当前 client API 发现和调用 |
| MCP 的研究工具执行 | 测试决策后端选择真实 MCP 工具，原生 ADK worker 执行；工具计数、来源引用和保存凭证可查；无虚构计算凭证 |
| 命令工具的研究执行 | 实际 JSON 子进程经原生 ADK worker 执行；声明模型费进入共享账本，越权角色不能调用；停用不改写已启动运行 |
| 命令生命周期 | 实际测试超时终止、凭据脱敏、分段 100 KB JSON 完整接收与 1 MB 输出上限 |
| command OCR | 实际命令适配器进入图片材料管线，测试识别结果和声明费用进入状态；返回文本是测试夹具，不作为 OCR 品质证明 |
| vision OCR | 本地替代推理函数捕获 PNG、OCR 合约及请求；检查独立单价、图片 token 预留、usage 结算、OCR／模型计数和材料定位；未请求外部视觉模型 |
| 计费配置 | 视觉 OCR 缺少价格或图片 token 上界时拒绝；有来源／日期的本地零费用价格可注册 |

## CLI 示例冒烟验证

使用临时配置逐条执行说明中的注册命令，并调用示例命令工具与示例 MCP 工具，所有步骤通过，临时配置随后删除：

- LLM 与 vision OCR 模板通过配置合约检查；没有连接模板模型端点。
- Tesseract 检查实际返回 `ready=true`、版本 `5.5.2`，语言包为 `chi_sim`、`eng`。
- 示例 cashflow skill 导入／检查通过，资源可复制和冻结。
- 示例 JSON tool 注册／检查／实际回传中文文本通过。
- 示例 stdio MCP 注册／检查／实际回传中文文本通过。
- CLI help 显示 `configure`、`models`、`extensions` 及 `--config`；包版本返回 `0.2.1`。

这些检查没有修改用户现有模型环境文件，没有把示例扩展启用到项目默认配置，也没有发起收费模型调用。

## 验证范围与剩余缺口

本次模型选择和扩展接入功能的自动化及本地集成检查通过。真实远端 LLM／视觉 OCR 供应商兼容性、OCR 品质及计费上界，需要使用对应服务、真实图像和供应商单价另行验证；此记录不以测试替代函数证明这些结果。

当前 LLM／vision 使用兼容 API；其他原生协议可通过新增适配器或 JSON 命令接入。MCP 实现 tools 发现和调用，未实现 OAuth、prompts／resources 及常驻服务管理。技能冻结内容及资源，命令程序和远端服务实现未冻结。费用按配置价格／上界记账，无法提供供应商实际账单。

本次未重新执行收费端到端投研，也未解决行情／共识、FCFF 调节等此前验收缺口。[v0.2 原始验收记录](VALIDATION_V0_2.md) 的结论继续保留，不能由本次功能测试通过推定完整投研验收已经通过。
