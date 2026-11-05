# -*- coding: utf-8 -*-
"""plugin.py —— 导出音频 插件（页签 + 无界面任务）。

契约（host 约定）：
    setup(self, ctx)            宿主加载时调一次，拿到通行证
    build(self, ctx) -> QWidget   返回一个页签控件（PySide6 只在里面 import）
    run_task(self, song_ids=None) -> dict   无界面入口，必须不崩
    summary(self) -> str         给宿主状态栏用的简短摘要
    on_unload(self)              插件卸载/关页签时清理

硬规则：
    · 不 import 主程序；只用 ctx 与 qishui_core
    · 不自己拼账号路径；一律 ctx.account_path
    · PySide6 只在 build() 里 import
    · build() 全程防御式：任何异常都显示 QLabel，绝不带崩宿主
"""
from __future__ import annotations

from . import engine


class ExportPlugin(object):
    def __init__(self):
        self.ctx = None
        self._out_dir = None         # 用户选择的输出目录（None=默认账号目录）
        self._list = None            # 作品列表控件
        self._status = None          # 顶部状态 QLabel
        self._progress = None        # 进度条
        self._log = None             # 日志文本区
        self._worker = None          # 后台线程
        self._export_btn = None
        self._export_all_btn = None
        self._refresh_btn = None
        self._dir_btn = None
        self._last_exported = 0

    # ---------------------------------------------------------- setup
    def setup(self, ctx):
        self.ctx = ctx
        try:
            ctx.on_account_switch(self._on_switch)
        except Exception:
            pass

    def _on_switch(self, uid):
        # 切号后清空列表（下次 build 重新拉取）
        self._out_dir = None
        try:
            if self._list is not None:
                self._list.clear()
        except Exception:
            pass

    # ---------------------------------------------------------- build
    def build(self, ctx):
        # ★ PySide6 只在这里 import（模块顶层不碰）
        try:
            from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                                           QLabel, QPushButton, QListWidget,
                                           QProgressBar, QTextEdit, QFrame,
                                           QFileDialog)
            from PySide6.QtCore import Qt, QThread, Signal
            from PySide6.QtGui import QFont
        except Exception as e:   # 没有 PySide6 / 环境异常 → 用 QLabel 兜底
            from PySide6 import QtWidgets  # noqa
            return QtWidgets.QLabel("无法加载界面依赖：%s" % e)

        try:
            return self._build_ui(ctx, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                  QPushButton, QListWidget, QProgressBar,
                                  QTextEdit, QFrame, QFileDialog, Qt, QThread,
                                  Signal, QFont)
        except Exception as e:
            # 任何构造异常都不许冒泡到宿主
            try:
                err = QLabel("「导出音频」界面加载失败：%s" % e)
                err.setWordWrap(True)
                return err
            except Exception:
                return QWidget()

    def _build_ui(self, ctx, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                  QPushButton, QListWidget, QProgressBar, QTextEdit, QFrame,
                  QFileDialog, Qt, QThread, Signal, QFont):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # ---------- 顶部状态条 + 目录选择 + 刷新 ----------
        bar = QHBoxLayout()
        title = QLabel("导出音频")
        title.setFont(QFont("", 13))
        self._status = QLabel("")
        self._status.setStyleSheet("color:#888;")
        self._dir_btn = QPushButton("选择目录")
        self._dir_btn.clicked.connect(lambda: self._choose_dir(ctx, QFileDialog))
        self._refresh_btn = QPushButton("刷新列表")
        self._refresh_btn.clicked.connect(lambda: self._load_works(ctx))
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(self._status)
        bar.addWidget(self._dir_btn)
        bar.addWidget(self._refresh_btn)
        lay.addLayout(bar)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        lay.addWidget(line)

        # ---------- 作品列表（带勾选） ----------
        hint = QLabel("勾选要导出的作品，点「导出所选」；或直接「导出全部」。")
        hint.setStyleSheet("color:#666;")
        lay.addWidget(hint)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        lay.addWidget(self._list, 1)

        # ---------- 导出按钮 ----------
        btn_row = QHBoxLayout()
        self._export_btn = QPushButton("导出所选")
        self._export_all_btn = QPushButton("导出全部")
        self._export_btn.clicked.connect(lambda: self._do_export(ctx, False))
        self._export_all_btn.clicked.connect(lambda: self._do_export(ctx, True))
        btn_row.addWidget(self._export_btn)
        btn_row.addWidget(self._export_all_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        # ---------- 进度条 ----------
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        lay.addWidget(self._progress)

        # ---------- 日志 ----------
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        self._log.setFont(QFont("Consolas", 10))
        lay.addWidget(self._log, 1)

        # 首次自动拉列表（Qt 事件循环起来后再跑）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: self._load_works(ctx))

        return root

    # ---------------------------------------------------------- 路径
    def _default_dir(self, ctx):
        try:
            return ctx.account_path("导出", ctx.uid)
        except Exception:
            return None

    def _choose_dir(self, ctx, QFileDialog):
        try:
            d = QFileDialog.getExistingDirectory(None, "选择导出目录",
                                                 self._out_dir or "")
            if d:
                self._out_dir = d
                self._set_status("导出到：%s" % d)
        except Exception as e:
            self._logline("选择目录失败：%s" % e)

    # ---------------------------------------------------------- 状态/日志
    def _set_status(self, text):
        if self._status is not None:
            try:
                self._status.setText(text)
            except Exception:
                pass

    def _logline(self, text):
        if self._log is not None:
            try:
                self._log.append(text)
            except Exception:
                pass

    # ---------------------------------------------------------- 拉作品列表
    def _load_works(self, ctx):
        if self._worker is not None and getattr(self._worker, "isRunning", lambda: False)():
            return  # 防连点
        try:
            from PySide6.QtCore import QThread, Signal
        except Exception:
            return
        self._set_status("正在拉取作品列表…")
        Worker = self._make_list_worker(QThread, Signal, ctx)
        w = Worker()
        self._worker = w
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(False)
        w.finished.connect(lambda: self._on_works_loaded(ctx, w))
        w.start()

    def _make_list_worker(self, QThread, Signal, ctx):
        class _ListWorker(QThread):
            finished = Signal(object)
            def run(self_inner):
                try:
                    works = engine.list_works(ctx, uid=ctx.uid)
                except Exception as e:
                    works = {"error": str(e)}
                self.finished.emit(works)  # noqa
        return _ListWorker

    def _on_works_loaded(self, ctx, w):
        works = getattr(w, "result", None)
        if works is None:
            works = []
        if isinstance(works, dict) and works.get("error"):
            self._logline("拉取失败：%s" % works["error"])
            self._set_status("拉取失败")
            if self._refresh_btn is not None:
                self._refresh_btn.setEnabled(True)
            self._worker = None
            return
        if self._list is not None:
            self._list.clear()
            for wk in works:
                title = wk.get("title") or wk.get("song") or "未命名"
                item = self._list_item(title, wk.get("song"))
                self._list.addItem(item)
        self._set_status("共 %d 首作品，已勾选即可导出" % len(works))
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(True)
        self._worker = None

    def _list_item(self, title, song):
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt
        it = QListWidgetItem("%s  [%s]" % (title, song or "?"))
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Unchecked)
        try:
            it.setData(Qt.UserRole, song)
        except Exception:
            pass
        return it

    # ---------------------------------------------------------- 导出
    def _do_export(self, ctx, all_of_them):
        if self._worker is not None and getattr(self._worker, "isRunning", lambda: False)():
            return
        try:
            from PySide6.QtCore import QThread, Signal
        except Exception:
            return

        # 决定导出哪些 song_id
        song_ids = None
        if not all_of_them and self._list is not None:
            from PySide6.QtCore import Qt
            song_ids = []
            for i in range(self._list.count()):
                it = self._list.item(i)
                if it.checkState() == Qt.Checked:
                    sid = it.data(Qt.UserRole)
                    if sid:
                        song_ids.append(sid)
            if not song_ids:
                self._logline("请先勾选要导出的作品。")
                return

        out_dir = self._out_dir or self._default_dir(ctx)
        self._set_status("正在导出…")
        if self._export_btn is not None:
            self._export_btn.setEnabled(False)
            self._export_all_btn.setEnabled(False)

        Worker = self._make_export_worker(QThread, Signal, ctx, song_ids, out_dir)
        w = Worker()
        self._worker = w
        w.progress.connect(self._on_progress)
        w.finished.connect(lambda: self._on_export_done(ctx, w))
        w.start()

    def _make_export_worker(self, QThread, Signal, ctx, song_ids, out_dir):
        class _ExportWorker(QThread):
            finished = Signal(object)
            progress = Signal(int, int, str)
            def run(self_inner):
                def cb(done, total, title):
                    self.progress.emit(done, total, title)  # noqa
                try:
                    res = engine.export_works(ctx, song_ids=song_ids,
                                              out_dir=out_dir, progress_cb=cb)
                except Exception as e:
                    res = {"ok": False, "exported": 0,
                           "failed": [{"title": "", "reason": str(e)}],
                           "total": 0, "out_dir": out_dir}
                self.finished.emit(res)  # noqa
        return _ExportWorker

    def _on_progress(self, done, total, title):
        if self._progress is not None:
            try:
                pct = int(100.0 * done / total) if total else 0
                self._progress.setValue(pct)
            except Exception:
                pass
        self._logline("导出中 [%d/%d] %s" % (done, total, title))

    def _on_export_done(self, ctx, w):
        res = getattr(w, "result", None) or {}
        exp = res.get("exported", 0)
        failed = res.get("failed", []) or []
        out_dir = res.get("out_dir", "")
        self._logline("完成：成功 %d 首，失败 %d 首。" % (exp, len(failed)))
        if out_dir:
            self._logline("输出目录：%s" % out_dir)
        for f in failed[:20]:
            self._logline("  · 失败：%s —— %s" % (f.get("title", ""), f.get("reason", "")))
        self._set_status("导出完成：%d 成功 / %d 失败" % (exp, len(failed)))
        if self._progress is not None:
            self._progress.setValue(100)
        if self._export_btn is not None:
            self._export_btn.setEnabled(True)
            self._export_all_btn.setEnabled(True)
        self._last_exported = exp
        self._worker = None

    # ---------------------------------------------------------- 无界面任务
    def run_task(self, song_ids=None):
        """无界面入口：导出当前账号作品到默认目录，返回结构化结果 dict。

        可能被计划任务或 `python -m export --task` 调用。必须不崩：
        即使全部失败，也返回带 ok/exported/failed 的结构化 dict。
        """
        ctx = self.ctx
        if ctx is None:
            return {"ok": False, "exported": 0, "failed": ["ctx 未初始化"]}
        try:
            res = engine.export_works(ctx, song_ids=song_ids)
        except Exception as e:
            return {"ok": False, "exported": 0, "failed": [str(e)]}
        self._last_exported = res.get("exported", 0)
        return res

    # ---------------------------------------------------------- 摘要
    def summary(self):
        """给宿主状态栏用的简短摘要（无界面也可能被调用）。"""
        n = getattr(self, "_last_exported", 0)
        if n:
            return "导出音频：%d 首" % n
        return "导出音频：待运行"

    # ---------------------------------------------------------- 卸载
    def on_unload(self):
        try:
            if self._worker is not None:
                self._worker.quit()
                self._worker.wait(1000)
        except Exception:
            pass
        self.ctx = None
