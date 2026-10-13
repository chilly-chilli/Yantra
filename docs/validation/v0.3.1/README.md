# v0.3.1版本与推送前检查

日期：2026-10-13。基线CLI实现提交为 `cd73029`，其父提交为v0.3的 `a8a1538`。本次仅同步项目与运行版本、CLI帮助、当前指南与索引，补充版本一致性校验；不修改研究、检索或预算行为。信源订阅、轮询仍未实现。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -p test_cli_contract.py -k output_help_and_version_positions -v
.venv/bin/python -m yantra --version
.venv/bin/python -m yantra --output json --version
.venv/bin/python -m compileall -q yantra/cli.py yantra/__init__.py tests/test_cli_contract.py
git diff --check
```

版本测试 **1项通过，5.842秒**，见[原始日志](version-unittest.log)。实际子进程检查文本／JSON版本、JSON帮助、输出选项位置，并比较 `pyproject.toml` 与 `yantra.__version__`：均为0.3.1。编译、当前指南版本标识及文档链接／锚点另行检查，见[摘要](summary.json)。

完整业务／CLI回归复用本轮此前的[111项通过记录及最终3项偏好补充验证](../v0.3/cli-agent-2026-10-13/README.md)，没有将旧日志改写为0.3.1重新执行，也未因版本与文档修改重复运行无关全套。未调用收费模型、真实研究或远程资料服务。

## Git范围

用户已授权版本提交与push；提交标题为 `v0.3.1`。目标为 `origin` 的 `codex/agent-cli-contract`，远端URL为 `https://github.com/chilly-chilli/Yantra.git`，推送CLI实现与本次版本整理。没有改写已有提交、force push、合并main、创建tag或GitHub Release。未跟踪的用户材料及输出不参与暂存。

本记录保存推送前的验证范围，不预先宣称网络推送成功；实际提交hash、推送结果与远端分支应以Git输出为准。
