# Remotion Final Video Assembly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, web-curated asset workflow and a Remotion renderer that produces the final 1920x1080 H.264 video for `资本主义潘多拉魔盒` with 25fps, burned-in subtitles, restrained still-image motion, and hard cuts.

**Architecture:** Python owns project state, legacy import, asset provenance validation, timing cleanup, render-contract generation, and invocation. A separate `renderer/` TypeScript project validates props and uses Remotion 4.0.494 to compose local images, local videos, narration, and parsed SRT captions. Generation adapters remain present but disabled and unused by the target project.

**Tech Stack:** Python 3.12, pytest 8, FFmpeg/ffprobe 8, Node.js 25, TypeScript 5, React 19, Remotion 4.0.494, Vitest 3, Zod 3.

## Global Constraints

- Preserve `E:\Projects\AIGC\ChaosMuseum` and `E:\Projects\Experiment\remotion-demo`; reference them read-only.
- Preserve original project audio, SRT, draft, and visual plan; write corrected derivatives with new filenames.
- Default render is exactly 1920x1080, 25fps, H.264 with one narration audio track.
- Scene boundaries use hard cuts only; do not add transition effects.
- Still images use restrained motion from approximately scale 1.00 to 1.05.
- Source video audio is always muted.
- Every non-`subtitle_only` scene requires a locally downloaded, approved web asset with provenance.
- Generation APIs are disabled and unused for the target project.
- Write tests before production code and verify each test fails for the expected missing behavior.

---

## File Map

- `videocreator/models.py`: immutable Python artifact and manifest records.
- `videocreator/asset_manifest.py`: asset request creation and manifest audit.
- `videocreator/project_import.py`: legacy artifact discovery and run creation.
- `videocreator/workflow_state.py`: declared stages and handler coverage.
- `videocreator/media.py`: ffprobe metadata and trailing-silence parsing/cleanup.
- `videocreator/render_contract.py`: contiguous frame timeline and Remotion input JSON.
- `scripts/create_asset_request.py`: CLI for producing AI web-research requests.
- `scripts/audit_asset_manifest.py`: CLI audit gate for sourced media.
- `scripts/import_legacy_project.py`: CLI that creates resumable state without moving source files.
- `scripts/render_video.py`: Python render gate and Node renderer invocation.
- `renderer/src/schema.ts`: Zod render-input schema.
- `renderer/src/timeline.ts`: deterministic frame helpers.
- `renderer/src/components/*`: scene and subtitle rendering.
- `renderer/scripts/render.mjs`: bundle, select composition, and render media.
- `main.py`: thin stage dispatch integration.

---

### Task 1: Python Test Foundation And Asset Manifest Contract

**Files:**
- Create: `pyproject.toml`
- Create: `videocreator/__init__.py`
- Create: `videocreator/models.py`
- Create: `videocreator/asset_manifest.py`
- Create: `tests/test_asset_manifest.py`

**Interfaces:**
- Consumes: visual-plan dictionaries and a project root path.
- Produces: `AssetRecord.from_dict()`, `AssetAuditResult`, and `audit_asset_manifest(project_root, visual_plan, manifest)`.

- [ ] **Step 1: Add the Python test configuration**

Create `pyproject.toml`:

```toml
[project]
name = "video-creator"
version = "0.1.0"
requires-python = ">=3.12"

[project.optional-dependencies]
dev = ["pytest>=8,<9"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
markers = ["integration: invokes FFmpeg and Remotion to render media"]
```

Create `videocreator/__init__.py` as an empty package marker.

- [ ] **Step 2: Write failing asset audit tests**

Create `tests/test_asset_manifest.py`:

```python
from pathlib import Path

from videocreator.asset_manifest import audit_asset_manifest


def plan():
    return {
        "segments": [
            {"segment_id": "scene-001", "material_type": "image"},
            {"segment_id": "scene-002", "material_type": "subtitle_only"},
        ]
    }


def test_audit_accepts_complete_approved_manifest(tmp_path: Path):
    asset = tmp_path / "assets" / "scene-001.jpg"
    asset.parent.mkdir()
    asset.write_bytes(b"jpeg-fixture")
    manifest = {
        "segments": [{
            "scene_id": "scene-001",
            "asset_type": "image",
            "local_path": "assets/scene-001.jpg",
            "source_page_url": "https://example.org/page",
            "direct_download_url": "https://example.org/image.jpg",
            "provider": "Example Archive",
            "license": "Public domain",
            "credit": "Example Archive",
            "retrieved_at": "2026-07-20T12:00:00+08:00",
            "fit_mode": "cover",
            "trim_start_ms": 0,
            "short_video_policy": "reject",
            "review_status": "approved",
        }]
    }

    result = audit_asset_manifest(tmp_path, plan(), manifest, probe_media=False)

    assert result.errors == []
    assert result.approved_scene_ids == ["scene-001"]


def test_audit_rejects_missing_provenance_and_path_escape(tmp_path: Path):
    manifest = {"segments": [{
        "scene_id": "scene-001",
        "asset_type": "image",
        "local_path": "../outside.jpg",
        "source_page_url": "",
        "provider": "",
        "license": "",
        "retrieved_at": "",
        "review_status": "approved",
    }]}

    result = audit_asset_manifest(tmp_path, plan(), manifest, probe_media=False)

    assert "scene-001: local_path escapes project root" in result.errors
    assert "scene-001: missing source_page_url" in result.errors
    assert "scene-001: missing license" in result.errors
```

- [ ] **Step 3: Run tests and verify the red state**

Run: `python -m pytest tests/test_asset_manifest.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'videocreator.asset_manifest'`.

- [ ] **Step 4: Implement immutable records and audit behavior**

Create `videocreator/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AssetRecord:
    scene_id: str
    asset_type: str
    local_path: str
    source_page_url: str
    direct_download_url: str
    provider: str
    license: str
    credit: str
    retrieved_at: str
    duration_ms: int | None
    fit_mode: str
    trim_start_ms: int
    short_video_policy: str
    review_status: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AssetRecord":
        return cls(
            scene_id=str(value.get("scene_id", "")),
            asset_type=str(value.get("asset_type", "")),
            local_path=str(value.get("local_path", "")),
            source_page_url=str(value.get("source_page_url", "")),
            direct_download_url=str(value.get("direct_download_url", "")),
            provider=str(value.get("provider", "")),
            license=str(value.get("license", "")),
            credit=str(value.get("credit", "")),
            retrieved_at=str(value.get("retrieved_at", "")),
            duration_ms=value.get("duration_ms"),
            fit_mode=str(value.get("fit_mode", "cover")),
            trim_start_ms=int(value.get("trim_start_ms", 0)),
            short_video_policy=str(value.get("short_video_policy", "reject")),
            review_status=str(value.get("review_status", "pending")),
        )


@dataclass
class AssetAuditResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approved_scene_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors
```

Create `videocreator/asset_manifest.py` with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AssetAuditResult, AssetRecord


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def audit_asset_manifest(
    project_root: Path,
    visual_plan: dict[str, Any],
    manifest: dict[str, Any],
    *,
    probe_media: bool = True,
) -> AssetAuditResult:
    result = AssetAuditResult()
    required = {
        s["segment_id"]
        for s in visual_plan.get("segments", [])
        if s.get("material_type") != "subtitle_only"
    }
    records = [AssetRecord.from_dict(v) for v in manifest.get("segments", [])]
    by_scene: dict[str, list[AssetRecord]] = {}
    for record in records:
        by_scene.setdefault(record.scene_id, []).append(record)

    for scene_id in sorted(required):
        matches = by_scene.get(scene_id, [])
        if len(matches) != 1:
            result.errors.append(f"{scene_id}: expected exactly one asset record")
            continue
        record = matches[0]
        path = project_root / record.local_path
        if not _inside(project_root, path):
            result.errors.append(f"{scene_id}: local_path escapes project root")
        elif not path.is_file():
            result.errors.append(f"{scene_id}: asset file does not exist")
        for field_name in ("source_page_url", "provider", "license", "retrieved_at"):
            if not getattr(record, field_name):
                result.errors.append(f"{scene_id}: missing {field_name}")
        if record.review_status != "approved":
            result.errors.append(f"{scene_id}: review_status must be approved")
        if not any(error.startswith(f"{scene_id}:") for error in result.errors):
            result.approved_scene_ids.append(scene_id)
    return result
```

Leave `probe_media` unused until Task 2 adds ffprobe integration; accepting the flag now keeps the public signature stable.

- [ ] **Step 5: Run tests and verify green**

Run: `python -m pytest tests/test_asset_manifest.py -v`

Expected: `2 passed`.

- [ ] **Step 6: Commit the contract**

```powershell
git add pyproject.toml videocreator tests/test_asset_manifest.py
git commit -m "feat: add web asset manifest contract"
```

---

### Task 2: AI Web Asset Requests And Media Audit CLI

**Files:**
- Modify: `videocreator/asset_manifest.py`
- Create: `videocreator/media.py`
- Create: `scripts/create_asset_request.py`
- Create: `scripts/audit_asset_manifest.py`
- Modify: `tests/test_asset_manifest.py`
- Create: `tests/test_media.py`

**Interfaces:**
- Consumes: `visual-plan.json`, `asset-manifest.json`, local media files.
- Produces: `create_asset_requests(visual_plan)`, `probe_media(path)`, and two JSON CLIs.

- [ ] **Step 1: Write failing request and media-probe tests**

Append to `tests/test_asset_manifest.py`:

```python
from videocreator.asset_manifest import create_asset_requests


def test_generate_only_is_normalized_to_web_curated():
    visual_plan = {"topic": "demo", "segments": [{
        "segment_id": "scene-001",
        "start_ms": 0,
        "end_ms": 5000,
        "text": "Narration",
        "brief": "Historical city",
        "material_type": "image",
        "asset_strategy": "generate_only",
        "search_queries": {"image": ["historical city archive"]},
    }]}

    result = create_asset_requests(visual_plan)

    assert result["requests"][0]["strategy"] == "web_curated"
    assert result["requests"][0]["rejection_criteria"]
```

Create `tests/test_media.py`:

```python
from videocreator.media import parse_ffprobe_json


def test_parse_ffprobe_json_returns_video_metadata():
    payload = {
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "duration": "4.25"}],
        "format": {"duration": "4.25"},
    }
    metadata = parse_ffprobe_json(payload)
    assert metadata.kind == "video"
    assert metadata.width == 1920
    assert metadata.duration_ms == 4250
```

- [ ] **Step 2: Verify both tests fail for missing behavior**

Run: `python -m pytest tests/test_asset_manifest.py tests/test_media.py -v`

Expected: import failures for `create_asset_requests` and `videocreator.media`.

- [ ] **Step 3: Implement request generation and media probing**

Add to `videocreator/asset_manifest.py`:

```python
def create_asset_requests(visual_plan: dict[str, Any]) -> dict[str, Any]:
    requests = []
    for scene in visual_plan.get("segments", []):
        if scene.get("material_type") == "subtitle_only":
            continue
        preferred = scene.get("material_type", "image")
        requests.append({
            "scene_id": scene["segment_id"],
            "start_ms": scene["start_ms"],
            "end_ms": scene["end_ms"],
            "narration": scene.get("text", ""),
            "visual_brief": scene.get("brief", ""),
            "preferred_asset_type": preferred,
            "strategy": "web_curated",
            "search_queries": (scene.get("search_queries") or {}).get(preferred, []),
            "acceptance_criteria": [
                "Matches the spoken point of this scene",
                "Can fill a 16:9 frame without visible watermarks",
                "Source page and usage basis can be recorded",
            ],
            "rejection_criteria": [
                "Unrelated decorative imagery",
                "Visible loading state or watermark",
                "Missing source page or usage basis",
            ],
        })
    return {"topic": visual_plan.get("topic", ""), "request_count": len(requests), "requests": requests}
```

Create `videocreator/media.py`:

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaMetadata:
    kind: str
    codec: str
    width: int | None
    height: int | None
    duration_ms: int


def parse_ffprobe_json(payload: dict[str, Any]) -> MediaMetadata:
    streams = payload.get("streams", [])
    stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    kind = "video" if stream else "audio"
    source = stream or next((s for s in streams if s.get("codec_type") == "audio"), {})
    duration = source.get("duration") or (payload.get("format") or {}).get("duration") or 0
    return MediaMetadata(
        kind=kind,
        codec=str(source.get("codec_name", "")),
        width=source.get("width"),
        height=source.get("height"),
        duration_ms=round(float(duration) * 1000),
    )


def probe_media(path: Path) -> MediaMetadata:
    completed = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ], check=True, capture_output=True, text=True, encoding="utf-8")
    return parse_ffprobe_json(json.loads(completed.stdout))
```

Extend `audit_asset_manifest()` to call `probe_media(path)` when `probe_media=True`, reject video below 1280x720, reject type mismatches, and warn when an image long edge is below 1280 pixels. Alias the imported function as `_probe_media` to avoid shadowing the parameter.

- [ ] **Step 4: Add both CLIs**

`scripts/create_asset_request.py` must load UTF-8 JSON, call `create_asset_requests()`, and write UTF-8 JSON with `ensure_ascii=False, indent=2`.

`scripts/audit_asset_manifest.py` must accept `--project-root`, `--visual-plan`, `--manifest`, and `--output`; write `errors`, `warnings`, `approved_scene_ids`, and `ok`; exit `0` only when `ok` is true and `1` otherwise.

- [ ] **Step 5: Verify unit tests and CLI behavior**

Run: `python -m pytest tests/test_asset_manifest.py tests/test_media.py -v`

Expected: all tests pass.

Run the request CLI against the target visual plan and assert it reports `request_count: 17`:

```powershell
python scripts/create_asset_request.py --visual-plan "projects/资本主义潘多拉魔盒/drafts/visual-plan.json" --output "$env:TEMP/capitalism-asset-request.json"
```

- [ ] **Step 6: Commit the asset workflow**

```powershell
git add videocreator scripts/create_asset_request.py scripts/audit_asset_manifest.py tests
git commit -m "feat: add AI-curated web asset workflow"
```

---

### Task 3: Legacy Project Import And Complete Stage Dispatch

**Files:**
- Create: `videocreator/project_import.py`
- Create: `videocreator/workflow_state.py`
- Create: `scripts/import_legacy_project.py`
- Modify: `main.py:20-29`
- Modify: `main.py:722-744`
- Create: `tests/test_project_import.py`
- Create: `tests/test_workflow_state.py`

**Interfaces:**
- Consumes: a legacy project directory and current workflow handlers.
- Produces: `discover_legacy_artifacts()`, `import_legacy_project()`, `STAGES`, and `missing_stage_handlers()`.

- [ ] **Step 1: Write failing legacy import tests**

Create `tests/test_project_import.py`:

```python
from pathlib import Path

import pytest

from videocreator.project_import import discover_legacy_artifacts, import_legacy_project


def make_project(root: Path):
    for folder in ("audio", "drafts", "runs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "audio" / "voice.mp3").write_bytes(b"audio")
    (root / "audio" / "voice.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nText\n", encoding="utf-8")
    (root / "drafts" / "draft.md").write_text("Draft", encoding="utf-8")
    (root / "drafts" / "visual-plan.json").write_text('{"segments": []}', encoding="utf-8")


def test_import_registers_existing_files_without_moving_them(tmp_path: Path):
    make_project(tmp_path)
    artifacts = discover_legacy_artifacts(tmp_path)
    run_dir = import_legacy_project(tmp_path, "legacy-run", artifacts)
    assert (run_dir / "state.json").is_file()
    assert (tmp_path / "audio" / "voice.mp3").is_file()


def test_discovery_rejects_ambiguous_audio(tmp_path: Path):
    make_project(tmp_path)
    (tmp_path / "audio" / "second.mp3").write_bytes(b"audio")
    with pytest.raises(ValueError, match="ambiguous voice_audio"):
        discover_legacy_artifacts(tmp_path)
```

Create `tests/test_workflow_state.py`:

```python
from videocreator.workflow_state import STAGES, missing_stage_handlers


def test_every_declared_stage_requires_a_handler():
    handlers = {stage: object() for stage in STAGES if stage != "visual_assets"}
    assert missing_stage_handlers(handlers) == ["visual_assets"]
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_project_import.py tests/test_workflow_state.py -v`

Expected: import failures for both new modules.

- [ ] **Step 3: Implement stage declarations and legacy import**

Create `videocreator/workflow_state.py`:

```python
STAGES = (
    "prepare", "chat", "draft", "draft_confirm", "tts", "tts_confirm",
    "visual_plan", "visual_plan_confirm", "visual_assets", "visual_assets_confirm",
    "video_render", "video_render_confirm", "done",
)


def missing_stage_handlers(handlers: dict[str, object]) -> list[str]:
    return sorted(set(STAGES) - set(handlers))
```

Implement `videocreator/project_import.py` with exact-one discovery for `*.mp3`, `*.srt`, draft `*.md`, and `visual-plan.json`. `import_legacy_project()` writes a state at `visual_assets` with status `ready`, and a manifest containing absolute artifact paths. It must fail if `runs/<run-id>` already exists.

- [ ] **Step 4: Implement the import CLI**

`scripts/import_legacy_project.py` accepts `project_root`, `--run-id`, calls discovery and import, and prints the created run directory.

- [ ] **Step 5: Replace the stage chain with a validated dispatch map**

In `main.py`, import `STAGES` and `missing_stage_handlers`, add `STAGE_VIDEO_RENDER` and `STAGE_VIDEO_RENDER_CONFIRM`, and build a mapping inside `execute_from_current_stage()`:

```python
handlers = {
    STAGE_PREPARE: run_prepare,
    STAGE_CHAT: run_chat,
    STAGE_DRAFT: run_draft,
    STAGE_DRAFT_CONFIRM: run_draft,
    STAGE_TTS: run_tts,
    STAGE_TTS_CONFIRM: confirm_tts,
    STAGE_VISUAL_PLAN: run_visual_plan,
    STAGE_VISUAL_PLAN_CONFIRM: confirm_visual_plan,
    STAGE_VISUAL_ASSETS: run_visual_assets,
    STAGE_VISUAL_ASSETS_CONFIRM: confirm_visual_assets,
    STAGE_VIDEO_RENDER: run_video_render,
    STAGE_VIDEO_RENDER_CONFIRM: confirm_video_render,
    STAGE_DONE: finish_workflow,
}
missing = missing_stage_handlers(handlers)
if missing:
    raise RuntimeError(f"Workflow is missing stage handlers: {', '.join(missing)}")
```

Add explicit unavailable handlers `run_video_render()` and `confirm_video_render()` that raise `RuntimeError("Remotion renderer is not installed; finish renderer setup before entering video_render")`; Task 8 replaces their behavior. `finish_workflow()` prints the run path and returns a sentinel that breaks the loop.

- [ ] **Step 6: Verify tests and current main syntax**

Run: `python -m pytest tests/test_project_import.py tests/test_workflow_state.py -v`

Expected: all tests pass.

Run: `python -m py_compile main.py videocreator/*.py scripts/import_legacy_project.py`

Expected: exit code 0.

- [ ] **Step 7: Commit workflow recovery**

```powershell
git add main.py videocreator scripts/import_legacy_project.py tests
git commit -m "refactor: make workflow stages resumable"
```

---

### Task 4: Trailing Silence Cleanup And Render Contract

**Files:**
- Modify: `videocreator/media.py`
- Create: `videocreator/render_contract.py`
- Create: `tests/test_render_contract.py`
- Modify: `tests/test_media.py`

**Interfaces:**
- Consumes: audio path, SRT path, visual plan, approved asset manifest, 25fps.
- Produces: `parse_trailing_silence()`, `clean_audio_and_srt()`, `normalize_scenes()`, and `build_render_input()`.

- [ ] **Step 1: Write failing silence and timeline tests**

Append to `tests/test_media.py`:

```python
from videocreator.media import parse_trailing_silence


def test_parse_trailing_silence_returns_absolute_spoken_end():
    log = "silence_start: 18.367417\nsilence_end: 69.064 | silence_duration: 50.696583"
    assert parse_trailing_silence(log, analysis_offset_ms=200_000, total_duration_ms=269_126) == 218_367
```

Create `tests/test_render_contract.py`:

```python
from videocreator.render_contract import normalize_scenes


def test_normalize_scenes_absorbs_gaps_and_ends_at_audio_boundary():
    plan = {"segments": [
        {"segment_id": "scene-001", "start_ms": 0, "end_ms": 1000, "material_type": "image"},
        {"segment_id": "scene-002", "start_ms": 1200, "end_ms": 2000, "material_type": "subtitle_only"},
    ]}
    scenes = normalize_scenes(plan, {}, fps=25, spoken_end_ms=2200)
    assert scenes[0]["fromFrame"] == 0
    assert scenes[0]["durationInFrames"] == 30
    assert scenes[1]["fromFrame"] == 30
    assert scenes[1]["durationInFrames"] == 25
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_media.py tests/test_render_contract.py -v`

Expected: missing function/module failures.

- [ ] **Step 3: Implement trailing-silence parsing and derivative creation**

Add `parse_trailing_silence()` to `videocreator/media.py`; it accepts only a silence ending within 500ms of total duration and returns `analysis_offset_ms + silence_start_ms`. Add `clean_audio_and_srt()` that:

- invokes FFmpeg with `-t <spoken_end_seconds> -c:a libmp3lame`
- writes `voice.cleaned.mp3`
- parses SRT blocks and clamps the final caption end to `spoken_end_ms`
- writes `voice.cleaned.srt`
- never modifies source paths

Test SRT clamping with a temporary two-caption file and assert the source text is unchanged.

- [ ] **Step 4: Implement timeline normalization and render input**

Create `videocreator/render_contract.py` with:

```python
def ms_to_frame(milliseconds: int, fps: int) -> int:
    return round(milliseconds * fps / 1000)


def normalize_scenes(visual_plan, assets_by_scene, *, fps: int, spoken_end_ms: int):
    source = visual_plan["segments"]
    scenes = []
    for index, segment in enumerate(source):
        start_ms = 0 if index == 0 else segment["start_ms"]
        end_ms = source[index + 1]["start_ms"] if index + 1 < len(source) else spoken_end_ms
        if scenes and ms_to_frame(start_ms, fps) < scenes[-1]["fromFrame"] + scenes[-1]["durationInFrames"]:
            raise ValueError(f"{segment['segment_id']}: scene overlaps previous scene")
        from_frame = ms_to_frame(start_ms, fps)
        end_frame = ms_to_frame(end_ms, fps)
        record = assets_by_scene.get(segment["segment_id"], {})
        scenes.append({
            "id": segment["segment_id"],
            "fromFrame": from_frame,
            "durationInFrames": max(1, end_frame - from_frame),
            "assetType": "subtitle_only" if segment.get("material_type") == "subtitle_only" else record["asset_type"],
            "assetPath": record.get("local_path", ""),
            "fitMode": record.get("fit_mode", "cover"),
            "trimBeforeFrames": ms_to_frame(record.get("trim_start_ms", 0), fps),
            "mediaDurationInFrames": ms_to_frame(record.get("duration_ms") or 0, fps),
            "shortVideoPolicy": record.get("short_video_policy", "reject"),
            "motionPreset": "push-left" if index % 2 == 0 else "push-right",
        })
    return scenes
```

`build_render_input()` adds video id, 1920x1080, 25fps, duration from the last scene, cleaned relative audio/SRT paths, dark background, and normalized scenes.

- [ ] **Step 5: Verify all timing tests**

Run: `python -m pytest tests/test_media.py tests/test_render_contract.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit media preparation**

```powershell
git add videocreator tests
git commit -m "feat: normalize narration timing for rendering"
```

---

### Task 5: Remotion Project, Schema, And Timeline Tests

**Files:**
- Create: `renderer/package.json`
- Create: `renderer/tsconfig.json`
- Create: `renderer/remotion.config.ts`
- Create: `renderer/src/schema.ts`
- Create: `renderer/src/timeline.ts`
- Create: `renderer/tests/schema.test.ts`
- Create: `renderer/tests/timeline.test.ts`
- Create: `renderer/vitest.config.ts`

**Interfaces:**
- Consumes: Python `render-input.json` shape.
- Produces: `renderInputSchema`, `RenderInput`, `assertContinuousTimeline()`, and `sceneAtFrame()`.

- [ ] **Step 1: Create the locked Node package metadata**

Use Remotion `4.0.494` for every `remotion` and `@remotion/*` package. Create `renderer/package.json`:

```json
{
  "name": "video-creator-renderer",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "studio": "remotion studio src/index.ts",
    "render": "node scripts/render.mjs"
  },
  "dependencies": {
    "@remotion/bundler": "4.0.494",
    "@remotion/captions": "4.0.494",
    "@remotion/media": "4.0.494",
    "@remotion/renderer": "4.0.494",
    "@remotion/cli": "4.0.494",
    "react": "^19.1.1",
    "react-dom": "^19.1.1",
    "remotion": "4.0.494",
    "zod": "^3.25.76"
  },
  "devDependencies": {
    "@types/node": "^24.0.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "typescript": "^5.8.0",
    "vitest": "^3.2.0"
  }
}
```

Create `renderer/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["node", "vitest/globals"]
  },
  "include": ["src", "tests", "remotion.config.ts", "vitest.config.ts"]
}
```

Create `renderer/vitest.config.ts`:

```ts
import {defineConfig} from 'vitest/config';

export default defineConfig({
  test: {environment: 'node', include: ['tests/**/*.test.{ts,tsx}']},
});
```

Create `renderer/remotion.config.ts`:

```ts
import {Config} from '@remotion/cli/config';

Config.setOverwriteOutput(true);
Config.setCodec('h264');
```

- [ ] **Step 2: Write failing schema and hard-cut tests**

Create `renderer/tests/schema.test.ts` to assert a valid 1920x1080/25fps input parses and an overlapping scene list is rejected by `assertContinuousTimeline()`.

Create `renderer/tests/timeline.test.ts`:

```ts
import {describe, expect, it} from 'vitest';
import {sceneAtFrame} from '../src/timeline';

const scenes = [
  {id: 'one', fromFrame: 0, durationInFrames: 25},
  {id: 'two', fromFrame: 25, durationInFrames: 25},
];

describe('hard-cut timeline', () => {
  it('switches scenes exactly on the boundary frame', () => {
    expect(sceneAtFrame(scenes, 24)?.id).toBe('one');
    expect(sceneAtFrame(scenes, 25)?.id).toBe('two');
  });
});
```

- [ ] **Step 3: Install dependencies and verify red**

Run: `npm --prefix renderer install`

Run: `npm --prefix renderer test`

Expected: failure because `src/schema.ts` and `src/timeline.ts` do not exist.

- [ ] **Step 4: Implement Zod schema and timeline helpers**

Create `renderer/src/schema.ts` with these exact fields:

```ts
import {z} from 'zod';

export const captionSchema = z.object({
  text: z.string(),
  startMs: z.number().nonnegative(),
  endMs: z.number().positive(),
  timestampMs: z.number().nonnegative(),
  confidence: z.number(),
});

export const renderSceneSchema = z.object({
  id: z.string().min(1),
  fromFrame: z.number().int().nonnegative(),
  durationInFrames: z.number().int().positive(),
  assetType: z.enum(['image', 'video', 'subtitle_only']),
  assetPath: z.string(),
  fitMode: z.enum(['cover', 'contain']),
  trimBeforeFrames: z.number().int().nonnegative(),
  mediaDurationInFrames: z.number().int().nonnegative(),
  shortVideoPolicy: z.enum(['loop', 'freeze_last_frame', 'reject']),
  motionPreset: z.enum(['push-left', 'push-right', 'none']),
});

export const renderInputSchema = z.object({
  videoId: z.string().min(1),
  width: z.literal(1920),
  height: z.literal(1080),
  fps: z.literal(25),
  durationInFrames: z.number().int().positive(),
  audioPath: z.string().min(1),
  subtitlePath: z.string().min(1),
  backgroundColor: z.string().min(1),
  scenes: z.array(renderSceneSchema).min(1),
  captions: z.array(captionSchema).default([]),
});

export type RenderScene = z.infer<typeof renderSceneSchema>;
export type RenderInput = z.infer<typeof renderInputSchema>;
```

`timeline.ts` implements:

```ts
export const sceneAtFrame = <T extends {fromFrame: number; durationInFrames: number}>(scenes: T[], frame: number) =>
  scenes.find((scene) => frame >= scene.fromFrame && frame < scene.fromFrame + scene.durationInFrames);

export const assertContinuousTimeline = (scenes: RenderScene[], durationInFrames: number) => {
  let cursor = 0;
  for (const scene of scenes) {
    if (scene.fromFrame !== cursor) throw new Error(`${scene.id}: expected frame ${cursor}, got ${scene.fromFrame}`);
    cursor += scene.durationInFrames;
  }
  if (cursor !== durationInFrames) throw new Error(`Timeline ends at ${cursor}, expected ${durationInFrames}`);
};
```

- [ ] **Step 5: Verify tests and typecheck**

Run: `npm --prefix renderer test`

Expected: all tests pass.

Run: `npm --prefix renderer run typecheck`

Expected: exit code 0.

- [ ] **Step 6: Commit renderer foundation**

```powershell
git add renderer
git commit -m "feat: scaffold typed Remotion renderer"
```

---

### Task 6: Remotion Scenes, Audio, And Burned-In Captions

**Files:**
- Create: `renderer/src/index.ts`
- Create: `renderer/src/Root.tsx`
- Create: `renderer/src/VideoComposition.tsx`
- Create: `renderer/src/components/Scene.tsx`
- Create: `renderer/src/components/StillScene.tsx`
- Create: `renderer/src/components/VideoScene.tsx`
- Create: `renderer/src/components/SubtitleTrack.tsx`
- Create: `renderer/tests/components.test.tsx`

**Interfaces:**
- Consumes: validated `RenderInput` plus in-memory Remotion captions.
- Produces: composition id `NarratedVideo` and deterministic frame rendering.

- [ ] **Step 1: Write failing component tests**

Use `react-dom/server` in `renderer/tests/components.test.tsx` to assert:

- `StillScene` renders an image with `objectFit: cover` and a scale transform.
- `VideoScene` renders a muted Remotion media video.
- `SubtitleTrack` renders a caption active at the supplied frame and omits inactive captions.

The tests pass explicit frame/fps props to pure inner components; they do not mock Remotion hooks.

- [ ] **Step 2: Verify red**

Run: `npm --prefix renderer test -- components.test.tsx`

Expected: module resolution failures for the missing components.

- [ ] **Step 3: Implement still and video scene components**

`StillScene` uses `Img`, `staticFile`, `interpolate`, and a deterministic scale `1` to `1.05`. `push-left` translates from `1.5%` to `-1.5%`; `push-right` reverses it.

`VideoScene` uses `Video` from `@remotion/media`, `staticFile(scene.assetPath)`, `muted`, `trimBefore`, and CSS cover positioning. Implement `loop` through Remotion `Loop`; implement `freeze_last_frame` using `scene.mediaDurationInFrames`; reject unresolved policy before rendering.

- [ ] **Step 4: Implement captions and composition**

`SubtitleTrack` receives captions parsed by `@remotion/captions`, computes current time from frame/fps, and renders one centered caption within a lower safe-area box. Use white 58px semibold text, `-2px -2px 0 #111, 2px 2px 0 #111, 0 4px 16px rgba(0,0,0,.75)` shadow, maximum width 1600px, and bottom 150px.

`VideoComposition` renders adjacent `Sequence` elements with no transition component, one global narration `Audio`, and the subtitle track above visuals.

`Root.tsx` registers `NarratedVideo` and uses `calculateMetadata()` to derive dimensions and duration from props. `index.ts` calls `registerRoot(Root)`.

- [ ] **Step 5: Verify tests and typecheck**

Run: `npm --prefix renderer test`

Expected: all tests pass.

Run: `npm --prefix renderer run typecheck`

Expected: exit code 0.

- [ ] **Step 6: Commit composition components**

```powershell
git add renderer/src renderer/tests
git commit -m "feat: compose hard-cut narrated scenes in Remotion"
```

---

### Task 7: Programmatic Renderer And Short Integration Render

**Files:**
- Create: `renderer/scripts/render.mjs`
- Create: `scripts/render_video.py`
- Create: `tests/integration/test_remotion_render.py`

**Interfaces:**
- Consumes: project root, `render-input.json`, output path.
- Produces: H.264 MP4 and `render-report.json`.

- [ ] **Step 1: Write a failing integration test**

Create `tests/integration/test_remotion_render.py`. It must:

1. use FFmpeg lavfi to generate a 3-second narration WAV, one PNG, and one 1-second MP4 under `tmp_path`
2. write a three-scene render input and SRT
3. invoke `python scripts/render_video.py`
4. assert the output exists
5. run ffprobe and assert width 1920, height 1080, H.264, 25fps, an audio stream, and duration within 40ms of 3 seconds

Mark the test `@pytest.mark.integration` and skip only when `node`, `npm`, or `ffmpeg` is absent.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/integration/test_remotion_render.py -v -m integration`

Expected: failure because `scripts/render_video.py` does not exist.

- [ ] **Step 3: Implement the Node render script**

`renderer/scripts/render.mjs` parses `--project-root`, `--input`, and `--output`; reads render JSON and SRT; calls `parseSrt()`; enriches input props with captions; validates props; calls `bundle({entryPoint, publicDir: projectRoot})`; calls `selectComposition()` and `renderMedia()` with codec `h264`, audio codec `aac`, and output path.

Write JSON progress lines to stdout and throw on schema or render failure.

- [ ] **Step 4: Implement the Python render gate**

`scripts/render_video.py` must:

- reject missing project/input/output paths
- invoke `npm --prefix renderer run render -- --project-root ... --input ... --output ...`
- capture logs beside the render input
- run ffprobe on success
- require H.264 video, 1920x1080, 25fps, and at least one audio stream
- write `render-report.json` containing status, output metadata, input path, started/finished timestamps, and Remotion version from `renderer/package.json`
- return nonzero while preserving logs when rendering fails

- [ ] **Step 5: Verify the short render**

Run: `python -m pytest tests/integration/test_remotion_render.py -v -m integration`

Expected: `1 passed`; rendered fixture satisfies every ffprobe assertion.

- [ ] **Step 6: Commit rendering bridge**

```powershell
git add renderer/scripts scripts/render_video.py tests/integration
git commit -m "feat: render verified MP4 output with Remotion"
```

---

### Task 8: Connect Rendering To The Workflow

**Files:**
- Modify: `workflow.config.json`
- Modify: `main.py`
- Create: `tests/test_main_stage_dispatch.py`
- Modify: `README.md`
- Modify: `plans/workflow-roadmap.md`

**Interfaces:**
- Consumes: run manifest containing approved draft, cleaned audio/SRT, visual plan, and asset manifest.
- Produces: registered `render_input`, `final_video`, and `render_report` artifacts.

- [ ] **Step 1: Write the failing dispatch test**

Create `tests/test_main_stage_dispatch.py` that imports `build_stage_handlers()` from `main.py`, asserts all `STAGES` are present, and asserts `video_render` maps to the production `run_video_render` handler rather than the explicit unavailable handler from Task 3.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_main_stage_dispatch.py -v`

Expected: failure because `build_stage_handlers()` does not exist or renderer remains an unavailable handler.

- [ ] **Step 3: Add renderer config and production stage handlers**

Add the exact renderer config from the design to `workflow.config.json`.

Refactor the mapping from Task 3 into `build_stage_handlers()`. Implement `run_video_render()` to:

1. audit manifest
2. detect and clean trailing silence when needed
3. write corrected SRT and synchronized render input
4. invoke `scripts/render_video.py`
5. register `render_input`, `voice_audio_cleaned`, `voice_subtitle_cleaned`, `final_video`, and `render_report`
6. set `video_render_confirm`

Implement `confirm_video_render()` using existing confirmation behavior; approval moves to `done`, rejection returns to `video_render` without deleting the failed render.

- [ ] **Step 4: Update user documentation**

README must document:

- `npm --prefix renderer install`
- legacy project import command
- asset request and audit commands
- Remotion Studio command
- resume and final render commands
- generation disabled by default

Update `plans/workflow-roadmap.md` to mark final timeline assembly implemented and leave vertical output and richer transitions as future work.

- [ ] **Step 5: Run all automated checks**

Run: `python -m pytest -v -m "not integration"`

Run: `npm --prefix renderer test`

Run: `npm --prefix renderer run typecheck`

Run: `python -m pytest tests/integration/test_remotion_render.py -v -m integration`

Expected: all commands pass.

- [ ] **Step 6: Commit workflow integration**

```powershell
git add workflow.config.json main.py README.md plans/workflow-roadmap.md tests/test_main_stage_dispatch.py
git commit -m "feat: integrate Remotion final render stage"
```

---

### Task 9: Complete `资本主义潘多拉魔盒`

**Files:**
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/state.json`
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/manifest.json`
- Create locally: `projects/资本主义潘多拉魔盒/drafts/asset-request.json`
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/asset-manifest.json`
- Create locally: `projects/资本主义潘多拉魔盒/audio/voice.cleaned.mp3`
- Create locally: `projects/资本主义潘多拉魔盒/audio/voice.cleaned.srt`
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/render-input.json`
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/final.mp4`
- Create locally: `projects/资本主义潘多拉魔盒/runs/<run-id>/render-report.json`

**Interfaces:**
- Consumes: completed implementation from Tasks 1-8 and existing local project artifacts.
- Produces: a verified final local MP4; project files remain ignored by git.

- [ ] **Step 1: Import the legacy project**

Run:

```powershell
python scripts/import_legacy_project.py "projects/资本主义潘多拉魔盒" --run-id "20260720-remotion-final"
```

Expected: new run state at `visual_assets`; original audio, SRT, draft, and visual plan hashes remain unchanged.

- [ ] **Step 2: Generate the 17 web asset requests**

Run:

```powershell
python scripts/create_asset_request.py --visual-plan "projects/资本主义潘多拉魔盒/drafts/visual-plan.json" --output "projects/资本主义潘多拉魔盒/drafts/asset-request.json"
```

Expected: exactly 17 requests; the subtitle-only closing scene is omitted.

- [ ] **Step 3: Source all assets through AI web research**

For each request, the AI agent searches the web, opens the source page, verifies semantic fit and visible quality, downloads the selected asset into `assets/`, and writes every required provenance field. Prefer public-domain archives, Wikimedia Commons, official institutions, and sources with explicit reuse terms. Do not use generation APIs.

Expected: 17 approved records and 17 readable local asset files.

- [ ] **Step 4: Run the asset gate**

Run:

```powershell
python scripts/audit_asset_manifest.py --project-root "projects/资本主义潘多拉魔盒" --visual-plan "projects/资本主义潘多拉魔盒/drafts/visual-plan.json" --manifest "projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/asset-manifest.json" --output "projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/asset-audit.json"
```

Expected: `ok: true`, 17 approved scene ids, no errors. Any low-resolution archival-image warning includes an explicit approval record before continuing.

- [ ] **Step 5: Render representative frames in Studio**

Run: `npm --prefix renderer run studio`

Inspect frames near scene 1, scene 8, scene 15, and scene 18. Confirm hard-cut boundaries, crop quality, 1.00-to-1.05 image motion, no source-video audio, and subtitle safe-area placement.

- [ ] **Step 6: Resume and render the final MP4**

Run:

```powershell
python main.py resume "projects/资本主义潘多拉魔盒/runs/20260720-remotion-final"
```

Expected: workflow reaches `video_render_confirm` and writes `final.mp4` plus `render-report.json`.

- [ ] **Step 7: Verify final output**

Run `ffprobe` and assert:

- width 1920
- height 1080
- average frame rate 25/1
- H.264 video stream
- AAC audio stream
- duration equals cleaned narration within one frame
- no 50.7-second trailing silence

Review the complete video once for semantic scene match, subtitle timing, cuts, and corrupted frames. Approve only after the full review.

- [ ] **Step 8: Run the complete regression suite**

Run:

```powershell
python -m pytest -v
npm --prefix renderer test
npm --prefix renderer run typecheck
```

Expected: all Python unit/integration tests, TypeScript tests, and type checks pass.

Do not commit the ignored local project outputs. Commit only source or documentation fixes discovered during final verification, each with a reproducing test.

---

## Completion Gate

Before declaring the milestone complete, verify all acceptance criteria from `docs/superpowers/specs/2026-07-20-remotion-final-video-assembly-design.md` against fresh test, ffprobe, and full-video review evidence. A successful short fixture render is not sufficient evidence that the target video is complete.
