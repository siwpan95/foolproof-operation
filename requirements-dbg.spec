# -*- mode: python ; coding: utf-8 -*-
# foolproof-operation 调试构建 spec（console=True，输出名带 -dbg）
# 与 requirements.spec 同口径：SPECPATH 相对路径 + 包内资源 + 动态导入模块清单。
# 生成：python -m PyInstaller requirements-dbg.spec --noconfirm
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
    name='requirements-dbg',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory='.',
)