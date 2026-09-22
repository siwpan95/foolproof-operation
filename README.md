# Python 依赖库安装器

图形化 Python 依赖库管理工具，按分类管理 100+ 常用库。

## 功能

- **分类管理** — 24 大功能分类、760 个库：游戏开发 / AI 机器学习 / LLM Agent / Web 开发 / 爬虫网络 / 数据可视化 / 数据库 / 图像音视频 / 音频音乐 / 办公自动化 / GUI 桌面 / 异步并发 / 加密安全 / 安全渗透 / 逆向二进制 / 工具链 / 打包构建 / 自然语言处理 / 文档文本 / 科学计算 / 云服务 DevOps / 自动化测试 / 本地自研包 / 传递依赖
- **自动发现分类** — 全盘扫描到的、尚未归类的包会落入「🔍 自动发现」，等待人工归类
- **本地 / 自研包** — 判定标准是「联网（PyPI / GitHub / Gitee）搜不到同名同项目」，仅凭包名眼生不算，PyPI 上有同名不同项目也不算（如 `alloy` 在 PyPI 上是 Elide 的 runtime，与本地自研版无关）。已收录 `allin-engine`（G:/repos/an-engine，统一游戏引擎，2026-09-19 由 alloy 改名）、`bloodmusic`（G:/repos/audio-generation），值均为本地仓库路径。
- **批量安装** — 勾选库名一键安装，支持 pip install -U
- **批量卸载** — 勾选库名一键卸载
- **版本检查** — 一次检查所有选中库的安装状态和更新情况
- **一键更新** — 批量更新选中库到最新版
- **pip 健康检查** — 启动自动检测 pip 状态，支持三种修复方案
- **全盘扫描** — 扫描系统中的所有 Python 环境及已安装包
- **requirements.txt 支持** — 读取任意目录的 requirements.txt，可视化勾选安装

## 快速开始

```bash
# 纯标准库 + tkinter，无需额外依赖
python requirements.py

# 指定目标 Python 解释器（默认使用运行本工具的 Python）
python requirements.py --python F:\Python314\python.exe
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
python requirements.py cli scan                       # 扫描全盘 Python 环境
python requirements.py cli es -n 20 "*.py"            # Everything 搜索（内置 es.exe）
```

- `install/uninstall/check` 可用 `--python <exe>` 指定目标解释器；
- `es` 需本机 Everything 主程序/服务在运行（详见 `tools/everything/README.md`，
  ES 为 MIT © voidtools）。

## 配置

编辑 `requirements_categories.json` 增删分类和库名。格式为 `{"显示名": "pip 安装目标"}`：

- **普通包**：值写 PyPI 包名，可带 extras，如 `"fastapi": "fastapi[all]"`；
- **自研 / 本地包**：值写本地仓库路径，如 `"bloodmusic": "G:/repos/audio-generation"`。安装时执行
  `pip install <路径>`；版本检查与卸载需要包名，会自动回退用显示名（见 `cli.pip_query_name()`）。
- **pip 选项前缀**：值经 `shlex.split()` 解析，可带选项，如 `"-e G:/repos/an-engine"`
  （editable 安装）。路径建议用正斜杠；含空格时用双引号包裹，如 `'-e "G:/my pkg"'`。

「🏠 本地 / 自研包」是**手动维护**分类：全盘扫描会跳过 PyPI 上不存在的包，纯自研包不会被自动收录。

## 依赖

- Python >= 3.8 + tkinter (标准库)
