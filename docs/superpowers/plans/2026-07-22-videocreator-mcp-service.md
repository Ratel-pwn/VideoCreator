# VideoCreator MCP Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing VideoCreator project and run workflow as an asynchronous Streamable HTTP MCP service usable by Codex and other MCP-capable agents.

**Architecture:** A FastMCP transport delegates high-level tools to a workflow application service. The application service persists operational jobs in SQLite, while project run files remain authoritative; an independent worker advances runs through a console-independent interaction port.

**Tech Stack:** Python 3.12, official `mcp` Python SDK, FastMCP, Starlette/Uvicorn, SQLite, pytest

## Global Constraints

- Preserve existing `vc templates/init/chat/import-chat/resume/status/runs` behavior.
- Keep `projects/<project>/runs/<run-id>/state.json` authoritative for workflow state.
- Store only queue, lease, cancellation, and service-event data in SQLite.
- Default to one worker while allowing Streamable HTTP to bind to any configured interface.
- Read bearer secrets from environment variables only; never persist or log them.
- Never accept arbitrary commands or unconstrained filesystem paths through MCP.
- Do not return binary audio or video payloads through MCP.

---

### Task 1: Runtime Configuration And Durable Queue

**Files:**
- Create: `videocreator/runtime_config.py`
- Create: `videocreator/job_queue.py`
- Create: `tests/test_runtime_config.py`
- Create: `tests/test_job_queue.py`
- Modify: `workflow.config.json`
- Modify: `.gitignore`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `McpRuntimeConfig.from_workflow(config, home) -> McpRuntimeConfig`
- Produces: `JobQueue(database_path)`, `enqueue()`, `claim()`, `renew()`, `complete()`, `fail()`, `request_cancel()`, `release_waiting()`, and `reconcile()`

- [ ] **Step 1: Write failing configuration tests**

Test default local configuration, arbitrary bind addresses, bearer environment lookup, and resolved runtime paths.

- [ ] **Step 2: Run configuration tests and verify missing-module failure**

Run: `pytest tests/test_runtime_config.py -v`
Expected: FAIL because `videocreator.runtime_config` does not exist.

- [ ] **Step 3: Implement immutable runtime configuration**

Use dataclasses for host, port, path, public base URL, runtime directory, worker count, lease duration, shutdown grace period, allowed hosts, and auth settings. Validate positive numeric values and supported auth modes without restricting deployment to loopback.

- [ ] **Step 4: Write failing queue tests**

Cover unique active jobs per run, FIFO claiming, lease renewal, expired-lease recovery, cancellation, waiting release, and restart persistence using a temporary SQLite file.

- [ ] **Step 5: Run queue tests and verify missing implementation failure**

Run: `pytest tests/test_job_queue.py -v`
Expected: FAIL because `videocreator.job_queue` does not exist.

- [ ] **Step 6: Implement the SQLite queue**

Create schema initialization and transactionally safe queue operations. Use `BEGIN IMMEDIATE` for claims, ISO timestamps, a partial unique index for active run jobs, and no workflow-stage payload duplication.

- [ ] **Step 7: Add dependency and public defaults**

Add `mcp>=1.0,<2` to project dependencies, add the approved `mcp` section to `workflow.config.json`, and ignore `.runtime/`.

- [ ] **Step 8: Run focused tests**

Run: `pytest tests/test_runtime_config.py tests/test_job_queue.py -v`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```powershell
git add pyproject.toml workflow.config.json .gitignore videocreator/runtime_config.py videocreator/job_queue.py tests/test_runtime_config.py tests/test_job_queue.py
git commit -m "feat: add durable MCP job queue"
```

### Task 2: Durable Workflow Interactions

**Files:**
- Create: `videocreator/interactions.py`
- Create: `tests/test_interactions.py`
- Modify: `main.py`
- Modify: `tests/test_main_stage_dispatch.py`

**Interfaces:**
- Produces: `InteractionPort.ask(ctx, key, prompt, kind, choices) -> str`
- Produces: `ConsoleInteractionPort`
- Produces: `DurableInteractionPort`
- Produces: `InteractionRequired`
- Changes: `WorkflowContext.interactions` defaults to `ConsoleInteractionPort`
- Produces: `execute_until_boundary(ctx) -> WorkflowOutcome`

- [ ] **Step 1: Write failing durable interaction tests**

Test creation of one pending interaction, JSONL event recording, worker pause, accepted response consumption, stale ID rejection, and duplicate response idempotency.

- [ ] **Step 2: Run interaction tests and verify failure**

Run: `pytest tests/test_interactions.py -v`
Expected: FAIL because the interaction types do not exist.

- [ ] **Step 3: Implement interaction ports**

Implement console prompts and durable state-backed prompts. Store the active interaction in `state.json` and append sanitized question/reply events to `session/interactions.jsonl`.

- [ ] **Step 4: Write failing workflow boundary tests**

Verify that prepare, chat, draft confirmation, TTS confirmation, visual confirmation, asset confirmation, and render confirmation use the injected port rather than direct `input()` calls.

- [ ] **Step 5: Run workflow boundary tests and verify direct-input failure**

Run: `pytest tests/test_main_stage_dispatch.py tests/test_interactions.py -v`
Expected: FAIL at the first direct terminal interaction.

- [ ] **Step 6: Refactor workflow execution**

Replace direct `input()` and `request_confirmation()` use with stable interaction keys. Add `execute_until_boundary()` that returns `waiting_for_input`, `completed`, `failed`, or `cancelled` without swallowing unexpected errors. Keep `execute_from_current_stage()` as the console-compatible wrapper.

- [ ] **Step 7: Run focused and existing workflow tests**

Run: `pytest tests/test_interactions.py tests/test_main_stage_dispatch.py tests/test_workflow_state.py -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add main.py videocreator/interactions.py tests/test_interactions.py tests/test_main_stage_dispatch.py
git commit -m "refactor: make workflow interactions resumable"
```

### Task 3: High-Level Workflow Application Service

**Files:**
- Create: `videocreator/workflow_service.py`
- Create: `tests/test_workflow_service.py`
- Modify: `videocreator/cli.py`

**Interfaces:**
- Produces: `WorkflowService.list_templates()`
- Produces: `WorkflowService.list_projects()`
- Produces: `WorkflowService.initialize_project()`
- Produces: `WorkflowService.start_workflow()`
- Produces: `WorkflowService.list_workflows()`
- Produces: `WorkflowService.get_workflow_status()`
- Produces: `WorkflowService.submit_workflow_input()`
- Produces: `WorkflowService.resume_workflow()`
- Produces: `WorkflowService.cancel_workflow()`
- Produces: `WorkflowService.get_workflow_result()`

- [ ] **Step 1: Write failing service tests**

Cover all ten operations, project containment, normalized statuses, run creation before enqueue, state conflicts, text artifact size limits, media metadata-only results, and remote public URL projection.

- [ ] **Step 2: Run service tests and verify missing-module failure**

Run: `pytest tests/test_workflow_service.py -v`
Expected: FAIL because `videocreator.workflow_service` does not exist.

- [ ] **Step 3: Implement application errors and service methods**

Return JSON-serializable dictionaries and raise typed errors carrying `invalid_argument`, `not_found`, `state_conflict`, `workflow_failed`, or `service_unavailable`. Reuse project layout, template discovery, and run summary functions.

- [ ] **Step 4: Route existing CLI reads and initialization through shared services**

Keep CLI output stable while removing duplicated project and run lookup logic where the service now owns it.

- [ ] **Step 5: Run service and CLI tests**

Run: `pytest tests/test_workflow_service.py tests/test_cli.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add videocreator/workflow_service.py videocreator/cli.py tests/test_workflow_service.py tests/test_cli.py
git commit -m "feat: add MCP workflow application service"
```

### Task 4: Worker And Recovery

**Files:**
- Create: `videocreator/worker.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Produces: `WorkflowWorker.run_once() -> bool`
- Produces: `WorkflowWorker.run(stop_event) -> None`
- Consumes: `JobQueue`, `WorkflowService`, `DurableInteractionPort`, and `execute_until_boundary()`

- [ ] **Step 1: Write failing worker tests**

Test queued execution, lease renewal, waiting release, completion, failure recording, cancellation at a boundary, expired-job recovery, and startup reconciliation.

- [ ] **Step 2: Run worker tests and verify missing-module failure**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL because `videocreator.worker` does not exist.

- [ ] **Step 3: Implement one-job worker execution**

Claim one job, acquire the shared run lock, resume its context with a durable interaction port, advance to a boundary, update run and queue state, and release resources in `finally`.

- [ ] **Step 4: Implement the polling loop and recovery hooks**

Use configurable polling and lease settings. Do not occupy the worker while a run is waiting for input.

- [ ] **Step 5: Run worker and queue tests**

Run: `pytest tests/test_worker.py tests/test_job_queue.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add videocreator/worker.py tests/test_worker.py
git commit -m "feat: add resumable workflow worker"
```

### Task 5: MCP Transport And Authentication

**Files:**
- Create: `videocreator/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `create_mcp_server(service) -> FastMCP`
- Produces: `create_http_app(config, service) -> ASGI application`
- Consumes: the ten `WorkflowService` operations

- [ ] **Step 1: Write failing MCP contract tests**

Use the official MCP client to verify discovery of exactly ten tools, structured results, typed errors, tool annotations, and absence of binary media payloads.

- [ ] **Step 2: Run contract tests and verify missing-module failure**

Run: `pytest tests/test_mcp_server.py -v`
Expected: FAIL because `videocreator.mcp_server` does not exist.

- [ ] **Step 3: Implement FastMCP tools**

Declare explicit Pydantic-compatible parameters and concise tool descriptions. Convert application errors to sanitized MCP errors without exposing stack traces.

- [ ] **Step 4: Write failing HTTP authentication tests**

Test `none` mode, valid bearer tokens, missing tokens, invalid tokens, configured host/path behavior, and non-loopback unauthenticated warning behavior.

- [ ] **Step 5: Implement the HTTP application and bearer middleware**

Read the bearer token from the configured environment variable at startup. Apply allowed-host validation without imposing loopback-only deployment.

- [ ] **Step 6: Run MCP tests**

Run: `pytest tests/test_mcp_server.py -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add videocreator/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: expose VideoCreator MCP tools"
```

### Task 6: Service Lifecycle Commands

**Files:**
- Create: `videocreator/mcp_runtime.py`
- Create: `tests/test_mcp_runtime.py`
- Modify: `videocreator/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `serve(home, config_path) -> int`
- Produces: `start(home, config_path) -> ServiceInfo`
- Produces: `stop(runtime_config) -> ServiceInfo`
- Produces: `status(runtime_config) -> ServiceInfo`
- Adds: `vc mcp start|stop|status|logs|serve`

- [ ] **Step 1: Write failing lifecycle tests**

Test foreground startup, PID metadata, duplicate-start detection, health status, stop requests, stale PID cleanup, log tailing, and configurable host/port.

- [ ] **Step 2: Run lifecycle tests and verify missing-module failure**

Run: `pytest tests/test_mcp_runtime.py -v`
Expected: FAIL because `videocreator.mcp_runtime` does not exist.

- [ ] **Step 3: Implement foreground supervisor**

Run Uvicorn and a worker child process, write `service.json` atomically, monitor the stop request, and reconcile queue state before accepting work.

- [ ] **Step 4: Implement background process management**

Use detached process flags on Windows and a new session on POSIX. Redirect server and worker output to `.runtime/logs/` and never place secrets in command arguments.

- [ ] **Step 5: Add CLI subcommands**

Parse `vc mcp start|stop|status|logs|serve`, preserve global home/config handling, and return script-friendly exit codes.

- [ ] **Step 6: Run lifecycle and CLI tests**

Run: `pytest tests/test_mcp_runtime.py tests/test_cli.py -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add videocreator/mcp_runtime.py videocreator/cli.py tests/test_mcp_runtime.py tests/test_cli.py
git commit -m "feat: add MCP service lifecycle commands"
```

### Task 7: End-To-End Verification And Documentation

**Files:**
- Create: `tests/integration/test_mcp_workflow.py`
- Modify: `README.md`
- Modify: `config/README.md`

**Interfaces:**
- Verifies: public CLI, MCP tool contract, durable queue, worker, interaction resume, result retrieval, and deployment configuration

- [ ] **Step 1: Write the failing end-to-end test**

Start a temporary HTTP service with deterministic workflow stage fakes; register an MCP client; list templates; initialize a project; start a run; observe waiting input; submit the matching interaction; complete the run; and read artifact metadata.

- [ ] **Step 2: Run the integration test and verify the first incomplete boundary**

Run: `pytest tests/integration/test_mcp_workflow.py -v`
Expected: FAIL at the first missing integration behavior.

- [ ] **Step 3: Complete only the integration wiring required by the test**

Keep production-stage APIs replaced by fakes. Do not add agent-specific behavior.

- [ ] **Step 4: Document operation and deployment**

Document local registration with `codex mcp add`, background lifecycle commands, remote binding, bearer configuration, reverse-proxy TLS responsibility, tool names, and the asynchronous interaction loop.

- [ ] **Step 5: Run full verification**

Run: `pytest`
Expected: all Python tests pass.

Run: `npm --prefix renderer test`
Expected: all renderer tests pass.

Run: `git diff --check`
Expected: no output and exit code 0.

- [ ] **Step 6: Perform a local smoke test**

Run: `vc mcp serve` with a temporary runtime configuration, connect an MCP client to `/mcp`, list tools, then stop the service.
Expected: ten tools are returned and both server and worker exit cleanly.

- [ ] **Step 7: Commit**

```powershell
git add README.md config/README.md tests/integration/test_mcp_workflow.py
git commit -m "docs: document MCP workflow service"
```

