# VideoCreator MCP Service Design

## 1. Objective

Expose VideoCreator as a standards-based MCP server so Codex and other MCP-capable agents can operate the existing high-level video workflow. The MCP layer must reuse the current project, template, run, state, and artifact contracts instead of creating a second orchestration system.

The service supports both local and remotely deployed Streamable HTTP endpoints. Local deployment remains the simplest default, but the implementation must not require loopback networking or a specific agent vendor.

## 2. Scope

The first release provides:

- high-level project and workflow tools
- asynchronous execution through a durable SQLite queue
- a worker process independent of individual MCP requests
- durable pause and resume for user input and approvals
- local or remote Streamable HTTP deployment
- optional bearer-token authentication
- service lifecycle commands under `vc mcp`
- shared workflow behavior between the existing CLI and MCP service

The first release does not provide:

- agent-vendor-specific adapters
- a ChatGPT-specific tunnel or deployment workflow
- stdio MCP transport
- binary audio or video transfer through MCP
- arbitrary shell, Python, or filesystem execution
- Redis, distributed workers, or multi-host queue coordination
- an interactive MCP UI

## 3. Architecture

```text
Codex / another MCP-capable agent
                |
                | Streamable HTTP
                v
       VideoCreator MCP Server
                |
                +-- tool validation and authorization
                +-- high-level application services
                +-- SQLite durable queue
                |
                v
       independent worker process
                |
                v
        shared WorkflowEngine
                |
                v
 projects/<project>/runs/<run-id>/
```

The main components are:

### 3.1 MCP Server

The server uses the official Python MCP SDK and FastMCP with Streamable HTTP. It validates tool inputs, applies deployment authentication settings, calls high-level application services, and returns structured results. It never performs a long-running production stage inside an MCP request.

### 3.2 Workflow Application Service

This layer owns project initialization, run creation, status projection, interaction submission, resume, cancellation, and result lookup. Both MCP tools and CLI commands call this layer. It is the boundary that prevents transport-specific behavior from leaking into the workflow engine.

### 3.3 Durable Queue

SQLite stores queue jobs, leases, cancellation requests, interaction wakeups, and an operational event index. SQLite does not become a duplicate source of truth for the video workflow.

### 3.4 Worker

A single worker claims jobs through a renewable lease and advances one run at a time. Serial execution is the default because Remotion, Whisper, media downloads, and rendering can contend for substantial local resources.

### 3.5 Workflow Engine

The current orchestration logic is extracted behind `WorkflowEngine.advance()`. It advances a run until it reaches a durable boundary: waiting for input, completed, failed, or cancelled.

## 4. Source Of Truth

`projects/<project>/runs/<run-id>/state.json` remains the authoritative workflow state. It records the current stage, status, timestamps, error summary, and the one active pending interaction when present.

`projects/<project>/runs/<run-id>/manifest.json` remains the authoritative artifact index.

SQLite is authoritative only for operational scheduling concerns:

- whether a job is queued, leased, or finished
- which worker owns a lease and when it expires
- whether cancellation has been requested
- whether an interaction reply should wake a run
- service-level event and diagnostic identifiers

If queue state and run state differ after a crash, reconciliation starts from the run state and reconstructs only the required scheduling record.

## 5. MCP Tool Contract

The server exposes ten high-level tools.

### 5.1 `list_templates`

Returns available template IDs, display names, versions, and concise descriptions. It has no side effects.

### 5.2 `list_projects`

Returns initialized projects with template ID, title, publication date, latest run summary, and project status. It has no side effects.

### 5.3 `initialize_project`

Creates a project through the existing project-layout contract.

Required inputs:

- `name`
- `template_id`

Optional inputs:

- `title`
- `publication_date`

The tool accepts no arbitrary destination path. Project location is resolved from configured VideoCreator roots.

### 5.4 `start_workflow`

Creates a run, enqueues it, and returns immediately.

Required inputs:

- `project`
- `topic`

Optional inputs:

- `context`: background material summarized by the calling agent
- `run_id`: caller-selected ID; generated when omitted

The result includes `project`, `run_id`, `status=queued`, and timestamps. An internal queue job ID may be included as diagnostic metadata but is not a user-facing workflow identity.

### 5.5 `list_workflows`

Lists runs for a project, optionally filtered by workflow status. It reports the existing project and run identities rather than queue implementation details.

### 5.6 `get_workflow_status`

Returns:

- project and run identity
- normalized workflow status
- current stage
- progress summary
- active pending interaction, if any
- latest recoverable error summary, if any
- update timestamps
- available artifact categories

### 5.7 `submit_workflow_input`

Submits a reply to the active interaction and requeues the run.

Required inputs:

- `project`
- `run_id`
- `interaction_id`
- `response`

The interaction ID must match the active interaction. Repeating an already accepted submission returns the original acceptance result without advancing the workflow twice. A stale or unrelated interaction ID returns a structured state-conflict error.

### 5.8 `resume_workflow`

Requeues a failed or interrupted run from its durable current stage. It rejects completed, actively running, or input-waiting runs.

### 5.9 `cancel_workflow`

Requests cancellation for a queued or running run. Queued jobs can be cancelled immediately. Running jobs stop at the next safe stage boundary so media files are not deliberately left half-written.

### 5.10 `get_workflow_result`

Returns the final status and artifact index. By default it returns artifact type, local or deployment-visible path, size, and state. The caller may request text content for approved scripts, subtitles, visual plans, and reports. Audio and video are represented by metadata and path or URL only; the MCP result never embeds their binary payload.

## 6. Workflow Status Model

MCP projects existing internal states into these stable external values:

```text
queued
running
waiting_for_input
completed
failed
cancelled
```

The current workflow stage remains a separate field. This preserves detailed progress without making MCP clients depend on every internal status string.

When status is `waiting_for_input`, the result contains one interaction object:

```json
{
  "id": "confirm-draft-1",
  "kind": "confirmation",
  "prompt": "The draft is ready. Continue to voice generation?",
  "choices": ["approve", "revise"],
  "created_at": "2026-07-22T12:00:00+08:00"
}
```

Supported interaction kinds in the first release are free-text input and explicit confirmation. The MCP server describes the interaction; the calling agent is responsible for presenting it naturally to the user.

## 7. Durable Interaction Model

The current direct `input()` calls are replaced by an interaction port:

```text
WorkflowEngine
+-- ConsoleInteractionPort  # existing vc chat and vc resume behavior
+-- DurableInteractionPort  # MCP worker pause and resume behavior
```

The durable port writes interaction events to:

```text
runs/<run-id>/session/interactions.jsonl  # append-only questions and replies
runs/<run-id>/state.json                  # current pending interaction
```

`WorkflowEngine.advance()` stops when the durable port creates a pending interaction. The worker releases the job and does not occupy its execution slot while waiting. A valid reply clears the pending interaction and enqueues a new advance job.

## 8. Queue And Recovery Semantics

The SQLite queue records job action, project, run ID, queue status, lease owner, lease expiry, attempt count, cancellation request, timestamps, and the latest service event ID.

Required behavior:

- one active execution job per run, enforced by a database constraint
- transactional job claiming
- renewable worker leases
- expired lease recovery after worker failure
- idempotent interaction wakeups
- bounded retry bookkeeping without silently skipping a failed workflow stage
- reconciliation on service startup

The worker checks run state and existing artifacts before executing a stage. Queue delivery may be repeated, but committing a workflow stage or interaction reply must be idempotent.

CLI resume and MCP execution use the same run lock. `vc resume` rejects a run currently leased by the worker and reports that the MCP worker owns it.

## 9. Process Lifecycle

The CLI adds:

```text
vc mcp start   # start the supervisor in the background
vc mcp stop    # request a graceful service shutdown
vc mcp status  # report endpoint, process, worker, and queue health
vc mcp logs    # display recent service logs
vc mcp serve   # run the supervisor in the foreground
```

The supervisor owns the HTTP server process and an independent worker child process. Disconnecting an MCP client does not affect the worker. A repeated `vc mcp start` detects the existing service instead of creating a duplicate.

On graceful stop, the worker stops claiming jobs and finishes or pauses the current stage within the configured grace policy. Restarting the service reconciles queue records against durable run states.

Runtime files use this ignored layout:

```text
.runtime/          # deployment-local runtime state, never committed
+-- mcp.sqlite3    # queue and lease database
+-- service.json   # endpoint, process IDs, and start time
+-- logs/          # server, supervisor, and worker logs
```

## 10. Deployment Configuration

The service supports arbitrary Streamable HTTP deployment. The committed default remains local-first:

```json
{
  "mcp": {
    "host": "127.0.0.1",
    "port": 8765,
    "path": "/mcp",
    "public_base_url": null,
    "runtime_dir": ".runtime",
    "worker_count": 1,
    "lease_seconds": 60,
    "shutdown_grace_seconds": 30,
    "allowed_hosts": ["127.0.0.1", "localhost"],
    "auth": {
      "mode": "none",
      "bearer_token_env": "VIDEO_CREATOR_MCP_TOKEN"
    }
  }
}
```

Deployment rules:

- `host`, `port`, `path`, `public_base_url`, and `allowed_hosts` are configurable.
- The application does not force loopback binding and permits container, LAN, private-network, or public server deployment.
- `auth.mode` supports `none` and `bearer` in the first release.
- Bearer secrets are read only from the configured environment variable and are never written to committed configuration or logs.
- Remote deployments should use bearer authentication and TLS termination through a trusted reverse proxy.
- The service emits a prominent warning, but does not refuse startup, when a non-loopback listener uses `auth.mode=none`.
- Artifact paths returned by remote deployments use `public_base_url` mappings when configured; otherwise they are explicitly labeled as server-local paths.
- Multi-host workers and shared network filesystems remain outside the first release even when the HTTP endpoint is remotely accessible.

Example agent registration for a local deployment:

```powershell
vc mcp start
codex mcp add videocreator --url http://127.0.0.1:8765/mcp
```

For an authenticated endpoint, the client supplies the configured bearer token according to its MCP client settings.

## 11. Security Boundaries

The server accepts semantic identifiers and structured workflow input, not arbitrary executable input.

It must:

- resolve projects only beneath the configured projects root
- resolve templates only beneath the configured templates root
- reject path traversal and absolute-path injection
- avoid returning API keys, tokens, local configuration contents, or stack traces
- annotate read-only, mutating, and destructive MCP tools accurately
- keep cancellation explicit and bounded by workflow-safe checkpoints
- limit text artifact reads by type and configured maximum size
- log event IDs and sanitized error summaries
- avoid following arbitrary artifact symlinks outside approved roots

Remote operators remain responsible for network exposure, TLS, firewall policy, and credential distribution.

## 12. Error Contract

Errors are structured into:

- `invalid_argument`: malformed or unsupported tool input
- `not_found`: unknown project, template, run, or artifact
- `state_conflict`: operation is invalid for the current workflow state
- `authentication_required`: missing or invalid bearer token
- `workflow_failed`: recoverable production-stage failure
- `service_unavailable`: queue, worker, or runtime service failure
- `internal_error`: sanitized unexpected service error with an event ID

Invalid requests do not enter the queue. Recoverable stage failures remain in run state and may be resumed. Internal errors never expose stack traces or sensitive configuration through MCP.

## 13. Compatibility And Migration

Existing commands remain available:

```text
vc templates
vc init
vc chat
vc import-chat
vc resume
vc status
vc runs
```

The refactor replaces direct terminal input with `ConsoleInteractionPort` but preserves interactive CLI behavior. Existing projects and runs require no layout migration beyond adding optional interaction fields to state when a new interaction occurs.

## 14. Testing Strategy

### 14.1 Unit Tests

- MCP input and output schema validation
- project and template path containment
- normalized status projection
- interaction creation, submission, staleness, and idempotency
- queue claiming, uniqueness, lease renewal, and lease expiry
- cancellation state transitions
- bearer-token validation and secret redaction
- remote path or URL projection

### 14.2 Workflow Tests

- console interaction behavior remains compatible
- durable interaction pauses without blocking a worker
- valid input resumes from the same stage
- duplicate queue delivery does not duplicate stage artifacts
- CLI resume rejects an actively leased run

### 14.3 MCP Integration Tests

Use the official MCP Python client against a real test Streamable HTTP server to verify tool discovery, structured calls, error responses, and lifecycle behavior. External LLM, TTS, asset, Whisper, and Remotion calls are replaced with deterministic fakes.

### 14.4 Lifecycle Tests

- foreground service startup and shutdown
- background start, duplicate-start detection, status, logs, and graceful stop
- worker crash followed by lease recovery
- supervisor restart followed by queue reconciliation
- configurable loopback and non-loopback startup
- authenticated and unauthenticated deployment modes

## 15. Acceptance Criteria

The design is complete when implementation demonstrates all of the following:

1. `vc mcp start` starts a healthy Streamable HTTP endpoint and worker.
2. Codex can register the endpoint and discover all ten tools.
3. An agent can list templates, initialize a project, and start a run.
4. Starting a run returns immediately with its project and run ID.
5. Client disconnection does not stop an active run.
6. A waiting interaction consumes no worker execution slot.
7. Stale or duplicate replies cannot advance a run twice.
8. A worker restart recovers eligible unfinished jobs from durable state.
9. An agent can inspect failure, resume the run, and retrieve final artifact metadata.
10. Existing `vc` commands continue to work against the same workflow contracts.
11. The server can bind to a configured local or remote interface without code changes.
12. Optional bearer authentication works without persisting its secret.
13. Automated tests do not call real external production services.

