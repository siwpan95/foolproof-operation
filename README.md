# Python 依赖库安装器

> 仓库：<https://gitee.com/siwpan/foolproof-operation> ｜ 许可：MIT（见 [LICENSE](LICENSE)）
> 注：文中 <PATH> 为路径占位符，请替换为你本机实际的目录 / 可执行文件路径。

图形化 Python 依赖库管理工具：**24 个功能分类、761 个库**，另设「🔍 自动发现」桶承接待归类项。

## 功能

- **分类管理** — 24 大功能分类、761 个库：游戏开发 / AI 机器学习 / LLM Agent / Web 开发 / 爬虫网络 / 数据可视化 / 数据库 / 图像音视频 / 音频音乐 / 办公自动化 / GUI 桌面 / 异步并发 / 加密安全 / 安全渗透 / 逆向二进制 / 工具链 / 打包构建 / 自然语言处理 / 文档文本 / 科学计算 / 云服务 DevOps / 自动化测试 / 本地自研包 / 传递依赖
- **自动发现分类** — 全盘扫描到的、尚未归类的包会落入「🔍 自动发现」（当前 3 条待归类），等待人工归类
- **本地 / 自研包** — 判定标准是「联网（PyPI / GitHub / Gitee）搜不到同名同项目」，仅凭包名眼生不算，PyPI 上有同名不同项目也不算（如 `alloy` 在 PyPI 上是 Elide 的 runtime，与本地自研版无关）。已收录 `allin-engine`（<PATH>，统一游戏引擎，2026-09-19 由 alloy 改名）、`bloodmusic`（<PATH>），值均为本地仓库路径。
- **批量安装** — 勾选库名一键安装，支持 pip install -U
- **批量卸载** — 勾选库名一键卸载
- **版本检查** — 一次检查所有选中库的安装状态和更新情况
- **一键更新** — 批量更新选中库到最新版
- **pip 健康检查** — 启动自动检测 pip 状态，支持三种修复方案
- **全盘扫描** — 扫描系统中的所有 Python 环境及已安装包
- **环境发现（索引驱动）** — 基于本机 NTFS 的 USN/MFT 元数据索引做环境发现，
  可覆盖目录名启发式看不见的深层 venv；索引不可用时**显式回退** Everything / 目录遍历
- **requirements.txt 支持** — 读取任意目录的 requirements.txt，可视化勾选安装
- **任务日志与启动审计** — 安装/卸载/检查/建库/发现等操作写入 JSONL 流水（`cli tasks` 查看）；
  `cli doctor` 一次核完插件清单、分类清单、索引状态、检索后端与日志可写性（只读检查）

## 快速开始

```bash
# 纯标准库 + tkinter，无需额外依赖
python requirements.py

# 指定目标 Python 解释器（默认使用运行本工具的 Python）
python requirements.py --python python
```

## 命令行接口（cmd / PowerShell 直接可用）

`python requirements.py cli <子命令>`，无 `cli` 参数时仍打开图形界面：

```bash
python requirements.py cli list                     # 列出全部分类与库名
python requirements.py cli list --category 加密      # 只列某个分类（可省略 emoji/写片段）
python requirements.py cli install requests aiohttp  # 安装指定库
python requirements.py cli install --category Web     # 安装整个分类
python requirements.py cli install --file req.txt     # 按 requirements.txt 安装
python requirements.py cli install --all --upgrade    # 全量安装并升级
python requirements.py cli uninstall requests         # 卸载
python requirements.py cli check --category 数据库     # 版本与更新检查
python requirements.py cli scan                       # 扫描全盘 Python 环境（原 glob 口径）
python requirements.py cli envs                       # 环境发现（索引驱动，覆盖深层 venv）
python requirements.py cli envs --build               # 同上，索引缺失时先建索引（只读卷）
python requirements.py cli envs --deep --json e.json  # 逐个真跑解释器验证 + 落 JSON
python requirements.py cli envs --backend walk --volumes <PATH>   # 指定兜底遍历目录
python requirements.py cli es -n 20 "*.py"            # Everything 搜索（内置 es.exe）
python requirements.py cli doctor                     # 启动审计：清单/分类/索引/后端/日志
python requirements.py cli tasks                      # 任务日志（JSONL 操作流水）
```

- `install/uninstall/check` 可用 `--python <exe>` 指定目标解释器；
- `es` 需本机 Everything 主程序/服务在运行（详见 `foolproof_operation/foolproof_operation/foolproof_operation/tools/everything/README.md`，
  ES 为 MIT © voidtools）；
- `scan` 是历史口径（glob + 目录名启发式，会漏深层 venv）；`envs` 走
  `foolproof.composition.json` 装配的插件，按 **index → es → walk** 依次回退，
  并打印「是哪个后端答的」与失败原因（缺索引 / Everything 未运行 / 拒绝整盘遍历等）。
- `envs` 的 `--volumes` 传给遍历后端时请写**具体目录**：盘符根（`G:\`）会触发整盘遍历
  （分钟~小时级），默认被拒绝，确需整盘请显式加 `--allow-whole-drives`。
- 分类名自带 emoji；若你的控制台是 GBK 代码页（Windows 中文默认 936），emoji 会退化成 `?`
  （命令不会崩）；想完整显示先执行 `set PYTHONIOENCODING=utf-8`。

## 配置

编辑 `foolproof_operation/requirements_categories.json` 增删分类和库名。格式为 `{"显示名": "pip 安装目标"}`：

- **普通包**：值写 PyPI 包名，可带 extras，如 `"fastapi": "fastapi[all]"`；
- **自研 / 本地包**：值写本地仓库路径，如 `"bloodmusic": "<PATH>"`。安装时执行
  `pip install <路径>`；版本检查与卸载需要包名，会自动回退用显示名（见 `cli.pip_query_name()`）。
- **pip 选项前缀**：值经 `shlex.split()` 解析，可带选项，如 `"-e <PATH>"`
  （editable 安装）。路径建议用正斜杠；含空格时用双引号包裹，如 `'-e "<PATH> pkg"'`。

「🏠 本地 / 自研包」是**手动维护**分类：全盘扫描会跳过 PyPI 上不存在的包，纯自研包不会被自动收录。

## 依赖

- Python >= 3.8 + tkinter (标准库)
