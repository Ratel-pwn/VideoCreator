# Declarative Template And Project Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scenario-coupled skills and mixed project artifacts with validated declarative templates and reproducible per-run project layouts, then migrate the completed capital project without touching the grasshopper project.

**Architecture:** Focused Python modules own template validation, library resolution, run paths, snapshots, visual-plan auditing, and migration. `main.py` remains the orchestration entry point but consumes these modules instead of hardcoded scenario skill paths. Templates provide only JSON/Markdown/resources; Remotion and all executable behavior remain in core code.

**Tech Stack:** Python 3.12, pytest, JSON/Markdown declarative templates, Remotion/React/TypeScript, FFmpeg/ffprobe.

## Global Constraints

- Templates contain no executable Python, JavaScript, TypeScript, Remotion, or shell code.
- Required templates are `chaos-museum`, `product-intro`, `science-explainer`, and `ai-daily`.
- Projects without a valid `template_id` cannot generate or resume.
- Library precedence is complete override per resource type: project, template, global; empty directories do not override.
- New generated artifacts live under `projects/<project>/runs/<run-id>/`; reusable source media live under project `media/`.
- Migrate only `资本主义潘多拉魔盒`; do not modify `蚱蜢：游戏、生命与乌托邦`.
- Use TDD for Python behavior and preserve current Remotion contracts.

---

## File Structure

- `videocreator/templates.py`: template model, discovery, path validation, snapshots, and library resolution.
- `videocreator/project_layout.py`: project initialization, canonical run paths, artifact manifest helpers, and input snapshots.
- `videocreator/visual_plan.py`: schema-v2 normalization and deterministic pacing audit.
- `videocreator/project_migration.py`: explicit legacy-project migration with hashes and cleanup gating.
- `scripts/migrate_project_layout.py`: migration CLI wrapper.
- `templates/*`: four complete declarative scenario definitions.
- `main.py`: CLI integration and orchestration path migration.
- `workflow.config.json`: shared engines and roots only; no scenario skill paths.
- `tests/test_templates.py`: template validation and library precedence.
- `tests/test_project_layout.py`: project initialization and run-path contract.
- `tests/test_visual_plan.py`: pacing and schema audit behavior.
- `tests/test_project_migration.py`: isolated migration and sibling-project protection.
- `tests/test_main_templates.py`: CLI/template selection and fail-fast behavior.
- `README.md`, `AGENTS.md`: public workflow and source-of-truth documentation.

### Task 1: Template Registry And Library Resolution

**Files:**
- Create: `tests/test_templates.py`
- Create: `videocreator/templates.py`
- Create: `templates/chaos-museum/*`
- Create: `templates/product-intro/*`
- Create: `templates/science-explainer/*`
- Create: `templates/ai-daily/*`

**Interfaces:**
- Produces: `TemplateDefinition`, `discover_templates(templates_root)`, `load_template(templates_root, template_id)`, `resolve_library(repo_root, project_root, template, resource_type)`, and `snapshot_template(template)`.
- Consumes: repository paths and declarative JSON/Markdown files only.

- [ ] **Step 1: Write failing tests** for four-template discovery, duplicate/unknown IDs, path traversal, executable files, missing declarations, complete override precedence, empty-directory fallback, and stable SHA-256 snapshots.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_templates.py -q`; expect import failure for `videocreator.templates`.
- [ ] **Step 3: Implement the registry** with immutable dataclasses, safe resolved-path containment checks, allowed capability names, required-file checks, executable-extension rejection, and sorted deterministic snapshots.
- [ ] **Step 4: Add all four templates** with complete preparation, writing, visual planning, pacing, subtitle, and composition declarations. Chaos Museum must encode single-line punctuation-free captions, hard cuts, mixed public image/video, muted video, entity cards, explainers, attribution, and the approved frame branding.
- [ ] **Step 5: Verify GREEN** with `python -m pytest tests/test_templates.py -q` and inspect that all four repository templates load.

### Task 2: Canonical Project And Run Layout

**Files:**
- Create: `tests/test_project_layout.py`
- Create: `videocreator/project_layout.py`
- Modify: `videocreator/models.py`
- Modify: `videocreator/workflow_state.py`
- Delete: `projects/sample-project/**`

**Interfaces:**
- Produces: `RunPaths`, `initialize_project(projects_root, name, template)`, `create_run(project_root, run_id, template, libraries)`, and `write_run_manifest(...)`.
- Consumes: validated `TemplateDefinition` and resolved library selections from Task 1.

- [ ] **Step 1: Write failing tests** asserting the exact annotated design layout, refusal to overwrite, project schema v2 with `template_id`, all run subdirectories, project-relative manifest paths, and four input snapshots.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_project_layout.py -q`; expect import failure.
- [ ] **Step 3: Implement project initialization and run creation** with path containment, deterministic JSON output, and no ambiguous filename prefixes.
- [ ] **Step 4: Adapt state/manifest models** so stage state stays at run root and every artifact is indexed by canonical relative path.
- [ ] **Step 5: Remove the tracked sample project** after tests demonstrate `initialize_project` replaces it.
- [ ] **Step 6: Verify GREEN** with `python -m pytest tests/test_project_layout.py tests/test_workflow_state.py -q`.

### Task 3: Template-Aware CLI And Orchestration

**Files:**
- Create: `tests/test_main_templates.py`
- Modify: `main.py`
- Modify: `workflow.config.json`
- Modify: `tests/test_main_config.py`
- Modify: `tests/test_main_stage_dispatch.py`

**Interfaces:**
- Produces CLI commands `templates`, `project init`, `chat --project`, `import-chat ... --project`, and compatible `resume`.
- Consumes template registry and canonical run paths from Tasks 1-2.

- [ ] **Step 1: Write failing CLI tests** for listing templates, initializing a named project, rejecting missing/unknown templates before API calls, and resolving preparation/writing/visual instructions from the selected template.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_main_templates.py -q`; expect parser/dispatch failures.
- [ ] **Step 3: Remove scenario paths from workflow config** and retain only shared engine, credential, projects, templates, library, and renderer configuration.
- [ ] **Step 4: Refactor `WorkflowContext`** to carry `TemplateDefinition`, `RunPaths`, and library selections. Replace `artifact_path(group, prefixed_name)` with named canonical run paths.
- [ ] **Step 5: Adapt stages** so session, writing, audio, subtitles, visual, asset, and render outputs land in their documented run directories; update cleaning/alignment calls where audio and subtitle destinations differ.
- [ ] **Step 6: Implement CLI commands** and ensure validation occurs before creating run state or loading credentials.
- [ ] **Step 7: Verify GREEN** with `python -m pytest tests/test_main_templates.py tests/test_main_config.py tests/test_main_stage_dispatch.py -q`.

### Task 4: Visual Plan V2 And Density Audit

**Files:**
- Create: `tests/test_visual_plan.py`
- Create: `videocreator/visual_plan.py`
- Modify: `scripts/generate_visual_plan.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `audit_visual_plan(plan, pacing, subtitle_policy) -> dict` and schema-v2 normalized plans.
- Consumes final render SRT plus selected template visual instructions and JSON policy.

- [ ] **Step 1: Write failing tests** for continuous timing, target/soft/hard duration, long-hold reason, subtitle-block count, Chinese-character count, shots per minute, entity fields, explainer fields, presentation modes, and sentence-final punctuation.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_visual_plan.py -q`; expect import failure.
- [ ] **Step 3: Implement deterministic audit** returning stable `errors`, `warnings`, metrics, and `ok`; hard-limit/schema/attribution policy errors must block later stages.
- [ ] **Step 4: Upgrade planner normalization** to preserve v2 `presentation_mode`, media slots, entity names, explainer declarations, source/attribution, and long-hold reasons instead of flattening to the v1 image contract.
- [ ] **Step 5: Integrate audit output** at `visual/visual-plan-audit.json` and gate asset collection.
- [ ] **Step 6: Verify GREEN** with `python -m pytest tests/test_visual_plan.py tests/test_asset_manifest.py tests/test_render_contract.py -q`.

### Task 5: Remove Scenario Coupling And Verify Templates

**Files:**
- Delete: `skills/prepare-topic-chat/**`
- Delete: `skills/article-from-chat/**`
- Delete: `skills/segment-visual-planner/**`
- Delete: `skills/video-scenarios/**`
- Delete: `skills/video-writing/**`
- Delete: `skills/video-visual/**`
- Modify: `skills/workflow-controller/SKILL.md`
- Modify: `skills/workflow-orchestrator/SKILL.md`

**Interfaces:**
- Consumes: the four template declarations.
- Produces: a core skill tree containing only shared capabilities and orchestration.

- [ ] **Step 1: Add a repository assertion** to `tests/test_templates.py` that fails while scenario-specific paths remain and fails if core files reference them.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_templates.py -q`; expect stale-path failures.
- [ ] **Step 3: Update shared workflow skills** to describe template selection and canonical run artifacts without scenario strategy.
- [ ] **Step 4: Delete migrated scenario directories** and search with `rg -n "prepare-topic-chat|article-from-chat|segment-visual-planner|scenario-(ai|product|science)|script-(ai|product|science)|scene-planner-" .`.
- [ ] **Step 5: Verify GREEN** with `python -m pytest tests/test_templates.py -q`; expected zero failures and no stale executable references.

### Task 6: Safe Legacy Migration And Capital Project Conversion

**Files:**
- Create: `tests/test_project_migration.py`
- Create: `videocreator/project_migration.py`
- Create: `scripts/migrate_project_layout.py`
- Modify locally: `projects/资本主义潘多拉魔盒/**` (ignored production artifacts)
- Must not modify: `projects/蚱蜢：游戏、生命与乌托邦/**`

**Interfaces:**
- Produces: `plan_legacy_migration(project_root, template_id)`, `execute_legacy_migration(plan)`, and a JSON report with source/destination hashes.
- Consumes explicit project path only; never discovers sibling projects.

- [ ] **Step 1: Write failing fixture-based tests** covering two historical runs, media relocation, manifest path rewriting, per-run script/audio/subtitle copies, cleanup only after matching hashes, idempotent rerun, and unchanged sibling sentinel hashes.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_project_migration.py -q`; expect import failure.
- [ ] **Step 3: Implement two-phase migration**: build/validate a complete mapping, copy and hash destinations, atomically write rewritten JSON, then remove only explicitly mapped legacy files after validation.
- [ ] **Step 4: Dry-run the real project** and capture the planned mapping. Hash the entire grasshopper directory before migration.
- [ ] **Step 5: Execute the real migration** for template `chaos-museum`, then verify both historical run manifests, referenced media, final videos, and migration report.
- [ ] **Step 6: Hash the grasshopper directory again** and require exact equality.
- [ ] **Step 7: Verify GREEN** with `python -m pytest tests/test_project_migration.py tests/test_project_import.py -q` plus `ffprobe`/decode checks for both migrated final videos.

### Task 7: Documentation, Regression Verification, Commit, And Push

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `.gitignore` if migration-report/local-run rules require clarification

**Interfaces:**
- Produces: current public documentation and a pushed feature branch.
- Consumes: all implemented commands and verified layouts.

- [ ] **Step 1: Update README and AGENTS** with template commands, template ownership, exact run layout, complete library override, local config rules, final assembly status, and migration behavior. Every displayed directory item must include an explanatory comment.
- [ ] **Step 2: Run stale-reference checks** for the old project layout, removed scenario skills, and the obsolete “final assembly not implemented” statement.
- [ ] **Step 3: Run full Python verification** with `python -m pytest -q` and record exact pass counts.
- [ ] **Step 4: Run renderer verification** with `npm test`, `npm run typecheck`, and the applicable Remotion integration/render-contract tests from `renderer/` and repository root.
- [ ] **Step 5: Review `git diff --check`, `git status --short`, and the complete diff**; confirm no local config, credential, generated media, or grasshopper artifact is staged.
- [ ] **Step 6: Commit** using the repository convention, then push `feat/remotion-final-assembly` to `origin`.

## Self-Review

- Spec coverage: template declaration, core boundaries, four templates, project/run layout, complete library override, visual density, migration isolation, documentation, verification, and push each map to a task.
- Placeholder scan: no deferred implementation markers or unspecified error-handling steps remain.
- Type consistency: Tasks 2-4 consume the `TemplateDefinition` and library selections defined in Task 1; migration produces the same canonical paths defined by `RunPaths` in Task 2.
- Scope control: the grasshopper project is explicitly protected; no unrelated source repository is modified.
