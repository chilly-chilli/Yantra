# 最终安装包 OpenCode 原生入口验收

日期：2026-10-15（Asia/Shanghai）。代码基线：`119a69b`，安装包：0.5.0，实际宿主：OpenCode 1.18.33。本文补齐[逐项审计](../contract-audit-2026-10-15/README.md)当时未完成的最终安装包宿主闭环；不改写此前的端口错误、超时或测试数量。

## 已执行范围

以实际 `opencode --pure run --dir ... --format json` 运行隔离项目，使用本机已安装的 OpenCode。provider 的 `npm` 配置指向受控本地模块，直接提供 LanguageModelV3 确定性流。字段核对本机 `@ai-sdk/provider` 的正式类型源码；替身只输出预定工具调用和文本，不读取用户资料、不连接推理服务，也不修改 OpenCode 的代码、工具执行或权限检查。stdio MCP 使用独立安装环境中的 Yantra，主要 Skill 从同一个 wheel 复制并校验哈希。

MCP 流程中 bash/edit 为 deny，显式放行主要 Skill 和 Yantra 工具；本次仅调用这些工具。宿主实际加载 Skill，完成 doctor（command_tool=denied，route=skill_mcp）→submit→waiting→confirm→partial→read v1→modify growth_rate 0.05→0.04→partial→read v2→read v1→list。实际49次宿主工具调用、50次主流程SDK替身请求（含状态轮询），进程退出0。报告版本为1、2、1，首次和最后一次v1哈希一致，v2不同。成果资格保留partial/unverified、假设版本、授权来源及四项资料缺口；每次只读2048字节，complete=false，不能把分片当成整份报告。末尾展示文本也标明研究ID、版本和限制；这是确定性模板，不证明真实自然语言模型理解或概括能力。

另用 OpenCode 原生 `debug agent build --tool bash` 精确放行安装环境的本地 doctor 命令，宿主和工具均退出0，route=skill_cli，network=not_probed，research=not_executed。这验证有命令权限时的CLI路径；MCP闭环没有放开bash。此前[命令拒绝检查](../contract-audit-2026-10-15/opencode-local.json)继续有效。仅写临时项目/XDG配置，没有改用户的既有宿主配置。

完整[结构化结果与原始trace哈希](opencode.json)记录实际资格和读取范围。原始驱动、模块、请求与宿主输出在 `/tmp/yantra-host-acceptance/opencode-final-native-flow/`，可能被系统清理；不随仓库分发。此前插件初始化超时没有被写成成功；本次使用原生本地SDK入口完成，不需要TCP监听，也没有绕过平台限制。替身最初使用旧版结束原因格式及一次换行生成错误，分别造成探测循环/初始化失败，修正V3格式和fixture生成后才得到上述成功结果。

## 保留的验证限制

- [完整回归](../contract-audit-2026-10-15/audit-full.log)的四项localhost HTTP fixture仍在socket.bind阶段报PermissionError，未被改为skip；本次不涉及这些HTTP入口，也不能宣称全量全绿。
- [隔离安装及依赖来源](../README.md#安装验收)保持原结论：95项依赖的独立环境已运行通过，三项缺少原始发行包的依赖由RECORD校验的安装快照重打包。公网代理不可连接，普通公网全新下载未验证。
- 没有真实金融资料联网、研究模型、宿主收费模型或judge调用；研究质量、真实服务兼容性及费用仍未知。没有push、合并、部署或发布。

OpenCode完整宿主闭环的待办已解决；本地HTTP完整回归和公网下载限制仍未解决。原验收记录中的更早状态保留作为历史证据。
