# -*- coding: utf-8 -*-
"""检查更新 / 下载新版本（只用标准库, 不依赖任何第三方包）。

launcher.py 里的用法：
    info = updater.latest()
    if updater.is_newer(info["version"], VERSION): ...
    path = updater.download(info["url"], info["asset"], progress=cb)
"""
import json
import os
import re
import zipfile
import urllib.request

REPO = "Ajiuwu101213/fortnite-auto-helper"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases" % REPO
UA = "fortnite-auto-helper-updater"


def _get_json(url, timeout=6):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def vtuple(s):
    nums = re.findall(r"\d+", str(s))
    return tuple(int(x) for x in nums[:4]) if nums else (0,)


def is_newer(latest_ver, current_ver):
    return vtuple(latest_ver) > vtuple(current_ver)


def latest(timeout=6):
    """返回 {version, page, asset, size, url}; 网络/接口异常会抛异常。"""
    d = _get_json(API_LATEST, timeout)
    tag = d.get("tag_name") or ""
    page = d.get("html_url") or RELEASES_PAGE
    assets = d.get("assets") or []
    pick = None
    for a in assets:
        if (a.get("name") or "").lower().endswith(".zip"):
            pick = a
            break
    if pick is None and assets:
        pick = assets[0]
    pick = pick or {}
    return {"version": tag, "page": page, "asset": pick.get("name") or "",
            "size": int(pick.get("size") or 0),
            "url": pick.get("browser_download_url") or ""}


def downloads_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    if not os.path.isdir(d):
        d = os.path.expanduser("~")
    return d


def download(url, asset_name, progress=None, timeout=30):
    """下载到「下载」文件夹，返回本地路径。progress(got, total) 用于进度。"""
    if not url:
        raise ValueError("没有下载地址")
    os.makedirs(downloads_dir(), exist_ok=True)
    dest = os.path.join(downloads_dir(), asset_name or "update.zip")
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    os.replace(tmp, dest)
    return dest


def verify_zip(path, min_size=1024 * 1024):
    """粗校验: 是不是完整 zip、大小是否可疑。"""
    try:
        if os.path.getsize(path) < min_size:
            return False
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None and len(z.namelist()) > 20
    except Exception:
        return False