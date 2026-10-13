# v0.3.3 更新与配置验证

范围：OSC 7501 CLI、四类信息命令扩展及 PaddleOCR 任务 API。核对开始于 2026-10-13，完成检查于 2026-10-14（Asia/Shanghai）；版本为 0.3.3。此记录为本地提交前证据，本轮没有 push、tag、Release、合并或部署。

结构化结果见 [summary.json](summary.json)；原生失败和后续条件复核分别保留。

后续配置整理：2026-10-14 已将本机凭据统一到根 `.env`，此前关于保留 `yantra/.env` 的内容是本轮实现时的历史状态；原始证据不改写。当前状态见[合并验证](env-consolidation-2026-10-14.md)。

## 离线回归

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests -p test_information.py -v
.venv/bin/python docs/validation/v0.3.3/check_fixture_clock.py
.venv/bin/python -m compileall -q yantra
.venv/bin/python -m pip check
git diff --check
```

| 检查 | 实际结果与证据 |
|---|---|
| 原生完整回归 | [tests.log](tests.log)：144 项，158.948 秒；141 通过、1 failure、1 error、1 skip |
| 新增功能在完整回归中的检查 | 6 项真实 PTY 协议测试、14 项 Paddle API 模拟测试、1 项 CLI 停用状态测试均通过；信息测试当时 11 通过、1 因可选依赖尚未安装而 skip |
| 安装后信息专项复测 | [information-tests.log](information-tests.log)：13 项全部通过，0.929 秒；含补充的交互导入停用状态回归，无 skip |
| 历史截止时间夹具复核 | [fixture-clock.log](fixture-clock.log)：2 项通过，3.872 秒；运行方式与控制范围见[复核脚本](check_fixture_clock.py) |
| 编译／依赖／差异 | compileall、pip check（No broken requirements found）、git diff --check 均通过 |

原生回归的两个失败均来自既有知识夹具：`test_two_sessions_reuse_verified_fact_not_historical_conclusion` 和 `test_two_actual_adk_sessions_reuse_compare_and_replay_fixed_revisions` 将截止固定为 2026-10-13，却按当前时间记录新知识。跨过北京时间午夜后，论点记录晚于截止，正确被检索排除，导致预期条数／比较关系缺失。知识产品代码和这两个测试文件未作修改；复核只将 `knowledge.store.now`、`knowledge.runtime.now` 固定在夹具日期，研究期限与费用预算继续使用真实时间。原生失败保留，不能把此轮描述为“未经条件控制的完整回归全部通过”。

最终新增检查共 34 项：PTY 6、Paddle 14、信息 13、CLI 状态 1，均已通过。Paddle 覆盖真实材料解析入口的模拟响应、multipart／URL payload、轮询状态、总超时、取消、原图坐标、仅 Markdown 的整图定位、错误费用预留、16 MiB 下载上限和凭据隔离。它们没有发送真实 OCR 请求。

## 实际联网与本地配置

公开文档核对包括 [OSC 7501 revision 0.4](https://www.superlogical.com/rex/docs/build/program-status)、[Paddle jobs API](https://ai.baidu.com/ai-doc/AISTUDIO/Mml7n69e7)、[Paddle 配额](https://ai.baidu.com/ai-doc/AISTUDIO/Xmjclapam)和[四类信息来源](../../INFORMATION_SOURCES.md)。文档读取不等于认证服务验收。

- 98dou：实际免费 GET 通过适配器执行，2026-10-13 23:58:32（Asia/Shanghai）；HTTP 成功、合法 JSON，返回 30 条电报。只记录结构／数量，没有将第三方全文加入 Git。
- AKShare：安装 1.19.1 后通过适配器执行一次 `stock_individual_info_em(symbol="600519")`，2026-10-14 00:02:08（Asia/Shanghai）；9 行、列为 item/value、未截断，查询耗时 1.138 秒。这是公共服务探测，不是投资研究结论。
- 可选依赖经临时清华 TUNA 索引安装，未修改全局 pip 配置；pandas 3.0.6、numpy 2.5.4，既有 google-adk 2.11.0、httpx 0.28.1。
- 本机 `.yantra/config.json` 注册四个 tools 和东方财富技能；AKShare／cls 启用，Fuyao／Eastmoney 停用。命令使用本机绝对解释器及适配器路径，迁移项目后须重新注册。
- 默认 OCR 选择 `paddleocr-api`；旧 `paddleocr-vl-16` MCP 条目保留并停用。研究恢复沿用旧能力快照。
- 根 `.env` 仅写 `FUYAO_API_KEY`、`EASTMONEY_APIKEY`、`PADDLEOCR_API_TOKEN` 占位值；原有 `yantra/.env` 未改动。`git check-ignore -v` 确认根 `.env` 和 `.yantra/config.json` 均忽略，不随源码提供。

```sh
.venv/bin/python -m yantra --output json --version
.venv/bin/python -m yantra --output json models --env-file .env check paddleocr-api
.venv/bin/python -m yantra --output json extensions check tools akshare
```

版本响应为 0.3.3；Paddle 检查为 `ready:false`、`live_api_verified:false`（占位令牌）；工具 check 只确认解释器存在，不能证明 SDK、网络或认证可用。可选 SDK 安装和上述只读调用另有实际结果。

## 未执行与人工剩余步骤

未调用 Fuyao／Eastmoney 认证 API、真实 PaddleOCR jobs、收费 LLM／embedding、完整端到端投研；未使用用户消息中的字面量令牌。终端控制序列已用真实 PTY 检查，但没有验证每一种终端的提示界面。

用户仍须申请或填写三个 Key；认证信息来源还须核实账户费率，填写调用费用上界后启用。Paddle 模板按官方每日免费配额声明 0 元，未来条款变更应先调整。真实 OCR 需用户填写令牌后验证；仅配置就绪不能替代成功 OCR。LLM 凭据与价格仍由用户现有环境或自行合并的 `.env` 提供。

人工步骤见[信息来源配置](../../INFORMATION_SOURCES.md)和[Paddle 配置](../../CAPABILITIES.md#paddleocr-任务-api)。本机运行状态及凭据文件不是 Git 交付物；克隆源码需按指南重建本地配置。
