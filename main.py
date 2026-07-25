#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from videocreator.asset_manifest import audit_asset_manifest
from videocreator.bgm_audit import (
    write_bgm_mix_report,
    write_narration_only_report,
)
from videocreator.durable_io import atomic_write_json
from videocreator.bgm_library import (
    BgmLibrarySelection,
    BgmTrack,
    resolve_bgm_library,
)
from videocreator.bgm_mix import (
    BgmMixResult,
    BgmMixSettings,
    bgm_policy_hash,
    mix_bgm,
    mix_configuration_hash,
)
from videocreator.bgm_policy import BgmPolicy, load_bgm_policy
from videocreator.bgm_selection import build_bgm_query
from videocreator.bgm_workflow import (
    BgmResolution,
    BgmResolutionRequest,
    acknowledge_bgm_resolution,
    resolve_bgm_for_run,
)
from videocreator.interactions import (
    ConsoleInteractionPort,
    InteractionPort,
    InteractionRequired,
    WorkflowOutcome,
)
from videocreator.media import (
    probe_media,
)
from videocreator.render_contract import (
    build_render_input,
    ensure_bgm_mix_gate,
    normalize_scenes,
    normalize_v2_scenes,
)
from videocreator.subtitle_sync import (
    SyncThresholds,
    audit_subtitle_sync,
    sha256_file,
)
from videocreator.subtitle_repair import choose_repair, run_repair
from videocreator.project_layout import create_run, initialize_project
from videocreator.templates import (
    TemplateDefinition,
    discover_templates,
    load_template,
    resolve_library,
)
from videocreator.visual_plan import audit_visual_plan
from videocreator.workflow_state import STAGES, missing_stage_handlers


STAGE_PREPARE = "prepare"
STAGE_PREPARE_CONFIRM = "prepare_confirm"
STAGE_CHAT = "chat"
STAGE_DRAFT = "draft"
STAGE_DRAFT_CONFIRM = "draft_confirm"
STAGE_TTS = "tts"
STAGE_TTS_CONFIRM = "tts_confirm"
STAGE_SUBTITLE_SYNC = "subtitle_sync"
STAGE_VISUAL_PLAN = "visual_plan"
STAGE_VISUAL_PLAN_CONFIRM = "visual_plan_confirm"
STAGE_VISUAL_ASSETS = "visual_assets"
STAGE_VISUAL_ASSETS_CONFIRM = "visual_assets_confirm"
STAGE_BGM = "bgm"
STAGE_VIDEO_RENDER = "video_render"
STAGE_VIDEO_RENDER_CONFIRM = "video_render_confirm"
STAGE_DONE = "done"

FINAL_ARTIFACT_KEYS = {
    "prepare_note": False,
    "session_md": False,
    "session_json": False,
    "draft_raw": False,
    "draft_approved": True,
    "voice_audio": True,
    "voice_subtitle": False,
    "visual_plan": True,
    "asset_manifest": True,
    "bgm_source": False,
    "bgm_selection": True,
    "bgm_prepared": False,
    "final_mix": False,
    "bgm_mix_report": True,
    "render_input": True,
    "voice_audio_cleaned": True,
    "voice_subtitle_cleaned": True,
    "final_video": True,
    "render_report": True,
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "topic"


def normalize_project_name(value: str) -> str:
    name = value.strip()
    name = re.sub(r"^(sample[-_])", "", name, flags=re.IGNORECASE)
    name = re.sub(r"([-_](\u6587\u6848|\u4f1a\u8bdd\u8bb0\u5f55|draft|session))$", "", name, flags=re.IGNORECASE)
    return slugify(name)

def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def resolve_path(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def resolve_from(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def directory_has_files(path: Path) -> bool:
    return path.exists() and any(child.is_file() for child in path.rglob("*"))


def subtitle_to_plain_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if re.fullmatch(r"\d+", value):
            continue
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[,.]\d{3}", value):
            continue
        lines.append(value)
    return " ".join(lines).strip()


def build_style_reference(style_dir: Path, limit: int = 3, max_chars: int = 700) -> str:
    if not style_dir.exists():
        return f"Style library directory not found: {style_dir}"
    candidates = []
    for pattern in ("*.srt", "*.md", "*.txt"):
        candidates.extend(sorted(style_dir.rglob(pattern)))
    if not candidates:
        return f"No style reference files found in: {style_dir}"

    samples = []
    for path in candidates[:limit]:
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
        content = subtitle_to_plain_text(raw) if path.suffix.lower() == ".srt" else strip_markdown(raw)
        content = re.sub(r"\s+", " ", content).strip()
        if not content:
            continue
        samples.append(f"[{path.name}] {content[:max_chars]}")
    return "\n\n".join(samples)

def render_session_markdown(messages: list[dict[str, str]]) -> str:
    lines = ["# 会话记录", ""]
    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            continue
        heading = "用户" if role == "user" else "助手"
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(message.get("content", "").strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def request_confirmation(prompt: str) -> str:
    while True:
        answer = input(f"{prompt} [y/n/q]: ").strip().lower()
        if answer in {"y", "n", "q"}:
            return answer
        print("请输入 y、n 或 q")


def extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("LLM response missing choices")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()
    raise ValueError("Unsupported LLM response content format")


def call_compatible_openai(base_url: str, api_key: str, model: str, messages: list[dict[str, str]]) -> str:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = endpoint + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    return extract_message_content(json.loads(body))


@dataclass
class WorkflowContext:
    repo_root: Path
    config_path: Path
    config: dict[str, Any]
    run_id: str
    project_name: str
    run_dir: Path
    project_root_override: Path | None = None
    topic: str = ""
    mode: str = "chat"
    imported_chat: Path | None = None
    state: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    project_config: dict[str, Any] = field(default_factory=dict)
    template: TemplateDefinition | None = None
    interactions: InteractionPort = field(default_factory=ConsoleInteractionPort)
    should_cancel: Callable[[], bool] = field(default=lambda: False)

    @property
    def output_root(self) -> Path:
        projects_root = self.config.get("projects", {}).get("root") or self.config.get("output", {}).get("root") or "projects"
        return resolve_path(self.repo_root, projects_root)

    @property
    def project_root(self) -> Path:
        return self.project_root_override or (self.output_root / self.project_name)

    @property
    def global_style_library_dir(self) -> Path:
        value = self.config.get("library", {}).get("style_default_dir") or "library/style/default"
        return resolve_path(self.repo_root, value)

    @property
    def global_voice_source_file(self) -> Path:
        value = self.config.get("library", {}).get("voice_default_file") or "library/voice/default/voice.mp3"
        return resolve_path(self.repo_root, value)

    @property
    def active_style_library_dir(self) -> Path:
        if self.template:
            selected = resolve_library(self.repo_root, self.project_root, self.template, "style")
            if selected.root:
                return selected.root
        configured = self.project_config.get("style_library_dir")
        if configured:
            candidate = resolve_from(self.project_root, configured)
            if directory_has_files(candidate):
                return candidate
        fallback = self.project_root / "library" / "style"
        if directory_has_files(fallback):
            return fallback
        return self.global_style_library_dir

    @property
    def active_voice_source_file(self) -> Path:
        if self.template:
            selected = resolve_library(self.repo_root, self.project_root, self.template, "voice")
            candidates = [item.path for item in selected.files if item.path.suffix.lower() in {".mp3", ".wav", ".m4a"}]
            if candidates:
                return candidates[0]
        configured = self.project_config.get("voice_source_file")
        if configured:
            candidate = resolve_from(self.project_root, configured)
            if candidate.exists():
                return candidate
        fallback = self.project_root / "library" / "voice" / "voice.mp3"
        if fallback.exists():
            return fallback
        return self.global_voice_source_file

    @property
    def llm_api_key(self) -> str:
        env_name = self.config["llm"]["api_key_env"]
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise RuntimeError(f"Missing LLM API key in environment variable: {env_name}")
        return api_key

    def artifact_path(self, group: str, name: str) -> Path:
        group_map = {"sessions": "session", "drafts": "writing", "audio": "audio"}
        name_map = {
            "session.json": "conversation.json", "session.md": "conversation.md",
            "draft.approved.md": "script.approved.md", "voice.mp3": "narration.generated.mp3",
        }
        if name == "voice.srt":
            return self.run_dir / "subtitles" / "subtitles.aligned.srt"
        return self.run_dir / group_map.get(group, group) / name_map.get(name, name)

    def register_artifact(self, key: str, path: Path) -> None:
        self.manifest.setdefault("artifacts", {})[key] = str(path)
        self.save_manifest()

    def set_stage(self, stage: str, status: str = "in_progress", error: str | None = None) -> None:
        self.state["current_stage"] = stage
        self.state["status"] = status
        self.state["updated_at"] = now_iso()
        if error:
            self.state["last_error"] = error
        elif "last_error" in self.state:
            del self.state["last_error"]
        self.save_state()

    def save_state(self) -> None:
        save_json(self.run_dir / "state.json", self.state)

    def save_manifest(self) -> None:
        save_json(self.run_dir / "manifest.json", self.manifest)


def make_run_context(repo_root: Path, config_path: Path, mode: str, topic: str, run_id: str | None, imported_chat: Path | None, project_name_override: str | None = None, template_id: str | None = None) -> WorkflowContext:
    config = load_json(config_path)
    if not config:
        raise RuntimeError(f"Config not found or empty: {config_path}")
    stem = slugify(topic or (imported_chat.stem if imported_chat else "workflow"))
    project_name = project_name_override or normalize_project_name(topic or (imported_chat.stem if imported_chat else stem))
    actual_run_id = run_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{stem}"
    projects_root = resolve_path(repo_root, config.get("projects", {}).get("root") or config.get("output", {}).get("root") or "projects")
    project_root = projects_root / project_name
    project_config_path = project_root / "project.json"
    if project_config_path.exists():
        project_config = load_json(project_config_path)
    else:
        if not template_id:
            raise RuntimeError("Project must be initialized with a template before generation")
        template = load_template(resolve_path(repo_root, config.get("templates", {}).get("root", "templates")), template_id)
        project_root = initialize_project(projects_root, project_name, template)
        project_config = load_json(project_root / "project.json")
    selected_template_id = project_config.get("template_id")
    if not selected_template_id:
        raise RuntimeError("Project is missing template_id; migrate or initialize it first")
    template = load_template(resolve_path(repo_root, config.get("templates", {}).get("root", "templates")), selected_template_id)
    libraries = {
        **{kind: resolve_library(repo_root, project_root, template, kind) for kind in ("style", "voice")},
        "bgm": resolve_bgm_library(repo_root, project_root, template),
    }
    run_dir = project_root / "runs" / actual_run_id
    if not run_dir.exists():
        create_run(project_root, actual_run_id, template, libraries)

    state_path = run_dir / "state.json"
    manifest_path = run_dir / "manifest.json"
    state = load_json(state_path) if state_path.exists() else {
        "run_id": actual_run_id,
        "project_name": project_name,
        "mode": mode,
        "current_stage": STAGE_PREPARE if mode == "chat" else STAGE_DRAFT,
        "status": "created",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    state.update({
        "run_id": actual_run_id,
        "project_name": project_name,
        "mode": mode,
    })
    state.setdefault("current_stage", STAGE_PREPARE if mode == "chat" else STAGE_DRAFT)
    state.setdefault("status", "created")
    state.setdefault("created_at", now_iso())
    state.setdefault("updated_at", now_iso())
    manifest = load_json(manifest_path) if manifest_path.exists() else {
        "run_id": actual_run_id,
        "project_name": project_name,
        "mode": mode,
        "topic": topic,
        "created_at": now_iso(),
        "artifacts": {},
        "template": {"id": template.id, "version": template.version},
    }
    manifest.update({
        "run_id": actual_run_id,
        "project_name": project_name,
        "mode": mode,
        "topic": topic,
        "template": {"id": template.id, "version": template.version},
    })
    manifest.setdefault("created_at", now_iso())
    manifest.setdefault("artifacts", {})
    ctx = WorkflowContext(
        repo_root=repo_root,
        config_path=config_path,
        config=config,
        run_id=actual_run_id,
        project_name=project_name,
        run_dir=run_dir,
        topic=topic,
        mode=mode,
        imported_chat=imported_chat,
        state=state,
        manifest=manifest,
        project_config=project_config,
        template=template,
    )
    ctx.manifest["resources"] = {
        "style_library_dir": str(ctx.active_style_library_dir),
        "voice_source_file": str(ctx.active_voice_source_file),
    }
    ctx.save_state()
    ctx.save_manifest()
    return ctx

def load_or_init_chat_messages(ctx: WorkflowContext) -> list[dict[str, str]]:
    session_json = ctx.artifact_path("sessions", "session.json")
    if session_json.exists():
        return json.loads(session_json.read_text(encoding="utf-8"))
    system_prompt = read_text(ctx.template.paths["prepare"]).strip()
    return [{"role": "system", "content": system_prompt}]


def persist_chat(ctx: WorkflowContext, messages: list[dict[str, str]]) -> None:
    session_json = ctx.artifact_path("sessions", "session.json")
    session_md = ctx.artifact_path("sessions", "session.md")
    session_json.parent.mkdir(parents=True, exist_ok=True)
    session_json.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    session_md.write_text(render_session_markdown(messages), encoding="utf-8")
    ctx.register_artifact("session_json", session_json)
    ctx.register_artifact("session_md", session_md)


def ask_confirmation(ctx: WorkflowContext, key: str, prompt: str) -> str:
    return ctx.interactions.ask(ctx, key, prompt, "confirmation", ("y", "n", "q"))


def run_prepare(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_PREPARE)
    skill_path = ctx.template.paths["prepare"]
    skill_text = read_text(skill_path)
    feedback = ctx.state.pop("prepare_feedback", "")
    messages = [
        {"role": "system", "content": skill_text},
        {"role": "user", "content": f"主题：{ctx.topic}\n\n请输出本次话题讨论的准备提纲。{feedback}"},
    ]
    result = call_compatible_openai(ctx.config["llm"]["base_url"], ctx.llm_api_key, ctx.config["llm"]["model"], messages)
    prepare_path = ctx.artifact_path("sessions", "prepare.md")
    prepare_path.parent.mkdir(parents=True, exist_ok=True)
    prepare_path.write_text(result + "\n", encoding="utf-8")
    ctx.register_artifact("prepare_note", prepare_path)
    print("\n=== 前置准备 ===\n")
    print(result)
    print()
    next_stage = STAGE_PREPARE_CONFIRM if ctx.config["confirm"]["prepare"] else STAGE_CHAT
    ctx.set_stage(next_stage, status="ready" if next_stage == STAGE_CHAT else "awaiting_confirmation")


def confirm_prepare(ctx: WorkflowContext) -> None:
    decision = ask_confirmation(ctx, "prepare-approval", "前置准备是否可用")
    if decision == "y":
        ctx.interactions.clear(ctx, "prepare-approval", "prepare-revision")
        ctx.set_stage(STAGE_CHAT, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    extra = ctx.interactions.ask(ctx, "prepare-revision", "补充想调整的方向", "text")
    ctx.state["prepare_feedback"] = f"\n\n用户补充要求：{extra}" if extra else ""
    ctx.interactions.clear(ctx, "prepare-approval", "prepare-revision")
    ctx.set_stage(STAGE_PREPARE, status="ready")


def run_chat(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_CHAT)
    messages = load_or_init_chat_messages(ctx)
    prepare_path = ctx.artifact_path("sessions", "prepare.md")
    if prepare_path.exists() and all(m["role"] != "system" or "前置讨论提纲" not in m["content"] for m in messages):
        messages[0]["content"] += "\n\n前置讨论提纲：\n" + read_text(prepare_path)
    if len(messages) == 1 and ctx.topic:
        messages.append({"role": "user", "content": f"我们开始聊这个话题：{ctx.topic}"})
        reply = call_compatible_openai(ctx.config["llm"]["base_url"], ctx.llm_api_key, ctx.config["llm"]["model"], messages)
        messages.append({"role": "assistant", "content": reply})
        persist_chat(ctx, messages)
        print(f"\n助手：{reply}\n")

    print("输入 /done 结束聊天并进入文稿阶段。\n")
    while True:
        interaction_key = f"chat-message-{sum(item['role'] == 'user' for item in messages)}"
        user_text = ctx.interactions.ask(ctx, interaction_key, "你", "text").strip()
        if not user_text:
            ctx.interactions.clear(ctx, interaction_key)
            continue
        if user_text == "/done":
            ctx.interactions.clear(ctx, interaction_key)
            break
        messages.append({"role": "user", "content": user_text})
        reply = call_compatible_openai(ctx.config["llm"]["base_url"], ctx.llm_api_key, ctx.config["llm"]["model"], messages)
        messages.append({"role": "assistant", "content": reply})
        persist_chat(ctx, messages)
        print(f"\n助手：{reply}\n")
        ctx.interactions.clear(ctx, interaction_key)
    persist_chat(ctx, messages)
    ctx.set_stage(STAGE_DRAFT, status="ready")


def import_chat(ctx: WorkflowContext) -> None:
    assert ctx.imported_chat is not None
    ctx.set_stage(STAGE_DRAFT)
    raw = read_text(ctx.imported_chat)
    session_md = ctx.artifact_path("sessions", "session.md")
    session_json = ctx.artifact_path("sessions", "session.json")
    session_md.parent.mkdir(parents=True, exist_ok=True)
    session_md.write_text(raw, encoding="utf-8")
    session_json.write_text(json.dumps([{"role": "user", "content": raw}], ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.register_artifact("session_md", session_md)
    ctx.register_artifact("session_json", session_json)
    ctx.set_stage(STAGE_DRAFT, status="ready")


def generate_draft(ctx: WorkflowContext, feedback: str = "") -> str:
    article_skill = read_text(ctx.template.paths["writing"])
    session_md = Path(ctx.manifest["artifacts"]["session_md"])
    transcript = read_text(session_md)
    style_reference = build_style_reference(ctx.active_style_library_dir)
    messages = [
        {"role": "system", "content": article_skill},
        {
            "role": "user",
            "content": (
                f"Active style library: {ctx.active_style_library_dir}\n"
                f"Active voice source: {ctx.active_voice_source_file}\n\n"
                f"Style reference samples:\n{style_reference}\n\n"
                f"Generate an article from the following conversation. {feedback}\n\n{transcript}"
            ),
        },
    ]
    return call_compatible_openai(ctx.config["llm"]["base_url"], ctx.llm_api_key, ctx.config["llm"]["model"], messages)


def run_draft(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_DRAFT)
    feedback = ctx.state.pop("draft_feedback", "")
    draft = generate_draft(ctx, feedback)
    raw_path = ctx.artifact_path("drafts", "draft.raw.md")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(draft + "\n", encoding="utf-8")
    ctx.register_artifact("draft_raw", raw_path)
    print("\n=== 文稿草稿 ===\n")
    print(draft)
    print()
    if not ctx.config["confirm"]["draft"]:
        approve_draft(ctx)
        return
    ctx.set_stage(STAGE_DRAFT_CONFIRM, status="awaiting_confirmation")


def approve_draft(ctx: WorkflowContext) -> None:
    raw_path = Path(ctx.manifest["artifacts"]["draft_raw"])
    approved_path = ctx.artifact_path("drafts", "draft.approved.md")
    approved_path.write_text(read_text(raw_path), encoding="utf-8")
    ctx.register_artifact("draft_approved", approved_path)
    ctx.set_stage(STAGE_TTS, status="ready")


def confirm_draft(ctx: WorkflowContext) -> None:
    decision = ask_confirmation(ctx, "draft-approval", "文稿是否可用")
    if decision == "y":
        ctx.interactions.clear(ctx, "draft-approval", "draft-revision")
        approve_draft(ctx)
        return
    if decision == "q":
        raise SystemExit(0)
    extra = ctx.interactions.ask(ctx, "draft-revision", "补充修改要求", "text")
    ctx.state["draft_feedback"] = f"\n\n用户补充修改要求：{extra}" if extra else ""
    ctx.interactions.clear(ctx, "draft-approval", "draft-revision")
    ctx.set_stage(STAGE_DRAFT, status="ready")


def prepare_cloned_voice(
    ctx: WorkflowContext,
    tts_config: Path,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    source = ctx.active_voice_source_file
    if not source.is_file():
        raise RuntimeError(f"Voice source file not found: {source}")
    if not tts_config.is_file():
        raise RuntimeError(f"TTS config file not found: {tts_config}")

    config = load_json(tts_config)
    speaker_id = str(config.get("clone_speaker_id") or config.get("speaker_id") or "").strip()
    if not speaker_id:
        raise RuntimeError(f"Missing clone_speaker_id or speaker_id in TTS config: {tts_config}")

    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    resources = {
        "voice_source_file": str(source),
        "voice_source_sha256": source_sha256,
        "voice_speaker_fingerprint": hashlib.sha256(
            speaker_id.encode("utf-8")
        ).hexdigest(),
    }
    already_bound = (
        config.get("voice_source_sha256") == source_sha256
        and config.get("voice_source_speaker_id") == speaker_id
    )
    if not already_bound:
        voice_source_mode = ctx.config.get("tts", {}).get("voice_source_mode", "clone")
        if voice_source_mode == "existing_speaker":
            raise RuntimeError(
                f"Voice source is not bound to the configured existing speaker: {source}"
            )
        clone_script_value = ctx.config.get("tts", {}).get("clone_script")
        if not clone_script_value:
            raise RuntimeError("TTS clone_script is required when voice_source_mode is clone")
        clone_script = resolve_path(ctx.repo_root, clone_script_value)
        runner(
            [
                sys.executable,
                str(clone_script),
                "--config",
                str(tts_config),
                "--audio",
                str(source),
            ],
            check=True,
        )
        config["voice_source_sha256"] = source_sha256
        config["voice_source_speaker_id"] = speaker_id
        tts_config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    ctx.manifest.setdefault("resources", {}).update(resources)
    ctx.save_manifest()
    return {**resources, "cloned": not already_bound}

def run_tts(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_TTS)
    draft_path = Path(ctx.manifest["artifacts"]["draft_approved"])
    draft_text = strip_markdown(read_text(draft_path))
    source_text_path = ctx.run_dir / "audio" / "narration.txt"
    source_text_path.parent.mkdir(parents=True, exist_ok=True)
    source_text_path.write_text(draft_text + "\n", encoding="utf-8")
    tts_script = resolve_path(ctx.repo_root, ctx.config["tts"]["script"])
    tts_config = resolve_path(ctx.repo_root, ctx.config["tts"]["config"])
    subtitle_script = resolve_path(ctx.repo_root, ctx.config["subtitle"]["script"])
    subtitle_config = resolve_path(ctx.repo_root, ctx.config["subtitle"]["config"])
    if ctx.config["tts"].get("voice_source_mode"):
        prepare_cloned_voice(ctx, tts_config)
    output_audio = ctx.artifact_path("audio", f"voice.{ctx.config['tts']['output_format']}")
    subtitle_path = ctx.artifact_path("audio", "voice.srt")
    segment_manifest_path = ctx.run_dir / "audio" / "tts-segments.json"
    timing_path = ctx.run_dir / "subtitles" / "alignment-timing.json"
    alignment_report_path = ctx.run_dir / "subtitles" / "alignment-report.json"
    tts_command = [
        sys.executable,
        str(tts_script),
        "--config",
        str(tts_config),
        "--text-file",
        str(source_text_path),
        "--output",
        str(output_audio),
        "--no-subtitle",
        "--segment-manifest",
        str(segment_manifest_path),
    ]
    subtitle_command = [
        sys.executable,
        str(subtitle_script),
        "--config",
        str(subtitle_config),
        "--audio-file",
        str(output_audio),
        "--text-file",
        str(source_text_path),
        "--output-srt",
        str(subtitle_path),
        "--output-timing-json",
        str(timing_path),
        "--output-report",
        str(alignment_report_path),
    ]
    try:
        subprocess.run(tts_command, check=True)
        subprocess.run(subtitle_command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Audio/subtitle script failed with code {exc.returncode}") from exc
    ctx.register_artifact("voice_audio", output_audio)
    ctx.register_artifact("narration_text", source_text_path)
    ctx.register_artifact("tts_segment_manifest", segment_manifest_path)
    if subtitle_path.exists():
        ctx.register_artifact("voice_subtitle", subtitle_path)
    ctx.register_artifact("subtitle_alignment_timing", timing_path)
    ctx.register_artifact("subtitle_alignment_report", alignment_report_path)
    ctx.set_stage(STAGE_TTS_CONFIRM, status="awaiting_confirmation")


def confirm_tts(ctx: WorkflowContext) -> None:
    audio_path = Path(ctx.manifest["artifacts"]["voice_audio"])
    print(f"??????{audio_path}")
    if "voice_subtitle" in ctx.manifest.get("artifacts", {}):
        print(f"??????{ctx.manifest['artifacts']['voice_subtitle']}")
    if not ctx.config["confirm"]["tts"]:
        ctx.set_stage(STAGE_SUBTITLE_SYNC, status="ready")
        return
    decision = ask_confirmation(ctx, "tts-approval", "配音与字幕是否可用")
    if decision == "y":
        ctx.interactions.clear(ctx, "tts-approval")
        ctx.set_stage(STAGE_SUBTITLE_SYNC, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    ctx.interactions.clear(ctx, "tts-approval")
    print("????? scripts/volc_tts_ws.config.json ?????????")
    ctx.set_stage(STAGE_TTS, status="ready")


def ensure_subtitle_sync_gate(
    audio_path: Path,
    subtitle_path: Path,
    alignment_report_path: Path,
    audit_output_path: Path,
    config: dict[str, Any],
    segment_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if not alignment_report_path.is_file():
        raise RuntimeError(
            f"Subtitle synchronization alignment report is missing: "
            f"{alignment_report_path}"
        )
    thresholds = SyncThresholds.from_dict(config)
    result = write_subtitle_sync_audit(
        audio_path,
        subtitle_path,
        alignment_report_path,
        thresholds=thresholds,
        audit_output_path=audit_output_path,
        segment_manifest_path=segment_manifest_path,
    )
    if result["status"] != "passed":
        codes = ", ".join(
            sorted({item["code"] for item in result.get("findings", [])})
        )
        raise RuntimeError(
            f"Subtitle synchronization audit failed ({codes}): "
            f"{audit_output_path}"
        )
    return result


def write_subtitle_sync_audit(
    audio_path: Path,
    subtitle_path: Path,
    alignment_report_path: Path,
    *,
    thresholds: SyncThresholds,
    audit_output_path: Path,
    segment_manifest_path: Path | None = None,
) -> dict[str, Any]:
    result = audit_subtitle_sync(
        audio_path,
        subtitle_path,
        alignment_report_path,
        thresholds=thresholds,
        segment_manifest=(
            segment_manifest_path
            if segment_manifest_path and segment_manifest_path.is_file()
            else None
        ),
    )
    audit_output_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(audit_output_path, result)
    return result


def bind_render_inputs_to_sync_audit(
    result: dict[str, Any],
    *,
    audio_path: Path,
    subtitle_path: Path,
    audit_output_path: Path,
) -> dict[str, Any]:
    source_inputs = result.get("inputs") or {}
    render_audio_hash = sha256_file(audio_path)
    render_srt_hash = sha256_file(subtitle_path)
    result["render_inputs"] = {
        "audio_path": str(audio_path),
        "audio_sha256": render_audio_hash,
        "srt_path": str(subtitle_path),
        "srt_sha256": render_srt_hash,
        "derived_from_audited_inputs": (
            source_inputs.get("audio_sha256") != render_audio_hash
            or source_inputs.get("srt_sha256") != render_srt_hash
        ),
    }
    save_json(audit_output_path, result)
    return result


def _run_alignment(ctx: WorkflowContext, *, stronger: bool = False) -> str:
    artifacts = ctx.manifest["artifacts"]
    command = [
        sys.executable,
        str(resolve_path(ctx.repo_root, ctx.config["subtitle"]["script"])),
        "--config",
        str(resolve_path(ctx.repo_root, ctx.config["subtitle"]["config"])),
        "--audio-file",
        str(artifacts["voice_audio"]),
        "--text-file",
        str(artifacts["narration_text"]),
        "--output-srt",
        str(artifacts["voice_subtitle"]),
        "--output-timing-json",
        str(artifacts["subtitle_alignment_timing"]),
        "--output-report",
        str(artifacts["subtitle_alignment_report"]),
    ]
    if stronger:
        command.extend(["--beam-size", "8"])
    subprocess.run(command, check=True)
    return "alignment rebuilt"


def _reassemble_tts_audio(ctx: WorkflowContext, _target: str) -> str:
    from scripts.volc_tts_ws import load_config as load_tts_manifest
    from scripts.volc_tts_ws import write_audio_chunks, write_tts_segment_manifest

    artifacts = ctx.manifest["artifacts"]
    manifest_path = Path(artifacts["tts_segment_manifest"])
    manifest = load_tts_manifest(manifest_path)
    segments = sorted(
        manifest.get("segments") or [],
        key=lambda item: int(item.get("ordinal", 0)),
    )
    audio = [Path(item["audio_path"]).read_bytes() for item in segments]
    output = Path(artifacts["voice_audio"])
    write_audio_chunks(
        audio,
        output,
        audio_format=ctx.config["tts"]["output_format"],
    )
    write_tts_segment_manifest(
        manifest_path,
        segments=segments,
        output=output,
        speaker_fingerprint=str(manifest.get("speaker_fingerprint", "")),
    )
    _run_alignment(ctx)
    return "TTS segments reassembled"


def _regenerate_tts_segment(ctx: WorkflowContext, target: str) -> str:
    if not target.startswith("segment-"):
        raise RuntimeError(
            f"Cannot localize TTS regeneration to a declared segment: {target}"
        )
    artifacts = ctx.manifest["artifacts"]
    command = [
        sys.executable,
        str(resolve_path(ctx.repo_root, ctx.config["tts"]["script"])),
        "--config",
        str(resolve_path(ctx.repo_root, ctx.config["tts"]["config"])),
        "--text-file",
        str(artifacts["narration_text"]),
        "--output",
        str(artifacts["voice_audio"]),
        "--no-subtitle",
        "--segment-manifest",
        str(artifacts["tts_segment_manifest"]),
        "--repair-segment",
        target,
    ]
    subprocess.run(command, check=True)
    _run_alignment(ctx)
    return f"regenerated {target}"


def _repair_subtitle_sync(ctx: WorkflowContext, audit_path: Path) -> dict[str, Any]:
    history_path = ctx.run_dir / "review" / "subtitle-sync-repairs.json"
    history_payload = load_json(history_path) if history_path.exists() else {}
    history = history_payload.get("by_fingerprint", {})
    attempts = history_payload.get("attempts", [])
    artifacts = ctx.manifest["artifacts"]
    thresholds = SyncThresholds.from_dict(ctx.config.get("subtitle_sync", {}))
    segment_manifest = (
        Path(artifacts["tts_segment_manifest"])
        if artifacts.get("tts_segment_manifest")
        else None
    )
    for _ in range(8):
        audit = load_json(audit_path)
        if audit.get("status") == "passed":
            return audit
        action = choose_repair(audit, history)
        if action is None:
            break
        handlers = {
            "rebuild_alignment": lambda _target: _run_alignment(ctx),
            "realign_range": lambda _target: _run_alignment(ctx),
            "recognize_window": lambda _target: _run_alignment(
                ctx, stronger=True
            ),
            "reassemble_audio": lambda target: _reassemble_tts_audio(
                ctx, target
            ),
            "regenerate_segment": lambda target: _regenerate_tts_segment(
                ctx, target
            ),
        }
        attempt = run_repair(action, handlers=handlers)
        history[action["fingerprint"]] = attempt
        attempts.append(attempt)
        save_json(history_path, {
            "schema_version": 1,
            "attempts": attempts,
            "by_fingerprint": history,
        })
        if attempt["status"] != "completed":
            break
        write_subtitle_sync_audit(
            Path(artifacts["voice_audio"]),
            Path(artifacts["voice_subtitle"]),
            Path(artifacts["subtitle_alignment_report"]),
            thresholds=thresholds,
            audit_output_path=audit_path,
            segment_manifest_path=segment_manifest,
        )
    return load_json(audit_path)


def audit_subtitles_for_context(
    ctx: WorkflowContext,
    allow_repair: bool,
) -> dict[str, Any]:
    artifacts = ctx.manifest.get("artifacts", {})
    audit_path = ctx.run_dir / "review" / "subtitle-sync-audit.json"
    audio_path = Path(artifacts["voice_audio"])
    subtitle_path = Path(artifacts["voice_subtitle"])
    report_path = Path(artifacts["subtitle_alignment_report"])
    segment_manifest = (
        Path(artifacts["tts_segment_manifest"])
        if artifacts.get("tts_segment_manifest")
        else None
    )
    thresholds = SyncThresholds.from_dict(ctx.config.get("subtitle_sync", {}))
    result = write_subtitle_sync_audit(
        audio_path,
        subtitle_path,
        report_path,
        thresholds=thresholds,
        audit_output_path=audit_path,
        segment_manifest_path=segment_manifest,
    )
    if result["status"] != "passed" and allow_repair:
        result = _repair_subtitle_sync(ctx, audit_path)
    ctx.register_artifact("subtitle_sync_audit", audit_path)
    return result


def run_subtitle_sync(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_SUBTITLE_SYNC)
    audit_path = ctx.run_dir / "review" / "subtitle-sync-audit.json"
    result = audit_subtitles_for_context(ctx, allow_repair=True)
    if result["status"] == "passed":
        ctx.set_stage(STAGE_VISUAL_PLAN, status="ready")
        return
    codes = ", ".join(
        sorted({item["code"] for item in result.get("findings", [])})
    )
    ctx.set_stage(
        STAGE_SUBTITLE_SYNC,
        status="blocked",
        error=f"Subtitle synchronization remains unresolved: {codes}",
    )
    raise RuntimeError(
        f"Subtitle synchronization remains unresolved ({codes}): {audit_path}"
    )


def detect_topic_category(ctx: WorkflowContext) -> str:
    default_category = ctx.config.get("visual_plan", {}).get("default_category", "general")
    lowered = ctx.topic.lower()
    if any(token in lowered for token in ["science", "??", "??", "??", "biology", "??"]):
        return "science"
    if any(token in lowered for token in ["technology", "tech", "??", "??", "ai", "????"]):
        return "technology"
    if any(token in lowered for token in ["history", "humanities", "??", "??", "??", "??"]):
        return "humanities"
    return default_category


def run_visual_plan(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_VISUAL_PLAN)
    visual_cfg = ctx.config["visual_plan"]
    script_path = resolve_path(ctx.repo_root, visual_cfg["script"])
    subtitle_path = Path(ctx.manifest["artifacts"]["voice_subtitle"])
    draft_path = Path(ctx.manifest["artifacts"]["draft_approved"])
    output_path = ctx.run_dir / "visual" / "visual-plan.json"
    command = [
        sys.executable,
        str(script_path),
        "--workflow-config", str(ctx.config_path),
        "--srt-file", str(subtitle_path),
        "--draft-file", str(draft_path),
        "--topic", ctx.topic,
        "--category", detect_topic_category(ctx),
        "--output", str(output_path),
        "--skill-file", str(ctx.template.paths["visual_planning"]),
        "--pacing-file", str(ctx.template.paths["pacing"]),
        "--subtitle-policy-file", str(ctx.template.paths["subtitle"]),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Visual plan script failed with code {exc.returncode}") from exc
    audit = audit_visual_plan(
        load_json(output_path),
        load_json(ctx.template.paths["pacing"]),
        load_json(ctx.template.paths["subtitle"]),
    )
    audit_path = ctx.run_dir / "visual" / "visual-plan-audit.json"
    save_json(audit_path, audit)
    ctx.register_artifact("visual_plan", output_path)
    ctx.register_artifact("visual_plan_audit", audit_path)
    if not audit["ok"]:
        raise RuntimeError(f"Visual plan audit failed with {len(audit['errors'])} error(s): {audit_path}")
    ctx.set_stage(STAGE_VISUAL_PLAN_CONFIRM, status="awaiting_confirmation")


def confirm_visual_plan(ctx: WorkflowContext) -> None:
    print(f"????????{ctx.manifest['artifacts']['visual_plan']}")
    if not ctx.config["confirm"].get("visual_plan", True):
        ctx.set_stage(STAGE_VISUAL_ASSETS, status="ready")
        return
    decision = ask_confirmation(ctx, "visual-plan-approval", "视觉规划是否可用")
    if decision == "y":
        ctx.interactions.clear(ctx, "visual-plan-approval")
        ctx.set_stage(STAGE_VISUAL_ASSETS, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    ctx.interactions.clear(ctx, "visual-plan-approval")
    print("请修改当前模板的视觉规划声明后重新生成")
    ctx.set_stage(STAGE_VISUAL_PLAN, status="ready")


def run_visual_assets(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_VISUAL_ASSETS)
    visual_cfg = ctx.config["visual_assets"]
    jimeng_cfg = ctx.config["jimeng"]
    script_path = resolve_path(ctx.repo_root, visual_cfg["script"])
    config_path = resolve_path(ctx.repo_root, visual_cfg["config"])
    image_script = resolve_path(ctx.repo_root, jimeng_cfg["image_script"])
    video_script = resolve_path(ctx.repo_root, jimeng_cfg["video_script"])
    jimeng_config = resolve_path(ctx.repo_root, jimeng_cfg["client_config"])
    plan_path = Path(ctx.manifest["artifacts"]["visual_plan"])
    manifest_path = ctx.run_dir / "visual" / visual_cfg.get("manifest_name", "asset-manifest.json")
    command = [
        sys.executable,
        str(script_path),
        "--plan-file", str(plan_path),
        "--config", str(config_path),
        "--output-dir", str(ctx.project_root / "media"),
        "--manifest-file", str(manifest_path),
        "--image-script", str(image_script),
        "--video-script", str(video_script),
        "--jimeng-config", str(jimeng_config),
    ]
    env = os.environ.copy()
    env["PYTHON_EXECUTABLE"] = sys.executable
    try:
        subprocess.run(command, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Visual asset build script failed with code {exc.returncode}") from exc
    ctx.register_artifact("asset_manifest", manifest_path)
    ctx.set_stage(STAGE_VISUAL_ASSETS_CONFIRM, status="awaiting_confirmation")


def confirm_visual_assets(ctx: WorkflowContext) -> None:
    print(f"????????{ctx.manifest['artifacts']['asset_manifest']}")
    if not ctx.config["confirm"].get("assets", True):
        ctx.set_stage(STAGE_BGM, status="ready")
        return
    decision = ask_confirmation(ctx, "visual-assets-approval", "视觉素材是否可用")
    if decision == "y":
        ctx.interactions.clear(ctx, "visual-assets-approval")
        ctx.set_stage(STAGE_BGM, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    ctx.interactions.clear(ctx, "visual-assets-approval")
    print("??? visual-plan.json??????????????????")
    ctx.set_stage(STAGE_VISUAL_ASSETS, status="ready")


def ensure_current_subtitle_sync_audit(
    ctx: WorkflowContext,
) -> dict[str, Any]:
    result = audit_subtitles_for_context(ctx, allow_repair=False)
    if result.get("status") != "passed":
        codes = ", ".join(
            sorted(
                {
                    str(item.get("code", "unknown"))
                    for item in result.get("findings", ())
                }
            )
        )
        raise RuntimeError(
            "BGM stage requires a passing narration/subtitle audit "
            f"({codes})"
        )
    return result


def _effective_bgm_policy(ctx: WorkflowContext) -> BgmPolicy:
    if ctx.template is None:
        raise RuntimeError("BGM stage requires a selected template")
    snapshot_path = ctx.run_dir / "inputs" / "template.snapshot.json"
    snapshot = load_json(snapshot_path) if snapshot_path.is_file() else {}
    frozen = snapshot.get("bgm_policy")
    if not isinstance(frozen, dict):
        selection_path = ctx.run_dir / "audio" / "bgm-selection.json"
        selection = load_json(selection_path) if selection_path.is_file() else {}
        recorded = selection.get("policy")
        if isinstance(recorded, dict):
            content = recorded
        else:
            content = asdict(load_bgm_policy(ctx.template))
        source = ctx.template.paths.get("bgm")
        source_path = (
            source.relative_to(ctx.template.root).as_posix()
            if source is not None
            else None
        )
        source_hash = sha256_file(source) if source is not None else None
        frozen = {
            "source_path": source_path,
            "source_sha256": source_hash,
            "content": content,
            "content_sha256": _stable_payload_hash(content),
        }
        snapshot["bgm_policy"] = frozen
        if source_path is not None:
            snapshot.setdefault("files", {})[source_path] = source_hash
        save_json(snapshot_path, snapshot)
    content = frozen.get("content")
    if not isinstance(content, dict):
        raise RuntimeError("bgm_policy_snapshot_invalid")
    if frozen.get("content_sha256") != _stable_payload_hash(content):
        raise RuntimeError("bgm_policy_snapshot_mismatch")
    source_path = frozen.get("source_path")
    if source_path is not None:
        if frozen.get("source_sha256") != snapshot.get("files", {}).get(source_path):
            raise RuntimeError("bgm_policy_snapshot_mismatch")
    policy = BgmPolicy.from_dict(content)
    enabled = bool(ctx.config.get("bgm", {}).get("enabled", True))
    return replace(policy, enabled=policy.enabled and enabled)


def _stable_payload_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _workflow_bgm_config_hash(ctx: WorkflowContext) -> str:
    return _stable_payload_hash(ctx.config.get("bgm", {}))


def _effective_bgm_policy_hash(ctx: WorkflowContext) -> str:
    return _stable_payload_hash(asdict(_effective_bgm_policy(ctx)))


def bgm_mix_settings_for_context(ctx: WorkflowContext) -> BgmMixSettings:
    config = ctx.config.get("bgm", {})
    target_lufs = float(config.get("final_lufs", -16.0))
    tolerance = float(config.get("lufs_tolerance", 2.0))
    true_peak = float(config.get("true_peak_dbtp", -1.5))
    duration_tolerance = int(config.get("max_duration_delta_ms", 100))
    crossfade = int(config.get("crossfade_ms", 1500))
    if not all(
        math.isfinite(value)
        for value in (target_lufs, tolerance, true_peak)
    ):
        raise ValueError("BGM loudness settings must be finite")
    if tolerance < 0:
        raise ValueError("BGM LUFS tolerance must be non-negative")
    if duration_tolerance < 0:
        raise ValueError("BGM duration tolerance must be non-negative")
    if crossfade <= 0:
        raise ValueError("BGM crossfade must be positive")
    return BgmMixSettings(
        crossfade_ms=crossfade,
        target_lufs=target_lufs,
        target_true_peak_dbtp=true_peak,
        min_lufs=target_lufs - tolerance,
        max_lufs=target_lufs + tolerance,
        max_true_peak_dbtp=true_peak,
        duration_tolerance_ms=duration_tolerance,
    )


def _load_bgm_search_config(ctx: WorkflowContext) -> dict[str, Any]:
    value = str(
        ctx.config.get("bgm", {}).get(
            "search_config",
            "config/bgm-search.local.json",
        )
    )
    configured = resolve_path(ctx.repo_root, value)
    candidates = [configured]
    if configured.name.endswith(".local.json"):
        candidates.append(
            configured.with_name(
                configured.name.removesuffix(".local.json") + ".example.json"
            )
        )
    candidates.append(ctx.repo_root / "config" / "bgm-search.example.json")
    for candidate in candidates:
        if candidate.is_file():
            payload = load_json(candidate)
            if not isinstance(payload, dict):
                raise RuntimeError(f"Invalid BGM search config: {candidate}")
            return payload
    return {}


def ensure_bgm_library_snapshot(
    ctx: WorkflowContext,
    library: BgmLibrarySelection,
) -> None:
    snapshot_path = ctx.run_dir / "inputs" / "library.snapshot.json"
    snapshot = load_json(snapshot_path) if snapshot_path.is_file() else {}
    expected = snapshot.get("bgm")
    if not isinstance(expected, dict):
        if library.tracks:
            raise RuntimeError("bgm_library_snapshot_mismatch")
        return
    if expected.get("level") != library.level:
        raise RuntimeError("bgm_library_snapshot_mismatch")

    def normalized_path(value: Any) -> str:
        return str(Path(str(value)).resolve()).casefold()

    expected_files = []
    for item in expected.get("files", ()):
        if not isinstance(item, dict):
            raise RuntimeError("bgm_library_snapshot_mismatch")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError("bgm_library_snapshot_mismatch")
        expected_files.append(
            (
                normalized_path(item.get("path")),
                item.get("sha256"),
                normalized_path(metadata.get("path")),
                metadata.get("sha256"),
            )
        )
    actual_files = [
        (
            normalized_path(track.path),
            track.sha256,
            normalized_path(track.metadata_path),
            track.metadata_sha256,
        )
        for track in library.tracks
    ]
    if sorted(expected_files) != sorted(actual_files):
        raise RuntimeError("bgm_library_snapshot_mismatch")


def _bgm_request_for_context(
    ctx: WorkflowContext,
) -> tuple[BgmResolutionRequest, tuple[str, ...]]:
    if ctx.template is None:
        raise RuntimeError("BGM stage requires a selected template")
    artifacts = ctx.manifest.get("artifacts", {})
    approved_path = artifacts.get("draft_approved")
    approved_text = (
        read_text(Path(approved_path))
        if approved_path and Path(approved_path).is_file()
        else ""
    )
    policy = _effective_bgm_policy(ctx)
    if policy.enabled:
        library = resolve_bgm_library(
            ctx.repo_root,
            ctx.project_root,
            ctx.template,
        )
        ensure_bgm_library_snapshot(ctx, library)
        provider_config = _load_bgm_search_config(ctx)
    else:
        library = BgmLibrarySelection("none", None, (), ())
        provider_config = {}
    query = build_bgm_query(
        str(ctx.project_config.get("title", "")),
        ctx.topic,
        approved_text,
        ctx.template.id,
        policy,
    )
    request = BgmResolutionRequest(
        context=ctx,
        local_tracks=library.tracks,
        query=query,
        policy=policy,
        provider_config=provider_config,
        download_dir=ctx.run_dir / "audio" / "bgm-downloads",
        max_agent_candidates=int(
            ctx.config.get("bgm", {}).get("max_agent_candidates", 20)
        ),
        max_agent_response_bytes=int(
            ctx.config.get("bgm", {}).get("max_agent_response_bytes", 200000)
        ),
    )
    return request, library.warnings


def resolve_bgm_for_context(ctx: WorkflowContext) -> BgmResolution:
    request, library_warnings = _bgm_request_for_context(ctx)
    setattr(ctx, "_bgm_resolution_request", request)
    setattr(ctx, "_bgm_library_warnings", library_warnings)
    resolution = resolve_bgm_for_run(request, ctx.interactions)
    warnings = tuple(
        dict.fromkeys((*library_warnings, *resolution.warnings))
    )
    return replace(resolution, warnings=warnings)


def acknowledge_bgm_for_context(
    ctx: WorkflowContext,
    resolution: BgmResolution,
) -> None:
    request = getattr(ctx, "_bgm_resolution_request", None)
    if not isinstance(request, BgmResolutionRequest):
        request, _warnings = _bgm_request_for_context(ctx)
    acknowledge_bgm_resolution(
        request,
        ctx.interactions,
        resolution.resolution_id,
    )


def _copy_frozen_file(source: Path, destination: Path, expected_hash: str) -> None:
    if not source.is_file() or sha256_file(source) != expected_hash:
        raise RuntimeError(f"BGM source changed before freezing: {source}")
    if destination.is_file() and sha256_file(destination) == expected_hash:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copyfile(source, temporary)
        if sha256_file(temporary) != expected_hash:
            raise RuntimeError("Frozen BGM copy hash mismatch")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def freeze_bgm_source(ctx: WorkflowContext, track: BgmTrack) -> BgmTrack:
    suffix = track.path.suffix.casefold()
    if not suffix:
        raise RuntimeError("Selected BGM source has no file extension")
    audio_path = ctx.run_dir / "audio" / f"bgm.source{suffix}"
    _copy_frozen_file(track.path, audio_path, track.sha256)
    metadata_path = ctx.run_dir / "audio" / "bgm.source.bgm.json"
    if track.metadata_path.resolve() == track.path.resolve():
        save_json(
            metadata_path,
            {
                "schema_version": 1,
                "id": track.id,
                "title": track.title,
                "creator": track.creator,
                "source_url": track.source_url,
                "license": track.license,
                "rights_status": track.rights_status,
                "subjects": list(track.subjects),
                "moods": list(track.moods),
                "energy": track.energy,
                "tempo_bpm": track.tempo_bpm,
                "instrumental": track.instrumental,
                "template_tags": list(track.template_tags),
                "avoid_for": list(track.avoid_for),
                "preferred_start_ms": track.preferred_start_ms,
                "loopable": track.loopable,
            },
        )
        metadata_hash = sha256_file(metadata_path)
    else:
        _copy_frozen_file(
            track.metadata_path,
            metadata_path,
            track.metadata_sha256,
        )
        metadata_hash = track.metadata_sha256
    return replace(
        track,
        path=audio_path,
        metadata_path=metadata_path,
        sha256=sha256_file(audio_path),
        metadata_sha256=metadata_hash,
    )


def _bgm_selection_payload(
    ctx: WorkflowContext,
    resolution: BgmResolution,
    track: BgmTrack | None,
) -> dict[str, Any]:
    request = getattr(ctx, "_bgm_resolution_request", None)
    library_warnings = getattr(ctx, "_bgm_library_warnings", ())
    if not isinstance(request, BgmResolutionRequest):
        request, library_warnings = _bgm_request_for_context(ctx)
    return {
        "schema_version": 1,
        "resolution_id": resolution.resolution_id,
        "request_fingerprint": resolution.request_fingerprint,
        "mode": resolution.mode,
        "source": resolution.source,
        "workflow_config_sha256": _workflow_bgm_config_hash(ctx),
        "effective_policy_sha256": _effective_bgm_policy_hash(ctx),
        "query": asdict(request.query),
        "policy": asdict(request.policy),
        "track": (
            {
                "id": track.id,
                "title": track.title,
                "path": str(track.path),
                "sha256": track.sha256,
                "metadata_path": str(track.metadata_path),
                "metadata_sha256": track.metadata_sha256,
                "level": track.level,
                "creator": track.creator,
                "source_url": track.source_url,
                "license": track.license,
                "rights_status": track.rights_status,
            }
            if track is not None
            else None
        ),
        "scores": [asdict(score) for score in resolution.scores],
        "warnings": list(
            dict.fromkeys((*library_warnings, *resolution.warnings))
        ),
    }


def _write_bgm_selection(
    ctx: WorkflowContext,
    resolution: BgmResolution,
    track: BgmTrack | None,
) -> Path:
    path = ctx.run_dir / "audio" / "bgm-selection.json"
    save_json(path, _bgm_selection_payload(ctx, resolution, track))
    ctx.register_artifact("bgm_selection", path)
    return path


def register_bgm_artifacts(
    ctx: WorkflowContext,
    result: BgmMixResult,
) -> None:
    ctx.register_artifact("bgm_source", result.bgm.path)
    if result.bgm.metadata_path != result.bgm.path:
        ctx.register_artifact("bgm_source_metadata", result.bgm.metadata_path)
    ctx.register_artifact("bgm_prepared", result.prepared_bgm_path)
    ctx.register_artifact("final_mix", result.mix_path)


def _bind_bgm_report_to_workflow(
    ctx: WorkflowContext,
    resolution: BgmResolution,
    report_path: Path,
) -> dict[str, Any]:
    selection_path = Path(ctx.manifest["artifacts"]["bgm_selection"])
    report = load_json(report_path)
    if report.get("mode") == "bgm":
        if report.get("configuration_sha256") != mix_configuration_hash(
            bgm_mix_settings_for_context(ctx)
        ):
            raise RuntimeError("bgm_mix_configuration_mismatch")
        if report.get("policy_sha256") != bgm_policy_hash(
            _effective_bgm_policy(ctx)
        ):
            raise RuntimeError("bgm_mix_policy_mismatch")
    report["workflow"] = {
        "resolution_id": resolution.resolution_id,
        "request_fingerprint": resolution.request_fingerprint,
        "workflow_config_sha256": _workflow_bgm_config_hash(ctx),
        "effective_policy_sha256": _effective_bgm_policy_hash(ctx),
        "selection_sha256": sha256_file(selection_path),
    }
    save_json(report_path, report)
    return report


def _record_bgm_lineage(
    ctx: WorkflowContext,
    resolution: BgmResolution,
    render_audio: Path,
    report_path: Path,
) -> None:
    ctx.manifest.setdefault("lineage", {})["bgm"] = {
        "resolution_id": resolution.resolution_id,
        "request_fingerprint": resolution.request_fingerprint,
        "mode": resolution.mode,
        "source": resolution.source,
        "narration": ctx.manifest["artifacts"]["voice_audio"],
        "subtitle_sync_audit": ctx.manifest["artifacts"].get(
            "subtitle_sync_audit"
        ),
        "selection": ctx.manifest["artifacts"]["bgm_selection"],
        "render_audio": str(render_audio),
        "render_audio_sha256": sha256_file(render_audio),
        "mix_report": str(report_path),
        "mix_report_sha256": sha256_file(report_path),
    }
    ctx.save_manifest()


def _reuse_current_bgm_outputs(
    ctx: WorkflowContext,
    resolution: BgmResolution,
    report_path: Path,
) -> bool:
    selection_path = ctx.run_dir / "audio" / "bgm-selection.json"
    if not selection_path.is_file() or not report_path.is_file():
        return False
    selection = load_json(selection_path)
    expected_config = _workflow_bgm_config_hash(ctx)
    expected_policy = _effective_bgm_policy_hash(ctx)
    if (
        selection.get("resolution_id") != resolution.resolution_id
        or selection.get("request_fingerprint")
        != resolution.request_fingerprint
        or selection.get("workflow_config_sha256") != expected_config
        or selection.get("effective_policy_sha256") != expected_policy
    ):
        return False
    report = load_json(report_path)
    workflow = report.get("workflow")
    if not isinstance(workflow, dict):
        return False
    if (
        workflow.get("resolution_id") != resolution.resolution_id
        or workflow.get("request_fingerprint")
        != resolution.request_fingerprint
        or workflow.get("workflow_config_sha256") != expected_config
        or workflow.get("effective_policy_sha256") != expected_policy
        or workflow.get("selection_sha256") != sha256_file(selection_path)
    ):
        return False

    ctx.register_artifact("bgm_selection", selection_path)
    ctx.register_artifact("bgm_mix_report", report_path)
    if resolution.mode == "narration_only":
        render_audio = Path(ctx.manifest["artifacts"]["voice_audio"])
    elif resolution.mode == "bgm":
        track = selection.get("track")
        if not isinstance(track, dict) or not isinstance(track.get("path"), str):
            return False
        source_path = Path(track["path"])
        prepared_path = ctx.run_dir / "audio" / "bgm.prepared.wav"
        render_audio = ctx.run_dir / "audio" / "final-mix.wav"
        ctx.register_artifact("bgm_source", source_path)
        metadata_path = track.get("metadata_path")
        if (
            isinstance(metadata_path, str)
            and Path(metadata_path) != source_path
        ):
            ctx.register_artifact(
                "bgm_source_metadata",
                Path(metadata_path),
            )
        ctx.register_artifact("bgm_prepared", prepared_path)
        ctx.register_artifact("final_mix", render_audio)
    else:
        return False

    ensure_bgm_mix_gate(render_audio, report_path)
    if isinstance(ctx.manifest.get("lineage", {}).get("bgm"), dict):
        _ensure_context_bgm_lineage(
            ctx,
            report,
            report_path,
            render_audio,
        )
    _record_bgm_lineage(
        ctx,
        resolution,
        render_audio,
        report_path,
    )
    acknowledge_bgm_for_context(ctx, resolution)
    ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
    return True


def _clear_selected_bgm_artifacts(ctx: WorkflowContext) -> None:
    artifacts = ctx.manifest.setdefault("artifacts", {})
    for key in (
        "bgm_source",
        "bgm_source_metadata",
        "bgm_prepared",
        "final_mix",
    ):
        artifacts.pop(key, None)
    ctx.save_manifest()


def run_bgm(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_BGM)
    ensure_current_subtitle_sync_audit(ctx)
    artifacts = ctx.manifest.get("artifacts", {})
    narration_value = artifacts.get("voice_audio")
    if not narration_value:
        raise RuntimeError("BGM stage is missing voice_audio")
    narration = Path(narration_value)
    resolution = resolve_bgm_for_context(ctx)
    report_path = ctx.run_dir / "audio" / "bgm-mix-report.json"
    if _reuse_current_bgm_outputs(ctx, resolution, report_path):
        return

    if resolution.mode == "narration_only":
        _clear_selected_bgm_artifacts(ctx)
        _write_bgm_selection(ctx, resolution, None)
        write_narration_only_report(
            narration,
            report_path,
            resolution.warnings,
        )
        _bind_bgm_report_to_workflow(ctx, resolution, report_path)
        ensure_bgm_mix_gate(narration, report_path)
        ctx.register_artifact("bgm_mix_report", report_path)
        _record_bgm_lineage(
            ctx,
            resolution,
            narration,
            report_path,
        )
        acknowledge_bgm_for_context(ctx, resolution)
        ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
        return

    if resolution.mode != "bgm" or resolution.track is None:
        raise RuntimeError(f"Unsupported BGM resolution mode: {resolution.mode}")
    frozen_track = freeze_bgm_source(ctx, resolution.track)
    _write_bgm_selection(ctx, resolution, frozen_track)
    prepared_path = ctx.run_dir / "audio" / "bgm.prepared.wav"
    mix_path = ctx.run_dir / "audio" / "final-mix.wav"
    result = mix_bgm(
        narration,
        frozen_track,
        prepared_path,
        mix_path,
        _effective_bgm_policy(ctx),
        subprocess.run,
        settings=bgm_mix_settings_for_context(ctx),
    )
    write_bgm_mix_report(result, report_path)
    _bind_bgm_report_to_workflow(ctx, resolution, report_path)
    ensure_bgm_mix_gate(mix_path, report_path)
    register_bgm_artifacts(ctx, result)
    ctx.register_artifact("bgm_mix_report", report_path)
    _record_bgm_lineage(
        ctx,
        resolution,
        mix_path,
        report_path,
    )
    acknowledge_bgm_for_context(ctx, resolution)
    ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")


def _ensure_context_bgm_lineage(
    ctx: WorkflowContext,
    report: dict[str, Any],
    report_path: Path,
    render_audio: Path,
) -> None:
    artifacts = ctx.manifest.get("artifacts", {})
    run_root = ctx.run_dir.resolve()
    audio_root = (ctx.run_dir / "audio").resolve()

    def canonical(value: Any, *, code: str) -> Path:
        path = Path(str(value)).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise RuntimeError("bgm_artifact_outside_run") from exc
        if path.parent != audio_root:
            raise RuntimeError(code)
        return path

    expected_report = audio_root / "bgm-mix-report.json"
    if canonical(report_path, code="bgm_report_path_mismatch") != expected_report:
        raise RuntimeError("bgm_report_path_mismatch")
    if canonical(
        artifacts.get("bgm_mix_report"),
        code="bgm_report_path_mismatch",
    ) != expected_report:
        raise RuntimeError("bgm_report_path_mismatch")
    voice_value = artifacts.get("voice_audio")
    if not voice_value:
        raise RuntimeError("bgm_narration_path_mismatch")
    narration_path = canonical(
        voice_value,
        code="bgm_narration_path_mismatch",
    )
    report_narration = report.get("inputs", {}).get("narration", {})
    if not isinstance(report_narration, dict):
        raise RuntimeError("bgm_narration_path_mismatch")
    if Path(str(report_narration.get("path"))).resolve() != narration_path:
        raise RuntimeError("bgm_narration_path_mismatch")
    if report_narration.get("sha256") != sha256_file(narration_path):
        raise RuntimeError("bgm_narration_hash_mismatch")

    selection_value = artifacts.get("bgm_selection")
    if not selection_value:
        raise RuntimeError("missing_bgm_selection")
    selection_path = canonical(
        selection_value,
        code="bgm_selection_path_mismatch",
    )
    if selection_path != audio_root / "bgm-selection.json":
        raise RuntimeError("bgm_selection_path_mismatch")
    if not selection_path.is_file():
        raise RuntimeError("missing_bgm_selection")
    selection = load_json(selection_path)
    if report.get("mode") == "bgm":
        expected_metadata = audio_root / "bgm.source.bgm.json"
        manifest_metadata = canonical(
            artifacts.get("bgm_source_metadata"),
            code="bgm_metadata_path_mismatch",
        )
        if manifest_metadata != expected_metadata or not manifest_metadata.is_file():
            raise RuntimeError("bgm_metadata_path_mismatch")
        selected_track = selection.get("track")
        report_bgm = report.get("inputs", {}).get("bgm")
        if not isinstance(selected_track, dict) or not isinstance(report_bgm, dict):
            raise RuntimeError("bgm_metadata_path_mismatch")
        selection_metadata = canonical(
            selected_track.get("metadata_path"),
            code="bgm_metadata_path_mismatch",
        )
        report_metadata = canonical(
            report_bgm.get("metadata_path"),
            code="bgm_metadata_path_mismatch",
        )
        if (
            selection_metadata != expected_metadata
            or report_metadata != expected_metadata
        ):
            raise RuntimeError("bgm_metadata_path_mismatch")
        metadata_hash = sha256_file(expected_metadata)
        if (
            selected_track.get("metadata_sha256") != metadata_hash
            or report_bgm.get("metadata_sha256") != metadata_hash
        ):
            raise RuntimeError("bgm_metadata_hash_mismatch")
    workflow = report.get("workflow")
    if not isinstance(workflow, dict):
        raise RuntimeError("missing_bgm_workflow_binding")
    expected_config = _workflow_bgm_config_hash(ctx)
    expected_policy = _effective_bgm_policy_hash(ctx)
    if (
        selection.get("workflow_config_sha256") != expected_config
        or workflow.get("workflow_config_sha256") != expected_config
    ):
        raise RuntimeError("workflow_config_mismatch")
    if (
        selection.get("effective_policy_sha256") != expected_policy
        or workflow.get("effective_policy_sha256") != expected_policy
    ):
        raise RuntimeError("bgm_policy_mismatch")
    if (
        workflow.get("resolution_id") != selection.get("resolution_id")
        or workflow.get("request_fingerprint")
        != selection.get("request_fingerprint")
        or workflow.get("selection_sha256") != sha256_file(selection_path)
    ):
        raise RuntimeError("bgm_selection_report_mismatch")

    if report.get("mode") == "bgm":
        expected_prepared = audio_root / "bgm.prepared.wav"
        expected_mix = audio_root / "final-mix.wav"
        if canonical(
            artifacts.get("bgm_prepared"),
            code="bgm_prepared_path_mismatch",
        ) != expected_prepared:
            raise RuntimeError("bgm_prepared_path_mismatch")
        if canonical(
            artifacts.get("final_mix"),
            code="bgm_final_mix_path_mismatch",
        ) != expected_mix:
            raise RuntimeError("bgm_final_mix_path_mismatch")
        if render_audio.resolve() != expected_mix:
            raise RuntimeError("bgm_final_mix_path_mismatch")
        outputs = report.get("outputs", {})
        if Path(str(outputs.get("prepared_bgm", {}).get("path"))).resolve() != expected_prepared:
            raise RuntimeError("bgm_prepared_path_mismatch")
        if Path(str(outputs.get("render_audio", {}).get("path"))).resolve() != expected_mix:
            raise RuntimeError("bgm_final_mix_path_mismatch")
        source_path = canonical(
            artifacts.get("bgm_source"),
            code="bgm_source_path_mismatch",
        )
        report_source = report.get("inputs", {}).get("bgm", {})
        if Path(str(report_source.get("path"))).resolve() != source_path:
            raise RuntimeError("bgm_source_path_mismatch")
        selected_track = selection.get("track")
        if not isinstance(selected_track, dict) or Path(
            str(selected_track.get("path"))
        ).resolve() != source_path:
            raise RuntimeError("bgm_source_path_mismatch")
        raw_settings = report.get("settings")
        if not isinstance(raw_settings, dict):
            raise RuntimeError("missing_bgm_mix_settings")
        expected_settings = asdict(bgm_mix_settings_for_context(ctx))
        for key, expected in expected_settings.items():
            if raw_settings.get(key) != expected:
                raise RuntimeError("bgm_mix_configuration_mismatch")
        if report.get("configuration_sha256") != mix_configuration_hash(
            bgm_mix_settings_for_context(ctx)
        ):
            raise RuntimeError("bgm_mix_configuration_mismatch")
        if report.get("policy_sha256") != bgm_policy_hash(
            _effective_bgm_policy(ctx)
        ):
            raise RuntimeError("bgm_mix_policy_mismatch")

    lineage = ctx.manifest.get("lineage", {}).get("bgm")
    if not isinstance(lineage, dict):
        raise RuntimeError("missing_bgm_manifest_lineage")
    if (
        Path(str(lineage.get("selection"))).resolve() != selection_path
        or Path(str(lineage.get("mix_report"))).resolve() != expected_report
        or Path(str(lineage.get("narration"))).resolve() != narration_path
        or Path(str(lineage.get("render_audio"))).resolve()
        != render_audio.resolve()
    ):
        raise RuntimeError("bgm_manifest_path_mismatch")
    if (
        lineage.get("resolution_id") != workflow.get("resolution_id")
        or lineage.get("request_fingerprint")
        != workflow.get("request_fingerprint")
        or lineage.get("mix_report_sha256") != sha256_file(report_path)
        or lineage.get("render_audio_sha256") != sha256_file(render_audio)
    ):
        raise RuntimeError("bgm_manifest_lineage_mismatch")


def _ensure_current_bgm_report_paths(
    ctx: WorkflowContext,
    report: dict[str, Any],
    report_path: Path,
) -> None:
    artifacts = ctx.manifest.get("artifacts", {})
    audio_root = (ctx.run_dir / "audio").resolve()
    expected_report = audio_root / "bgm-mix-report.json"
    if (
        report_path.resolve() != expected_report
        or Path(str(artifacts.get("bgm_mix_report"))).resolve()
        != expected_report
    ):
        raise RuntimeError("bgm_report_path_mismatch")
    narration = Path(str(artifacts.get("voice_audio"))).resolve()
    try:
        narration.relative_to(audio_root)
    except ValueError as exc:
        raise RuntimeError("bgm_artifact_outside_run") from exc
    report_narration = report.get("inputs", {}).get("narration", {})
    if (
        not isinstance(report_narration, dict)
        or Path(str(report_narration.get("path"))).resolve() != narration
    ):
        raise RuntimeError("bgm_narration_path_mismatch")
    if report_narration.get("sha256") != sha256_file(narration):
        raise RuntimeError("bgm_narration_hash_mismatch")


def resolve_context_render_audio(ctx: WorkflowContext) -> Path:
    artifacts = ctx.manifest.get("artifacts", {})
    report_value = artifacts.get("bgm_mix_report")
    if not report_value:
        raise RuntimeError("Video render is missing bgm_mix_report")
    report_path = Path(report_value)
    report = load_json(report_path)
    mode = report.get("mode")
    if mode == "bgm":
        audio_value = artifacts.get("final_mix")
        if not audio_value:
            raise RuntimeError("BGM report requires final_mix")
    elif mode == "narration_only":
        audio_value = artifacts.get("voice_audio")
        if not audio_value:
            raise RuntimeError("Narration-only report requires voice_audio")
    else:
        raise RuntimeError(f"Invalid BGM report mode: {mode}")
    render_audio = Path(audio_value)
    _ensure_current_bgm_report_paths(ctx, report, report_path)
    ensure_bgm_mix_gate(render_audio, report_path)
    _ensure_context_bgm_lineage(
        ctx,
        report,
        report_path,
        render_audio,
    )
    return render_audio


def run_video_render(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_VIDEO_RENDER)
    artifacts = ctx.manifest.get("artifacts", {})
    required = (
        "voice_audio",
        "voice_subtitle",
        "visual_plan",
        "asset_manifest",
        "bgm_mix_report",
    )
    missing = [key for key in required if not artifacts.get(key)]
    if missing:
        raise RuntimeError(f"Video render is missing artifacts: {', '.join(missing)}")

    narration_path = Path(artifacts["voice_audio"])
    subtitle_path = Path(artifacts["voice_subtitle"])
    visual_plan_path = Path(artifacts["visual_plan"])
    asset_manifest_path = Path(artifacts["asset_manifest"])
    visual_plan = load_json(visual_plan_path)
    asset_manifest = load_json(asset_manifest_path)
    audit = audit_asset_manifest(ctx.project_root, visual_plan, asset_manifest)
    if not audit.ok:
        raise RuntimeError("Asset audit failed:\n" + "\n".join(audit.errors))

    sync_audit_path = ctx.run_dir / "review" / "subtitle-sync-audit.json"
    sync_result: dict[str, Any] | None = None
    alignment_report = artifacts.get("subtitle_alignment_report")
    if ctx.config.get("subtitle_sync", {}).get("enabled", True):
        if not alignment_report:
            raise RuntimeError("Subtitle synchronization alignment report is missing")
        sync_result = ensure_subtitle_sync_gate(
            narration_path,
            subtitle_path,
            Path(alignment_report),
            sync_audit_path,
            ctx.config.get("subtitle_sync", {}),
            Path(artifacts["tts_segment_manifest"])
            if artifacts.get("tts_segment_manifest")
            else None,
        )

    render_audio = resolve_context_render_audio(ctx)
    render_subtitle = subtitle_path
    spoken_end_ms = probe_media(render_audio).duration_ms

    if sync_result is not None:
        bind_render_inputs_to_sync_audit(
            sync_result,
            audio_path=narration_path,
            subtitle_path=render_subtitle,
            audit_output_path=sync_audit_path,
        )
        ctx.register_artifact("subtitle_sync_audit", sync_audit_path)

    renderer_cfg = ctx.config["renderer"]
    if int(visual_plan.get("schema_version", 1)) == 2:
        records = {record["request_id"]: record for record in asset_manifest.get("assets", [])}
        scenes = normalize_v2_scenes(visual_plan, records, fps=int(renderer_cfg["fps"]), spoken_end_ms=spoken_end_ms)
    else:
        records = {record["scene_id"]: record for record in asset_manifest.get("segments", [])}
        scenes = normalize_scenes(visual_plan, records, fps=int(renderer_cfg["fps"]), spoken_end_ms=spoken_end_ms)

    def project_relative(path: Path) -> str:
        try:
            return path.resolve().relative_to(ctx.project_root.resolve()).as_posix()
        except ValueError as exc:
            raise RuntimeError(f"Render media must be inside project root: {path}") from exc

    composition = load_json(ctx.template.paths["composition"]) if ctx.template else {}
    presentation = None
    if composition.get("frame") == "editorial-wide":
        presentation = {
            "frame_preset": composition["frame"],
            "video_title": ctx.project_config.get("title", ""),
            "publication_date": ctx.project_config.get("publication_date", ""),
            "creator_handle": composition.get("brand", ""),
        }
    render_input = build_render_input(
        video_id=slugify(ctx.project_name),
        scenes=scenes,
        audio_path=project_relative(render_audio),
        subtitle_path=project_relative(render_subtitle),
        fps=int(renderer_cfg["fps"]),
        presentation=presentation,
    )
    render_input_path = ctx.run_dir / "render" / "render-input.json"
    final_video_path = ctx.run_dir / "render" / "final.mp4"
    render_report_path = ctx.run_dir / "render" / "render-report.json"
    save_json(render_input_path, render_input)
    command = [
        sys.executable,
        str(ctx.repo_root / "scripts" / "render_video.py"),
        "--project-root",
        str(ctx.project_root),
        "--input",
        str(render_input_path),
        "--output",
        str(final_video_path),
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Remotion render failed with code {exc.returncode}") from exc

    ctx.register_artifact("render_input", render_input_path)
    ctx.register_artifact("voice_audio_cleaned", render_audio)
    ctx.register_artifact("voice_subtitle_cleaned", render_subtitle)
    ctx.register_artifact("final_video", final_video_path)
    ctx.register_artifact("render_report", render_report_path)
    ctx.set_stage(STAGE_VIDEO_RENDER_CONFIRM, status="awaiting_confirmation")


def confirm_video_render(ctx: WorkflowContext) -> None:
    print(f"Final video: {ctx.manifest['artifacts']['final_video']}")
    if not ctx.config["confirm"].get("video", True):
        cleanup_intermediate(ctx)
        ctx.set_stage(STAGE_DONE, status="completed")
        return
    decision = ask_confirmation(ctx, "video-approval", "Approve final video")
    if decision == "y":
        ctx.interactions.clear(ctx, "video-approval")
        cleanup_intermediate(ctx)
        ctx.set_stage(STAGE_DONE, status="completed")
        return
    if decision == "q":
        raise SystemExit(0)
    ctx.interactions.clear(ctx, "video-approval")
    ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")


def cleanup_intermediate(ctx: WorkflowContext) -> None:
    retain = ctx.config["retain"]
    artifacts = ctx.manifest.get("artifacts", {})
    for key, path_str in list(artifacts.items()):
        path = Path(path_str)
        keep = True
        if key in {"prepare_note", "session_md", "session_json"} and not retain["chat_log"]:
            keep = False
        if key == "draft_raw" and not retain["draft"]:
            keep = False
        if key == "voice_subtitle" and not retain["subtitle"]:
            keep = False
        if key == "voice_audio" and not retain["audio"]:
            keep = True
        if key == "draft_approved":
            keep = True
        if keep or not path.exists() or FINAL_ARTIFACT_KEYS.get(key, False):
            continue
        path.unlink(missing_ok=True)
        del artifacts[key]
    ctx.save_manifest()


def resume_context(repo_root: Path, config_path: Path, run_dir: Path) -> WorkflowContext:
    state = load_json(run_dir / "state.json")
    manifest = load_json(run_dir / "manifest.json")
    config = load_json(config_path)
    if not state or not manifest:
        raise RuntimeError(f"Run directory is missing state or manifest: {run_dir}")
    artifacts = manifest.get("artifacts", {})
    migrations = state.setdefault("migrations", {})
    if (
        state.get("current_stage") == STAGE_VIDEO_RENDER
        and state.get("status") not in {"completed", "cancelled"}
        and not artifacts.get("bgm_mix_report")
        and "task6_bgm_stage" not in migrations
    ):
        migrations["task6_bgm_stage"] = {
            "from": STAGE_VIDEO_RENDER,
            "to": STAGE_BGM,
            "migrated_at": datetime.now().isoformat(),
        }
        state["current_stage"] = STAGE_BGM
        state["status"] = "ready"
        state.pop("last_error", None)
        save_json(run_dir / "state.json", state)
    project_name = state.get("project_name") or manifest.get("project_name") or (run_dir.parent.parent.name if run_dir.parent.name == "runs" else "legacy")
    project_root = run_dir.parent.parent if run_dir.parent.name == "runs" else run_dir.parent
    project_config = load_json(project_root / "project.json")
    template = None
    if project_config.get("template_id"):
        template = load_template(resolve_path(repo_root, config.get("templates", {}).get("root", "templates")), project_config["template_id"])
    return WorkflowContext(
        repo_root=repo_root,
        config_path=config_path,
        config=config,
        run_id=state["run_id"],
        project_name=project_name,
        run_dir=run_dir,
        project_root_override=project_root,
        topic=manifest.get("topic", ""),
        mode=state.get("mode", "chat"),
        imported_chat=None,
        state=state,
        manifest=manifest,
        project_config=project_config,
        template=template,
    )


def finish_workflow(ctx: WorkflowContext) -> bool:
    print(f"流程已完成：{ctx.run_dir}")
    return True


def build_stage_handlers() -> dict[str, Any]:
    return {
        STAGE_PREPARE: run_prepare,
        STAGE_PREPARE_CONFIRM: confirm_prepare,
        STAGE_CHAT: run_chat,
        STAGE_DRAFT: run_draft,
        STAGE_DRAFT_CONFIRM: confirm_draft,
        STAGE_TTS: run_tts,
        STAGE_TTS_CONFIRM: confirm_tts,
        STAGE_SUBTITLE_SYNC: run_subtitle_sync,
        STAGE_VISUAL_PLAN: run_visual_plan,
        STAGE_VISUAL_PLAN_CONFIRM: confirm_visual_plan,
        STAGE_VISUAL_ASSETS: run_visual_assets,
        STAGE_VISUAL_ASSETS_CONFIRM: confirm_visual_assets,
        STAGE_BGM: run_bgm,
        STAGE_VIDEO_RENDER: run_video_render,
        STAGE_VIDEO_RENDER_CONFIRM: confirm_video_render,
        STAGE_DONE: finish_workflow,
    }


def execute_until_boundary(ctx: WorkflowContext) -> WorkflowOutcome:
    handlers = build_stage_handlers()
    missing = missing_stage_handlers(handlers)
    if missing:
        raise RuntimeError(f"Missing stage handlers: {', '.join(missing)}")

    while True:
        stage = ctx.state.get("current_stage")
        if ctx.should_cancel():
            ctx.set_stage(stage, status="cancelled")
            return WorkflowOutcome("cancelled")
        handler = handlers.get(stage)
        if handler is None:
            raise RuntimeError(f"Unknown stage: {stage}")
        try:
            if handler(ctx):
                return WorkflowOutcome("completed")
        except InteractionRequired as exc:
            return WorkflowOutcome("waiting_for_input", interaction=exc.interaction)
        except SystemExit:
            ctx.set_stage(stage, status="cancelled")
            return WorkflowOutcome("cancelled")
        except Exception as exc:
            ctx.set_stage(stage, status="failed", error=str(exc))
            return WorkflowOutcome("failed", error=str(exc))


def execute_from_current_stage(ctx: WorkflowContext) -> None:
    outcome = execute_until_boundary(ctx)
    if outcome.status == "failed":
        raise RuntimeError(outcome.error or "Workflow failed")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified topic -> draft -> voice -> visual workflow")
    parser.add_argument("--config", default="workflow.config.json", help="Path to workflow config")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("templates", help="List valid declarative templates")

    project = sub.add_parser("project", help="Manage projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_init = project_sub.add_parser("init", help="Initialize a project")
    project_init.add_argument("--template", required=True, help="Template id")
    project_init.add_argument("--name", required=True, help="Project directory and display name")
    project_init.add_argument("--title", default="", help="Video title")
    project_init.add_argument("--publication-date", default="", help="Fixed publication date")

    chat = sub.add_parser("chat", help="Start a new conversation workflow")
    chat.add_argument("--topic", default="", help="Topic to start with")
    chat.add_argument("--run-id", default=None, help="Optional custom run id")
    chat.add_argument("--project", default="", help="Initialized project name")
    chat.add_argument("--template", default="", help="Template for implicit project initialization")

    imported = sub.add_parser("import-chat", help="Import an existing chat record and continue from draft stage")
    imported.add_argument("chat_file", help="Path to the imported chat markdown or text file")
    imported.add_argument("--topic", default="", help="Optional topic label")
    imported.add_argument("--run-id", default=None, help="Optional custom run id")
    imported.add_argument("--project", default="", help="Initialized project name")
    imported.add_argument("--template", default="", help="Template for implicit project initialization")

    resume = sub.add_parser("resume", help="Resume an existing run from state.json")
    resume.add_argument("run_dir", help="Path to projects/<project>/runs/<run-id>")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent
    config_path = resolve_path(repo_root, args.config)

    try:
        if args.command == "templates":
            templates_root = resolve_path(repo_root, load_json(config_path).get("templates", {}).get("root", "templates"))
            for template in discover_templates(templates_root).values():
                print(f"{template.id}\t{template.raw.get('display_name', template.id)}\tv{template.version}")
        elif args.command == "project" and args.project_command == "init":
            config = load_json(config_path)
            template = load_template(resolve_path(repo_root, config.get("templates", {}).get("root", "templates")), args.template)
            projects_root = resolve_path(repo_root, config.get("projects", {}).get("root", "projects"))
            metadata = {key: value for key, value in {"title": args.title, "publication_date": args.publication_date}.items() if value}
            print(initialize_project(projects_root, args.name, template, **metadata))
        elif args.command == "chat":
            topic = args.topic.strip() or input("请输入本次话题：").strip()
            ctx = make_run_context(repo_root, config_path, "chat", topic, args.run_id, None, args.project or None, args.template or None)
            execute_from_current_stage(ctx)
        elif args.command == "import-chat":
            imported_chat = resolve_path(repo_root, args.chat_file)
            topic = args.topic.strip() or imported_chat.stem
            ctx = make_run_context(repo_root, config_path, "import-chat", topic, args.run_id, imported_chat, args.project or None, args.template or None)
            import_chat(ctx)
            execute_from_current_stage(ctx)
        elif args.command == "resume":
            run_dir = resolve_path(repo_root, args.run_dir)
            ctx = resume_context(repo_root, config_path, run_dir)
            execute_from_current_stage(ctx)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
    except KeyboardInterrupt:
        print("\n用户中断。")
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Workflow failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
