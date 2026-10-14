# agents-cli 1.10.0 / Yantra google-adk 2.11.0 research

Scope: read-only investigation; git archive HEAD isolated copy; no tracked Yantra edits or commits; no dependency upgrade; no model/eval-service paid calls. Run time: 2026-10-14T01:27:14.146150+00:00.

## Authoritative installed files

CLI environment: /Users/chillychili/.local/share/uv/tools/google-agents-cli/lib/python3.13/site-packages/
- google/agents/cli/_project.py: ProjectConfig.from_dict, find_project_root, require_agent_directory, resolve_root_agent_name.
- google/agents/cli/eval/cmd_generate.py: split_case_history, run_case, _run_cases, _run_against_local_server.
- google/agents/cli/eval/_events.py: parse_content_event, to_adk_event_payload.
- google/agents/cli/eval/eval_utils.py: prepare_eval_metrics, _resolve_custom_function_file.
- google/agents/cli/eval/cmd_grade.py: cmd_grade constructs AgentPlatformClient even with only local functions.
- google/agents/cli/run/_local_server.py: _python_serve_command; ensure_server.
- google/agents/cli/scaffold/commands/enhance.py: position argument is template source; always targets cwd; dry-run requires saved metadata and requested change.
- agentplatform/_genai/client.py: Client.__init__ always calls genai Client._get_api_client(vertexai=True), even for local grading.

Installed: google-agents-cli 1.10.0; tool's google-cloud-aiplatform 2.4.0; google-genai 2.30.1; tool does not itself install google-adk. Yantra .venv owns pinned ADK 2.11.0.

## Minimum manifest

`find_project_root(/Users/chillychili/Yantra)` returned None before implementation; plain pyproject.toml is not enough. Minimal recognized config:

```yaml
name: yantra
acli_version: '1.10.0'
agent_directory: yantra
base_template: adk
language: python
framework: google-adk
create_params:
  root_agent_name: yantra
  deployment_target: none
  session_type: none
  cicd_runner: skip
  agent_guidance_filename: AGENTS.md
```

Region can be omitted (default us-east1); eval service region defaults global and is distinct. Manifest metadata is declarative; adding it alone neither supplies a server nor changes native Runner behavior. `App.name` must match the loaded package directory yantra; root Agent.name should match manifest root_agent_name but need not generally equal App.name.

## Entry and developer commands

Existing yantra/agent.py root_agent is loadable by native fixed ADK. If defining App, export `app = App(name="yantra", root_agent=root_agent, plugins=[...])` from that entry; native loader discovers app first.

CLI local generate chooses app_name from cfg.agent_directory and launches:
- if yantra/fast_api_app.py exists: `uv run uvicorn yantra.fast_api_app:app --host 127.0.0.1 --port <allocated>`
- else: `uv run adk api_server --host 127.0.0.1 --port <allocated> --reload_agents --no-reload .`

It sets USE_IN_MEMORY_SESSION=false for evaluation, but generic adk api_server controls its own services. It starts/stops its own server, and leaves a reused server running. Runtime PID/log files go under .google-agents-cli/; this is generated local state.

For pinned, isolated validation prefer manually starting `.venv/bin/adk api_server` and `agents-cli eval generate --url http://127.0.0.1:<port> --app-name yantra --concurrency 1 ...`, bypassing `uv run` dependency resolution entirely. Do not invoke raw `uv run` against this dependency list without preserving fixed installed versions / lock behavior.

## Evaluation format and semantics

Paths: tests/eval/datasets/basic-dataset.json, tests/eval/eval_config.yaml. Dataset container `eval_cases`; single case `eval_case_id` + `prompt: {role: user, parts: [{text: ...}]}`. Generated traces add `agent_data.agents`, `agent_data.turns[*].turn_index/events`, `responses: [{response: {role: model, parts: [...]}}]`. `reference` likewise wraps Content in `{response: ...}`. `context` and rubric_groups can carry authored provenance and case criteria.

Config accepts YAML/JSON: metrics_to_run and custom_metrics. Code metric `{name, custom_function_file: metrics.py, execution: local}` resolves file relative to config directory; source must define evaluate(instance). Inline custom_function also supported. Prompt-template metrics and managed built-ins are service calls. Defining a metric does not select it. Local functions may retry up to five times and execute concurrently, so model-calling judges need their own shared persistent budget / lock; avoid side effects. `--qps 1` paces dispatch, not an overall call/cost cap.

SSE multi-turn generation does NOT execute all authored user turns: it seeds history and responds only to trailing user. Trailing event state_delta gets moved to a prior content-less ADK event with actions.stateDelta. This can target a prepared run_id for a bounded run; seeded state is test setup and must never be represented as actual historical execution. True wait/confirm/restart acceptance should drive sequential requests against persisted Session and inspect durable budget.

CLI event conversion only preserves author, content, event_time, state_delta; it drops native run/task/session/invocation IDs and usage. Therefore canonical Yantra full trace / usage evidence must be exported separately, with sanitized business associations, and can be attached as eval context or extension metadata.

`eval generate` exit 0 can mean partial success: failures are dropped from output. Business status=error returned in Content is still a successful transport case. `eval run`/grade exit status is not the score gate. CI must require expected case IDs, no dropped/error cases, threshold metrics, and no num_cases_error.

## Measured isolated probes

1. Temp copy recognized by manifest. Actual command:
   `UV_OFFLINE=1 agents-cli scaffold enhance --prototype --deployment-target none --cicd-runner skip --agent-directory yantra --agent-guidance-filename AGENTS.md --skip-checks --skip-deps --dry-run`
   Exit 0, no changes made. Would add .env, .gcloudignore, Dockerfile, default Gemini model judge, dummy unit/integration tests, A2A utilities, fast_api_app. Preserve existing files. These are outside the requested minimal engineering scope; do not apply whole enhance output.

2. Standard CLI through loopback native ADK server with controlled fictional materials from tracked tests/test_adk_structure.py:test_materials. `eval generate` exit 0, one case produced. Full output and summary: eval_probe/generated-waiting.json and eval_probe/summary.json. Actual business state waiting; finance/thesis/valuation tasks; 11 deterministic backend decisions, 4 tools, zero provider calls. Generated trace has only three yantra events, one execute_research call; app-info declares all four workers. This is an observable pre-change trace baseline, not a real model behavioral evaluation.

3. `agents-cli eval grade --traces eval_probe/traces.json --config eval_probe/config.yaml --output eval_probe/results` using only pure local custom function FAILED. Root error DefaultCredentialsError; no scores/output artifact. Contrary to current docs' no-GCP claim, installed agentplatform.Client requests ADC in constructor before local metric can run. Do not invent ADC/GCP/API-key placeholders or alter credentials. Direct prepare_eval_metrics + evaluate(instance) parser/function smoke succeeds and is explicitly only offline schema check.

4. Initial API probe without fixture returned status error/FileNotFoundError while CLI generate exited 0. This is additional evidence that transport success cannot be an eval pass gate.

## Bounded live option

No `agents-cli eval generate/run` cost cap or model-call cap exists. `adk api_server --max_llm_calls` caps one outer invocation and does not automatically reach separately constructed nested Runner. Implement only via authoritative Yantra Limits + RunBudget reservations, selected existing model/pricing snapshot, and immutable per-run input setup. A viable local command prepares a controlled RawEngine with `max_models`, `max_cost_cny`, `run_seconds`, explicitly disabled external tools/extensions, then drives native HTTP Session against that existing run_id, using concurrency=1 and sanitized full trace export. It must fail before paid calls if approved environment/pricing/budget is absent, and preserve incomplete/error responses. Use separate eval generation and grading. If a local custom judge calls the selected model, that judge needs the same bounded accounting and retry safety; managed cloud metrics are not authorized by merely authorizing the agent model. Current ADC limitation blocks installed CLI grade here; leave real model/service eval as not executed unless approved credentials+budget actually exist.

## Official primary supplements

- https://github.com/google/agents-cli/blob/v1.10.0/src/google/agents/cli/_project.py
- https://google.github.io/agents-cli/cli/
- https://google.github.io/agents-cli/guide/evaluation/

Current docs confirm generate+grade decomposition and config schemas, but installed source/probes govern the above limits. Browser CDP prerequisite probe timed out on authorization; no browser tabs created. Public primary sources accessed via web-access static search/open path.
