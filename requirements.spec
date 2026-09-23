# -*- mode: python ; coding: utf-8 -*-
# foolproof-operation 便携版打包 spec（PyInstaller）
#
# 口径（2026-09-23 核对后重写）：
#   - **不写死绝对路径**：一律用 SPECPATH 推导（本文件所在目录 = 仓库根）；
#   - **datas 必须含运行期资源**：分类清单、插件清单、内置 es.exe（ES 后端要用）、LICENSE；
#   - **hiddenimports 必须含「动态导入」的模块**：插件清单是 JSON，按 "模块:属性" 字符串导入
#     （core.loader.import_plugin），PyInstaller 的静态分析看不见；漏了只在**打包版**运行时
#     报「无法导入插件模块」—— 此类缺陷 frozen 才暴露，故在此显式列出。
import os

ROOT = SPECPATH

a = Analysis(
    [os.path.join(ROOT, 'requirements.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'requirements_categories.json'), '.'),
        (os.path.join(ROOT, 'foolproof.composition.json'), '.'),
        (os.path.join(ROOT, 'LICENSE'), '.'),
        (os.path.join(ROOT, 'tools', 'everything'), os.path.join('tools', 'everything')),
    ],
    hiddenimports=[
        # foolproof.composition.json 中按字符串引用的插件模块
        'core.events',
        'core.tasklog',
        'core.audit',
        'core.runtime',
        'index.service',
        'plugins.categories',
        'plugins.pip',
        'plugins.installer',
        'plugins.reqtxt',
        'plugins.search',
        'plugins.scanner',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch','cupy','dask','jax','jaxlib','ndonnx','sparse','sqlalchemy','matplotlib','IPython','Cython','setuptools','traitlets','jedi','pygments','yt_dlp','playwright','botocore','boto3','transformers','tokenizers','pyarrow','llvmlite','numba','scipy','sklearn','scikit-learn','imageio','imageio_ffmpeg','tensorflow','keras','moviepy','sympy','statsmodels','nltk','networkx','PyQt5','PyQt6','PySide2','PySide6','pymongo','bson'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='requirements',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # CLI 子命令需要能看到输出（不带 cli 参数时仍会开 GUI 窗口）
    contents_directory='.',
)
