# Changelog

本项目所有值得注意的变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **环境发现（索引驱动）**：新增 `cli envs` —— 基于本机 NTFS **USN/MFT 元数据索引**发现 Python 环境，
  覆盖目录名启发式看不见的深层 venv（如 `G:\notpython\kard_CN\venv`）。选项：`--build`（索引缺失时先建，
  只读卷、不创建 USN journal）、`--deep`（逐个真跑解释器验证）、`--json`（导出）、`--volumes`、`--backend`。
  实测本机 5 卷 479 万条记录建库 ≈50s，发现 **208 个环境候选**（187 venv / 20 standalone / 1 embed），
  而原 `scan` 口径为 **4 个**；查询延迟 0.02~1.46ms。原理、实测与许可记账见 `docs/usn-indexer.md`。
- **检索后端三级回退**：`index`（本仓索引）→ `es`（内置 Everything 命令行，MIT © voidtools）→
  `walk`（目录遍历）依次回退，且每次检索都记录**每个后端的尝试与原因**（缺索引 / Everything 未运行 /
  拒绝整盘遍历 / 命中条数），不静默降级。整盘遍历默认拒绝（`--allow-whole-drives` 显式许可），
  另有 30s 墙钟预算兜底并标注「结果可能不全」。
- **Cordis 式可逆插件内核（零第三方依赖）**：`core/` 提供 Context（服务注册表 + effect 逆序回收 +
  `fork` 作用域 + 事务化清单装配），`foolproof.composition.json` 为插件清单，
  `plugins/search.py`、`plugins/scanner.py` 为插件行；`core/runtime.py` 供 CLI/GUI 共用装配。
  服务可见性与回收责任分离（服务冒泡到 realm 根、disposer 留在注册者），多实例私有服务用 `isolate`。
- `cli scan --backend index|auto`：在保持原 glob 口径（默认 `walk`）之外，可选走插件化发现。
- 测试：新增 `tests/`（stdlib `unittest`，214 例），覆盖内核语义、USN 解析、路径还原、索引库、
  插件回退与溯源、清单装配；另含「批量名字查询必须命中索引」的 `EXPLAIN QUERY PLAN` 性能护栏。
- 文档：新增 `docs/usn-indexer.md`（原理 / 实测 / 权限矩阵 / 性能基准 / 踩坑记录）
  与实盘基准 JSON（`docs/usn-bench-*.json`、`docs/usn-store-bench-2026-09-23.json`、`docs/usn-envs-2026-09-23.json`）。
- **插件化迁移（阶段 5）**：把四类逻辑迁入插件行 —— `plugins/categories.py`（分类清单 +
  启动审计 fail-loud + 模糊匹配/规格拆分/pip 查询名）、`plugins/pip.py`（子进程调用 + 报错解读 +
  健康检查 + 两种修复方式）、`plugins/reqtxt.py`（requirements.txt 解析 + 逐条安装编排）、
  `plugins/installer.py`（安装/卸载/检查/升级编排，`inject = pip + categories`）。
  清单扩至 **8 行**；`cli.py` 与 `requirements.py` 保留同名入口，内部委托插件。
  **行为零变化有硬证据**：以 `git archive HEAD` 快照为基线，迁移前后 **52 项断言逐字一致**
  （分类数与键、`target_of`/`split_spec`/`pip_query_name`/`is_local_spec`、
  `find_category` 的 `SystemExit` 措辞、`_resolve_packages` 各分支、`cmd_list` 全量与单分类输出、
  requirements.txt 解析、`stderr_tail`/`failure_hint` 映射）。
  - 唯一**有意硬化**：`cli check` 在 `pip show` 超时/异常时按「未安装」处理（原实现在该路径会抛栈）。
  - GUI 的流程编排（线程 / 日志 / 音效 / 进度）保持原样，仅叶子逻辑改为委托插件。
- **任务日志与启动审计（阶段 6，B 类模板标配）**：`core/tasklog.py`（JSONL 操作流水，
  默认落用户级目录、可用 `FOOLPROOF_TASKLOG` 覆盖、写失败不影响主流程）+ `cli tasks`
  （事件计数 / 失败计数 / 最近记录 / `--json` / `--clear`）；`core/audit.py` +
  `cli doctor`（一次核完插件清单、分类清单、索引状态、检索后端、日志可写性，只读，
  有 error 时退出码 1）。清单扩至 **10 行**（新增 `tasklog` / `audit`）。
  `install`/`uninstall`/`check` 在**不改变任何 stdout** 的前提下静默记录事件。

### Changed

- **（结构层）代码收进 `foolproof_operation` 包**，为 PyPI 发布铺路：平铺的 `core/` `index/` `plugins/`
  `cli.py` `requirements.py` 移入包目录（`gui.py`），清单与内置 es.exe 随包分发；包内导入改相对形式，
  插件清单里的动态导入改全路径。仓库根保留 `requirements.py` / `cli.py` **兼容 shim**，
  老命令 `python requirements.py cli …` 继续可用；新增 `python -m foolproof_operation [cli …]` 统一入口。
  两个 PyInstaller spec 改为 `SPECPATH` 相对路径 + 包内资源 + 带包名的 `hiddenimports`。
  **行为零变化**：与重构前快照对照，52 项可观测断言逐字一致。
  原因：直接以平铺布局发布会在 site-packages 留下 `core/` `index/` `plugins/` 这类通用顶层名
  （与其它包高概率冲突），且顶层绝对导入在安装后必然 ImportError。
- README / README.en 去本地化：绝对路径改为 `<PATH>` 占位符，首屏补仓库链接与 MIT 许可声明；
  两版逐节对齐（章节 / 要点数 / 占位符数量一致），分类与库数统一为「24 分类 / 761 个库」。

### Fixed

- **GBK 控制台下 `cli` 因 emoji 直接崩**（既有缺陷，打包冒烟时暴露）：分类名含 emoji，
  在默认代码页 936 的控制台打印时抛 `UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f3ae'`，
  **源码版与冻结版都会中招**。现于 CLI 入口放宽 stdout/stderr 错误策略（`errors="replace"`）：
  中文正常、emoji 退化为 `?`，命令不再崩；要完整显示可 `set PYTHONIOENCODING=utf-8`（README 已注明）。
- 便携版打包 spec（`requirements.spec`）：原写死绝对路径且**漏收运行期资源与新模块** ——
  现改用 `SPECPATH` 推导，`datas` 补 `foolproof.composition.json`、`LICENSE`、
  `tools/everything/`（ES 后端要用），`hiddenimports` 显式列出插件清单里按字符串动态导入的模块
  （`core.events`/`core.tasklog`/`core.audit`/`core.runtime`/`index.service`/`plugins.*`）——
  这类遗漏只在冻结版运行时才暴露，已实测修复（重建 exe 后 `cli list`/`doctor`/`envs` 全部 exit 0）。

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