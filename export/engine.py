# -*- coding: utf-8 -*-
"""engine.py —— 导出音频 插件核心逻辑。

只依赖 ``ctx`` + 标准库 + ``requests``（惰性 import），不 import 主程序、
不 import PySide6、不自己拼账号路径。所有数据来自 ``ctx.api``（作品列表 +
单曲信息取音频直链），下载用 ``requests`` 带 cookie 直连（trust_env=False）。

任何一步失败都降级/跳过，绝不把异常冒泡到宿主（宿主已对 build/run_task
做整体 try/except，但 engine 自己先兜一层更稳）。
"""
from __future__ import annotations

import os
import re

AUDIO_EXT = (".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg")


# ----------------------------------------------------------------- 工具
def _safe_name(name):
    """把歌名转成安全的文件名（去 Windows 非法字符）。"""
    bad = '\\/:*?"<>|'
    s = "".join("_" if ch in bad else ch for ch in (name or "")).strip()
    return s[:80] or "未命名"


def _ts():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------- 拉作品
def _slim(w):
    """把一条 work-list 原始记录收成导出需要的字段。"""
    ii = w.get("IssueInfo") or {}
    return {
        "title": (w.get("Title") or "").strip(),
        "song": str(w.get("SongID") or ""),
        "task": str(w.get("TaskID") or ""),
        "vid": (w.get("VID") or "").strip(),
        "ct": int(w.get("CreateTime") or 0),
        "dur": int(w.get("Duration") or 0),
        "lyrics": w.get("Lyrics") or "",
        "issued": bool(ii.get("Issued")),
    }


def list_works(ctx, uid=None, page_size=1000):
    """读当前账号作品（含 SongID）。失败返回 []，不抛异常。"""
    out = []
    try:
        first = ctx.api.work_list(1, page_size, 2)
        total = int(first.get("total") or 0)
        out.extend(_slim(w) for w in (first.get("works") or []))
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        for p in range(2, pages + 1):
            r = ctx.api.work_list(p, page_size, 2)
            out.extend(_slim(w) for w in (r.get("works") or []))
    except Exception as e:
        try:
            ctx.warn("导出：拉取作品失败：%s" % e)
        except Exception:
            pass
    return out


# ----------------------------------------------------------------- 取直链
def fetch_clip_url(ctx, song_id):
    """取单曲音频直链（clips[].clipUrl 优先，或主音频 URL）。无则 None。"""
    if not song_id:
        return None
    try:
        r = ctx.api.song_info(song_id)
    except Exception:
        return None
    d = (r or {}).get("data") or {}
    # 1) clips 数组里的 clipUrl
    for c in (d.get("clips") or []):
        u = c.get("clipUrl") or c.get("url") or c.get("path") or ""
        if u and (u.startswith("http") or u.startswith("//")):
            if u.startswith("//"):
                u = "https:" + u
            return u
    # 2) 退路：单曲主音频字段
    for k in ("audioUrl", "playUrl", "url", "mp3Url"):
        u = d.get(k) or ""
        if u and (u.startswith("http") or u.startswith("//")):
            if u.startswith("//"):
                u = "https:" + u
            return u
    return None


# ----------------------------------------------------------------- 下载
def _cookie(ctx):
    """取下载用 cookie 串：优先 ctx.api.cookies()，失败回退 ctx.auth.cookies(port)。"""
    try:
        return ctx.api.cookies()
    except Exception:
        pass
    try:
        port = int(getattr(ctx, "port", 0) or 9222)
        return ctx.auth.cookies(port)
    except Exception:
        return None


def download(ctx, url, dest_path):
    """带 cookie 直连下载；trust_env=False。成功返回 True，否则 False。"""
    try:
        import requests
    except Exception as e:
        try:
            ctx.warn("导出：未安装 requests：%s" % e)
        except Exception:
            pass
        return False
    ck = _cookie(ctx)
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Referer": "https://music.douyin.com/"}
    if ck:
        hdr["Cookie"] = ck
    try:
        r = requests.get(url, headers=hdr, timeout=180, trust_env=False,
                         proxies={"http": None, "https": None})
        if r.status_code != 200 or len(r.content) < 1024:
            return False
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------- 导出
def export_works(ctx, song_ids=None, out_dir=None, uid=None, progress_cb=None):
    """导出给定（或全部）作品到 out_dir。返回结构化结果 dict，绝不抛异常。

    参数 progress_cb(done, total, title) 可选，每导出一首回调一次（用于 UI 进度条）。
    """
    uid = uid or ctx.uid
    if not out_dir:
        try:
            out_dir = ctx.account_path("导出", uid)
        except Exception:
            out_dir = os.path.join(os.getcwd(), "导出")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    works = list_works(ctx, uid=uid)
    if song_ids:
        wanted = set(str(x) for x in song_ids)
        works = [w for w in works
                 if w["song"] in wanted or w["task"] in wanted
                 or w["vid"] in wanted or w["title"] in wanted]

    exported = 0
    failed = []
    seen = set()
    total = len(works)
    for i, w in enumerate(works, 1):
        song = w["song"]
        if not song or song in seen:
            continue
        seen.add(song)
        title = w["title"] or song
        if callable(progress_cb):
            try:
                progress_cb(i, total, title)
            except Exception:
                pass
        url = fetch_clip_url(ctx, song)
        if not url:
            failed.append({"title": title, "song": song,
                           "reason": "找不到音频直链(clip 缺失)"})
            continue
        ext = ".m4a"
        base = url.split("?")[0]
        m = re.search(r"\.([a-zA-Z0-9]{2,4})$", base)
        if m:
            e = "." + m.group(1).lower()
            if e in AUDIO_EXT:
                ext = e
        dest = os.path.join(out_dir, _safe_name(title) + ext)
        if download(ctx, url, dest):
            exported += 1
            ly = w.get("lyrics") or ""
            if ly.strip():
                try:
                    with open(os.path.join(out_dir, _safe_name(title) + ".txt"),
                              "w", encoding="utf-8") as f:
                        f.write(ly)
                except Exception:
                    pass
        else:
            failed.append({"title": title, "song": song, "reason": "下载失败"})

    result = {
        "ok": len(failed) == 0,
        "exported": exported,
        "failed": failed,
        "total": total,
        "out_dir": out_dir,
        "ts": _ts(),
    }
    return result
