# 合成行为评估与工程入口

适用0.4.0（Unreleased）、ADK 2.11.0；agents-cli 固定工具版本1.10.0。所有案例及材料为虚构合成数据，不包含用户财报。案例覆盖缺数字、角色／工具与查询进展、估值及越权、独立否决、暂定交付／确认／恢复（固定演示仍验证原等待契约）。确定性保障由单元／集成测试检查；模型选择和判断按案例criteria做行为评估，不断言模型措辞。

## 分开执行

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_offline_trace.py --output tmp/offline-trace.json
# 已安装CLI时，可同时验证真正的CLI localhost入口；不会安装或升级：
.venv/bin/python scripts/check_offline_trace.py --agents-cli /absolute/path/agents-cli --output tmp/cli-trace.json
.venv/bin/python scripts/eval_behavior.py
```

前三项为确定性／离线轨迹，最后只打印未执行的真实评估计划。离线脚本用固定策略，实际启动本机socket服务器并跨进程重启，读取持久Session、公开trace和Artifact。CLI退出0仍可能漏掉案例，因此额外检查完整case ID、响应状态和直接transport metric；它不是模型质量分数。

本地开发入口使用已有固定环境：`.venv/bin/python -m uvicorn yantra.fast_api_app:app --host 127.0.0.1 --port 8000`。需要已配置本地研究材料；未设置时返回缺材料错误。不要用自动脚手架覆盖项目；实际dry-run会添加无关云配置／A2A／默认模型judge，本次仅采纳manifest和package入口。也不以`uv run`自动解析依赖替代固定.venv。

## 真实生成与评分

真实生成须由用户先批准**已注册模型**、案例ID、总模型调用数、总人民币费用及总墙钟时间。复制[审批模板](live-approval.example.json)到本地忽略目录，填入实际批准值；模板`approved=false`及null不能执行。现有研究默认预算不是评估授权。

```sh
# 仅在具体批准完成后执行；这里的路径都是占位符。
.venv/bin/python scripts/eval_behavior.py --live \
  --approval /absolute/path/approved-eval.json --config /absolute/path/existing-config.json \
  --env-file /absolute/path/existing.env --output tmp/new-live-eval
```

每案例预算均分总上限、共享原总截止，不重置恢复预算；只复制批准的既有LLM配置到临时评估目录，禁用MCP／命令工具／技能／embedding／模型OCR，不读取用户知识库。材料仅来自脚本的合成纯文本。评估工厂在API加载运行时按已有SourceAdapter接口绑定固定空发现，四类检索仍记录但不访问公网。没有替换或收集Runner。每轮经实际ASGI `/run_sse`及公开trace读取，SQLite服务每轮重建；不会自动调用judge。账本仍由RawEngine／RunBudget预留和结算，费用未知时保留预留。部分失败的真实run、完整原账本和generation.json仍保留；traces.json缺案例不能通过验收。

`behavior.json`包含自定义context和真实多轮计划；直接agents-cli generate只会把既往轮次当作history并执行末轮，不能验证真实等待／恢复。应使用上述逐轮生成器。agents-cli转换还丢弃内部Session／invocation／usage；生成器另附公开研究轨迹及合成控制器结果。judge若缺足够证据须给0或人工判未验证，不能猜测通过。

可以由人逐案例审阅criteria，记录通过／失败／未验证、理由与证据位置。托管judge配置为[behavior_eval_config.yaml](behavior_eval_config.yaml)，需**另行批准评估服务凭据及费用**。本次未执行托管judge，也没有自动创建ADC或改用Gemini。命令形态为`agents-cli eval grade --traces tmp/new-live-eval/traces.json --config tests/eval/behavior_eval_config.yaml --output tmp/new-judge-results`，不在本任务授权内。

安装的CLI1.10.0／SDK2.4.0即使只选[纯本地metric](eval_config.yaml)，grade也先构建云client并要求ADC。这里实际探测失败，未产生分数；本地函数／配置解析检查不能替代grade。评估验收须核对预期case集合、不丢案例、无case error、每项criteria有证据且分数达1；命令退出0或平均分不能代替逐项门槛。

CI分别报告deterministic、offline-trace、live-behavior。后者明确NOT EXECUTED，不注入现有用户凭据或假分数。CI配置尚未远端执行；本地对应命令的实际结果见[v0.4.0验证](../../docs/validation/v0.4.0/README.md)。

首轮投资逻辑整合后，隔离的合成生成器在其临时知识库显式拒绝自主调整，使用暂定情景契约；不会复制或改变操作者的长期偏好。`wait_confirm_restore`案例ID为兼容保留，其criteria已改为未回复仍交付、首页不发布正式估值、确认后新版本重算及核验。首次setup门槛和新原始研究的API恢复由 `test_investment_logic.py` 确定性夹具验证；不会把旧固定演示的等待轨迹声称为新行为质量评估。
