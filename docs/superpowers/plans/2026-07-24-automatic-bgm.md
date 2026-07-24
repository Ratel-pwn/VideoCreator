# Automatic BGM Selection And Mixing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one automatically selected, provenance-tracked BGM track to each video, preferring project/template/global local libraries and falling back to provider plus MCP-Agent search before producing an audited FFmpeg mix.

**Architecture:** Focused Python modules own local-library validation, deterministic matching, online candidate handling, FFmpeg preparation, and mix auditing. Templates provide declarative BGM preferences, the durable MCP interaction channel supplies Agent candidates only when core providers fail, and Remotion continues to consume one authoritative audio file.

**Tech Stack:** Python 3.12, dataclasses, urllib, FFmpeg/ffprobe, Remotion 4, TypeScript, pytest, Vitest, existing MCP FastMCP service.

## Global Constraints

- Use exactly one BGM track for the whole video.
- Resolve local BGM as complete override: project, then template, then global default.
- Do not merge tracks from different library levels.
- Missing local BGM triggers provider search, then MCP-Agent search.
- No eligible online candidate degrades to narration-only output with a warning.
- Public downloadability is not represented as a license.
- `rights_status: unknown` warns but does not block rendering.
- A selected-track preparation, mixing, duration, clipping, loudness, or hash failure blocks rendering.
- Subtitle synchronization remains bound to the narration stem.
- Remotion receives exactly one authoritative render audio path.
- Templates remain declarative and contain no executable code.
- Provider credentials stay in ignored local configuration.
- Do not modify external source projects.

---

### Task 0: Build An Integrated Feature Baseline

**Files:**
- Merge branch: `feat/codex-feat-mcp-workflow-service`
- Merge branch: `feat/subtitle-sync-self-healing`
- Verify: `main.py`
- Verify: `pyproject.toml`
- Verify: `videocreator/cli.py`
- Verify: `videocreator/workflow_state.py`
- Verify: `workflow.config.json`

**Interfaces:**
- Consumes: durable MCP interactions from `videocreator.interactions`
- Consumes: narration/subtitle gate from `videocreator.subtitle_sync`
- Produces: one tested branch containing both prerequisites

- [ ] **Step 1: Create the isolated implementation worktree**

Run:

```powershell
git worktree add .worktrees/automatic-bgm -b feat/automatic-bgm main
```

Expected: a clean worktree on `feat/automatic-bgm`, leaving the dirty main
checkout untouched.

- [ ] **Step 2: Merge the MCP workflow branch**

Run:

```powershell
git merge --no-ff feat/codex-feat-mcp-workflow-service
```

Expected: MCP runtime, queue, worker, durable interactions, and high-level tools
are present.

- [ ] **Step 3: Merge the subtitle synchronization branch**

Run:

```powershell
git merge --no-ff feat/subtitle-sync-self-healing
```

Resolve overlapping files with these exact combined requirements:

```toml
dependencies = [
  "mcp>=1.0,<2",
  "opencc-python-reimplemented>=0.1.7,<0.2"
]
```

`main.py` must retain both `DurableInteractionPort` workflow boundaries and the
`subtitle_sync` stage. `videocreator/cli.py` must retain both `vc mcp ...` and
`vc audit/repair subtitles ...`. `workflow.config.json` must retain both `mcp`
and `subtitle_sync` sections.

- [ ] **Step 4: Run the integrated baseline tests**

Run:

```powershell
pytest -q
npm --prefix renderer test
npm --prefix renderer run typecheck
git diff --check
```

Expected: all checks pass before BGM code is added.

- [ ] **Step 5: Commit conflict resolutions if the merges required them**

Run:

```powershell
git add main.py pyproject.toml videocreator/cli.py videocreator/workflow_state.py workflow.config.json tests
git commit -m "chore: integrate workflow prerequisites"
```

Expected: clean integrated baseline.

### Task 1: Declarative BGM Policies And Local Libraries

**Files:**
- Create: `videocreator/bgm_library.py`
- Create: `videocreator/bgm_policy.py`
- Modify: `videocreator/templates.py`
- Modify: `videocreator/project_layout.py`
- Modify: `main.py`
- Modify: `templates/ai-daily/template.json`
- Modify: `templates/chaos-museum/template.json`
- Modify: `templates/product-intro/template.json`
- Modify: `templates/science-explainer/template.json`
- Modify: `templates/infinite-game-manifesto/template.json`
- Create: `templates/*/bgm.json`
- Create: `library/bgm/default/README.md`
- Test: `tests/test_bgm_library.py`
- Test: `tests/test_bgm_policy.py`
- Modify: `tests/test_project_layout.py`
- Modify: `tests/test_templates.py`

**Interfaces:**
- Produces: `BgmTrack`
- Produces: `BgmLibrarySelection`
- Produces: `resolve_bgm_library(repo_root, project_root, template) -> BgmLibrarySelection`
- Produces: `BgmPolicy.from_dict(value) -> BgmPolicy`
- Produces: `load_bgm_policy(template) -> BgmPolicy`
- Consumes: `TemplateDefinition.paths["bgm"]` when declared

- [ ] **Step 1: Write failing local-library tests**

Create tests containing:

```python
def test_project_valid_track_completely_overrides_template_and_global(tmp_path):
    repo, project, template = make_bgm_tree(tmp_path)
    write_track(project / "library/bgm", "project-track", moods=["reflective"])
    write_track(template.root / "library/bgm", "template-track", moods=["calm"])
    write_track(repo / "library/bgm/default", "global-track", moods=["neutral"])

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "project"
    assert [track.id for track in selected.tracks] == ["project-track"]


def test_invalid_project_directory_does_not_mask_valid_template(tmp_path):
    repo, project, template = make_bgm_tree(tmp_path)
    (project / "library/bgm/broken.mp3").write_bytes(b"not audio")
    write_track(template.root / "library/bgm", "template-track")

    selected = resolve_bgm_library(repo, project, template)

    assert selected.level == "template"
    assert selected.warnings
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_library.py tests/test_bgm_policy.py -q
```

Expected: import failures because the BGM modules do not exist.

- [ ] **Step 3: Implement the local track contract**

In `videocreator/bgm_library.py`, implement:

```python
SUPPORTED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

@dataclass(frozen=True)
class BgmTrack:
    id: str
    path: Path
    metadata_path: Path
    level: str
    sha256: str
    title: str
    creator: str | None
    source_url: str | None
    license: str | None
    rights_status: str
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    energy: str
    tempo_bpm: float | None
    instrumental: bool
    template_tags: tuple[str, ...]
    avoid_for: tuple[str, ...]
    preferred_start_ms: int
    loopable: bool

@dataclass(frozen=True)
class BgmLibrarySelection:
    level: str
    root: Path | None
    tracks: tuple[BgmTrack, ...]
    warnings: tuple[str, ...]
```

Validate the required sidecar fields from the design. Use `probe_media()` to
exclude undecodable files. Derive the sidecar as
`audio_path.with_suffix(".bgm.json")`.

- [ ] **Step 4: Implement complete-override BGM resolution**

Implement:

```python
def resolve_bgm_library(
    repo_root: Path,
    project_root: Path,
    template: TemplateDefinition,
) -> BgmLibrarySelection:
    candidates = (
        ("project", project_root / "library" / "bgm"),
        ("template", template.root / "library" / "bgm"),
        ("global", repo_root / "library" / "bgm" / "default"),
    )
    accumulated_warnings: list[str] = []
    for level, root in candidates:
        tracks, warnings = load_bgm_directory(root, level)
        accumulated_warnings.extend(warnings)
        if tracks:
            return BgmLibrarySelection(
                level, root, tuple(sorted(tracks, key=lambda item: item.id)),
                tuple(accumulated_warnings),
            )
    return BgmLibrarySelection("none", None, (), tuple(accumulated_warnings))
```

- [ ] **Step 5: Implement the template policy contract**

In `videocreator/bgm_policy.py`, implement:

```python
@dataclass(frozen=True)
class BgmPolicy:
    enabled: bool = True
    instrumental_only: bool = True
    preferred_moods: tuple[str, ...] = ()
    preferred_energy: str = "low-medium"
    preferred_tempo_bpm: tuple[float, float] = (70.0, 105.0)
    avoid_tags: tuple[str, ...] = ("vocal", "heavy-drums")
    ducking_strength: str = "medium"
    fade_in_ms: int = 2000
    fade_out_ms: int = 3000

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BgmPolicy":
        tempo = tuple(value.get("preferred_tempo_bpm", (70, 105)))
        if len(tempo) != 2 or float(tempo[0]) > float(tempo[1]):
            raise ValueError("preferred_tempo_bpm must be an ascending pair")
        ducking = str(value.get("ducking_strength", "medium"))
        if ducking not in {"light", "medium", "strong"}:
            raise ValueError(f"Unsupported ducking strength: {ducking}")
        fade_in = int(value.get("fade_in_ms", 2000))
        fade_out = int(value.get("fade_out_ms", 3000))
        if fade_in < 0 or fade_out < 0:
            raise ValueError("BGM fades must be non-negative")
        return cls(
            enabled=bool(value.get("enabled", True)),
            instrumental_only=bool(value.get("instrumental_only", True)),
            preferred_moods=tuple(map(str, value.get("preferred_moods", ()))),
            preferred_energy=str(value.get("preferred_energy", "low-medium")),
            preferred_tempo_bpm=(float(tempo[0]), float(tempo[1])),
            avoid_tags=tuple(map(str, value.get("avoid_tags", ("vocal", "heavy-drums")))),
            ducking_strength=ducking,
            fade_in_ms=fade_in,
            fade_out_ms=fade_out,
        )
```

Reject unsupported ducking strengths, inverted tempo ranges, and negative
fades. `load_bgm_policy()` loads `template.paths["bgm"]` or returns defaults.

- [ ] **Step 6: Declare BGM in templates and project layout**

Add `"bgm"` to `ALLOWED_CAPABILITIES`, allow an optional `paths.bgm`, and add a
valid `bgm.json` to every repository template. Update project initialization:

```python
for relative in (
    "sources",
    "library/style",
    "library/voice",
    "library/bgm",
    "media/images",
    "media/videos",
    "runs",
):
    (project / relative).mkdir(parents=True, exist_ok=True)
```

Update run creation in `main.py` so `inputs/library.snapshot.json` includes the
selected BGM level and file hashes independently from style and voice.

- [ ] **Step 7: Verify Task 1**

Run:

```powershell
pytest tests/test_bgm_library.py tests/test_bgm_policy.py tests/test_project_layout.py tests/test_templates.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

Run:

```powershell
git add videocreator/bgm_library.py videocreator/bgm_policy.py videocreator/templates.py videocreator/project_layout.py main.py templates library/bgm/default tests
git commit -m "feat: add declarative BGM libraries"
```

### Task 2: Deterministic Query Generation And Candidate Selection

**Files:**
- Create: `videocreator/bgm_selection.py`
- Test: `tests/test_bgm_selection.py`

**Interfaces:**
- Consumes: `BgmTrack`, `BgmPolicy`
- Produces: `BgmQuery`
- Produces: `CandidateScore`
- Produces: `build_bgm_query(title, topic, approved_text, template_id, policy) -> BgmQuery`
- Produces: `score_candidate(track, query, policy) -> CandidateScore`
- Produces: `select_bgm_candidate(tracks, query, policy) -> SelectionResult`

- [ ] **Step 1: Write failing deterministic selection tests**

```python
def test_selector_prefers_subject_mood_template_and_tempo_matches():
    policy = BgmPolicy(
        preferred_moods=("reflective",),
        preferred_tempo_bpm=(70, 105),
    )
    query = BgmQuery(
        subjects=("technology",),
        moods=("reflective",),
        template_id="science-explainer",
        terms_zh=("科技", "思考"),
        terms_en=("technology", "reflective"),
    )
    selected = select_bgm_candidate(
        [
            track("loud", energy="high", tempo_bpm=150),
            track(
                "calm",
                subjects=("technology",),
                moods=("reflective",),
                template_tags=("science-explainer",),
                tempo_bpm=88,
            ),
        ],
        query,
        policy,
    )
    assert selected.track.id == "calm"
    assert selected.scores[0].components["mood"] > 0


def test_selector_uses_stable_track_id_as_final_tie_breaker():
    selected = select_bgm_candidate(
        [track("b"), track("a")],
        neutral_query(),
        BgmPolicy(),
    )
    assert selected.track.id == "a"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_selection.py -q
```

Expected: module import failure.

- [ ] **Step 3: Implement query and scoring types**

Implement immutable types:

```python
@dataclass(frozen=True)
class BgmQuery:
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    template_id: str
    terms_zh: tuple[str, ...]
    terms_en: tuple[str, ...]

@dataclass(frozen=True)
class CandidateScore:
    track_id: str
    total: float
    eligible: bool
    components: dict[str, float]
    rejection_reasons: tuple[str, ...]

@dataclass(frozen=True)
class SelectionResult:
    track: BgmTrack | None
    scores: tuple[CandidateScore, ...]
```

Use exact weights:

```python
WEIGHTS = {
    "subject": 30.0,
    "mood": 25.0,
    "template": 20.0,
    "energy": 10.0,
    "tempo": 10.0,
    "instrumental": 5.0,
    "avoid": -50.0,
}
```

An instrumental-only violation is ineligible, not merely penalized.

- [ ] **Step 4: Implement bounded query generation**

Generate terms from template policy, title/topic tokens, and a capped approved
text sample. Do not call an LLM. Normalize and deduplicate, cap each language
list at 12 terms, and include the template ID as a stable classification input.

- [ ] **Step 5: Verify and commit Task 2**

Run:

```powershell
pytest tests/test_bgm_selection.py -q
git add videocreator/bgm_selection.py tests/test_bgm_selection.py
git commit -m "feat: select BGM deterministically"
```

Expected: tests pass and the selection commit is created.

### Task 3: Provider Search, Secure Download, And Agent Candidates

**Files:**
- Create: `videocreator/bgm_search.py`
- Create: `config/bgm-search.example.json`
- Modify: `.gitignore`
- Test: `tests/test_bgm_search.py`

**Interfaces:**
- Consumes: `BgmQuery`, `BgmTrack`
- Produces: `OnlineBgmCandidate`
- Produces: `search_configured_providers(query, config, opener) -> list[OnlineBgmCandidate]`
- Produces: `parse_agent_candidates(response) -> list[OnlineBgmCandidate]`
- Produces: `download_candidate(candidate, output_dir, opener) -> Path`
- Produces: `candidate_to_track(candidate, downloaded_path) -> BgmTrack`

- [ ] **Step 1: Write failing provider and security tests**

```python
def test_provider_results_are_normalized_without_claiming_unknown_rights():
    candidates = search_configured_providers(
        reflective_query(),
        {"providers": [{"type": "wikimedia", "enabled": True}]},
        opener=fake_wikimedia_response,
    )
    assert candidates[0].rights_status == "unknown"
    assert candidates[0].source_page_url.startswith("https://")


def test_download_rejects_non_http_url(tmp_path):
    candidate = online_candidate(download_url="file:///C:/secret.mp3")
    with pytest.raises(BgmSearchError, match="http"):
        download_candidate(candidate, tmp_path, opener=lambda *_: None)


def test_agent_response_must_be_a_bounded_json_candidate_list():
    value = parse_agent_candidates(json.dumps({"candidates": [
        {"title": "Track", "source_page_url": "https://example.test/page",
         "download_url": "https://example.test/file.mp3", "provider": "web"}
    ]}))
    assert len(value) == 1
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_search.py -q
```

Expected: module import failure.

- [ ] **Step 3: Implement normalized online candidates**

```python
@dataclass(frozen=True)
class OnlineBgmCandidate:
    id: str
    title: str
    creator: str | None
    source_page_url: str
    download_url: str
    provider: str
    license: str | None
    rights_status: str
    subjects: tuple[str, ...]
    moods: tuple[str, ...]
    energy: str
    tempo_bpm: float | None
    instrumental: bool
    template_tags: tuple[str, ...]
    loopable: bool
```

Only `http` and `https` URLs are accepted. Unknown license or rights values are
normalized to `rights_status="unknown"`.

- [ ] **Step 4: Implement the first core provider adapter**

Implement a Wikimedia Commons audio adapter using the MediaWiki API. Search
namespace 6, request `imageinfo` URL and extmetadata, accept only supported
audio suffixes, and return at most the configured `max_candidates`. Provider
errors return a structured warning and do not abort the stage.

`config/bgm-search.example.json` contains:

```json
{
  "max_candidates": 8,
  "max_download_bytes": 52428800,
  "providers": [
    {"type": "wikimedia", "enabled": true}
  ]
}
```

Ignore `config/bgm-search.local.json`.

- [ ] **Step 5: Implement secure bounded downloads and Agent parsing**

Downloads must:

- remain inside the requested run audio directory,
- use a generated safe filename,
- enforce `max_download_bytes`,
- reject redirects to non-HTTP schemes,
- delete partial files on failure,
- compute SHA-256 after completion.

Agent JSON accepts at most 20 candidates and 200 KB. Reject unknown top-level
keys and candidates missing source or download URLs.

- [ ] **Step 6: Verify and commit Task 3**

Run:

```powershell
pytest tests/test_bgm_search.py -q
git add videocreator/bgm_search.py config/bgm-search.example.json .gitignore tests/test_bgm_search.py
git commit -m "feat: search and download BGM candidates"
```

Expected: tests pass and no real network request occurs in unit tests.

### Task 4: FFmpeg Preparation, Mixing, And Audit

**Files:**
- Create: `videocreator/bgm_mix.py`
- Create: `videocreator/bgm_audit.py`
- Test: `tests/test_bgm_mix.py`
- Test: `tests/test_bgm_audit.py`
- Test: `tests/integration/test_bgm_mix.py`

**Interfaces:**
- Produces: `BgmMixSettings`
- Produces: `BgmMixResult`
- Produces: `build_bgm_filter(track_duration_ms, narration_duration_ms, policy) -> str`
- Produces: `mix_bgm(narration, bgm, prepared_output, mix_output, policy, runner) -> BgmMixResult`
- Produces: `write_bgm_mix_report(result, path) -> dict`
- Produces: `write_narration_only_report(narration, path, warnings) -> dict`
- Produces: `audit_bgm_render_audio(render_audio, report) -> dict`

- [ ] **Step 1: Write failing filter and audit tests**

```python
def test_short_track_filter_uses_equal_power_crossfades():
    value = build_bgm_filter(
        track_duration_ms=12_000,
        narration_duration_ms=35_000,
        policy=BgmPolicy(),
    )
    assert "acrossfade" in value
    assert "c1=tri" in value
    assert "atrim=duration=35" in value


def test_mix_audit_rejects_stale_render_audio(tmp_path):
    report = write_report_fixture(tmp_path)
    render_audio = tmp_path / "final-mix.wav"
    render_audio.write_bytes(render_audio.read_bytes() + b"changed")
    result = audit_bgm_render_audio(render_audio, report)
    assert result["status"] == "failed"
    assert "artifact_hash_mismatch" in finding_codes(result)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_mix.py tests/test_bgm_audit.py -q
```

Expected: module import failures.

- [ ] **Step 3: Implement preparation and loop-filter construction**

Probe narration and BGM with `probe_media()`. For a short loopable track,
calculate:

```python
repeat_count = math.ceil(
    (narration_duration_ms - crossfade_ms)
    / (track_duration_ms - crossfade_ms)
)
```

Build an `asplit` chain and join repeats with:

```text
acrossfade=d=<seconds>:c1=tri:c2=tri
```

Then apply `atrim`, `afade`, resampling, and stereo layout. A non-loopable short
track raises `BgmMixError("track is too short and is not loopable")`.

- [ ] **Step 4: Implement sidechain mixing**

Use a validated ducking preset:

```python
DUCKING = {
    "light": {"threshold": 0.05, "ratio": 4, "attack": 30, "release": 650},
    "medium": {"threshold": 0.03, "ratio": 8, "attack": 20, "release": 500},
    "strong": {"threshold": 0.02, "ratio": 12, "attack": 15, "release": 400},
}
```

The FFmpeg graph standardizes narration, prepares BGM, applies
`sidechaincompress` with narration as key input, mixes with `amix`, and applies
`loudnorm=I=-16:LRA=11:TP=-1.5`. Output `final-mix.wav` as PCM.

- [ ] **Step 5: Implement measurement and report writing**

Run a post-mix `loudnorm` analysis pass and parse its JSON. The report includes
all input/output hashes, FFmpeg version, durations, policy hash, configuration
hash, measured LUFS, true peak, warnings, provenance, and `mode` equal to
`"bgm"` or `"narration_only"`.

The audit requires:

```python
abs(mix_duration_ms - narration_duration_ms) <= 100
measured_lufs >= -18.0
measured_lufs <= -14.0
true_peak_dbtp <= -1.0
```

Unknown rights add a warning only.

- [ ] **Step 6: Add real FFmpeg integration coverage**

Generate narration and BGM sine fixtures with FFmpeg. Verify:

- long BGM crops to narration,
- short BGM loops with a valid output,
- output duration is within 100 ms,
- speech intervals reduce BGM level relative to pauses,
- output decodes with `ffmpeg -v error -f null`.

Run:

```powershell
pytest tests/integration/test_bgm_mix.py -q
```

Expected: PASS when FFmpeg/ffprobe are installed; otherwise a declared skip.

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add videocreator/bgm_mix.py videocreator/bgm_audit.py tests/test_bgm_mix.py tests/test_bgm_audit.py tests/integration/test_bgm_mix.py
git commit -m "feat: mix and audit narration BGM"
```

### Task 5: Durable Provider-To-Agent Fallback

**Files:**
- Modify: `videocreator/interactions.py`
- Modify: `videocreator/workflow_service.py`
- Modify: `videocreator/mcp_server.py`
- Create: `videocreator/bgm_workflow.py`
- Test: `tests/test_bgm_workflow.py`
- Modify: `tests/test_interactions.py`
- Modify: `tests/test_workflow_service.py`
- Modify: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: provider search and `parse_agent_candidates`
- Produces: `resolve_bgm_for_run(request, interaction_port) -> BgmResolution`
- Produces interaction kind: `bgm_candidates`
- Reuses: `submit_workflow_input(project, run_id, interaction_id, response)`

- [ ] **Step 1: Write failing fallback tests**

```python
def test_provider_failure_requests_agent_candidates_in_durable_mode(tmp_path):
    port = DurableInteractionPort()
    ctx = make_context(tmp_path, port)
    with pytest.raises(InteractionRequired) as raised:
        resolve_bgm_for_run(empty_provider_request(ctx), port)
    assert raised.value.interaction["kind"] == "bgm_candidates"
    assert "query" in raised.value.interaction["payload"]


def test_console_mode_skips_agent_and_returns_narration_only(tmp_path):
    result = resolve_bgm_for_run(
        empty_provider_request(make_context(tmp_path)),
        ConsoleInteractionPort(),
    )
    assert result.mode == "narration_only"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_workflow.py tests/test_interactions.py -q
```

Expected: missing BGM workflow and interaction payload support.

- [ ] **Step 3: Add typed interaction payloads and handoff capability**

Extend `InteractionPort`:

```python
class InteractionPort(Protocol):
    supports_agent_handoff: bool
    def ask(
        self,
        ctx: InteractionContext,
        key: str,
        prompt: str,
        kind: str = "text",
        choices: tuple[str, ...] = (),
        payload: dict[str, Any] | None = None,
    ) -> str:
        raise NotImplementedError
```

Set `ConsoleInteractionPort.supports_agent_handoff = False` and
`DurableInteractionPort.supports_agent_handoff = True`. Persist payloads in the
pending interaction and expose them unchanged through workflow status.

- [ ] **Step 4: Implement the BGM resolution orchestrator**

`resolve_bgm_for_run()` performs:

1. local selection,
2. configured provider search,
3. candidate download, validation, and scoring,
4. durable `bgm_candidates` interaction when provider candidates are empty,
5. parsed Agent candidate validation and scoring after resume,
6. narration-only fallback when no Agent channel or no eligible candidate
   remains.

Use the stable interaction key `bgm-online-candidates`. Clear its stored answer
after a candidate is accepted or fallback is finalized.

- [ ] **Step 5: Verify MCP exposure without adding a low-level tool**

The existing `get_workflow_status` returns the pending interaction including
its `kind` and `payload`. The existing `submit_workflow_input` accepts the Agent
candidate JSON string. Do not add a `search_bgm` MCP tool.

Add tests proving the high-level MCP tool set is unchanged and that a
`bgm_candidates` interaction round-trips through service status and submission.

- [ ] **Step 6: Verify and commit Task 5**

Run:

```powershell
pytest tests/test_bgm_workflow.py tests/test_interactions.py tests/test_workflow_service.py tests/test_mcp_server.py -q
git add videocreator/interactions.py videocreator/workflow_service.py videocreator/mcp_server.py videocreator/bgm_workflow.py tests
git commit -m "feat: request BGM candidates through MCP workflow"
```

### Task 6: Workflow Stage And Mandatory Render Gate

**Files:**
- Modify: `main.py`
- Modify: `videocreator/workflow_state.py`
- Modify: `videocreator/render_contract.py`
- Modify: `workflow.config.json`
- Modify: `skills/workflow-controller/SKILL.md`
- Test: `tests/test_bgm_stage.py`
- Modify: `tests/test_main_stage_dispatch.py`
- Modify: `tests/test_render_contract.py`
- Modify: `renderer/tests/schema.test.ts`

**Interfaces:**
- Produces stage: `bgm`
- Produces artifacts: `bgm_source`, `bgm_selection`, `bgm_prepared`, `final_mix`, `bgm_mix_report`
- Produces: `resolve_bgm_for_context(ctx) -> BgmResolution`
- Produces: `ensure_bgm_mix_gate(render_audio, report_path) -> dict`
- Consumes: passing narration/subtitle audit
- Supplies: one audited `audioPath` to `build_render_input`

- [ ] **Step 1: Write failing workflow and render tests**

```python
def test_assets_confirmation_advances_to_bgm(context):
    confirm_visual_assets(context)
    assert context.state["current_stage"] == "bgm"


def test_render_refuses_stale_bgm_mix_report(context):
    write_passing_bgm_report(context)
    Path(context.manifest["artifacts"]["final_mix"]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="artifact_hash_mismatch"):
        run_video_render(context)


def test_no_bgm_fallback_renders_narration(context):
    write_narration_only_bgm_report(context)
    value = build_context_render_input(context)
    assert value["audioPath"].endswith("voice.mp3")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_bgm_stage.py tests/test_main_stage_dispatch.py tests/test_render_contract.py -q
```

Expected: missing stage and gate.

- [ ] **Step 3: Add workflow configuration**

Add:

```json
"bgm": {
  "enabled": true,
  "search_config": "config/bgm-search.local.json",
  "final_lufs": -16.0,
  "lufs_tolerance": 2.0,
  "true_peak_dbtp": -1.5,
  "max_duration_delta_ms": 100,
  "crossfade_ms": 1500,
  "max_agent_candidates": 20,
  "max_agent_response_bytes": 200000
}
```

When the local search config is absent, use the committed example defaults
without treating it as credentials.

- [ ] **Step 4: Implement `run_bgm`**

The stage:

```python
def run_bgm(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_BGM)
    ensure_current_subtitle_sync_audit(ctx)
    narration = Path(ctx.manifest["artifacts"]["voice_audio"])
    report_path = ctx.run_dir / "audio" / "bgm-mix-report.json"
    resolution = resolve_bgm_for_context(ctx)
    if resolution.mode == "narration_only":
        report = write_narration_only_report(
            narration,
            report_path,
            resolution.warnings,
        )
        ctx.register_artifact("bgm_mix_report", report_path)
        ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
        return
    prepared_path = ctx.run_dir / "audio" / "bgm.prepared.wav"
    mix_path = ctx.run_dir / "audio" / "final-mix.wav"
    result = mix_bgm(
        narration,
        resolution.track.path,
        prepared_path,
        mix_path,
        resolution.policy,
    )
    write_bgm_mix_report(result, report_path)
    ensure_bgm_mix_gate(mix_path, report_path)
    register_bgm_artifacts(ctx, result)
    ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
```

Add `bgm` to `STAGES` and stage handlers. Asset confirmation advances to BGM,
not directly to render.

- [ ] **Step 5: Enforce the final audio gate**

Before writing `render-input.json`, run `audit_bgm_render_audio()` against the
exact selected render audio. Use:

- `final_mix` when report mode is `bgm`,
- original narration when report mode is `narration_only`.

Pass this one path to the existing
`build_render_input(audio_path=render_audio_path)`.
Do not add a second audio field to the renderer schema.

- [ ] **Step 6: Verify and commit Task 6**

Run:

```powershell
pytest tests/test_bgm_stage.py tests/test_main_stage_dispatch.py tests/test_render_contract.py -q
npm --prefix renderer test
npm --prefix renderer run typecheck
git add main.py videocreator/workflow_state.py videocreator/render_contract.py workflow.config.json skills/workflow-controller/SKILL.md tests renderer/tests/schema.test.ts
git commit -m "feat: gate rendering on audited BGM mix"
```

### Task 7: Documentation And End-To-End Verification

**Files:**
- Modify: `README.md`
- Create: `docs/bgm-library.md`
- Modify: `config/bgm-search.example.json`
- Test: `tests/integration/test_bgm_workflow.py`

**Interfaces:**
- Consumes: all previous tasks
- Produces: documented library and run workflow
- Produces: verified MP4 with one final audio stream

- [ ] **Step 1: Add a short end-to-end fixture**

Create an integration fixture with:

- a valid project-level BGM sidecar,
- generated 8-second narration,
- generated 3-second loopable BGM,
- a passing subtitle synchronization report,
- one visual scene.

Assert:

```python
assert mix_report["status"] == "passed"
assert mix_report["mode"] == "bgm"
assert render_input["audioPath"].endswith("final-mix.wav")
assert ffprobe_stream_types(final_mp4) == {"video", "audio"}
```

- [ ] **Step 2: Document local BGM addition**

`docs/bgm-library.md` must show:

```text
projects/<project>/library/bgm/
├── calm-technology.mp3
└── calm-technology.bgm.json
```

Document every sidecar field, complete-override priority, online fallback,
unknown-rights warning, narration-only degradation, and the fact that source
video audio remains muted.

- [ ] **Step 3: Document workflow and reports**

Update README with:

- the automatic `bgm` stage,
- local library priority,
- provider then Agent fallback,
- `audio/bgm-selection.json`,
- `audio/bgm-mix-report.json`,
- `audio/final-mix.wav`,
- render-gate behavior.

- [ ] **Step 4: Run all verification**

Run:

```powershell
pytest -q
npm --prefix renderer test
npm --prefix renderer run typecheck
pytest tests/integration/test_bgm_mix.py tests/integration/test_bgm_workflow.py -q
git diff --check
```

Then render the integration fixture and run:

```powershell
ffmpeg -v error -i <fixture-final.mp4> -f null NUL
ffprobe -v error -show_entries stream=codec_type,codec_name -show_entries format=duration -of json <fixture-final.mp4>
```

Expected:

- all Python, Vitest, TypeScript, and integration checks pass,
- full MP4 decode prints no errors,
- one H.264 video stream and one AAC audio stream exist,
- final duration matches the audited mix,
- sibling projects and external source repositories remain unchanged.

- [ ] **Step 5: Commit Task 7**

Run:

```powershell
git add README.md docs/bgm-library.md config/bgm-search.example.json tests/integration/test_bgm_workflow.py
git commit -m "docs: document automatic BGM workflow"
```

- [ ] **Step 6: Final branch verification**

Run:

```powershell
git status --short
git log --oneline --decorate -10
```

Expected: clean `feat/automatic-bgm` branch with the BGM implementation commits
on top of the integrated MCP and subtitle-sync baseline.
