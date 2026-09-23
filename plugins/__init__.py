"""foolproof-operation 的插件行（Cordis 式插件；每个模块可被清单按 ``模块:属性`` 引用）。

- ``search``：检索服务 —— 后端起决定作用：``index``（USN 索引）→ ``es``（Everything CLI）
  → ``walk``（目录遍历）。三者**都在**，按可用性回退，并如实记录「是哪个后端答的」；
- ``scanner``：环境发现 —— 用检索服务找证据文件（``python.exe``/``pyvenv.cfg``/``conda-meta``/
  ``site-packages``）→ 聚合成环境候选 → 可选深验证。

索引服务本身不需要本包：清单直接把 ``index.service:IndexService`` 作为一行（类式插件，
内核会实例化、provide 为 ``fileindex``、调 ``start``、并把 ``stop`` 登记为 disposer）。

装配方式见仓库根 ``foolproof.composition.json``；清单语义见 ``core/loader.py``。
"""
