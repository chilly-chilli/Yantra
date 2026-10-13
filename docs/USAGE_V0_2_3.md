# Yantra v0.2.3 运行说明

本次按用户要求，将已完成的交付物集成版本从0.2.2调整为0.2.3，并使用`v0.2.3`作为Git提交标题。版本号调整没有改变估值算法、授权流程或凭证格式。

```bash
cd /Users/chillychili/Yantra
.venv/bin/python -m yantra --help
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m examples.v022.synthetic
.venv/bin/python -m examples.v022.local_materials
```

方法输入、单位转换、人工改参、历史兼容和市场基准契约沿用[集成运行说明](USAGE_V0_2_2.md)。`examples/v022/`保留原集成阶段的目录名；新建运行记录实际程序版本0.2.3。历史0.2.2快照及其验收记录不改写。

本地真实材料案例需要用户提供的`公开财报及数据/`文件；这些文件与运行输出不纳入本次Git提交。脚本没有调用收费模型，不能等同真实LLM端到端投研。验证结果见[本版记录](VALIDATION_V0_2_3.md)，能力状态与实际依赖见[登记表](DELIVERY_STATUS_V0_2_2.md)。
