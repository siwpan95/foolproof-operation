# NTFS USN/MFT 索引：原理、实测与本仓设计

> 本文是「foolproof-operation 环境发现从目录遍历升级为 NTFS 元数据索引」的设计与事实基线。
> 实测环境：本机 `SIWPAN\13122`，2026-09-23，Python 3.13.14，卷 C/D/E/F/G 全 NTFS（G: 4.0 TB）。
> 规划全文见 `G:\allin\docs\md\allin主工作区\USN索引与Cordis内核规划_2026-09-23.md`（v2）。

## 1. 来源与许可记账（借鉴必记账）

| 对象 | 许可 | 借用范围 |
|---|---|---|
| [voidtools ES](https://www.voidtools.com/) 1.1.0.37（本仓 `tools/everything/es.exe`） | MIT © 2025 voidtools | **二进制已内置**，`cli es` 通道继续使用（零改动） |
| Everything SDK（`Everything64.dll` / `Everything.h` / `ipc/everything_ipc.h`） | MIT © voidtools | **仅接口语义参考**（IPC 查询模型），本仓不引入 DLL |
| `github.com/yuzhengyang/Everything`（`Everything-master.zip`）及其内 `EverythingSZ 源码` | **许可不明** | **仅原理参考，零代码复制**。该包是 2017 年 C# 学习程序 + codeplex TFS 复刻，**不含 voidtools 源码**（官方从未开源 Everything 本体） |
| CSDN / cnblogs 两篇网络文章 | 博客版权 | 仅线索；CSDN 一篇为 AI 生成的下载导流页，无技术内容 |
| [Microsoft Learn: USN Journal / MFT_ENUM_DATA](https://learn.microsoft.com/windows/win32/api/winioctl/) | 文档 | API 语义与结构体定义（唯一权威来源） |
| `allin_engine/core/context.py`（siwpan，MIT） | MIT | **仅语义参考**（`provide/effect/fork/dispose` 模型），按仓库法「复制优于共享、互不 import」**内联重写、零代码复制** |

> **结论**：Everything 本体不开源；本仓实现的是**原理**（NTFS USN journal + MFT 枚举），不是任何「Everything 源码」。

## 2. 原理

- **MFT**（Master File Table）：NTFS 上每个文件/目录一条记录，含名称、父目录引用号（FRN）。枚举 MFT 即可**不遍历文件系统**拿到全盘名称与路径。
- **USN journal**（Update Sequence Number Journal）：卷级变更日志，记录增/删/改名/改属性。用于**增量**同步。
- 两者分工即 Everything 快的根因：**一次全量枚举建索引 + 之后只读日志增量**。

本仓只用两个只读控制码 + 一个增量读码：

| 控制码 | 作用 | 本仓用法 |
|---|---|---|
| `FSCTL_QUERY_USN_JOURNAL` (0x000900F4) | 读日志元数据（`JournalID` / `FirstUsn` / `NextUsn`） | 判定索引是否失效、取增量起点 |
| `FSCTL_ENUM_USN_DATA` (0x000900B3) | 枚举 MFT 记录 | 首次全量建索引 |
| `FSCTL_READ_USN_JOURNAL` (0x000900BB) | 从 `NextUsn` 起读变更 | 增量同步（仅日志存在的卷） |

**不做**：`FSCTL_CREATE_USN_JOURNAL`（改卷状态的写操作，需逐项放行；无日志的卷只做全量枚举，增量降级为重扫）。

## 3. 本机实测（2026-09-23，全部只读）

| # | 探测 | 结果 |
|---|---|---|
| 1 | 非提权 `CreateFileW(\\.\G:, GENERIC_READ)` | **成功**（句柄 1444） |
| 2 | 非提权 `CreateFileW(\\.\C:, GENERIC_READ)` | **INVALID_HANDLE_VALUE**（拒绝） |
| 3 | `FSCTL_QUERY_USN_JOURNAL` on G: | 成功，返回 56 字节：`JournalID=0x1dd157da37b8f67`，`FirstUsn=2566914048`，`NextUsn=2608812960` |
| 4 | `ENUM_USN_DATA`，`LowUsn=FirstUsn`（网络/复刻代码的写法） | 仅 **12,071 条**后即 `err=38 ERROR_HANDLE_EOF` |
| 5 | `ENUM_USN_DATA`，`LowUsn=0` + `HighUsn=0x7FFFFFFFFFFFFFFF` | **480,523 条 / 60s ≈ 8,000 条/秒**（速率随预热爬升），60s 未到 EOF |
| 6 | 第 5 项期间的命中 | `python.exe` / `pythonw.exe` / `pyvenv.cfg` / `site-packages` 各 ≥10（尚未扫完） |
| 7 | 非提权 `es.exe` 查询（Everything 未运行） | `Error 8: Everything IPC not found`，0 结果 |
| 8 | Everything 运行中 `es.exe folder:site-packages` | 正常返回（/Python314 等路径） |

### 关键结论（#4 是本仓相对网络资料的实质修正）

用 `FirstUsn` 当 `LowUsn` **只能枚举到 journal 窗口内的记录**——G: 上仅 12,071 条，随即 EOF，会得到**严重不完整的全盘索引**且不报错。
**正确做法**：`LowUsn=0` + `HighUsn=MAXLONGLONG` 才能拿到全量 MFT 记录。

### 3.1 落库与查询基准（阶段 3 实测，索引写在临时目录）

| 卷 | 记录数 | 落库速率 | 建库耗时 | 库大小 | 查询 平均/最大 | 路径还原 平均 | 命中 |
|---|---|---|---|---|---|---|---|
| C: | 1,018,935 | 83,629/s | 12.18s | 91.2 MB | 0.13 / 0.69 ms | 0.06 ms | python=17 pyvenv.cfg=7 site-packages=14 |
| D: | 57,756 | 87,346/s | 0.66s | 5.4 MB | 0.02 / 0.08 ms | 0.033 ms | python=1 pyvenv.cfg=1 site-packages=1 |
| E: | 67,833 | 101,880/s | 0.67s | 4.5 MB | 0.03 / 0.10 ms | 0.028 ms | python=3 pyvenv.cfg=3 site-packages=3 |
| F: | 1,126,708 | 102,347/s | 11.01s | 87.9 MB | 0.16 / 0.97 ms | 0.056 ms | python=18 pyvenv.cfg=9 site-packages=24 |
| G: | 2,518,517 | 97,029/s | 25.96s | 238.8 MB | 1.46 / 3.13 ms | 0.042 ms | python=169 pyvenv.cfg=167 site-packages=169 |

- **全盘建库（枚举 + 落库）≈ 50 秒 / 479 万行**，落库速率 8.4~10.2 万行/秒；索引总体积 ≈ 428 MB（约 95 B/行，含两个二级索引）。
- **查询延迟 0.02~1.46 ms（最大 3.13 ms）**，远优于 100 ms 目标；路径按父链懒回溯，**每条 0.03~0.06 ms**。
- 证据：`docs/usn-store-bench-2026-09-23.json`（可重跑：`python -m index.probe --store <目录> --max-records 0`）。

### 3.2 性能护栏：批量名字查询必须走索引（阶段 3 踩坑并固化）

| 写法 | 查询计划 | G: 卷（251 万行）延迟 |
|---|---|---|
| `WHERE name IN (?,?,?,?,?) COLLATE NOCASE` | **`SCAN nodes`（全表扫描）** | **355 ms** ❌ |
| `WHERE name = ? COLLATE NOCASE OR name = ? COLLATE NOCASE ...` | `MULTI-INDEX OR` + `idx_nodes_name_lower` | **2.0 ms** ✅ |

`IN (...) COLLATE NOCASE` 把排序规则套在整个 IN 表达式上，SQLite 用不上
`name COLLATE NOCASE` 索引。该结论已固化为 `index.store.name_lookup_sql()` +
`tests/test_store.py::QueryPlanTest`（对 SQL 取 `EXPLAIN QUERY PLAN`，断言不出现 `SCAN nodes`），
**防止日后被"简化"回去而性能静默劣化**。

### 权限矩阵（决定降级策略）

| 卷 | 非提权枚举 | 备注 | 本仓策略 |
|---|---|---|---|
| C:/D:/E:/F:/G:（本机全部 5 卷，均 NTFS） | ✅ **实测全部可读**（2026-09-23 全量探针，journal 有效） | 首测（BSOD 前）C: 曾返回 `INVALID_HANDLE_VALUE`；重启后复测可读 —— 卷可读性受系统状态/争用影响，**不是**固定权限位 | 索引；**仍必须实现降级路径**，因为可读性是环境相关的 |
| 非 NTFS 卷 | 不适用 | 无 USN | 不走索引，回退遍历 |
| 卷句柄被拒时 | — | `VolumeAccessError`（含错误码与「需管理员」提示） | **显式降级并明示**：该卷回退现有目录遍历，绝不静默漏环境 |

> **口径订正**：规划 v2 曾写「C: 系统盘非提权被拒 → 需提权」。实测不成立（同一台机器、同为非提权，前后两次结果相反）。
> 结论改为：**降级路径是必需的工程防御，但「系统盘必然不可读」是错的**。不得据此把 C: 排除在索引之外。

### 性能量级（实测，2026-09-23）

**全量枚举（`LowUsn=0`，每卷完整跑完，内存恒定模式）**：

| 卷 | 记录数 | 目录数 | 速率 | 耗时 | 命中 `python.exe` / `pyvenv.cfg` / `site-packages` |
|---|---|---|---|---|---|
| C: | 1,018,500 | 139,115 | 63,976/s | 15.92s | 17 / 7 / 14 |
| D: | 57,756 | 2,232 | 140,868/s | 0.41s | 1 / 1 / 1 |
| E: | 67,832 | 4,148 | 161,505/s | 0.42s | 3 / 3 / 3 |
| F: | 1,126,708 | 135,240 | 45,913/s | 24.54s | 18 / 9 / 24 |
| G: | 2,518,498 | 167,079 | 150,179/s | 16.77s | 169 / 167 / 169 |
| **合计** | **4,789,294** | **447,814** | — | **58.1s** | **208 / 187 / 211** |

- **全盘首次建库 ≈ 1 分钟**（本机 5 卷、479 万条记录）。原规划的风险 R1 估「20~40 分钟」**偏悲观一个数量级**，已订正。
- 速率区间 **46k~161k 条/秒**；早期测得的 8,000 条/秒出现在 BSOD 前的机器异常状态 + Everything 冷启动全盘索引并发场景 —— 即**速率受并发索引与磁盘争用影响可达 10 倍以上**，首次建库仍应后台化并显示进度。
- 新瓶颈转移到**落库**：479 万行插入需批量事务（阶段 3 验收项），而非枚举本身。
- 查询走本地 SQLite（WAL）→ 目标 **< 100 ms**。

**环境发现能力对比（同一台机器，实测）**：

| 手段 | 结果 |
|---|---|
| 现有 `cli scan`（目录名启发式多层遍历） | **4 个 Python 环境** |
| USN/MFT 索引（全盘枚举，无需遍历） | **208 个 `python.exe`、187 个 `pyvenv.cfg`、211 个 `site-packages`** |

差距的根因是现有实现只在各盘顶层 + 一层子目录按目录名关键字匹配，**深层 venv 一律漏掉** ——
例如 `G:\notpython\kard_CN\venv\Scripts\python.exe`（3 层）就被索引一查即得，旧遍历看不见。
（索引给出的是**候选**，阶段 4 仍需用 `python --version` / `pyvenv.cfg` 解析做有效性验证。）

## 4. 本仓设计

### 4.1 分层（每层一个插件行，可独立卸载）

```
foolproof_operation/              包根（2026-09-23 起代码全部收进包内；仓库根只留兼容 shim
                                  requirements.py / cli.py + 文档 + 测试 + 打包 spec）
  core/context.py                 内核：Context（provide/effect/fork/isolate/dispose）+ Service 基类
  core/events.py                  事件总线（on/emit/waterfall），作为 'bus' 服务
  core/loader.py                  清单装配：插件行声明（id/plugin/config/isolate）+ 事务化装配
  core/runtime.py                 装配助手（build_runtime，按行覆盖配置）
  core/tasklog.py                 JSONL 操作流水（cli tasks 的数据源）
  core/audit.py                   启动审计（cli doctor：清单/分类/索引/后端/日志）
  index/usn.py                    USN/MFT 只读客户端（ctypes）
  index/paths.py                  FRN → 路径还原（父链回溯 + rename/delete 处理）
  index/store.py                  SQLite 索引（按卷分库 / WAL / JournalID 判定 / 批量事务）
  index/service.py                索引服务（后台建库 + 增量同步 + 查询 API）
  index/probe.py                  实盘基准探针（--store 产出落库/查询基准）
  plugins/categories.py           分类清单 + 启动审计 + 规格拆分（清单即数据）
  plugins/pip.py                  pip 子进程 + 报错解读 + 健康检查 + 修复
  plugins/reqtxt.py               requirements.txt 解析 + 逐条安装编排
  plugins/installer.py            安装/卸载/检查/升级编排（inject: pip + categories）
  plugins/search.py               检索三后端 index → es → walk（全程溯源）
  plugins/scanner.py              环境发现（证据优先 + legacy 兜底）
  cli.py / gui.py                 命令行 / tkinter 界面
  requirements_categories.json    分类清单（事实源，随包分发）
  foolproof.composition.json      插件清单（随包分发）
  tools/everything/               内置 es.exe（MIT © voidtools，随包分发）
```

索引目录默认在 **用户级**（``%LOCALAPPDATA%\foolproof-operation\index``，可经
``FOOLPROOF_INDEX_DIR`` 覆盖），刻意不入仓库：索引是运行期缓存，单卷可达数百 MB。

### 4.2 检索后端（**加法，不替换**）

| 后端 | 依赖 | 定位 |
|---|---|---|
| `fileindex`（USN/MFT 索引） | 无外部进程；数据盘非提权可用 | 首选：全盘任意深度、秒级 |
| `es`（现有 ES 通道） | Everything 进程/服务在运行 | 保留（功能面零减少） |
| `walk`（现有目录遍历） | 无 | 兜底：C: 无权限、非 NTFS、索引未建好时 |

### 4.3 与 Cordis 模型的映射

| Cordis 概念 | 本仓落点 |
|---|---|
| effect / 逆序回滚 | `Context.effect` + `dispose()` 逆序串行容错 |
| Service / inject | `Service` 基类 + `inject` 声明，缺失即拒绝装配（fail loud） |
| fork / reusable | **每个 Python 环境 = `ctx.fork(env_id)`**；`fork_install` 供多环境多实例 |
| isolate | `Context.isolate(*keys)`：作用域内不向上可见指定服务（多环境互不串味） |
| 配置即数据 | `foolproof.composition.json` 声明插件行；`requirements_categories.json` 升格为「配置即清单」（启动审计 fail-loud） |

### 4.3.1 实现期修正（阶段 1，关键语义）

- **服务可见性与回收责任分离**（Cordis 真实语义，本项目关键设计）：
  ``provide`` 默认把服务**冒泡到所属 realm 的根**（所以 A 行发布的 `bus`，B 行能 `inject`），
  而 **disposer 与摘除责任留在注册者本层**（所以卸载 B 行 fork 时，B 行发布到 realm 根的服务同步摘除，
  不留孤儿服务）。等价说法：**服务是全局的，effect 是 fiber 的**。
  —— 若服务只落注册者本层，「行级 fork 隔离」会直接切断跨行依赖，多插件宿主无法成立。
- **多实例私有服务须 `isolate`**：同一 realm 内重名服务 `provide` 直接 fail loud
  （`DuplicateServiceError`）；`mount(..., isolate=(...))` / `fork_install(..., isolate=(...))`
  声明私有服务名后，多实例（多 Python 环境）互不干扰。与本机 DSH preset
  「服务必须坐在 isolate realm 内，否则多会话冲突」同构。
- 三种插件形态（函数式 / 对象式 / `Service` 子类）之外，另支持**普通类插件**
  （无参可构造 + `apply(ctx, config)`），与 Cordis「插件可以是类」对齐。

### 4.3.2 阶段 2 实测发现与修正

- **`is_ntfs_volume` 取错参数位**（实盘测试暴露）：`GetVolumeInformationW` 的第 2 个参数是**卷标**
  （如「新加卷」），第 7 个才是**文件系统名**。初版读卷标判断 NTFS，结果只认出一卷、
  导致实盘测试「无数据盘可测」而静默跳过。**教训：凡是「值看起来对但数量不对」的探测，
  必须先怀疑参数位，而不是先怀疑权限。**
- **探针内存设计**：命中与目录数在**枚举过程中**计数（不受记录上限影响），
  只有需要样例路径时才建内存索引表；`--samples 0` 即得内存恒定的完整总数测量。
  初版在枚举后遍历索引表统计，479 万条会吃掉约 1GB。
- **`PathNode` 用 `__slots__`**：单卷数百万条，无 slot 的 dataclass 每条多一层 `__dict__`。
- **目录数含根记录**：`.` 记录本身带 DIRECTORY 位，统计时按记录位计数（不特判根），
  故比「用户可见目录数」多 1，文档与测试统一按记录位口径。
- **`FSCTL_READ_USN_JOURNAL` 的即时语义**：`timeout=0` + `bytes_to_wait=0` 表示
  「有多少读多少、立即返回」，适合增量同步的拉取积压；游标不前进即退出，避免死循环。

### 4.3.3 阶段 3 实测发现与修正

- **查询性能护栏**（见 §3.2）：批量名字查询从 `IN (...) COLLATE NOCASE`（全表扫描 355ms）
  改为逐项 `name = ? COLLATE NOCASE` 的 OR 形式（2.0ms），并用 `EXPLAIN QUERY PLAN` 测试锁死。
- **连接泄漏**（`ResourceWarning` 逼出来的）：`IndexStore.open()` 自建连接却按「外部传入」处理，
  `_owns_conn=False` 导致 `close()` 从不真正关闭连接 —— 功能上被 WAL + 每批 commit 掩盖，
  长时间运行会持续泄漏句柄。修法：`open()` 显式声明接管连接；打开失败路径也回收连接。
  **教训：把 `PYTHONWARNINGS=error::ResourceWarning` 当黄金值跑一次，能抓出「功能正确但资源泄漏」这类静默缺陷。**
- **`max_records=0` 边界**：0 曾被当成「上限 0」立即停止枚举（应表示「不限」）。
- 计数口径：`store.apply_changes` 的计数键是 ``upsert``/``remove``/``ignore``（批量写入不逐行回查是否已存在，
  故不区分新增与更新）；需要该区分的是内存版 `paths.PathResolver.apply_changes`。

### 4.3.4 阶段 4 实测发现与修正（插件接入）

- **三后端并存且全程可溯源**：``index``（本仓索引）→ ``es``（Everything CLI，MIT © voidtools）→ ``walk``（目录遍历）。
  每次检索都记录**每个后端的尝试与原因**（缺索引 / Everything 未运行 / 拒绝整盘遍历 / 命中条数），
  因此「为什么没走索引」永远可查，不存在静默降级。
- **遍历根必须归一化**（静默错误结果类缺陷）：``os.walk("G:")`` 被解释为「G 盘的**当前目录**」，
  实测 0.03 秒返回 1 条命中 —— 既不是整盘也不是用户以为的目录。故 ``normalize_walk_root()``
  把 ``"G:"`` 规范为 ``"G:\\"``，并据此识别「整盘根」。
- **整盘遍历默认拒绝**：盘符根意味着整卷遍历（分钟~小时级），默认抛 ``WalkRefused`` 并在溯源里如实标注；
  确需整盘须显式 ``--allow-whole-drives``。另有 ``budget_seconds``（默认 30s）墙钟预算兜底，
  超时返回部分结果并标注「已达墙钟预算，结果可能不全」——**绝不静默返回残缺结果**。
- **环境聚合要做假阳性标注**：证据检索会命中 CPython 自带的 venv 模板资产
  （``…\\venv\\scripts\\nt\\python.exe``）。本仓不静默丢弃，而是标为 ``valid=False`` +
  原因「疑似 CPython venv 模板资产」；实测 208 个候选中即含此类项。
- **`IndexService.status()` 必须探测磁盘**：初版新进程里所有卷都报 ``missing``（内存里没有状态），
  导致 ``index`` 后端被误判不可用而整条链静默回退；现改为对未登记卷用 ``create=False`` 探测已有库文件
  （**不建库、不产生空库文件**）。

### 4.4 偏差记录

- 规划 v2 写的是 `foolproof.yml`，**实现改为 `foolproof.composition.json`**：本仓 README 承诺「纯标准库 + tkinter，无需额外依赖」，引入 PyYAML 会破坏该承诺；JSON 由 stdlib 解析，语义等价（配置即数据）。

## 5. 阶段计划（与规划 v2 一致）

| 阶段 | 内容 | 黄金值 |
|---|---|---|
| 0 | 本文档（事实基线） | 仅新增文档 ✅ **已完成 2026-09-23** |
| 1 | 内核 `core/{context,events,loader}.py` + `tests/` | `python -m unittest discover` 全绿（**55/55**）；新增文件 `py_compile` exit 0 ✅ **已完成 2026-09-23** |
| 2 | `index/{usn,paths}.py` | 实盘对照表与 §3 同量级 ✅ **已完成 2026-09-23**（`python -m index.probe`；证据 `docs/usn-bench-2026-09-23.json`（含样例路径）+ `docs/usn-bench-full-2026-09-23.json`（完整总数）；全盘 479 万条 58.1s；发现能力 4 → 208） |
| 3 | `index/{store,service}.py` | 查询 <100ms；`JournalID` 变化 → 重建有测试 ✅ **已完成 2026-09-23**（查询 0.02~1.46ms；全盘建库 ≈50s；证据 `docs/usn-store-bench-2026-09-23.json`） |
| 4 | 接入内核（`fileindex`/`search`/`scanner`，遍历保留为兜底） | CLI 全子命令 exit 0；GUI 冒烟 stderr 无 Traceback；环境数不减少 ✅ **已完成 2026-09-23**（环境发现 **4 → 208** 候选；CLI 全绿；GUI 冒烟 stderr 长度 0；证据 `docs/usn-envs-2026-09-23.json`） |
| 5 | 其余插件行迁移（行为零变化） | 全测试绿 + 回归对照 |
| 6 | 文档/同步/打包/复盘 | `smoke_test.py` exit 0 |
