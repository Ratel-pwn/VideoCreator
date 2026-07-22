# Global `vc` CLI Design

**Status:** Approved parameter design, pending written-spec review  
**Date:** 2026-07-22

## Objective

Replace routine `python main.py ...` usage with an installable Git-style `vc` command that works from any directory. Keep the existing Python entry point compatible while making common project operations shorter, discoverable, interactive when convenient, and deterministic when scripted.

## Installation And Repository Resolution

The supported one-time installation command is:

```powershell
python -m pip install -e E:\Projects\AIGC\VideoCreator
```

`pyproject.toml` registers `vc` as a console script. Editable installation is intentional: the command always runs the current checkout without reinstalling after source changes.

The command resolves the VideoCreator home directory in this order:

1. explicit global `--home PATH`
2. `VIDEO_CREATOR_HOME` environment variable
3. repository root containing the installed `videocreator` package

The resolved home must contain `workflow.config.json`, `templates/`, and `projects/`. An invalid home fails before creating or changing project files.

Configuration resolves from explicit global `--config FILE`, otherwise `<home>/workflow.config.json`. Relative config paths are relative to the resolved home, not the caller's current directory.

## Architecture

`videocreator/cli.py` owns command parsing, interactive prompting, repository/config resolution, project/run lookup, status formatting, and dispatch into existing orchestration functions. It does not own writing, TTS, subtitle, visual planning, asset collection, or rendering behavior.

`main.py` remains a compatibility entry point. It delegates to the same CLI implementation so the two interfaces cannot drift. Existing long-form commands continue to work during migration, but README and normal usage lead with `vc`.

The CLI accepts an optional argument list in tests, returns integer exit codes, writes normal results to stdout, and writes actionable errors to stderr.

## Global Syntax

```text
vc [--home PATH] [--config FILE] [--json] <command>
```

- `--home PATH` selects the VideoCreator repository.
- `--config FILE` selects a workflow configuration file.
- `--json` requests machine-readable output for commands that produce listings or status.
- Global options may appear before the command. Command-specific options follow the command.

Interactive production commands do not convert their conversation stream to JSON. `--json` applies to `templates`, `status`, and `runs`; unsupported combinations fail clearly.

## Commands

### `vc templates`

```text
vc templates [--json]
```

Lists all valid declarative templates in stable ID order. Text output shows ID, display name, and version. JSON output is an array of objects with `id`, `display_name`, and `version`.

### `vc init`

```text
vc init [NAME] [-t TEMPLATE] [--title TITLE] [--date DATE] [--non-interactive]
```

- `NAME` is the project name and directory name.
- `-t, --template TEMPLATE` is the template ID.
- `--title TITLE` is the fixed video title.
- `--date DATE` is the fixed publication date string.
- `--non-interactive` disables all prompting.

When interactive input is allowed, omitted values behave as follows:

- missing `NAME`: prompt for a non-empty project name
- missing template: print numbered valid templates and prompt for one selection
- missing title: prompt and allow an empty value
- missing date: prompt and allow an empty value

When `--non-interactive` is set, `NAME` and template are required; title and date remain optional. Unknown templates and existing project paths fail without overwriting anything.

Example:

```powershell
vc init "货币的起源" -t chaos-museum --title "货币是如何被发明出来的？" --date 2026.07.22
```

### `vc chat`

```text
vc chat PROJECT [TOPIC] [--run-id ID]
```

- `PROJECT` identifies an initialized project.
- `TOPIC` is the topic for this new production run.
- `--run-id ID` overrides the generated run ID.

Omitted topic is prompted interactively. `chat` always creates a new run. It validates the project and template before creating run state or loading credentials.

Example:

```powershell
vc chat "货币的起源" "金属货币为什么会被纸币替代"
```

### `vc import-chat`

```text
vc import-chat PROJECT FILE [--topic TOPIC] [--run-id ID]
```

- `PROJECT` identifies the target project.
- `FILE` is a Markdown or text conversation record.
- `--topic TOPIC` overrides the topic inferred from the filename.
- `--run-id ID` overrides the generated run ID.

The file must exist before a run is created. Import starts at the draft stage and preserves the source conversation in the run.

### `vc resume`

```text
vc resume PROJECT [-r RUN_ID]
```

- without `--run`, resume the most recently updated unfinished run
- with `-r, --run RUN_ID`, resume that exact run

An unfinished run is one whose state status is not `completed` and whose current stage is not `done`. Candidate ordering uses parsed `updated_at`, falling back to filesystem modification time, then run ID for deterministic ties. Invalid or corrupt run state is reported and not silently selected.

If no unfinished run exists, the command fails with an instruction to use `vc chat PROJECT TOPIC` for a new version.

### `vc status`

```text
vc status PROJECT [-r RUN_ID] [--json]
```

Without `--run`, inspect the most recently updated run, including completed runs. Output includes project, template, run ID, status, current stage, last update, and known final-video path. A project with no runs reports that state without error.

### `vc runs`

```text
vc runs PROJECT [--json]
```

Lists all runs newest first with run ID, status, current stage, and update time. This command never changes state.

## Compatibility

These existing forms remain accepted:

```text
python main.py templates
python main.py project init --template TEMPLATE --name NAME
python main.py chat --project PROJECT --topic TOPIC
python main.py import-chat FILE --project PROJECT
python main.py resume projects/PROJECT/runs/RUN_ID
```

Compatibility parsing delegates to the same command handlers. New documentation does not encourage the old forms, and no duplicate orchestration logic is added.

## Error Handling

- Invalid home/config: exit 2 with the failed path and correction guidance.
- Invalid command arguments: argparse-style exit 2 and usage text.
- Unknown project/template/run: exit 1 with the missing identifier.
- Existing project during init: exit 1 without modifying it.
- Missing import file: exit 1 before run creation.
- No resumable run: exit 1 and suggest `vc chat`.
- Workflow runtime failure: preserve resumable state and exit 1.
- Keyboard interruption: exit 130.

No stack trace is printed for expected user errors. Tests may call handlers directly and inspect typed exceptions before top-level formatting.

## Testing

Automated tests cover:

- console-script registration and direct module invocation
- home precedence and invalid-home failures
- config path resolution independent of current working directory
- text and JSON template listing
- interactive and non-interactive initialization
- positional project/topic/file arguments
- latest unfinished-run selection and deterministic ordering
- explicit run selection
- status and run listing with missing, valid, completed, and corrupt states
- compatibility forms from `main.py`
- no project/run creation on validation failures

Acceptance also requires installing editable in the active Python environment, invoking `vc --help` and representative read-only commands from outside the repository, then running the complete Python and Remotion verification suites.

## Documentation

README leads with the one-time editable install and the short `vc` workflow. Any displayed directory tree keeps an explanatory comment on every item. `vc --help` and each subcommand help are treated as user-facing documentation and use concise Chinese descriptions where practical.
