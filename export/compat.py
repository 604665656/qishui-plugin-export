# -*- coding: utf-8 -*-
"""compat —— 把「旧主程序模块名」重新导出成 qishui_core 的同名能力。

历史：移植自主程序里的 `export_work.py`，原文件直接 `import account_store /
api_client / datapath / cdp_lite`。插件**禁止** import 主程序模块，所以这里把
旧名字统一映射到 `qishui_core` 的对应模块，移植代码写到一半想引用旧名也不会
误触主程序。

⚠ 本插件真正的业务逻辑只走 `ctx`，compat 仅作「名字兼容层」与文档证据，
   不在运行时强依赖这些别名（避免无意间绕开 ctx 的限制）。
"""
from qishui_core import store as account_store   # 旧：import account_store as AS
from qishui_core import store as AS
from qishui_core.paths import data_root           # 旧：from datapath import base_dir
from qishui_core import api as api_client         # 旧：import api_client as A
from qishui_core import api as A
from qishui_core import cdp_lite as cdp_lite      # 旧：import cdp_lite as C
from qishui_core import cdp_lite as C

__all__ = ["account_store", "AS", "data_root",
           "api_client", "A", "cdp_lite", "C"]
