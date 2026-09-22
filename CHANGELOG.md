# Changelog

本项目所有值得注意的变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Changed

- 自研包随源仓库改名：`alloy` → `allin-engine`。an-engine 于 2026-09-19 把包名与模块名由 `alloy` 改为 `allin_engine`（PyPI 项目名 `allin-engine`）。本清单同步：「🏠 本地 / 自研包」条目由 `"alloy"` 改为 `"allin-engine"`（值仍为 `-e G:/repos/an-engine`）；「🛠️ 工具链」里同名的公开包 `alloy` 条目移除 —— 它本就是 PyPI 上的同名不同项目（Elide 的 High-performance runtime）。
- **value 现支持 pip 选项前缀**（`shlex.split()`）：可写 `"-e G:/repos/an-engine"` 实现 editable 安装。两个自研包已切换到 `-e` 模式（都在活跃开发中，editable 更顺手）。已实测 `pip install --dry-run -e G:/repos/an-engine` → `Would install alloy-1.0.0`。
- `shlex` 模式按输入自适应：含引号 → `posix=True`；无引号但含反斜杠 → `posix=False`（否则 `G:\repos` 会被吃掉分隔符且**不报错**）；其余 → `posix=True`。路径建议写正斜杠最省心。
- 接入自研包：「🏠 本地 / 自研包」收录 `alloy` → `G:/repos/an-engine`（统一游戏引擎 v1.0.0）、`bloodmusic` → `G:/repos/audio-generation`（程序化音频合成引擎 v2.0.0），值为本地仓库路径。
- **新增本地路径型 value 支持**：`requirements_categories.json` 的值原为 PyPI 包名，现允许写本地路径。安装照常 `pip install <路径>`；而 `pip show` / `pip uninstall` 需要的是包名，故新增 `cli.pip_query_name()`，路径型 value 自动回退到显示名（GUI 与 CLI 共用）。已实测：`pip install --dry-run G:/repos/an-engine` → `Would install alloy-1.0.0`。
- **修复 CLI 忽略 value 的缺陷**：`cli.py` 原先把分类内层 dict 的**键**当作 pip 包名，导致 `value` 形同虚设——带 extras 的条目（如 `fastapi[all]`）装成裸包，`--category` 批量安装同样受影响。现改为用 `value` 安装、用包名检查/卸载。
- 明确「🏠 本地 / 自研包」判定标准：**联网（PyPI / GitHub / Gitee）搜不到同名同项目**才算自研，仅凭包名眼生不算，PyPI 同名不同项目也不算。原 9 个条目逐包联网核验后**无一属自研** —— `alloy` 在 PyPI 上是 Elide 的 "High-performance runtime"（作者 Sam Gammon），与本地自研的 `an-engine`/alloy 无关；其归属最终由自研仓库接入（见上条）。
- 9 个包按实际来源归位：`alloy` · `ast_serialize`（mypyc 官方）· `offshoot`（SerpentAI 插件系统）→ 工具链；`udapi` · `udtools`（Universal Dependencies）→ 自然语言处理；`datarecorder`（g1879/DrissionPage 生态）→ 爬虫网络；`sswg`（静态网站生成器）→ Web 开发；`pyvers`（依赖多版本管理）→ 打包构建；`groovy`（gradio-app 的 SSRF 防护）→ 加密安全。
- 「🏠 本地 / 自研包」转为**手动维护**分类：自动扫描因 `_pypi_exists()` 过滤掉 PyPI 上不存在的包，纯自研包天然无法被收录（这正是 `bloodmusic` 从未出现在清单中的原因），故需手动加入；已收录 `bloodmusic`。
- 全量核验：自动归类的 760 个包在 PyPI 均可查询（0 个 404）；手动收录的 `bloodmusic` 为 PyPI 404，属预期。
- ComfyUI 生态归位：`comfy-aimdo` · `comfy-angle` · `comfy-kitchen` 与 `comfyui-*` 系列同属 `comfy*` 前缀，整组归入「🤖 AI / 机器学习」（共 14 个），不再按包名拆分。
- 分类体系整理：全盘扫描积累在「🔍 自动发现」的 601 个包全部归入 24 个功能分类。新增 9 类——GUI 桌面、音频音乐、LLM Agent、安全渗透、逆向二进制、文档文本、科学计算、打包构建、本地自研包；213 个传递依赖单独归入「📦 传递依赖」，不再混入精选分类。分类总数 15 → 25，条目 764 → 761（剔除 4 条跨分类重复项 + 手动新增 1 条自研包 `bloodmusic`）。
- 修复 `httpx` / `aiohttp` 同时存在于「🕷️ 爬虫 / 网络」与「⚡ 异步 / 并发」的重复收录问题；该重复会导致 GUI 勾选状态互相覆盖（UI 以包名为 key）。
- 「🔍 自动发现」分类保留为空，继续作为后续扫描的落点。
- 新增 `classify_auto.py`（dry-run 默认 / `--write` 落盘），承载本次归类映射表，可复现、可增量复用。
- 文档同步：README / README.en 的分类数与脚本名更正为 `requirements.py`。
- 与 siwpan/requirements 归一同源：合入其更新版本的工具链与打包 spec（requirements.py 重试/镜像工具链；2026-08-27 深度审核 R6——两仓实为同一项目「傻瓜式操作」，requirements 仓已退役改名 requirements-legacy）。

## [1.0.0] - 2026-08-27

### Added

- 首个发行版：便携源码 zip（剔除 .venv/构建产物/输出物，依赖见 README）。
- 发布面：gitee [siwpan/foolproof-operation](https://gitee.com/siwpan/foolproof-operation) release `v1.0.0`（依赖库安装器 v1.0.0）。