# Python 依赖库安装器

基于 tkinter 的图形化 Python 依赖库管理工具，按分类批量安装、版本检查和一键更新。

## 功能

- **分类管理**：11 大类依赖库（游戏开发、AI/机器学习、Web 开发、爬虫/网络、数据可视化、数据库、图像/音视频、办公自动化、异步/并发、加密/安全、工具链）
- **批量安装**：勾选需要的库，一键安装
- **版本检查**：对比已安装版本与 PyPI 最新版本
- **一键更新**：批量升级到最新版本

## 运行

```bash
cd D:\allin\dabao\requirements
python requirements.py
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `requirements.py` | 主程序（tkinter GUI） |
| `requirements_categories.json` | 依赖分类与库名配置，可按需增删 |
| `失败日志.txt` | 安装失败记录 |

## 环境要求

- Python 3.8+
- tkinter（标准库自带）
- pip

## 注意事项

- `pip` 自身更新需二次确认，更新过程中可能影响当前进程
- 部分库（如 tensorflow）对 Python 版本有特定要求
- 安装/更新失败会自动记录到 `失败日志.txt`


---

## 仓库索引标注（2026-08-25 · siwpan 统一治理）

> Python 依赖库安装器（master/2.0/3.0/4.0 迭代）；本地对应 G:\tools\dev\requirements。
> 本标注为发布面统一追加，不改变原有内容。
