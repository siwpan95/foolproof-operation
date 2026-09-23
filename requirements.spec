# -*- mode: python ; coding: utf-8 -*-
# foolproof-operation 便携版打包 spec（PyInstaller）
#
# 口径（2026-09-23 按新包结构重写）：
#   - **不写死绝对路径**：一律用 SPECPATH 推导（本文件所在目录 = 仓库根）；
#   - 入口用仓库根 shim `requirements.py`（`python -m foolproof_operation` 的等价入口，
#     shim 自己会把仓库根加入 sys.path 再转交包内实现）；
#   - **datas 必须含运行期资源**：分类清单、插件清单、内置 es.exe（ES 后端要用）、LICENSE；
#   - **hiddenimports 必须含「动态导入」的模块**：插件清单是 JSON，按 "模块:属性" 字符串导入
#     （core.loader.import_plugin），PyInstaller 的静态分析看不见；漏了只在**打包版**运行时
#     报「无法导入插件模块」—— 此类缺陷 frozen 才暴露，故在此显式列出（路径须带包名）。
import os

ROOT = SPECPATH
PKG = os.path.join(ROOT, 'foolproof_operation')

a = Analysis(
    [os.path.join(ROOT, 'requirements.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(PKG, 'requirements_categories.json'), 'foolproof_operation'),
        (os.path.join(PKG, 'foolproof.composition.json'), 'foolproof_operation'),
        (os.path.join(ROOT, 'LICENSE'), '.'),
        (os.path.join(PKG, 'tools', 'everything'), os.path.join('foolproof_operation', 'tools', 'everything')),
    ],
    hiddenimports=[
        # foolproof.composition.json 中按字符串引用的插件模块（须带包名前缀）
        'foolproof_operation.core.events',
        'foolproof_operation.core.tasklog',
        'foolproof_operation.core.audit',
        'foolproof_operation.core.runtime',
        'foolproof_operation.index.service',
        'foolproof_operation.plugins.categories',
        'foolproof_operation.plugins.pip',
        'foolproof_operation.plugins.installer',
        'foolproof_operation.plugins.reqtxt',
        'foolproof_operation.plugins.search',
        'foolproof_operation.plugins.scanner',
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
