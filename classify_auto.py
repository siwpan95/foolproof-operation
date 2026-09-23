# -*- coding: utf-8 -*-
"""把 requirements_categories.json 的「🔍 自动发现」分类归入功能分类。

用法：
    python classify_auto.py            # dry-run，只打印结果
    python classify_auto.py --write    # 写回 JSON
规则：
    - 显式映射优先；
    - 未显式映射的包兜底归入「📦 传递依赖」；
    - 已在其它分类出现的包不重复归类（自动发现里的重复项直接丢弃）。
"""
import json
import sys
from collections import OrderedDict
from pathlib import Path

BASE = Path(__file__).parent
JP = BASE / "foolproof_operation" / "requirements_categories.json"
AUTO = "🔍 自动发现"
DEP = "📦 传递依赖"

# ---------------- 显式映射表 ----------------
M = OrderedDict([
    ("🎮 游戏开发", [
        "panda3d-gltf", "panda3d-simplepbr", "pygame_gui", "pyopengl", "perlin-noise",
    ]),
    ("🤖 AI / 机器学习", [
        "onnxruntime", "safetensors", "tokenizers", "datasets", "einops", "kornia",
        "kornia_rs", "tensorboardx", "tensordict", "tensorflow-hub", "tf-keras",
        "torchvision", "torchsde", "xformers", "comet_ml", "mindpet", "pycocotools",
        "simsimd", "gradio", "gradio_client", "hf-gradio", "spandrel",
        "spandrel_extra_arches", "comfyui-manager", "comfyui-embedded-docs",
        "comfyui-workflow-templates-core", "comfyui-workflow-templates-json",
        "comfyui-workflow-templates-media-api", "comfyui-workflow-templates-media-assets-01",
        "comfyui-workflow-templates-media-image", "comfyui-workflow-templates-media-other",
        "comfyui-workflow-templates-media-video", "comfyui_frontend_package",
        "comfyui_workflow_templates",
        # ComfyUI 生态：comfy* 前缀同源，整组归此，勿按类名拆分
        "comfy-aimdo", "comfy-angle", "comfy-kitchen",
        # jupyter 生态（与已有 jupyter 条目保持一致）
        "ipython", "ipykernel", "ipywidgets", "jupyter-console", "jupyter-lsp",
        "jupyter_builder", "jupyter_client", "jupyter_core", "jupyter_server",
        "jupyter_server_terminals", "jupyterlab", "jupyterlab_pygments",
        "jupyterlab_server", "jupyterlab_widgets", "notebook", "nbclient",
        "nbconvert", "nbformat",
    ]),
    ("🌐 Web 开发", [
        "flask-login", "flask-cors", "flask-migrate", "flask-sqlalchemy", "flask-script",
        "flask-sock", "mako", "werkzeug", "email-validator", "fastapi-cli",
        "fastapi-cloud-cli", "grpcio", "html5tagger", "pydantic-extra-types",
        "python-engineio", "python-socketio", "simple-websocket", "websocket-client",
        "websocket", "waitress", "safehttpx", "sswg",
    ]),
    ("🕷️ 爬虫 / 网络", [
        "yt-dlp", "pysocks", "socks", "tldextract", "tld", "dnspython", "w3lib",
        "cssselect", "protego", "pyexecjs", "builtwith", "ddddocr", "twocaptcha",
        "pyppeteer", "pyquery", "readability", "justext", "htmldate", "feedparser",
        "feedfinder2", "newspaper3k", "trafilatura", "copyheaders", "proxy_fetcher",
        "requests-cache", "lxml_html_clean", "httpx2", "downloadkit", "datarecorder",
    ]),
    ("📊 数据可视化", [
        "brewer2mpl", "colorcet", "graphviz", "panel", "panel-material-ui", "param",
        "pydeck", "webcolors", "wordcloud", "xyzservices",
    ]),
    ("🗄️ 数据库", [
        "chromadb", "memcache", "mysql", "mysqlclient", "pypika", "pypika-tortoise",
    ]),
    ("🖼️ 图像 / 音视频", [
        "imageio-ffmpeg", "imagesize", "imutils", "opencv-python-headless", "pyqrcode",
        "qrcode", "pyzbar", "pypng", "pyclipper", "pytesseract", "rapidocr-onnxruntime",
        "fonttools", "python-vlc",
    ]),
    ("📄 办公自动化", [
        "pdfminer.six", "pypdfium2", "docx", "xlutils", "xlwings", "xlwt",
    ]),
    ("⚡ 异步 / 并发", [
        "twisted", "gevent", "geventhttpclient", "janus", "nest-asyncio",
        "nest-asyncio2", "pyzmq", "trio-websocket", "async-timeout",
    ]),
    ("🔐 加密 / 安全", [
        "pynacl", "argon2-cffi", "ecdsa", "oauthlib", "pyopenssl", "pyasn1",
        "pyasn1_modules", "pycryptodome", "pycryptodomex", "requests-oauthlib", "rsa",
        "service-identity", "truststore", "defusedxml", "groovy",
    ]),
    ("🛠️ 工具链", [
        "configargparse", "gitpython", "send2trash", "babel", "colorama", "debugpy",
        "jedi", "jsonschema", "lark", "posthog", "prompt_toolkit", "psutil",
        "python-json-logger", "python-i18n", "rich-toolkit", "termcolor", "watchfiles",
        "pygithub", "codetiming", "configobj", "depyf", "dulwich", "pygit2", "dingding",
        "itchat", "wechat_sender", "wxpy", "getmac", "py-cpuinfo", "progressbar", "sh",
        "simpleeval", "tabulate", "texttable", "tomlkit", "ruamel.yaml", "hydra-core",
        "omegaconf", "more-itertools", "pycodestyle", "prettytable", "backoff",
        "retrying", "tenacity", "pyparsing", "ast_serialize", "offshoot",
    ]),
    ("📝 自然语言处理", [
        "jieba3k", "rouge-chinese", "sentencepiece", "tinysegmenter", "word2number",
        "udapi", "udtools",
    ]),
    ("☁️ 云服务 / DevOps", [
        "google-auth", "azure-ai-inference", "azure-core", "googleapis-common-protos",
        "google-api-core", "google-cloud-core", "google-resumable-media", "ansible-core",
        "invoke", "botocore", "cloudpathlib", "opentelemetry-api", "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-common",
        "opentelemetry-exporter-otlp-proto-grpc", "prometheus_client",
        "prometheus-fastapi-instrumentator", "sentry-sdk", "smart_open",
    ]),
    ("🧪 自动化测试", [
        "allure-python-commons", "factory_boy", "pytest-aiohttp",
    ]),
    # ---- 新增分类 ----
    ("🖥️ GUI / 桌面应用", [
        "customtkinter", "pyperclip", "pyqt5", "pyqt5-qt5", "pyqt5_sip", "pyrect",
        "pyscreeze", "pygetwindow", "pymsgbox", "mouseinfo", "pytweening", "darkdetect",
        "pystray", "screeninfo", "mss", "keyboard", "pywin32", "pypiwin32", "pywinpty",
        "cefpython3", "comtypes", "urwid", "tkhtmlview", "tkinterweb",
        "tkinterweb-tkhtml", "tksheet", "win_unicode_console",
    ]),
    ("🎵 音频 / 音乐", [
        "audioop-lts", "sounddevice", "soundfile", "mido", "pretty_midi", "mutagen",
        "librosa", "demucs", "lameenc", "miniaudio", "music21", "noisereduce",
        "pedalboard", "pyfluidsynth", "pyttsx3", "soxr", "sphn", "standard-aifc",
        "standard-chunk", "standard-sunau", "torchaudio", "edge-tts", "julius",
    ]),
    ("🧠 LLM / Agent", [
        "google-genai", "langchain", "langchain-chroma", "langchain-core",
        "langchain-openai", "langchain-protocol", "langchain-text-splitters",
        "langgraph", "langgraph-checkpoint", "langgraph-prebuilt", "langgraph-sdk",
        "langsmith", "tiktoken", "ollama", "gguf", "peft", "mistral_common",
        "compressed-tensors", "xgrammar", "interegular", "lm-format-enforcer",
        "partial-json-parser", "sageattention",
    ]),
    ("🛡️ 安全 / 渗透", [
        "fofa", "impacket", "ldap3", "ldapdomaindump", "mitmproxy",
        "mitmproxy-windows", "mitmproxy_rs", "pydivert",
    ]),
    ("🔧 逆向 / 二进制", [
        "pefile", "uncompyle6", "xdis", "kaitaistruct",
    ]),
    ("📚 文档 / 文本处理", [
        "markdown", "markdown-it-py", "mdit-py-plugins", "mdurl", "linkify-it-py",
        "uc-micro-py", "docutils", "pygments", "pymdown-extensions", "tomd",
        "html2text", "chardet", "ftfy", "emoji", "dateparser", "text-unidecode",
        "regex", "bleach", "nh3", "mistune", "pycountry",
    ]),
    ("🔢 科学计算 / 数值", [
        "h5py", "mpmath", "networkx", "patsy", "pyarrow", "sympy", "llvmlite",
        "numba", "numexpr", "tables", "shapely", "trimesh", "latex2sympy2", "joblib",
    ]),
    ("📦 打包 / 构建", [
        "wheel", "build", "distlib", "pyproject-api", "pyproject_hooks", "resolvelib",
        "pip", "uv", "virtualenv", "setuptools", "cython", "pybind11", "pyinstall",
        "pyinstaller-hooks-contrib", "macholib", "altgraph", "py7zr",
        "semantic-version", "detect-installer", "pyvers",
    ]),
    # 「🏠 本地 / 自研包」为**手动维护**分类，不参与自动归类：
    #   判定标准——联网（PyPI / GitHub / Gitee）搜不到同名同项目才算自研，仅凭包名眼生不算。
    #   自动扫描不会填充它：_pypi_exists() 会过滤掉 PyPI 上不存在的包，纯自研包天然进不来。
    #   注意 PyPI 同名不同项目不算自研（如 alloy 在 PyPI 上是 Elide 的 High-performance runtime，
    #   与本地自研的 an-engine 无关 —— 该引擎 2026-09-19 已由 alloy 改名为 allin_engine）。
    #   该分类的 value 写**本地仓库路径**，如
    #       "allin-engine": "-e G:/repos/an-engine"、"bloodmusic": "-e G:/repos/audio-generation"；
    #   安装时 pip install <路径>，检查/卸载由 cli.pip_query_name() 回退到显示名。
    ("🏠 本地 / 自研包", []),
])

def norm(s):
    return s.lower().strip().replace("-", "_")

def main():
    write = "--write" in sys.argv
    data = json.loads(JP.read_text(encoding="utf-8"))
    auto = data.get(AUTO)
    if auto is None:
        raise SystemExit(f"未找到「{AUTO}」分类")

    # 其它分类已占用的规范名（避免跨分类重复）
    used = {}
    for cat, libs in data.items():
        if cat == AUTO:
            continue
        for name in libs:
            used[norm(name)] = cat

    # 显式映射表内部查重
    explicit = {}
    for cat, names in M.items():
        for n in names:
            if norm(n) in explicit:
                raise SystemExit(f"映射表重复：{n}（{explicit[norm(n)]} / {cat}）")
            explicit[norm(n)] = (cat, n)

    buckets = {cat: [] for cat in M}
    buckets[DEP] = []
    skipped_dup = []
    fallback = []

    for name in auto:
        k = norm(name)
        if k in used:
            skipped_dup.append((name, used[k]))
            continue
        if k in explicit:
            cat, disp = explicit[k]
            buckets[cat].append(disp)
        else:
            buckets[DEP].append(name)
            fallback.append(name)

    # ---- 报告 ----
    print(f"{'分类':<26} 条目")
    print("-" * 40)
    for cat, items in buckets.items():
        print(f"{cat:<26} {len(items)}")
    print("-" * 40)
    print(f"{'合计':<26} {sum(len(v) for v in buckets.values())}"
          f"（自动发现共 {len(auto)}）")

    print(f"\n[兜底 →「{DEP}」] {len(fallback)} 个：")
    print("  " + ", ".join(fallback))
    if skipped_dup:
        print(f"\n[跨分类重复，丢弃] {len(skipped_dup)} 个：")
        for n, c in skipped_dup:
            print(f"  - {n}（已在 {c}）")

    # 未覆盖检查
    covered = sum(len(v) for v in buckets.values()) + len(skipped_dup)
    assert covered == len(auto), f"覆盖数不符：{covered} != {len(auto)}"

    if not write:
        print("\n(dry-run，未写盘。加 --write 落盘)")
        return

    out = OrderedDict()
    for cat, libs in data.items():
        if cat == AUTO:
            continue
        out[cat] = dict(libs)
    for cat, items in buckets.items():
        if not items:
            continue
        # 合并进同名分类（现有分类追加），新分类直接建
        if cat in out:
            for n in items:
                out[cat][n] = n
        else:
            out[cat] = {n: n for n in items}
    out[AUTO] = {}          # 保留空兜底分类，供后续扫描落点
    JP.write_text(json.dumps(out, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"\n✅ 已写回 {JP.name}：{len(out)} 个分类，"
          f"{sum(len(v) for v in out.values())} 个条目")

if __name__ == "__main__":
    main()
