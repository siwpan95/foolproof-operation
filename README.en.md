# Python Dependency Installer (foolproof-operation)

A tkinter GUI tool for managing 100+ common Python packages, organized into 15 categories
(game dev / AI / web / scraping / visualization / database / media / office automation /
async / crypto / toolchain / NLP / cloud / testing / auto-discovery).

## Features

- Category-based package management (install / uninstall / batch upgrade)
- Version check against PyPI
- pip health check with three repair options
- Full-disk scan for all Python environments
- requirements.txt support

## Quick start

```bash
# stdlib + tkinter only, no extra dependencies
python requirements.py

# target a different Python interpreter
python requirements.py --python F:\Python314\python.exe
```

## Command-line interface

`python requirements.py cli <subcommand>` — running without `cli` opens the GUI as before:

```bash
python requirements.py cli list                      # list all categories & packages
python requirements.py cli list --category Web       # filter by category (fuzzy)
python requirements.py cli install requests aiohttp  # install packages
python requirements.py cli install --category Web    # install a whole category
python requirements.py cli install --file req.txt    # install from requirements.txt
python requirements.py cli install --all --upgrade   # install everything, upgraded
python requirements.py cli uninstall requests        # uninstall
python requirements.py cli check --category database # version/update check
python requirements.py cli scan                      # scan for Python environments
python requirements.py cli es -n 20 "*.py"           # Everything search (bundled es.exe)
```

- `install/uninstall/check` accept `--python <exe>` to pick the target interpreter.
- `es` requires the Everything app/service to be running on this machine
  (see `tools/everything/README.md`; ES is MIT © voidtools).

## Configuration

Edit `requirements_categories.json` to add/remove categories and packages.

## Dependencies

- Python >= 3.8 + tkinter (stdlib)
