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
```

## 配置

编辑 `requirements_categories.json` 增删分类和库名。

## 依赖

- Python >= 3.8 + tkinter (标准库)
