# tools/everything — ES（Everything 命令行搜索接口）

本目录内置 **ES 1.1.0.37**（[voidtools Everything](https://www.voidtools.com/) 的命令行
搜索接口），供本项目 CLI 的 `es` 子命令调用：

```cmd
python requirements.py cli es -n 20 "*.py"
python requirements.py cli es -s -ext doc;xls 报告
```

## 文件

- `es.exe` — ES 1.1.0.37 x64（复制自 Everything 1.5.0.1418b 便携包，未修改）
- `LICENSE` — ES 的 MIT 许可（© 2025 voidtools；源出自官方 ES-1.1.0.37.src.zip）

## 依赖（重要）

`es.exe` 需要本机 **Everything 主程序或服务正在运行**（默认连接 1.5a 实例）。
未运行时 es 返回错误码 8，本项目 CLI 会给出明确提示。

## 常用参数（节选，完整语法见官方 [ES README](https://www.voidtools.com/support/everything/command_line_interface/)）

| 参数 | 作用 |
|---|---|
| `-n <数量>` | 限制结果条数 |
| `-s` / `-d` | 按完整路径升/降序排序 |
| `-r` | 按运行次数降序 |
| `-ext <扩展名;...>` | 限定扩展名 |
| `-path <目录>` | 限定目录 |
| `-csv` | 以 CSV 输出 |
| `-size` / `-dm` | 显示大小 / 修改日期列 |
| `-instance <名>` | 连接指定 Everything 实例（默认 1.5a） |

搜索语法与 Everything 一致（通配符 `*`、`file:`、`folder:`、`ext:` 等）。

## 许可

- ES 本体：MIT License，© 2025 voidtools（见本目录 LICENSE）；
- Everything 主程序：voidtools 免费软件（不随本仓库分发，需用户自行安装/运行）。
