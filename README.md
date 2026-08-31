# Python 依赖库安装器

图形化 Python 依赖库管理工具，按分类管理 100+ 常用库。

## 功能

- **分类管理** — 11 大分类（游戏开发 / AI / Web / 爬虫 / 数据可视化 / 数据库 / 图像音视频 / 办公自动化 / 异步并发 / 加密安全 / 工具链）
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

编辑 `requirements_categories.json` 增删分类和库名。

## 依赖

- Python >= 3.8 + tkinter (标准库)
