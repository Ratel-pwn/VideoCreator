# Global `vc` CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a Git-style `vc` command that provides concise project, workflow, run, and status operations from any directory while preserving `python main.py` compatibility.

**Architecture:** A focused `videocreator.cli` module owns argument parsing, repository resolution, prompting, run lookup, formatting, and dispatch. Existing workflow functions in `main.py` remain the execution backend during this iteration; `main.py` delegates to the new parser instead of retaining a second command tree.

**Tech Stack:** Python 3.12, argparse, `pyproject.toml` console scripts, pytest, editable pip installation.

## Global Constraints

- Console command name is exactly `vc`.
- Home precedence is `--home`, `VIDEO_CREATOR_HOME`, installed repository root.
- Config paths are relative to resolved home.
- `vc init` supports both prompts and `--non-interactive`.
- `vc resume PROJECT` selects the newest valid unfinished run deterministically.
- Existing `python main.py` forms remain accepted.
- No workflow stage implementation is duplicated in the CLI.

---

### Task 1: CLI Resolution And Read-Only Commands

**Files:**
- Create: `videocreator/cli.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `resolve_home(explicit, environ, package_root)`, `build_parser(compatibility=False)`, `run_cli(argv=None)`, and console entry `main()`.
- Consumes: `videocreator.templates.discover_templates` and repository `workflow.config.json`.

- [ ] Write failing tests for home precedence, invalid-home errors, stable text/JSON template listing, and console-script metadata.
- [ ] Run `python -m pytest tests/test_cli.py -q` and verify failures are due to the missing module/entry point.
- [ ] Implement minimal resolver, parser, structured user errors, and `templates` command.
- [ ] Add `[project.scripts] vc = "videocreator.cli:main"`.
- [ ] Run the focused tests and require all pass.

### Task 2: Initialization And Workflow Dispatch

**Files:**
- Modify: `videocreator/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `main.py`

**Interfaces:**
- Produces handlers for `init`, `chat`, and `import-chat` with positional project/topic/file arguments.
- Consumes existing `initialize_project`, `make_run_context`, `import_chat`, and `execute_from_current_stage` functions.

- [ ] Add failing tests for non-interactive init, prompt-based template selection, refusal to overwrite, missing import file before run creation, and positional dispatch arguments.
- [ ] Run focused tests and verify the intended failures.
- [ ] Implement prompt injection through `input_fn`, validation-before-write, and lazy imports of workflow execution functions to avoid circular imports.
- [ ] Change `main.py` to delegate through compatibility parsing while preserving existing command forms.
- [ ] Run focused CLI and existing main-stage tests.

### Task 3: Run Discovery, Resume, Status, And Runs

**Files:**
- Modify: `videocreator/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `RunSummary`, `list_runs(project_root)`, `select_run(project_root, run_id=None, unfinished_only=False)`, and handlers for `resume`, `status`, and `runs`.
- Consumes run `state.json`, `manifest.json`, and existing `resume_context`/workflow execution.

- [ ] Add failing tests for newest unfinished selection, completed-run exclusion, explicit selection, deterministic ties, corrupt state, empty projects, text output, and JSON output.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement strict state loading, parsed ISO timestamps with mtime fallback, status/final-video extraction, and read-only formatting.
- [ ] Dispatch resume only after a valid run is selected.
- [ ] Run focused and workflow-state tests.

### Task 4: Documentation And Compatibility

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `tests/test_main_templates.py`

**Interfaces:**
- Produces current installation and usage documentation.
- Consumes the final CLI syntax from Tasks 1-3.

- [ ] Add compatibility tests for current `python main.py` commands and help output.
- [ ] Update README to lead with editable installation and short `vc` examples.
- [ ] Document global home/config behavior and retain comments on every directory-tree item.
- [ ] Run stale-command searches and focused compatibility tests.

### Task 5: Installation And Full Verification

**Files:**
- Modify only if verification reveals a tested defect.

**Interfaces:**
- Produces an installed, globally callable `vc` command and verified commit.

- [ ] Run `python -m pytest -q`.
- [ ] Run renderer `npm test` and `npm run typecheck`.
- [ ] Run `python -m pip install -e E:\Projects\AIGC\VideoCreator`.
- [ ] From `C:\tmp`, run `vc --help`, `vc templates`, and `vc --json templates`.
- [ ] Run `git diff --check`, credential scan, and confirm generated projects/local configs remain untracked.
- [ ] Commit with `feat: add global vc command` and push `main`.

## Self-Review

- Spec coverage: all six commands, global options, prompting, run selection, compatibility, installation, errors, and external-directory verification map to tasks.
- Placeholder scan: no deferred implementation markers remain.
- Type consistency: Tasks 2-3 consume the resolver/parser introduced in Task 1; all workflow execution remains behind existing `main.py` functions.
- Scope: no new workflow stages, GUI, plugin system, or unrelated refactor is included.
