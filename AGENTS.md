# AGENTS.md

Scope: the entire Yantra repository. Explicit instructions from the user take precedence. Task prompts, proposals, and historical validation records are reference material; reading them does not authorize additional work. Start with the [project README](README.md) and [documentation index](docs/README.md).

## 1. Expression Clarification

When the user's wording contains inaccurate terminology, awkward phrasing, semantic ambiguity, inappropriate domain vocabulary, or imprecise concepts, infer the intended meaning from context and answer normally instead of merely repeating the wording.

When clarification is warranted, append a brief Expression Optimization section at the end of the response:

1. Identify the imprecise or nonstandard wording.
2. Suggest a clearer or more appropriate alternative.
3. Optionally provide the conventional terminology or collocation.

The purpose is better communication, not grammatical correction. Keep it concise and non-pedantic. Use the language appropriate to the user; this file's language does not require English responses.

## 2. Karpathy Coding Principles

### Think Before Coding

Do not assume or hide uncertainty. Make tradeoffs explicit.

- State assumptions before implementation. Ask when uncertainty affects the result.
- Explain multiple plausible interpretations rather than silently choosing one.
- Point out simpler approaches and push back when warranted.
- If a blocking point is unclear, identify it and ask a concrete question.

### Simplicity First

Write the minimum code that solves the requested problem.

- Add no unrequested features.
- Avoid abstractions for single-use code.
- Add no speculative flexibility or configuration.
- Avoid error handling for impossible scenarios.
- If 200 lines could be 50, simplify the implementation.
- Ask whether a senior engineer would consider the solution overcomplicated.

### Surgical Changes

Change only what the task requires and clean up only what your changes make obsolete.

- Do not improve adjacent code, comments, or formatting without a task-related reason.
- Do not refactor working code outside the requested scope.
- Follow the existing style.
- Mention unrelated dead code instead of deleting it.
- Remove imports, variables, and functions made unused by your changes.
- Every changed line should be traceable to the user's request.

### Goal-Driven Execution

Define observable success criteria and verify them.

- Validation changes: test invalid inputs, then make the tests pass.
- Bug fixes: reproduce the bug with an appropriate test, then fix it.
- Refactoring: ensure relevant tests pass before and after.
- Multi-step work: provide a brief plan and a verification check for each step.
- Keep checks proportional. Documentation changes need link, command, and consistency checks; behavior changes need meaningful tests and relevant regression checks.

## 3. Commit and Push Rules

### Local Commits

1. Check `git status` and the current branch before editing. Identify changes from the current task, earlier tasks, and other contributors.
2. For authorized development or documentation work, create a local commit after completing a coherent, verified unit whose scope can be isolated. Commit large tasks in stages. Do not repeatedly ask for permission already granted; honor explicit instructions not to commit.
3. Stage only relevant files or hunks. Review the staged diff, run `git diff --check`, and complete the applicable checks. Keep implementation, tests, and documentation consistent within the commit. Do not use `git add .` to include unrelated changes from a mixed working tree.
4. If the current documentation or change depends on an uncommitted earlier implementation and cannot form a consistent standalone commit, preserve the changes and report the dependency and pending scope. Do not automatically bundle earlier work or another contributor's changes into the current commit.
5. Use descriptive titles, such as `docs: organize project documentation`, `feat(knowledge): add hybrid retrieval`, or `fix(replay): preserve pinned revisions`. Follow a title explicitly requested by the user. For complex changes, describe the reason, verification, and limitations in the body.
6. Completed commits should pass their relevant checks. If an intermediate checkpoint is needed, use an explicitly labeled WIP commit on a personal development branch, then verify or organize it before completing the task. Do not present WIP as a completed release.
7. Do not change the global Git identity, discard another contributor's changes, or rewrite shared commits without authorization. Report the actual commit hash, or state that no commit was created.

### Remote Pushes

1. Push when explicitly requested, including requests for remote synchronization, remote backup, or a PR that requires a push. Questions about concepts, documentation edits, or adding policies do not automatically authorize a push. Existing explicit authorization remains valid.
2. Confirm the remote, target branch, pending commit range, and relevant checks before pushing. Normally push a development branch. Follow the user's instructions and the repository workflow for the main branch; do not independently merge or release.
3. Specify the remote and branch target explicitly. Without additional explicit authorization, do not force push, delete remote branches or tags, or rewrite remote history.
4. A push is separate from a merge, deployment, or release. Perform subsequent actions only within the task's authorization. Investigate and fix failed checks or CI; do not report a successful push as successful validation.
5. Distinguish local edits, committed changes, pushed changes, merged changes, and released changes. On a Git failure, report the failed stage and recoverable state accurately.

## 4. Documentation Standards

### Primary Documents and Responsibilities

| Location | Responsibility |
|---|---|
| `README.md` | Concise project overview, quick start, and documentation navigation |
| `docs/README.md` | Index of current guides, history, decisions, proposals, and validation |
| `docs/USAGE.md` | Current installation, research, budgets, recovery, and replay operations |
| `docs/CAPABILITIES.md` | Current model, OCR, embedding, and extension configuration |
| `docs/VALUATION.md` | Current methods, sources, units, authorization, and mathematical recomputation |
| `docs/KNOWLEDGE.md` | Current knowledge storage, indexes, reuse, deletion, and explicit user requirements |
| `docs/ARCHITECTURE.md` | Current module boundaries, data flow, permissions, and constraints |
| `docs/decisions/` | Dated decisions with status and links to superseded decisions |
| `docs/proposals/`, `docs/prompts/` | Proposals, historical reviews, and original task instructions with version and status |
| `docs/validation/<version>/` | Validation commands, results, evidence, live-service scope, and limitations for that version |
| `docs/archive/<version>/` | Historical usage, implementation, and delivery documents |
| `CHANGELOG.md` | Reader-facing version changes; unpublished changes belong under Unreleased |

### Content and Version Maintenance

1. Maintain one primary document for each topic. Link to it from other documents instead of copying complete procedures or rules into multiple sources.
2. Use stable filenames for current guides and state the applicable version. Update them when the CLI, interfaces, defaults, storage, or permissions change. Normally deliver related code, tests, and documentation in the same commit or PR.
3. Preserve the original version, date, commands, results, and failures in historical records. Do not rewrite an old result as current success. Limit migrations to links and status notices; add explicit corrections when a factual error needs to be addressed.
4. Label proposals as proposed, adopted, or superseded, and record differences from the implementation. Reserved interfaces are not implemented capabilities. Function tests or offline fixtures are not live end-to-end validation.
5. Separate actual execution, offline fixtures, real parsing, live network access, and live model calls in validation records. Cite actual logs or summaries for test counts, elapsed time, and versions. State checks not performed and remaining limitations.
6. Give new decisions and archives a date, version or scope, and related links. Update the changelog for feature changes. Claims about tags, pushes, merges, and releases must reflect actual operations.
7. Update repository links when moving files. Preserve short compatibility entries for paths already delivered or externally referenced, without duplicating the body. Keep existing flat historical evidence files at their original paths; organize new evidence by version.
8. Use portable relative links and explicit heading anchors in repository documents. Assume commands run from the repository root. Distinguish runnable examples from placeholders for paths, IDs, JSON, or financial data.
9. Configuration examples should reference credential environment variable names. Do not automatically include user materials, local credentials, research output, or temporary files as documentation attachments or Git content. Clearly label controlled synthetic fixtures.
10. Documentation organization must preserve source behavior, historical validation evidence, and user input boundaries. Record unrelated code problems instead of fixing them during documentation cleanup.

### Completion Checks

- Confirm the README and index lead to current guides and relevant historical records.
- Check local Markdown file links, heading anchors, moved paths, and compatibility entries. Explain the availability of local run artifacts.
- Check examples with the existing CLI parser, `--help`, or offline temporary fixtures. Do not trigger paid models, real research, or data deletion to validate documentation.
- Run `git diff --check` and review the change scope. Do not rerun an unrelated full suite for documentation-only work.
- Report document changes, verification, limitations, and actual commit and push status.

## 5. ADK Native Mechanisms and Business Ownership

1. Check the installed, pinned Google ADK interfaces before adding a custom execution, orchestration, Session, event, tool lifecycle, or artifact mechanism. Latest documentation is supplementary; do not upgrade ADK or change the model to satisfy an integration.
2. ADK owns general execution interfaces, Session/events, and tool/plugin lifecycle. Yantra owns the research contract, isolation and permissions, evidence revisions, budgets, valuation authorization, deterministic calculation, independent verification, and business recovery.
3. Each business state has one authoritative writer. Observation plugins must not settle fees, copy tool execution, or maintain a competing business ledger. Every separately constructed Runner must use the shared native App/plugin factory.
4. A custom exception must state the pinned ADK capability gap, the business necessity, and external behavior checks. Native integration must preserve role/parent isolation, frozen permissions, depth limits, evidence hashes/CAS, deadlines, and truthful partial/failed results.
5. Runtime commits must explain the reused capability, remaining custom mechanisms, and validation. Test externally observable behavior (including standard API and trace access); importing ADK classes is not architecture acceptance. Follow [ADR 0002](docs/decisions/0002-adk-native-boundaries.md).
6. Keep deterministic checks, offline API/CLI traces, live model generation and judge evaluation separate. Use [the evaluation entry](tests/eval/README.md); explicit model/service credentials and total budgets are required for live work. Unknown performance, cost or model quality must remain unknown, and a pilot with regressions must not become the default without new evidence.

## 6. Git Ignore Policy

### Default Ignore Categories

Use specific paths and purposes when deciding what belongs in `.gitignore`.

| Category | Recommended patterns or scope |
|---|---|
| Virtual environments and Python caches | `.venv/`, `__pycache__/`, `*.py[cod]`, `*.egg-info/` |
| Local credentials | `.env`, `.env.*`; preserve `.env.example` and `.env.template` |
| Runtime state and local knowledge | `.adk/`, `.yantra/`, `runs/`, including their databases, vectors, and source snapshots |
| Generated output and temporary files | Root `/output/`, root `/tmp/`, and known generated example directories such as `examples/v022/output/` |
| User research materials | Root `/materials/` and root `/公开财报及数据/` |
| Build and checking artifacts | Root `/build/`, root `/dist/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `.coverage.*`, and `htmlcov/` |
| Operating-system metadata | `.DS_Store` |

Actual repository path names remain unchanged, including paths written in another language.

### Content to Keep Versioned

Keep source code, tests, clearly labeled synthetic fixtures, configuration templates, dependency specifications and lockfiles, project documentation, and reviewed validation logs and JSON summaries in Git. Examples must contain placeholders or controlled synthetic values rather than actual credentials or private research data.

### Applying and Checking Rules

1. Do not globally ignore `*.log`, `*.json`, `*.pdf`, or `*.sqlite`. These extensions may contain formal evidence, documentation resources, or test fixtures. Place disposable files in explicitly ignored runtime or output directories instead.
2. Anchor the input-materials rule as `/materials/` at the repository root. Do not unintentionally hide package fixtures such as `yantra/materials/` or controlled test data.
3. Classify directories that mix code and inputs before ignoring them. Do not blanket ignore `my_agent/` or `交付物/`; preserve source that should be maintained and ignore only identified local or generated content.
4. Put personal editor preferences in `.git/info/exclude` or a personal global ignore file. Keep shared team configuration when it serves the project.
5. Preserve `.env.example` and `.env.template` with explicit exceptions when adding `.env.*` patterns, for example `!.env.example` and `!.env.template`.
6. After changing ignore rules, use `git check-ignore -v` on representative ignored and retained paths, and review `git status`. Check both runtime output and intended source, fixtures, templates, and validation evidence.
7. Ignore rules do not remove files already tracked by Git. Review tracked-file changes separately; do not automatically remove them from the index or delete their contents when adding a rule.
8. Keep ignore edits within the approved task scope. A request to document this policy does not by itself authorize changing `.gitignore`, deleting data, or changing tracked-file membership.
