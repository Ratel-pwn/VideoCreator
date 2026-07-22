from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from .project_layout import initialize_project
from .templates import discover_templates


class CliError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str
    current_stage: str
    updated_at: str
    path: Path
    final_video: str | None = None
    sort_value: float = 0

    def as_json(self) -> dict:
        value = asdict(self)
        value["path"] = str(self.path)
        value.pop("sort_value")
        return value


def resolve_home(explicit: Path | str | None, environ: Mapping[str, str], package_root: Path) -> Path:
    value = explicit or environ.get("VIDEO_CREATOR_HOME") or package_root
    home = Path(value).expanduser().resolve()
    required = (home / "workflow.config.json", home / "templates", home / "projects")
    missing = [str(path.name) for path in required if not path.exists()]
    if missing:
        raise CliError(f"Invalid VideoCreator home {home}: missing {', '.join(missing)}")
    return home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vc", description="VideoCreator 视频制作命令")
    parser.add_argument("--home", help="VideoCreator 仓库路径")
    parser.add_argument("--config", default="workflow.config.json", help="工作流配置文件")
    parser.add_argument("--json", action="store_true", dest="global_json", help="使用 JSON 输出")
    subcommands = parser.add_subparsers(dest="command", required=True)
    templates = subcommands.add_parser("templates", help="列出可用模板")
    templates.add_argument("--json", action="store_true", help="使用 JSON 输出")

    init = subcommands.add_parser("init", help="初始化视频项目")
    init.add_argument("name", nargs="?", metavar="NAME", help="项目名称")
    init.add_argument("-t", "--template", help="模板 ID")
    init.add_argument("--title", help="视频标题")
    init.add_argument("--date", help="固定发布日期")
    init.add_argument("--non-interactive", action="store_true", help="禁止交互询问")

    chat = subcommands.add_parser("chat", help="开始新的制作任务")
    chat.add_argument("project", metavar="PROJECT", help="项目名称")
    chat.add_argument("topic", nargs="?", metavar="TOPIC", help="讨论主题")
    chat.add_argument("--run-id", help="自定义 run ID")

    imported = subcommands.add_parser("import-chat", help="从已有对话开始制作")
    imported.add_argument("project", metavar="PROJECT", help="项目名称")
    imported.add_argument("file", metavar="FILE", help="对话文件")
    imported.add_argument("--topic", help="覆盖文件名推断的主题")
    imported.add_argument("--run-id", help="自定义 run ID")

    resume = subcommands.add_parser("resume", help="继续未完成的制作任务")
    resume.add_argument("project", metavar="PROJECT", help="项目名称")
    resume.add_argument("-r", "--run", dest="run_id", help="指定 run ID")

    status = subcommands.add_parser("status", help="查看项目或 run 状态")
    status.add_argument("project", metavar="PROJECT", help="项目名称")
    status.add_argument("-r", "--run", dest="run_id", help="指定 run ID")
    status.add_argument("--json", action="store_true", help="使用 JSON 输出")

    runs = subcommands.add_parser("runs", help="列出项目历史 run")
    runs.add_argument("project", metavar="PROJECT", help="项目名称")
    runs.add_argument("--json", action="store_true", help="使用 JSON 输出")
    return parser


def _config_path(home: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (home / path).resolve()


def _load_config(path: Path) -> dict:
    if not path.is_file():
        raise CliError(f"Workflow config not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"Invalid workflow config {path}: {exc}") from exc


def _projects_root(home: Path, config: dict) -> Path:
    value = config.get("projects", {}).get("root", "projects")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (home / path).resolve()


def _project_root(projects_root: Path, name: str) -> Path:
    project = (projects_root / name).resolve()
    try:
        project.relative_to(projects_root.resolve())
    except ValueError:
        raise CliError(f"Project must stay inside projects root: {name}") from None
    if not (project / "project.json").is_file():
        raise CliError(f"Project not found: {name}")
    return project


def _timestamp(value: str, fallback: float) -> float:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


def _load_run(path: Path) -> RunSummary:
    state_path = path / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"Invalid run state {state_path}: {exc}") from exc
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"Invalid run manifest {manifest_path}: {exc}") from exc
    updated_at = str(state.get("updated_at", ""))
    fallback = state_path.stat().st_mtime
    final_video = (manifest.get("artifacts") or {}).get("final_video")
    return RunSummary(
        run_id=str(state.get("run_id") or path.name),
        status=str(state.get("status", "unknown")),
        current_stage=str(state.get("current_stage", "unknown")),
        updated_at=updated_at,
        path=path,
        final_video=str(final_video) if final_video else None,
        sort_value=_timestamp(updated_at, fallback),
    )


def list_runs(project_root: Path) -> list[RunSummary]:
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        return []
    values = [_load_run(path) for path in runs_root.iterdir() if path.is_dir()]
    return sorted(values, key=lambda item: (item.sort_value, item.run_id), reverse=True)


def select_run(project_root: Path, run_id: str | None = None, *, unfinished_only: bool = False) -> RunSummary:
    if run_id:
        path = project_root / "runs" / run_id
        if not path.is_dir():
            raise CliError(f"Run not found: {run_id}")
        value = _load_run(path)
        if unfinished_only and (value.status == "completed" or value.current_stage == "done"):
            raise CliError(f"Run is already completed: {run_id}")
        return value
    values = list_runs(project_root)
    if unfinished_only:
        values = [item for item in values if item.status != "completed" and item.current_stage != "done"]
    if not values:
        message = "No unfinished run; use vc chat to start a new version" if unfinished_only else "No runs found"
        raise CliError(message)
    return values[0]


def _prompt_template(templates: dict, input_fn: Callable[[str], str], stdout: TextIO) -> str:
    values = list(templates.values())
    for index, template in enumerate(values, 1):
        print(f"{index}. {template.raw.get('display_name', template.id)} ({template.id})", file=stdout)
    answer = input_fn("请选择模板编号：").strip()
    try:
        return values[int(answer) - 1].id
    except (ValueError, IndexError):
        raise CliError(f"Invalid template selection: {answer}") from None


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    environ: Mapping[str, str] | None = None,
    package_root: Path | None = None,
    input_fn: Callable[[str], str] = input,
) -> int:
    args = build_parser().parse_args(argv)
    home = resolve_home(args.home, environ or os.environ, package_root or Path(__file__).resolve().parents[1])
    config_path = _config_path(home, args.config)
    if args.command == "templates":
        templates = discover_templates(home / "templates")
        values = [
            {"id": item.id, "display_name": item.raw.get("display_name", item.id), "version": item.version}
            for item in templates.values()
        ]
        if args.global_json or args.json:
            print(json.dumps(values, ensure_ascii=False, indent=2), file=stdout)
        else:
            for item in values:
                print(f"{item['id']}\t{item['display_name']}\tv{item['version']}", file=stdout)
        return 0
    if args.command == "init":
        templates = discover_templates(home / "templates")
        name = (args.name or "").strip()
        template_id = (args.template or "").strip()
        if args.non_interactive and (not name or not template_id):
            raise CliError("NAME and --template are required with --non-interactive")
        if not name:
            name = input_fn("项目名称：").strip()
        if not name:
            raise CliError("Project name cannot be empty")
        if not template_id:
            template_id = _prompt_template(templates, input_fn, stdout)
        template = templates.get(template_id)
        if template is None:
            raise CliError(f"Unknown template: {template_id}")
        title = args.title
        date = args.date
        if not args.non_interactive:
            if title is None:
                title = input_fn("视频标题（可留空）：").strip()
            if date is None:
                date = input_fn("发布日期（可留空）：").strip()
        config = _load_config(config_path)
        metadata = {key: value for key, value in {"title": title, "publication_date": date}.items() if value}
        try:
            project = initialize_project(_projects_root(home, config), name, template, **metadata)
        except FileExistsError as exc:
            raise CliError(str(exc)) from exc
        print(project, file=stdout)
        return 0
    if args.command == "chat":
        import main as workflow

        topic = (args.topic or "").strip() or input_fn("本次话题：").strip()
        if not topic:
            raise CliError("Topic cannot be empty")
        context = workflow.make_run_context(home, config_path, "chat", topic, args.run_id, None, args.project, None)
        workflow.execute_from_current_stage(context)
        return 0
    if args.command == "import-chat":
        import main as workflow

        source = Path(args.file).expanduser().resolve()
        if not source.is_file():
            raise CliError(f"Conversation file not found: {source}")
        topic = (args.topic or "").strip() or source.stem
        context = workflow.make_run_context(home, config_path, "import-chat", topic, args.run_id, source, args.project, None)
        workflow.import_chat(context)
        workflow.execute_from_current_stage(context)
        return 0
    if args.command in {"resume", "status", "runs"}:
        config = _load_config(config_path)
        project = _project_root(_projects_root(home, config), args.project)
        if args.command == "resume":
            import main as workflow

            selected = select_run(project, args.run_id, unfinished_only=True)
            context = workflow.resume_context(home, config_path, selected.path)
            workflow.execute_from_current_stage(context)
            return 0
        if args.command == "runs":
            values = list_runs(project)
            if args.global_json or args.json:
                print(json.dumps([item.as_json() for item in values], ensure_ascii=False, indent=2), file=stdout)
            elif not values:
                print("暂无 run", file=stdout)
            else:
                for item in values:
                    print(f"{item.run_id}\t{item.status}\t{item.current_stage}\t{item.updated_at}", file=stdout)
            return 0
        values = list_runs(project)
        if not values:
            if args.global_json or args.json:
                print(json.dumps({"project": args.project, "runs": []}, ensure_ascii=False, indent=2), file=stdout)
            else:
                print(f"{args.project}: 暂无 run", file=stdout)
            return 0
        selected = select_run(project, args.run_id)
        project_config = json.loads((project / "project.json").read_text(encoding="utf-8-sig"))
        value = {"project": args.project, "template_id": project_config.get("template_id"), **selected.as_json()}
        if args.global_json or args.json:
            print(json.dumps(value, ensure_ascii=False, indent=2), file=stdout)
        else:
            print(f"项目: {args.project}", file=stdout)
            print(f"模板: {value['template_id']}", file=stdout)
            print(f"Run: {selected.run_id}", file=stdout)
            print(f"状态: {selected.status}", file=stdout)
            print(f"阶段: {selected.current_stage}", file=stdout)
            print(f"更新: {selected.updated_at or '-'}", file=stdout)
            if selected.final_video:
                print(f"成片: {selected.final_video}", file=stdout)
        return 0
    raise CliError(f"Unsupported command: {args.command}")


def main() -> int:
    try:
        return run_cli()
    except KeyboardInterrupt:
        print("\n用户中断", file=sys.stderr)
        return 130
    except CliError as exc:
        print(f"vc: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"vc: workflow failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
