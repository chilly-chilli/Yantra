# 本地凭据文件合并

日期：2026-10-14（Asia/Shanghai）。适用版本：0.3.3。本轮按用户指令将本项目的本地 dotenv 配置合并为根目录 `.env`；配置方式见[使用指南](../../USAGE.md#配置入口)。此记录不包含变量值、密钥或用户材料。

## 实际变更

- 合并四个本机文件：根 `.env`、`yantra/.env`、`yantra/.env.save` 和此前被 Git 忽略的 `.yantra/paddleocr.env`。
- 对同名变量检查冲突；重复的原模型配置值一致，未遇到不同真实值的冲突。合并后保留 12 个不同变量，包含模型／研究设置、三个新服务变量及旧 PaddleOCR MCP 引用；没有将旧 MCP 令牌自动填入新 API 变量。
- 使用原始 dotenv 赋值语句保留值及引用，原子替换根文件；逐项确认解析值一致后，删除三个冗余文件。根 `.env` 权限为 `0600`。
- `.env.example` 仅保留为无真实凭据的模板，不是第二个运行配置。当前指南统一使用 `--env-file .env`；从其他目录调用可提供根文件绝对路径。
- 本轮未改变应用加载逻辑、模型选择、扩展权限、价格或预算。CLI 继续显式加载；本机 ADK 2.11.0 的 run/web 支持从 agent 目录向父目录查找 `.env`，无需保留包目录副本。

## 验证

实际执行并通过：

1. 合并前／后用 `dotenv_values(interpolate=False)` 对全部变量值比较，确认 12 个不同变量保留；确认无 dotenv 解析错误。
2. 递归检查项目（排除 `.git`、`.venv` 和缓存）：仅剩 `.env` 与 `.env.example`；无 `.env.save` 或其他 `*.env` 副本。
3. `load_dotenv` 读取根文件后，逐项比较进程环境值；未输出内容。
4. 在隔离的进程环境字典中调用实际 ADK `envs.load_dotenv_for_agent("yantra", repository_root)`，确认加载根文件的变量值。
5. 真实 CLI 子进程执行 `.venv/bin/python -m yantra --output json models --env-file .env list`，退出 0，输出满足 JSON 响应结构；输出未作为凭据附件保存。
6. `yantra --help`、`yantra models --help`、当前文档相对链接／标题锚点和 `git diff --check` 检查通过。
7. `git check-ignore` 确认根 `.env` 被忽略、`.env.example` 保持可版本化；暂存内容不包含本机凭据文件。

没有调用模型、OCR 或信息 API，没有执行真实研究，也未重新运行无关的全套回归。旧验收的命令路径和当时配置状态仍作为历史证据保留；当前运行使用根 `.env`。旧命令若显式指向 `yantra/.env`，再次执行时应改为 `--env-file .env`。

凭据合并和冗余文件删除是本机已执行的操作，因文件被忽略不进入 Git；源码克隆仅获得统一配置说明与模板。文档更新按本地提交记录追踪，本轮未推送或发布。
