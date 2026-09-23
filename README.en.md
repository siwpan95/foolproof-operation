# Python Dependency Installer (foolproof-operation)

> Repository: <https://gitee.com/siwpan/foolproof-operation> | License: MIT (see [LICENSE](LICENSE))
> Note: `<PATH>` is a placeholder for a directory / executable path — replace it with the real one on your machine.

A tkinter GUI tool for managing **761 Python packages across 24 functional categories**, plus an
"🔍 Auto-discovery" bucket for packages that are not filed yet.

## Features

- **Category management** — 24 functional categories, 761 packages: game dev / AI & ML / LLM agents / web / scraping & networking / visualization / database / image & media / audio & music / office automation / GUI & desktop / async & concurrency / crypto & security / pentesting / reverse engineering / toolchain / packaging & build / NLP / document & text / scientific computing / cloud & DevOps / automated testing / local in-house packages / transitive dependencies
- **Auto-discovery bucket** — packages found by a full-disk scan that are not filed anywhere yet land in "🔍 Auto-discovery" (3 awaiting triage), waiting for manual sorting
- **Local / in-house packages** — the test is "no same-name, same-project match on PyPI / GitHub / Gitee"; an unfamiliar name alone does not qualify, and a same-name-but-different project on PyPI does not qualify either (e.g. `alloy` on PyPI is Elide's runtime, unrelated to the local in-house engine). Currently listed: `allin-engine` (`<PATH>`, the unified game engine, renamed from `alloy` on 2026-09-19) and `bloodmusic` (`<PATH>`); both values are local repository paths.
- **Batch install** — tick packages and install them in one go, supports `pip install -U`
- **Batch uninstall** — tick packages and uninstall them in one go
- **Version check** — check install state and available updates for every selected package at once
- **One-click upgrade** — batch-upgrade the selected packages to their latest versions
- **pip health check** — pip state is checked on startup, with three repair paths
- **Full-disk scan** — scan every Python environment on the machine and the packages installed in it
- **Index-driven environment discovery** — discovers environments from the local NTFS USN/MFT metadata
  index, so deep virtualenvs that name-based heuristics never see are still found; when the index is
  unavailable it **falls back explicitly** to Everything / directory walking
- **requirements.txt support** — read a `requirements.txt` from any directory and tick packages to install
- **Task log and startup audit** — install / uninstall / check / index-build / discovery runs are appended
  to a JSONL history (`cli tasks`), and `cli doctor` checks the plugin manifest, category manifest, index
  state, search backends and log writability in one read-only pass
- **GUI panels** — "📋 Task log" (filter by event / failures only / clear) and "🐍 Environment discovery"
  (candidate list with the answering backend, index state, one-click index build / incremental sync; all
  work runs in background threads so the window stays responsive)

## Install

```bash
# from PyPI (Windows; Python >= 3.8, zero third-party runtime dependencies)
pip install foolproof-operation

foolproof-operation              # GUI
foolproof-operation cli list     # CLI (same subcommands as the next section)
```

Running straight from a source checkout still works, no install required:

```bash
python requirements.py            # GUI
python requirements.py cli list   # CLI
python -m foolproof_operation     # equivalent unified entry point
```

## Quick start

```bash
# stdlib + tkinter only, no extra dependencies
python requirements.py

# target a different Python interpreter (defaults to the Python running this tool)
python requirements.py --python python
```

## Command-line interface (works directly in cmd / PowerShell)

`python requirements.py cli <subcommand>`; running without `cli` still opens the GUI:

```bash
python requirements.py cli list                     # list every category and package
python requirements.py cli list --category crypto    # list one category (emoji optional / partial match)
python requirements.py cli install requests aiohttp  # install the given packages
python requirements.py cli install --category Web     # install a whole category
python requirements.py cli install --file req.txt     # install from a requirements.txt
python requirements.py cli install --all --upgrade    # install everything, upgraded
python requirements.py cli uninstall requests         # uninstall
python requirements.py cli check --category database  # version and update check
python requirements.py cli scan                       # scan for Python environments (original glob path)
python requirements.py cli envs                       # index-driven environment discovery (deep venvs)
python requirements.py cli envs --build               # same, building the index first if missing (read-only)
python requirements.py cli envs --deep --json e.json  # verify each interpreter for real + dump JSON
python requirements.py cli envs --backend walk --volumes <PATH>   # specify directories for walk fallback
python requirements.py cli es -n 20 "*.py"            # Everything search (bundled es.exe)
python requirements.py cli doctor                     # startup audit: manifest/categories/index/backends/log
python requirements.py cli tasks                      # task log (JSONL operation history)
```

- `install` / `uninstall` / `check` accept `--python <exe>` to pick the target interpreter;
- `es` requires the Everything app/service to be running on this machine (see
  `foolproof_operation/foolproof_operation/foolproof_operation/tools/everything/README.md`; ES is MIT © voidtools);
- `scan` is the historical path (glob + directory-name heuristics, misses deep virtualenvs); `envs`
  runs the plugins composed by `foolproof.composition.json`, falling back **index → es → walk** and
  printing which backend answered plus why the others did not (no index / Everything not running /
  whole-drive walk refused, …).
- With `envs`, pass `--volumes` **directories** to the walk fallback: a drive root (`G:\`) means a
  whole-volume walk (minutes to hours) and is refused by default — add `--allow-whole-drives` if you
  really mean it.
- Category names carry emoji; on a GBK code page (Windows Chinese default 936) they degrade to `?`
  instead of crashing the command. Run `set PYTHONIOENCODING=utf-8` first to render them fully.

## Configuration

Edit `foolproof_operation/requirements_categories.json` to add or remove categories and packages. The format is
`{"Display name": "pip install target"}`:

- **Ordinary packages**: the value is a PyPI package name, optionally with extras, e.g. `"fastapi": "fastapi[all]"`;
- **In-house / local packages**: the value is a local repository path, e.g. `"bloodmusic": "<PATH>"`. Install
  runs `pip install <path>`; version checks and uninstalls need a package name, so they fall back to the
  display name (see `cli.pip_query_name()`).
- **pip option prefixes**: the value goes through `shlex.split()`, so options are allowed, e.g. `"-e <PATH>"`
  (editable install). Prefer forward slashes in paths; quote paths containing spaces, e.g. `'-e "<PATH> pkg"'`.

"🏠 Local / in-house packages" is a **manually maintained** category: the full-disk scan skips packages that
do not exist on PyPI, so purely in-house packages are never auto-collected.

## Dependencies

- Python >= 3.8 + tkinter (stdlib)
