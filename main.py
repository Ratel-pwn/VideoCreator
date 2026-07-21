#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from videocreator.asset_manifest import audit_asset_manifest
from videocreator.media import (
    clean_audio_and_srt,
    detect_trailing_silence,
    probe_media,
)
from videocreator.render_contract import build_render_input, normalize_scenes, normalize_v2_scenes
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
STAGE_CHAT = "chat"
STAGE_DRAFT = "draft"
STAGE_DRAFT_CONFIRM = "draft_confirm"
STAGE_TTS = "tts"
STAGE_TTS_CONFIRM = "tts_confirm"
STAGE_VISUAL_PLAN = "visual_plan"
STAGE_VISUAL_PLAN_CONFIRM = "visual_plan_confirm"
STAGE_VISUAL_ASSETS = "visual_assets"
STAGE_VISUAL_ASSETS_CONFIRM = "visual_assets_confirm"
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    libraries = {kind: resolve_library(repo_root, project_root, template, kind) for kind in ("style", "voice")}
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


def run_prepare(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_PREPARE)
    skill_path = ctx.template.paths["prepare"]
    skill_text = read_text(skill_path)
    feedback = ""
    while True:
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
        if not ctx.config["confirm"]["prepare"]:
            break
        decision = request_confirmation("前置准备是否可用")
        if decision == "y":
            break
        if decision == "q":
            raise SystemExit(0)
        extra = input("补充想调整的方向，直接回车则按同一主题重试： ").strip()
        feedback = f"\n\n用户补充要求：{extra}" if extra else ""
    ctx.set_stage(STAGE_CHAT, status="ready")


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
        user_text = input("你：").strip()
        if not user_text:
            continue
        if user_text == "/done":
            break
        messages.append({"role": "user", "content": user_text})
        reply = call_compatible_openai(ctx.config["llm"]["base_url"], ctx.llm_api_key, ctx.config["llm"]["model"], messages)
        messages.append({"role": "assistant", "content": reply})
        persist_chat(ctx, messages)
        print(f"\n助手：{reply}\n")
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
    feedback = ""
    while True:
        draft = generate_draft(ctx, feedback)
        raw_path = ctx.artifact_path("drafts", "draft.raw.md")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(draft + "\n", encoding="utf-8")
        ctx.register_artifact("draft_raw", raw_path)
        print("\n=== 文稿草稿 ===\n")
        print(draft)
        print()
        ctx.set_stage(STAGE_DRAFT_CONFIRM, status="awaiting_confirmation")
        if not ctx.config["confirm"]["draft"]:
            approved_path = ctx.artifact_path("drafts", "draft.approved.md")
            approved_path.write_text(draft + "\n", encoding="utf-8")
            ctx.register_artifact("draft_approved", approved_path)
            break
        decision = request_confirmation("文稿是否可用")
        if decision == "y":
            approved_path = ctx.artifact_path("drafts", "draft.approved.md")
            approved_path.write_text(draft + "\n", encoding="utf-8")
            ctx.register_artifact("draft_approved", approved_path)
            break
        if decision == "q":
            raise SystemExit(0)
        extra = input("补充修改要求，直接回车则按当前规则重写： ").strip()
        feedback = f"\n\n用户补充修改要求：{extra}" if extra else ""
    ctx.set_stage(STAGE_TTS, status="ready")

def run_tts(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_TTS)
    draft_path = Path(ctx.manifest["artifacts"]["draft_approved"])
    draft_text = strip_markdown(read_text(draft_path))
    source_text_path = ctx.run_dir / "voice_source.txt"
    source_text_path.write_text(draft_text + "\n", encoding="utf-8")
    tts_script = resolve_path(ctx.repo_root, ctx.config["tts"]["script"])
    tts_config = resolve_path(ctx.repo_root, ctx.config["tts"]["config"])
    subtitle_script = resolve_path(ctx.repo_root, ctx.config["subtitle"]["script"])
    subtitle_config = resolve_path(ctx.repo_root, ctx.config["subtitle"]["config"])
    output_audio = ctx.artifact_path("audio", f"voice.{ctx.config['tts']['output_format']}")
    subtitle_path = ctx.artifact_path("audio", "voice.srt")
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
    ]
    try:
        subprocess.run(tts_command, check=True)
        subprocess.run(subtitle_command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Audio/subtitle script failed with code {exc.returncode}") from exc
    ctx.register_artifact("voice_audio", output_audio)
    if subtitle_path.exists():
        ctx.register_artifact("voice_subtitle", subtitle_path)
    ctx.set_stage(STAGE_TTS_CONFIRM, status="awaiting_confirmation")


def confirm_tts(ctx: WorkflowContext) -> None:
    audio_path = Path(ctx.manifest["artifacts"]["voice_audio"])
    print(f"??????{audio_path}")
    if "voice_subtitle" in ctx.manifest.get("artifacts", {}):
        print(f"??????{ctx.manifest['artifacts']['voice_subtitle']}")
    if not ctx.config["confirm"]["tts"]:
        ctx.set_stage(STAGE_VISUAL_PLAN, status="ready")
        return
    decision = request_confirmation("??????")
    if decision == "y":
        ctx.set_stage(STAGE_VISUAL_PLAN, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    print("????? scripts/volc_tts_ws.config.json ?????????")
    ctx.set_stage(STAGE_TTS, status="ready")


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
    decision = request_confirmation("????????")
    if decision == "y":
        ctx.set_stage(STAGE_VISUAL_ASSETS, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
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
        ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
        return
    decision = request_confirmation("??????")
    if decision == "y":
        ctx.set_stage(STAGE_VIDEO_RENDER, status="ready")
        return
    if decision == "q":
        raise SystemExit(0)
    print("??? visual-plan.json??????????????????")
    ctx.set_stage(STAGE_VISUAL_ASSETS, status="ready")
def run_video_render(ctx: WorkflowContext) -> None:
    ctx.set_stage(STAGE_VIDEO_RENDER)
    artifacts = ctx.manifest.get("artifacts", {})
    required = ("voice_audio", "voice_subtitle", "visual_plan", "asset_manifest")
    missing = [key for key in required if not artifacts.get(key)]
    if missing:
        raise RuntimeError(f"Video render is missing artifacts: {', '.join(missing)}")

    audio_path = Path(artifacts["voice_audio"])
    subtitle_path = Path(artifacts["voice_subtitle"])
    visual_plan_path = Path(artifacts["visual_plan"])
    asset_manifest_path = Path(artifacts["asset_manifest"])
    visual_plan = load_json(visual_plan_path)
    asset_manifest = load_json(asset_manifest_path)
    audit = audit_asset_manifest(ctx.project_root, visual_plan, asset_manifest)
    if not audit.ok:
        raise RuntimeError("Asset audit failed:\n" + "\n".join(audit.errors))

    duration_ms = probe_media(audio_path).duration_ms
    spoken_end_ms = detect_trailing_silence(
        audio_path, total_duration_ms=duration_ms
    )
    if spoken_end_ms is not None and duration_ms - spoken_end_ms >= 1_000:
        render_audio, render_subtitle = clean_audio_and_srt(
            audio_path,
            subtitle_path,
            ctx.run_dir / "audio",
            subtitle_output_dir=ctx.run_dir / "subtitles",
            spoken_end_ms=spoken_end_ms,
        )
    else:
        spoken_end_ms = duration_ms
        render_audio, render_subtitle = audio_path, subtitle_path

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
    decision = request_confirmation("Approve final video")
    if decision == "y":
        cleanup_intermediate(ctx)
        ctx.set_stage(STAGE_DONE, status="completed")
        return
    if decision == "q":
        raise SystemExit(0)
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


def execute_from_current_stage(ctx: WorkflowContext) -> None:
    handlers = build_stage_handlers()
    missing = missing_stage_handlers(handlers)
    if missing:
        raise RuntimeError(f"Missing stage handlers: {', '.join(missing)}")

    while True:
        stage = ctx.state.get("current_stage")
        handler = handlers.get(stage)
        if handler is None:
            raise RuntimeError(f"Unknown stage: {stage}")
        if handler(ctx):
            break

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
