#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fortnite OCR 自动循环机器人
- 每 N 秒截屏 + Windows OCR(zh-Hans-CN) 识别全屏文字
- 状态机: LOBBY(开始) -> MATCHING(搜索服务器/取消) -> LOADING(连接中/大逃杀) -> BUS(船长是/空中巴士)
          -> INGAME(风暴圈/收集地图碎片) -> DEAD(观战/被淘汰) -> RESULTS(返回大厅/再匹配一局) -> LOBBY
- 需要动手时用 SendInput 点击/按空格, 否则只观察
用法:
  python fortnite_bot.py --replay a.png b.png     离线回放分类
  python fortnite_bot.py --once                  实时扫描一次(不操作)
  python fortnite_bot.py --loop                  正式循环(5 秒一次)
"""
import argparse
import ctypes
import io
import os
import random
import re
import shutil
import sys
import time
from ctypes import wintypes
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    r"""设置类文件放 %LOCALAPPDATA%\FortniteAutoBot —— 换新版本/重新解压也不会被覆盖"""
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'FortniteAutoBot')
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return ROOT


DATA_DIR = _data_dir()


def data_file(name):
    """可持久化的设置文件; 老版本放在程序目录里的同名文件会自动迁移过去"""
    new = os.path.join(DATA_DIR, name)
    old = os.path.join(ROOT, name)
    if not os.path.isfile(new) and os.path.isfile(old):
        try:
            shutil.copyfile(old, new)
        except Exception:
            return old
    return new
SHOT_DIR = os.path.join(ROOT, 'bot_shots')
LOG_PATH = os.path.join(ROOT, 'bot_log.txt')
LANG = 'zh-Hans-CN'
SCREEN_W, SCREEN_H = 2560, 1600

# 结算判定规则(用户指定): 全程不按 Esc、不做任何"主动退出"的操作,
# 只做一件事——屏幕上出现「返回大厅」就点它。
# 自己死了但队友还活着时, 对局没结束, 屏幕上没有「返回大厅」→ 继续观战;
# 等队友全死(或全队吃鸡)弹出结算页面, 页面里有「返回大厅」→ 这时才点。
DEATH_RULE = 'team'

import winocr                       # noqa: E402
from PIL import ImageGrab, Image, ImageStat   # noqa: E402

user32 = ctypes.WinDLL('user32', use_last_error=True)

# 强制 DPI 感知: 否则在 125%/150% 缩放的电脑上, 截图拿到的是"逻辑像素",
# 而 SendInput 用的是物理像素 → 点击会整体偏左上("点歪")。
def _dpi_aware():
    done = ''
    try:                                   # per-monitor v2 (Win10 1703+)
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return 'per-monitor-v2'
    except Exception:
        pass
    try:                                   # shcore: 1=系统级 2=per-monitor
        shcore = ctypes.WinDLL('shcore', use_last_error=True)
        if shcore.SetProcessDpiAwareness(2) == 0:
            return 'per-monitor'
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        done = 'system'
    except Exception:
        done = 'none'
    return done


DPI_MODE = _dpi_aware()

try:                                # 分辨率不写死: 用户换窗口大小也不用改代码
    SCREEN_W = user32.GetSystemMetrics(0) or 2560
    SCREEN_H = user32.GetSystemMetrics(1) or 1600
except Exception:
    SCREEN_W, SCREEN_H = 2560, 1600


# ---------------------------------------------------------------- 日志
_log_lines = 0


def log(msg):
    global _log_lines
    line = '[%s] %s' % (datetime.now().strftime('%H:%M:%S'), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        _log_lines += 1
        if _log_lines % 200 == 0 and os.path.getsize(LOG_PATH) > 8 * 1024 * 1024:
            os.replace(LOG_PATH, LOG_PATH.replace('.txt', '.1.txt'))
            _log_lines = 0
    except Exception:
        pass


# ---------------------------------------------------------------- 输入
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_void_p)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', _INPUTUNION)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 先声明原型, 否则 EnumWindows 回调拿到的 HWND 传回 API 会报类型错误
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.c_void_p]
user32.mouse_event.restype = None
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None


def hwnd_int(h):
    if h is None:
        return 0
    if isinstance(h, ctypes.c_void_p):
        return h.value or 0
    try:
        return int(h)
    except Exception:
        return 0


def list_windows():
    found = []

    def _cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 512)
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            found.append({'hwnd': hwnd_int(hwnd), 'cls': cls.value, 'title': title.value,
                          'w': r.right - r.left, 'h': r.bottom - r.top})
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


GAME_PROC_NAMES = (
    'fortniteclient-win64-shipping.exe',
    'fortniteclient-win64-shipping_eac.exe',
    'fortniteclient-win64-shipping_be.exe',
    'fortnitelauncher.exe',
)


def game_process_pids():
    """用任务管理器那套(CreateToolhelp32Snapshot)扫进程, 找堡垒之夜相关 exe"""
    pids = set()
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [('dwSize', ctypes.c_ulong), ('cntUsage', ctypes.c_ulong),
                        ('th32ProcessID', ctypes.c_ulong), ('th32DefaultHeapID', ctypes.c_void_p),
                        ('th32ModuleID', ctypes.c_ulong), ('cntThreads', ctypes.c_ulong),
                        ('th32ParentProcessID', ctypes.c_ulong), ('pcPriClassBase', ctypes.c_long),
                        ('dwFlags', ctypes.c_ulong), ('szExeFile', ctypes.c_wchar * 260)]

        k = ctypes.windll.kernel32
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        snap = k.CreateToolhelp32Snapshot(2, 0)          # TH32CS_SNAPPROCESS
        if not snap or snap == -1:
            return pids
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
            while ok:
                name = (entry.szExeFile or '').lower()
                if name in GAME_PROC_NAMES:
                    pids.add(int(entry.th32ProcessID))
                ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
        finally:
            k.CloseHandle(ctypes.c_void_p(snap))
    except Exception:
        pass
    return pids


def foreground_title():
    """当前前台窗口标题(诊断用: PAUSED 时告诉用户是谁抢了前台)"""
    try:
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value or ''
    except Exception:
        return ''


def game_process_alive():
    return bool(game_process_pids())


def _window_pid(hwnd):
    try:
        pid = wintypes.DWORD() if False else None
        import ctypes
        from ctypes import wintypes as wt
        out = wt.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd_int(hwnd), ctypes.byref(out))
        return int(out.value)
    except Exception:
        return 0


def find_game_window():
    """先按窗口类名/标题找; 找不到就按"属于堡垒之夜进程的窗口"找(窗口标题被改也能认出来)"""
    wins = list_windows()
    cand = [w for w in wins if w['cls'] == 'UnrealWindow' and w['w'] > 800]
    if not cand:
        cand = [w for w in wins if 'fortnite' in w['title'].lower()]
    if not cand:
        pids = game_process_pids()
        if pids:
            cand = [w for w in wins
                    if w['w'] > 400 and w['h'] > 300 and _window_pid(w['hwnd']) in pids]
    if not cand:
        return None
    return max(cand, key=lambda w: w['w'] * w['h'])['hwnd']


def game_window_alive(hwnd):
    """窗口句柄还有效吗(游戏被关掉后句柄会失效)"""
    try:
        return bool(hwnd) and bool(user32.IsWindow(hwnd))
    except Exception:
        return False


def focus_game(hwnd):
    if not hwnd:
        return False
    if hwnd_int(user32.GetForegroundWindow()) == hwnd:
        return True
    user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.30)
    if hwnd_int(user32.GetForegroundWindow()) == hwnd:
        return True
    user32.keybd_event(0x12, 0, 0, None)          # ALT 轻按, 破解前台锁定
    time.sleep(0.03)
    user32.keybd_event(0x12, 0, 2, None)
    time.sleep(0.10)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return hwnd_int(user32.GetForegroundWindow()) == hwnd


def click(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, None)     # LEFTDOWN
    time.sleep(0.07)
    user32.mouse_event(0x0004, 0, 0, 0, None)     # LEFTUP


def press_scan(vk):
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    down = INPUT(type=1)
    down.u.ki = KEYBDINPUT(0, scan, 0x0008, 0, None)
    n1 = user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(0.05)
    up = INPUT(type=1)
    up.u.ki = KEYBDINPUT(0, scan, 0x0008 | 0x0002, 0, None)
    n2 = user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    return bool(n1 == 1 and n2 == 1)


def hold_scan(vk, ms):
    """按住某个键 ms 毫秒再松开(走动用)"""
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    down = INPUT(type=1)
    down.u.ki = KEYBDINPUT(0, scan, 0x0008, 0, None)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(max(0.05, ms / 1000.0))
    up = INPUT(type=1)
    up.u.ki = KEYBDINPUT(0, scan, 0x0008 | 0x0002, 0, None)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


def mouse_look(dx, dy):
    """相对移动鼠标 = 转视角"""
    user32.mouse_event(0x0001, int(dx), int(dy), 0, None)


MOUSE_STEP = 0.011          # 每小步约 11ms, 接近 90Hz 鼠标回报


def mouse_look_smooth(dx, dy, dur=None, halt=None, ease=True, speed=None):
    """平滑转视角: 把一次大位移拆成几十个小步鼠标移动(约 90Hz), 缓入缓出。

    dx,dy : 总位移(像素), 符号 = 方向
    dur   : 总时长(秒); 不给就按真人速度(1500~2600 px/s)推算
    halt  : 可选回调, 返回 True 时立刻中断(用于 F9 停止)
    返回 True = 走完了整段, False = 被 halt 中断
    """
    dx = float(dx)
    dy = float(dy)
    if max(abs(dx), abs(dy)) < 1.0:
        return True
    if dur is None:
        spd = speed if speed else random.uniform(2600.0, 4200.0)   # 计数/秒(比之前快一截)
        dur = max(0.16, min(1.4, max(abs(dx), abs(dy)) / spd))
    n = max(4, int(dur / MOUSE_STEP))          # 拆成多少小步
    cur_x = cur_y = 0
    for i in range(n):
        if halt is not None and halt():
            return False
        t = (i + 1) / float(n)
        # 缓入缓出: 起手/收尾慢、中间快, 和真手腕发力接近
        e = (t * t * (3.0 - 2.0 * t)) if ease else t
        tx = int(round(dx * e))
        ty = int(round(dy * e))
        mx, my = tx - cur_x, ty - cur_y
        if mx or my:
            user32.mouse_event(0x0001, mx, my, 0, None)
            cur_x, cur_y = tx, ty
        time.sleep(MOUSE_STEP * random.uniform(0.85, 1.15))
    rx, ry = int(round(dx)) - cur_x, int(round(dy)) - cur_y
    if rx or ry:                                # 收尾补足(一般没有)
        user32.mouse_event(0x0001, rx, ry, 0, None)
    return True


def mouse_left():
    """单击鼠标左键(局内 = 开一枪/挥一下)"""
    user32.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, None)



def key_down_scan(vk):
    """按下某个键不松开(配合鼠标同时操作, 用来一边走一边转视角)"""
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(0, scan, 0x0008, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key_up_scan(vk):
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(0, scan, 0x0008 | 0x0002, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def mouse_left_hold(ms):
    """按住鼠标左键 ms 毫秒再松开(局内 = 长按开火/连续挥击)"""
    user32.mouse_event(0x0002, 0, 0, 0, None)
    time.sleep(max(0.05, ms / 1000.0))
    user32.mouse_event(0x0004, 0, 0, 0, None)


VK_SPACE = 0x20
VK_ESC = 0x1B              # ESC(打开局内菜单/退出用)
VK_F9 = 0x78        # 停止自动打
VK_F10 = 0x79       # 开始自动打
# 反挂机小动作用的键(局内偶尔动一下, 防止游戏判定没人操作)
VK_W, VK_A, VK_S, VK_D = 0x57, 0x41, 0x53, 0x44
VK_LSHIFT = 0xA0           # 左 Shift(冲刺)
VK_RSHIFT = 0xA1           # 右 Shift
VK_LCONTROL_REAL = 0xA2    # 左 Ctrl
VK_LCONTROL = 0xA2         # 左 Ctrl(蹲走)  ← 修正: 0xA1 其实是右Shift
VK_RCONTROL = 0xA3         # 右 Ctrl
VK_LALT = 0xA4             # 左 Alt
VK_RALT = 0xA5             # 右 Alt
VK_F = 0x46         # 堡垒之夜默认: F = 互动/使用
VK_B = 0x42         # 堡垒之夜默认: B = 表情/跳舞
VK_1 = 0x31         # 表情轮盘里选第 1 个表情
VK_2, VK_3, VK_4, VK_5 = 0x32, 0x33, 0x34, 0x35   # 切换武器/物品

# ==== 按键绑定 (v1.6.8) ====================================================
VK_CROUCH = VK_LCONTROL       # 下蹲
VK_JUMP = VK_SPACE            # 跳跃
VK_SPRINT = VK_LSHIFT         # 奔跑
VK_PICKAXE = 0x31             # 稿子(采集工具) —— 堡垒之夜默认 1
VK_EMOTE = 0x42               # 动作(表情/跳舞) —— 堡垒之夜默认 B
VK_INTERACT = 0x45            # 交互(搜索/开箱/拾取/救援/召回) —— v1.12.4 起默认 E
KEY_BINDINGS = {              # 中文名 -> 默认 VK (下蹲已取消; v1.12.6 去掉没用的召回键)
    '跳跃': VK_JUMP, '奔跑': VK_SPRINT, '稿子': VK_PICKAXE,
    '动作': VK_EMOTE, '交互': VK_INTERACT,
}


KEY_TEXT_MAP = {
    '空格': 0x20, 'space': 0x20, '空格键': 0x20, 'spacebar': 0x20,
    '回车': 0x0D, 'enter': 0x0D, 'return': 0x0D,
    'esc': 0x1B, 'escape': 0x1B, 'tab': 0x09,
    'ctrl': 0xA2, 'control': 0xA2, '左ctrl': 0xA2, '左control': 0xA2, 'leftctrl': 0xA2,
    '右ctrl': 0xA3, 'rightctrl': 0xA3,
    'shift': 0xA0, '左shift': 0xA0, 'leftshift': 0xA0,
    '右shift': 0xA1, 'rightshift': 0xA1,
    'alt': 0xA4, '左alt': 0xA4, '左menu': 0xA4,
    '右alt': 0xA5, '右menu': 0xA5,
    '上方向': 0x26, '下方向': 0x28, '左方向': 0x25, '右方向': 0x27,
    '上': 0x26, '下': 0x28, '左': 0x25, '右': 0x27,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27,
    '鼠标左键': 0x01, '鼠标右键': 0x02,
}
for _i in range(1, 13):
    KEY_TEXT_MAP['f%d' % _i] = 0x6F + _i


def _parse_key(txt, default):
    """设置.txt 里的按键: 支持 0x11 / 17 / 单个字母数字 / 中文名(左shift、空格…)"""
    t = (txt or '').strip()
    if not t:
        return default
    low = t.lower().replace(' ', '')
    if low in KEY_TEXT_MAP:
        return KEY_TEXT_MAP[low]
    if len(low) == 1 and low.isdigit():          # 单个数字 = 数字键(3 -> 0x33), 不是 VK 3
        return 0x30 + int(low)
    try:
        if t.lower().startswith('0x'):
            v = int(t, 16)
        elif t.isdigit():
            v = int(t)                          # 多位数 = 十进制 VK (例如 160 = 左Shift)
        elif len(t) == 1 and t.isascii():
            v = ord(t.upper())
        else:
            return default
        return v if 1 <= v <= 0xFF else default
    except Exception:
        return default


AFTER_MATCH_DEFAULT = 'again'      # 结算后行为: again=点「再玩一次」(默认) / lobby=点「返回大厅」
AGAIN_WORDS = ('再玩一次', '再来一局', '再匹配一局', '再匹配', '再来一次')


def load_after_match():
    """结算后行为: again(直接再开一局, 默认) / lobby(回大厅)"""
    try:
        for ln in io.open(data_file('设置.txt'), encoding='utf-8-sig'):
            ln = ln.strip()
            if ln.startswith('after_match') and '=' in ln:
                v = ln.split('=', 1)[1].strip().lower()
                if v in ('lobby', 'again'):
                    return v
    except Exception:
        pass
    return AFTER_MATCH_DEFAULT


AFTER_MATCH = AFTER_MATCH_DEFAULT


def load_squad_mode():
    """退出重排策略: alive=全程(局内只剩 2 人也退) / start=只在开局前"""
    try:
        for ln in io.open(data_file('设置.txt'), encoding='utf-8-sig'):
            ln = ln.strip()
            if ln.startswith('squad_mode') and '=' in ln:
                v = ln.split('=', 1)[1].strip().lower()
                if v in ('alive', 'start'):
                    return v
    except Exception:
        pass
    return SQUAD_MODE_DEFAULT


def load_key_bindings():
    """从 设置.txt 读 key_下蹲 / key_跳跃 / key_奔跑 / key_稿子, 缺省用默认"""
    path = data_file('设置.txt')
    cfg = {}
    try:
        if os.path.isfile(path):
            for line in io.open(path, encoding='utf-8-sig', errors='ignore'):
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                cfg[k.strip().lower()] = v.strip()
    except Exception:
        cfg = {}
    for name in list(KEY_BINDINGS.keys()):
        KEY_BINDINGS[name] = _parse_key(cfg.get('key_' + name), KEY_BINDINGS[name])
    if KEY_BINDINGS.get('下蹲') == 0xA1:      # 旧版本写过 0xA1(其实是右Shift), 当左Ctrl迁移
        KEY_BINDINGS['下蹲'] = 0xA2
    # 逐项刷新全局 VK 常量: 缺项就跳过(比如 v1.7.2 起「下蹲」已按用户要求取消,
    # 但老版本 设置.txt 里可能还留着 key_下蹲, 这里不能因为缺键就 KeyError 崩掉)
    for _name, _gname in (('下蹲', 'VK_CROUCH'), ('跳跃', 'VK_JUMP'), ('奔跑', 'VK_SPRINT'),
                          ('稿子', 'VK_PICKAXE'), ('动作', 'VK_EMOTE'),
                          ('交互', 'VK_INTERACT')):
        if _name in KEY_BINDINGS:
            globals()[_gname] = KEY_BINDINGS[_name]
    return dict(KEY_BINDINGS)


def vk_label(vk):
    """0x43 -> 'C'; 0x20 -> '空格'; 其它 -> '0xNN'"""
    try:
        v = int(vk)
    except Exception:
        return str(vk)
    if 0x41 <= v <= 0x5A:
        return chr(v)
    if 0x30 <= v <= 0x39:
        return chr(v)
    return {0x20: '空格', 0xA0: '左Shift', 0xA1: '左Ctrl', 0x0D: '回车',
            0x09: 'Tab', 0x1B: 'Esc'}.get(v, '0x%02X' % v)


def key_vk(name_or_vk):
    """'下蹲' / 'VK_SPRINT' / 0x11 都能取到 VK"""
    if isinstance(name_or_vk, int):
        return name_or_vk
    n = str(name_or_vk)
    if n in KEY_BINDINGS:
        return KEY_BINDINGS[n]
    if n.upper() in globals() and isinstance(globals()[n.upper()], int):
        return globals()[n.upper()]
    return None


def press_key(name_or_vk):
    """点一下某个绑定键(下蹲/跳跃/奔跑/稿子 或任意 VK)"""
    vk = key_vk(name_or_vk)
    if vk is None:
        return False
    return press_scan(vk)


def hold_key(name_or_vk, ms):
    """按住某个绑定键 ms 毫秒"""
    vk = key_vk(name_or_vk)
    if vk is None:
        return False
    key_down_scan(vk)
    try:
        time.sleep(max(0, ms) / 1000.0)
    finally:
        key_up_scan(vk)
    return True


def pickaxe():
    """换成稿子(采集工具)"""
    return press_key('稿子')

# ==========================================================================
VIEW_PX_PER_DEG = 28.0     # 兜底值: 1 度 ≈ 多少屏幕像素(FOV≈92° 时的 2560 宽)
FOV_HDEG = 92.0            # 堡垒之夜水平视野(度), 用来按屏幕宽度换算 像素/度
SQUAD_MIN = 3               # 队伍人数下限(含自己): 不足就退出重排
TEAMMATE_ALIVE_SEC = 45.0   # 多少秒内看见过队友的名字牌, 就认为他"还活着"(可在 设置.txt 用 teammate_alive_sec 改)
SQUAD_CHECK_STATES = ('WAITING',)          # v1.9.4: 巴士上看不到名字/血量, 不能查
# 全程模式(默认): 开局后如果队伍栏只剩 2 人也退出重排 —— 可在 [12] 里改回"只在开局前"
SQUAD_ALL_STATES = ('WAITING', 'INGAME')   # v1.9.4: 只有"能看到队伍栏名字"的状态才查
SQUAD_MODE_DEFAULT = 'alive'
MENU_HAMBURGER_REL = (0.874, 0.062)       # 局内 ESC 侧栏右上角 ☰ 的相对位置(2560x1600 实测 2239,99)
LEAVE_WORDS = ('返回大厅', '离开对局', '退出对局')
NAME_WATCH_SEC = 60.0      # v1.12.9: 局内连续多少秒看不到自己的名字 -> 退回大厅重排(0=关闭)
SPACE_WORDS = ('空格',)            # v1.12.10: 画面出现「空格」字样(跳出/跳伞/上升提示)
SPACE_BLOCK_SEC = 3.0              # 出现后继续封多少秒, 期间挂机动作里带空格的一律不做
CONFIRM_WORDS = ('确认', '确定', '是', '离开', '退出')
SQ_LIST_RELX = 0.32        # 左下角队伍栏右边界(占屏宽比例)
SQ_LIST_RELY = 0.60        # 左下角队伍栏上边界(占屏高比例)
FIDGET_INTERVAL = 15.0     # 每隔多少秒来一串反挂机动作
FIDGET_FOLLOW_WAIT = 1.4   # 朝队友走时, 每次转向后先走多久再做下一次修正
FIDGET_EMOTE_WAIT = 10.0   # 按过 B(表情)之后要等多久才做下一步
LOBBY_CLICK_CD = 3.0       # 两次点「开始」之间至少间隔多少秒(可在 设置.txt 改)
RESULTS_CLICK_CD = 3.0     # 两次点「返回大厅」之间至少间隔多少秒(可在 设置.txt 改, 上限 3 秒)
MAX_CLICK_CD = 3.0         # 所有点击间隔的硬上限: 用户要求任何点击都不超过 3 秒
CLICK_REACT_MIN = 0.8      # 看到按钮文字后, 先"反应"一会儿再点(更像真人)
CLICK_REACT_MAX = 2.0
# 说明: 本程序会按: 空格(跳伞); 局内反挂机时的 W/A/S/D、B、鼠标左键/转向。
# 不按 Esc / 不按回车 / 不做任何"离开对局"的键鼠操作, 退出只有一个途径:
# 结算页面上出现「返回大厅」, 鼠标点它(且只在结算页点)。反挂机动作只在 INGAME 画面做。

# 弹窗上常见的"确认"字样(宝藏代币结算、奖励领取、断线重连都靠它)
# 注意: 不要放 'ok' 这类 2 字母词, 会误命中玩家名(如 lookism 里的 ok)
# 「局内」判定的两组词:
#   INGAME_HUD  : 只有对局 HUD 上才有的文字(任务进度条)
#   NOT_INGAME  : 只要屏幕上出现任何一个, 就绝对不是"纯粹的对局画面"(大厅/菜单/加载/结算/面板)
# 宁可判错成"不是局内"(那样只是不动、不操作, 是安全的), 也不能把局外画面当局内乱按。
# 屏幕顶部方位条(对局中且未死亡时才有): 东北/东南 + 角度数字
COMPASS_DIRS = ('东北', '东南', '西南', '西北', '北', '东', '南', '西')

# 观战特征词: 屏幕上方「正在观战 XXX」、底部「上一个队友/下一个队友」、死亡后「等待重启」
# 这些只在「自己已死、正在看队友」时出现, 所以出现任何一个就绝不点「返回大厅」。
SPECTATE_KWS = ('正在观战', '正在旁观', '正在观看', '观战', '旁观',
                '上一个队友', '下一个队友', '等待重启', '退出观战', '继续观战',
                '返回观战', '观战队友', '观看队友')

# 疑似结算页先观察几轮(轮询间隔约 2 秒)。0 = 不观察直接点
RESULTSQ_WAIT_ROUNDS = 4
INGAME_REGION_W = 900   # 任务条区域: 左上角 900x300
INGAME_REGION_H = 300
BAR_SEARCH_REL = (0.010, 0.850, 0.200, 1.000)  # 找血条的搜索区(相对屏幕, 兼容UI缩放)
HP_BAR_REL = (0.030, 0.928, 0.130, 0.943)   # 旧版固定条(保留兼容)
PLAYER_NAME = 'YOUR_GAME_NAME'                  # 自己的游戏ID(可用 --player-name 覆盖)
LAX_NAME = False                                # 宽松模式: 认不到名字也做反挂机(默认关)
SELF_BOX_REL = (0.0, 0.68, 0.42, 1.0)           # 左下角队伍列表里自己名字的位置(相对屏幕)
INGAME_HUD = ('搜集地图碎片', '收集地图碎片', '打开宝箱', '风暴圈', '剩余')
NOT_INGAME = ('商城', '仓库', '通行证', '任务', '生涯', '返回大厅', '进入睡眠模式',
              '已进入休眠模式', '继续游戏', '搜索服务器', '连接中', '正在等待玩家',
              '船长是', '司机平稳', '比赛回顾', '你的排名', '继续和此小队', '再玩一次',
              '再匹配', '零建造', '装备一个精灵', '队伍成员', '可加入的队伍', '好友',
              '部署卓耿', '空格跳')

CONFIRM_KWS = ('确定', '确认', '领取', '知道了', '好的')
MODAL_HINTS = ('宝藏', '挖到', '代币', '奖励', '获得', '领取',
               '重新连接', '重连', '连接已断开', '重试', '断开')

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

WM_HOTKEY = 0x0312
_HK_ID_F10 = 0xB10
_HK_ID_F9 = 0xB09
_hk_hook_ok = [None]

try:
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
except Exception:
    pass


_hk_last = {}


def _queue_hotkey(name, how):
    now = time.time()
    if now - _hk_last.get(name, 0.0) < 0.6:      # 去抖: 同一次按键只算一次
        return
    _hk_last[name] = now
    if _hk_lock is None:
        _hk_queue.append(name)
    else:
        with _hk_lock:
            _hk_queue.append(name)
    log('[热键] 识别到 %s (%s)' % ('F10 (开始)' if name == 'start' else 'F9 (停止)', how))


_hk_prev = {}


def key_down(vk):
    return (user32.GetAsyncKeyState(vk) & 0x8000) != 0


def key_pressed(vk):
    """用 GetAsyncKeyState 的低比特: 自上次调用以来按过一次(不依赖轮询速度)"""
    return (user32.GetAsyncKeyState(vk) & 0x0001) != 0


_hk_prev = {}


_hk_queue = []
_hk_lock = None
_hk_thread = [None]


def hotkey_status():
    """给 /--hotkeys 和启动日志用"""
    if _hk_hook_ok[0] is None:
        return '未初始化'
    a, b = _hk_hook_ok[0]
    if a and b:
        return '全局热键已注册 (F9/F10 任何窗口下都有效)'
    if a or b:
        return '部分注册成功 (F10=%s F9=%s), 轮询兜底' % (a, b)
    return '全局热键注册失败, 只靠轮询(游戏全屏时可能不灵)'


def start_hotkey_thread():
    """独立线程每 40ms 看一次 F9/F10, 不再依赖主循环的节奏.
    以前只在主循环/_nap 里轮询, 一遇到 8~20 秒的动作串就反应迟钝;
    现在即使主循环正忙, 按键也会被立刻记下来(排队), 下一轮立刻生效."""
    global _hk_lock
    if _hk_thread[0] is not None:
        return
    import threading
    _hk_lock = threading.Lock()
    stop_ev = threading.Event()

    def _watch():
        prev = {VK_F10: False, VK_F9: False}
        while not stop_ev.is_set():
            for vk, name in ((VK_F10, 'start'), (VK_F9, 'stop')):
                try:
                    st = user32.GetAsyncKeyState(vk) & 0xFFFF
                except Exception:
                    continue
                held = bool(st & 0x8000)
                edge = bool(st & 0x0001)
                if edge or (held and not prev.get(vk, False)):
                    _queue_hotkey(name, '轮询')
                prev[vk] = held
            time.sleep(0.04)

    t = threading.Thread(target=_watch, name='hotkey-watch', daemon=True)
    t.start()
    _hk_thread[0] = t
    _start_hotkey_hook(threading, stop_ev)


def _start_hotkey_hook(threading, stop_ev):
    """RegisterHotKey 全局热键: 不管前台是谁(包括游戏全屏)都能收到 F9/F10.
    比轮询可靠; 注册失败(被别的程序占用)就退回轮询线程."""
    def _loop():
        ok10 = user32.RegisterHotKey(None, _HK_ID_F10, 0, VK_F10)
        ok9 = user32.RegisterHotKey(None, _HK_ID_F9, 0, VK_F9)
        _hk_hook_ok[0] = (bool(ok10), bool(ok9))
        log('[热键] RegisterHotKey: F10=%s F9=%s%s'
            % ('OK' if ok10 else 'FAIL', 'OK' if ok9 else 'FAIL',
               '' if (ok10 and ok9) else '（失败可能被其它程序占用, 已用轮询兜底）'))
        if not (ok10 or ok9):
            return
        msg = wintypes.MSG()
        while not stop_ev.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                try:
                    _queue_hotkey('start' if msg.wParam == _HK_ID_F10 else 'stop', 'RegisterHotKey')
                except Exception:
                    pass

    t = threading.Thread(target=_loop, name='hotkey-hook', daemon=True)
    t.start()


def poll_hotkeys():
    """返回 'start' / 'stop' / None
    优先读独立线程排的队; 队列空时再直接看一次按键(兜底)."""
    global _hk_lock
    if _hk_queue:
        if _hk_lock is None:
            return _hk_queue.pop(0)
        with _hk_lock:
            if _hk_queue:
                return _hk_queue.pop(0)
    if _hk_thread[0] is not None and (_hk_hook_ok[0] is not None or True):
        return None       # 独立线程/全局热键在跑: 只认队列, 避免重复触发
    hit_start = hit_stop = False
    for vk, name in ((VK_F10, 'start'), (VK_F9, 'stop')):
        st = user32.GetAsyncKeyState(vk) & 0xFFFF
        edge = bool(st & 0x0001)          # 自上次调用以来按过
        held = bool(st & 0x8000)          # 此刻正按着
        was = _hk_prev.get(vk, False)
        _hk_prev[vk] = held
        if edge or (held and not was):
            if name == 'start':
                hit_start = True
            else:
                hit_stop = True
    if hit_start:
        return 'start'
    if hit_stop:
        return 'stop'
    return None


CONTROL_FILE = os.path.join(ROOT, '控制.txt')


def control_command():
    """启动器写的控制文件: start / stop (读完就清空, 只生效一次)"""
    try:
        if not os.path.isfile(CONTROL_FILE):
            return None
        txt = io.open(CONTROL_FILE, encoding='utf-8-sig', errors='ignore').read().strip().lower()
        if not txt:
            return None
        try:
            io.open(CONTROL_FILE, 'w', encoding='utf-8').write('')
        except Exception:
            pass
        if 'stop' in txt or '停' in txt:
            return 'stop'
        if 'start' in txt or '开始' in txt:
            return 'start'
    except Exception:
        pass
    return None


def poll_control():
    """键盘热键 + 启动器控制文件, 两路任一触发都算"""
    return poll_hotkeys() or control_command()


# ---------------------------------------------------------------- 截图 + OCR
_SIZE_WARNED = [False]


def grab():
    img = ImageGrab.grab(bbox=(0, 0, SCREEN_W, SCREEN_H))
    if img.size != (SCREEN_W, SCREEN_H):
        # 极少数情况(第三方缩放/远程桌面): 截图是逻辑像素, 拉伸到物理像素,
        # 这样后面 OCR 出来的坐标点击时才是准的
        if not _SIZE_WARNED[0]:
            _SIZE_WARNED[0] = True
            log('WARN 截图尺寸 %dx%d 与系统像素 %dx%d 不一致, 已按比例换算坐标(DPI=%s)'
                % (img.size[0], img.size[1], SCREEN_W, SCREEN_H, DPI_MODE))
        img = img.resize((SCREEN_W, SCREEN_H), Image.BILINEAR)
    return img


def view_px_per_deg():
    """1 度水平视角 ≈ 多少屏幕像素. 按屏幕宽度和水平 FOV 算, 换分辨率/UI 缩放都不用改."""
    try:
        v = float(SCREEN_W) / float(FOV_HDEG)
    except Exception:
        return VIEW_PX_PER_DEG
    if v < 8.0 or v > 80.0:          # 读数异常就用兜底值
        return VIEW_PX_PER_DEG
    return v


def is_cjk(ch):
    """汉字 + 日文平假名/片假名都算"中日文字"(日语队友名字也能认)"""
    return ('\u4e00' <= ch <= '\u9fff') or ('\u3040' <= ch <= '\u30ff') \
        or ('\uff66' <= ch <= '\uff9f')


def plate_name(text):
    """把 OCR 出来的名字串清理成可比较的形式(去空格/标点, 留中日文/字母/数字)"""
    if not text:
        return ''
    out = []
    for ch in text:
        if ch.isalnum() or is_cjk(ch):
            out.append(ch.lower())
    return ''.join(out)


def name_match(a, b):
    """两个名字是否像同一个(允许 OCR 漏字/多字)"""
    a, b = plate_name(a), plate_name(b)
    if len(a) < 2 or len(b) < 2:
        return False
    if a == b or a in b or b in a:
        return True
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def norm(s):
    return re.sub(r'\s+', '', s or '').lower()


def available_ocr_langs():
    """这台机器装了哪些 Windows OCR 语言(空列表=调用失败)"""
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


INSTALL_OCR_PS1 = 'install_ocr.ps1'


def ocr_install_cmd(ps1):
    """提权执行 install_ocr.ps1 的命令行(会弹 UAC)"""
    return ("Start-Process powershell -Verb RunAs -Wait -ArgumentList "
            "'-NoProfile','-ExecutionPolicy','Bypass','-File','%s'" % ps1)


def install_ocr(dry=False):
    """尝试安装 Windows 中文 OCR 语言功能. 返回 (ok, msg). dry=只打印要执行的命令"""
    ps1 = os.path.join(ROOT, INSTALL_OCR_PS1)
    cmd = ocr_install_cmd(ps1)
    if dry:
        return False, 'DRY ' + cmd
    if not os.path.isfile(ps1):
        return False, '缺少 %s（请重新解压完整文件夹）' % INSTALL_OCR_PS1
    try:
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-Command', cmd], capture_output=True, text=True, timeout=900)
    except Exception as e:
        return False, '调用安装失败: %s' % e
    langs = available_ocr_langs()
    zh = [x for x in langs if x.startswith('zh')]
    if zh:
        return True, '已安装: ' + ','.join(zh)
    return False, '安装命令跑完了, 但仍没检测到中文OCR(可能取消了UAC, 或系统拒绝)'


def pick_ocr_lang(langs):
    """从已装语言里挑一个能读中文的; 都没中文就退英文"""
    for lg in ('zh-Hans-CN', 'zh-Hant-CN', 'zh-TW', 'zh-HK'):
        if lg in langs:
            return lg
    if 'en-US' in langs:
        return 'en-US'
    return langs[0] if langs else ''


_OCR_LANG = [LANG]      # 第一次成功后记住, 失败会自动换语言再试(别人电脑没装中文OCR也能跑)


def ocr_lines(img):
    global _OCR_LANG
    cands = [_OCR_LANG[0]] if _OCR_LANG[0] else []
    for lg in ('zh-Hans-CN', 'zh-Hant-CN', 'zh-TW', 'en-US'):
        if lg not in cands:
            cands.append(lg)
    r = None
    err = None
    for lg in cands:
        try:
            r = winocr.recognize_pil_sync(img, lg)
            _OCR_LANG[0] = lg
            break
        except Exception as e:          # 该机器没装这个语言的 OCR
            err = e
    if r is None:
        raise err if err else RuntimeError('OCR failed')
    out = []
    for ln in r['lines']:
        ws = ln['words']
        if not ws:
            continue
        xs = [w['bounding_rect']['x'] for w in ws]
        ys = [w['bounding_rect']['y'] for w in ws]
        x2 = [w['bounding_rect']['x'] + w['bounding_rect']['width'] for w in ws]
        y2 = [w['bounding_rect']['y'] + w['bounding_rect']['height'] for w in ws]
        x, y = min(xs), min(ys)
        out.append({'text': ln['text'], 'norm': norm(ln['text']),
                    'x': x, 'y': y, 'w': max(x2) - x, 'h': max(y2) - y})
    return out


def joined(lines):
    return '|'.join(l['norm'] for l in lines)


# 这些词出现在按钮/文字上时一律不点(用户要求: 任何带"装备"的都不点)
USER_WORDS_PATH = data_file('点击词.txt')   # 用户自定义: 出现就点
_user_cache = {'mtime': 0, 'words': []}


def load_user_words():
    """读 点击词.txt(一行一个, # 开头是注释), 按修改时间缓存"""
    try:
        mt = os.path.getmtime(USER_WORDS_PATH)
    except Exception:
        return []
    if _user_cache['mtime'] == mt:
        return _user_cache['words']
    words = []
    try:
        for ln in io.open(USER_WORDS_PATH, encoding='utf-8-sig', errors='replace').read().splitlines():
            t = ln.strip()
            if not t or t.startswith('#'):
                continue
            words.append(t)
    except Exception:
        words = []
    _user_cache['mtime'], _user_cache['words'] = mt, words
    return words


def find_user_word(lines):
    """屏幕上出现用户自定义词就返回那一行(取最短匹配, 避免匹配到长句)"""
    words = load_user_words()
    if not words:
        return None
    best = None
    for l in lines:
        t = l.get('norm') or ''
        for w in words:
            wn = w.replace(' ', '').lower()
            if wn and wn in t:
                if best is None or len(t) < len(best['norm']):
                    best = l
                break
    return best


# ==== 交互关键词优先 (v1.12.4) ================================================
# 屏幕上出现这些字眼 -> 立刻按一次交互键(默认 E), 优先级高于反挂机(能打断正在做的动作);
# 按完后等 5 秒再识别, 还在就再按一次; 「救援/召回」按完停 15 秒, 期间不抢任何动作。
INTERACT_WORDS = ('打开', '搜索', '上升', '救援', '召回', '拾取', '互动')
LONG_ACTION_WORDS = ('救援', '召回')
ACTION_RETRY_SEC = 5.0          # 普通关键词: 两次按键之间至少隔几秒
ACTION_HOLD_SEC = 15.0          # 救援/召回: 按完停多少秒
ACTION_PRESS_HOLD = 0.8         # v1.12.6: 救援/召回是「按住」型提示, 交互键按住多少秒
ACTION_RULES_PATH = data_file('识别动作.txt')   # 用户自定义: 字眼 => 动作
ACTION_RULES_HEADER = [
    '# 识别动作: 一行一条, 格式  字眼 => 动作',
    '# 字眼可以写多个, 用 / 或 , 分开; 例: 打开/搜索/上升 => 交互',
    '# 动作可以写: 点击 / 交互 / 按键:E / 组合键:Ctrl+E',
    '# 例: 领取 => 点击',
    '# 例: 召回 => 按键:G',
    '# 一行只写词(没写 =>)时, 等同「点击」; # 开头是注释',
]

_ACTION_RULES_CACHE = {'mtime': None, 'rules': None}


class _FidgetInterrupt(Exception):
    """反挂机动作串被「交互关键词」打断时用, 不算错误"""
    pass


def _parse_action_spec(spec):
    """把「点击 / 交互 / 按键:E / 组合键:Ctrl+E」解析成 (kind, vk 或 vk 列表, 说明)"""
    t = (spec or '').strip().lower().replace(' ', '').replace('＋', '+')
    if not t or t in ('点击', 'click', '点'):
        return ('click', None, '点击')
    if t in ('交互', '交互键', 'interact'):
        return ('interact', None, '交互键')
    if t.startswith('按键:') or t.startswith('键:'):
        vk = _parse_key(t.split(':', 1)[1], None)
        if vk:
            return ('key', vk, '按键 %s' % vk_label(vk))
        return None
    if t.startswith('组合键:') or t.startswith('组合:'):
        vks = []
        for p in [x for x in t.split(':', 1)[1].split('+') if x][:4]:
            vk = _parse_key(p, None)
            if not vk:
                return None
            vks.append(vk)
        if vks:
            return ('combo', vks, '组合键 %s' % '+'.join(vk_label(v) for v in vks))
        return None
    return None


def load_action_rules():
    """读 识别动作.txt(改了立刻生效, 按修改时间缓存); 结果里始终带内置默认规则。

    返回 [{'words': (...), 'kind': 'interact'|'key'|'combo'|'click',
           'vk': int|None, 'vks': (..), 'label': str, 'src': '文件'|'默认'}]
    """
    try:
        mt = os.path.getmtime(ACTION_RULES_PATH)
    except Exception:
        mt = None
    if _ACTION_RULES_CACHE['mtime'] == mt and _ACTION_RULES_CACHE['rules'] is not None:
        return _ACTION_RULES_CACHE['rules']
    rules = []
    if mt is not None:
        try:
            for ln in io.open(ACTION_RULES_PATH, encoding='utf-8-sig',
                              errors='replace').read().splitlines():
                t = ln.strip()
                if not t or t.startswith('#'):
                    continue
                if '=>' in t:
                    words_txt, spec_txt = t.split('=>', 1)
                else:
                    words_txt, spec_txt = t, '点击'
                words = tuple(w.strip() for w in
                              words_txt.replace('，', ',').replace('、', ',')
                              .replace('/', ',').split(',') if w.strip())
                if not words:
                    continue
                act = _parse_action_spec(spec_txt)
                if act is None:
                    log('WARN 识别动作.txt 有一行看不懂, 已跳过: %s' % t[:60])
                    continue
                kind, payload, label = act
                rules.append({'words': words, 'kind': kind,
                              'vk': payload if kind == 'key' else None,
                              'vks': tuple(payload) if kind == 'combo' else (),
                              'label': label, 'src': '文件'})
        except Exception as e:
            log('WARN 读 识别动作.txt 失败: %s' % e)
            rules = []
    # v1.12.6: 自定义规则和内置默认合并(自定义排前面, 先命中先执行)。
    # 以前是「文件里有规则就把内置 7 词整个丢掉」: 只加一条自定义规则,
    # 打开/搜索/上升/救援/召回/拾取/互动 就全部失效, 救援/召回的 15 秒静默也不再触发。
    rules.append({'words': INTERACT_WORDS, 'kind': 'interact', 'vk': None, 'vks': (),
                  'label': '交互键', 'src': '默认'})
    _ACTION_RULES_CACHE['mtime'], _ACTION_RULES_CACHE['rules'] = mt, rules
    return rules


# ══ v1.12.5 挂机机制: 内置串开关 + 自定义动作 ═══════════════════════════
# 内置反挂机串 A/B/D 可以在启动器 [17] 挂机机制 里关掉;
# 追踪队友(E, 自动朝队友走)是内置核心, 不能关也不能改。
# 自定义动作写在 挂机动作.txt: 一行一条  名称 => 动作 @ 间隔秒
FIDGET_RULES_PATH = data_file('挂机动作.txt')
FIDGET_RULES_HEADER = [
    '# 挂机动作(自定义): 一行一条, 格式  名称 => 动作 @ 间隔秒',
    '# 动作可以写:',
    '#   按:E            按一次 E',
    '#   长按:E:2         按住 E 两秒',
    '#   连点:E:3         按 E 三次(每次间隔 0.25 秒)',
    '#   连点:E:3:0.4     按 E 三次, 每次隔 0.4 秒',
    '#   点击             点一下鼠标左键(同 点击:左键)',
    '#   点击:左键:3      连点左键三次',
    '#   长按左键:10      按住鼠标左键 10 秒',
    '#   组合键:Ctrl+E    依次按下再松开',
    '# 间隔不写默认 20 秒(最少 2 秒)',
    '# 例: 点左键 => 点击:左键:3 @ 6',
    '# 例: 按住W  => 长按:W:2 @ 15',
]
_FIDGET_RULES_CACHE = {'mtime': None, 'rules': None}
FIDGET_MOUSE_WORDS = {'左键': 'left', '左': 'left', 'left': 'left', 'lmb': 'left',
                      '右键': 'right', '右': 'right', 'right': 'right', 'rmb': 'right'}


def _fnum(txt, default, lo=0.0, hi=60.0):
    try:
        v = float(str(txt).strip())
    except Exception:
        return default
    return max(lo, min(hi, v))


def _fint(txt, default, lo=1, hi=50):
    try:
        v = int(float(str(txt).strip()))
    except Exception:
        return default
    return max(lo, min(hi, v))


def _parse_fidget_action(spec):
    """解析自定义挂机动作; 返回 dict 或 None"""
    t = (spec or '').strip().lower().replace(' ', '').replace('＋', '+')
    if not t:
        return None
    verb, _, rest = t.partition(':')
    parts = [p for p in rest.split(':') if p] if rest else []

    def key_of(s):
        if not s or s in FIDGET_MOUSE_WORDS:
            return None
        return _parse_key(s, None)

    if verb in ('按', '按键', '点一下'):
        vk = key_of(parts[0] if parts else '')
        if not vk:
            return None
        return {'kind': 'key', 'vk': vk, 'count': 1, 'gap': 0.25, 'hold': 0.0,
                'label': '按 %s' % vk_label(vk)}
    if verb in ('长按左键', '长按右键', '按住左键', '按住右键'):
        btn = 'left' if '左' in verb else 'right'
        hold = _fnum(parts[0] if parts else '', 3.0, 0.1, 30.0)
        return {'kind': 'mouse', 'button': btn, 'count': 1, 'gap': 0.0, 'hold': hold,
                'label': '长按%s %.1f 秒' % ('左键' if btn == 'left' else '右键', hold)}
    if verb in ('长按', '按住', 'hold'):
        if not parts:
            return None
        hold = _fnum(parts[1] if len(parts) > 1 else '', 3.0, 0.1, 30.0)
        vk = key_of(parts[0])
        if vk:
            return {'kind': 'key', 'vk': vk, 'count': 1, 'gap': 0.0, 'hold': hold,
                    'label': '长按 %s %.1f 秒' % (vk_label(vk), hold)}
        btn = FIDGET_MOUSE_WORDS.get(parts[0])
        if btn:
            return {'kind': 'mouse', 'button': btn, 'count': 1, 'gap': 0.0, 'hold': hold,
                    'label': '长按%s %.1f 秒' % ('左键' if btn == 'left' else '右键', hold)}
        return None
    if verb in ('连点', '连续点击', '多点'):
        if not parts:
            return None
        vk = key_of(parts[0])
        btn = None if vk else FIDGET_MOUSE_WORDS.get(parts[0])
        if not vk and not btn:
            return None
        count = _fint(parts[1] if len(parts) > 1 else '', 3, 1, 50)
        gap = _fnum(parts[2] if len(parts) > 2 else '', 0.25, 0.05, 5.0)
        if vk:
            return {'kind': 'key', 'vk': vk, 'count': count, 'gap': gap, 'hold': 0.0,
                    'label': '连点 %s ×%d' % (vk_label(vk), count)}
        return {'kind': 'mouse', 'button': btn, 'count': count, 'gap': gap, 'hold': 0.0,
                'label': '连点%s ×%d' % ('左键' if btn == 'left' else '右键', count)}
    if verb in ('点击', '单击', 'click'):
        target = parts[0] if parts else '左键'
        btn = FIDGET_MOUSE_WORDS.get(target)
        if not btn:
            return None
        count = _fint(parts[1] if len(parts) > 1 else '', 1, 1, 50)
        gap = _fnum(parts[2] if len(parts) > 2 else '', 0.25, 0.05, 5.0)
        return {'kind': 'mouse', 'button': btn, 'count': count, 'gap': gap, 'hold': 0.0,
                'label': '点击%s ×%d' % ('左键' if btn == 'left' else '右键', count)}
    if verb in ('组合键', '组合'):
        vks = []
        for p in [x for x in rest.split('+') if x][:4]:
            vk = _parse_key(p, None)
            if not vk:
                return None
            vks.append(vk)
        if not vks:
            return None
        return {'kind': 'combo', 'vks': tuple(vks), 'count': 1, 'gap': 0.0, 'hold': 0.0,
                'label': '组合键 %s' % '+'.join(vk_label(v) for v in vks)}
    return None


def load_fidget_rules():
    """读 挂机动作.txt(改完立刻生效, 按 mtime 缓存); 没有就返回 []"""
    try:
        mt = os.path.getmtime(FIDGET_RULES_PATH)
    except Exception:
        mt = None
    if _FIDGET_RULES_CACHE['mtime'] == mt and _FIDGET_RULES_CACHE['rules'] is not None:
        return _FIDGET_RULES_CACHE['rules']
    rules = []
    if mt is not None:
        try:
            for ln in io.open(FIDGET_RULES_PATH, encoding='utf-8-sig',
                              errors='replace').read().splitlines():
                t = ln.strip()
                if not t or t.startswith('#'):
                    continue
                body, interval = t, 20.0
                if '@' in t:
                    body, iv = t.rsplit('@', 1)
                    interval = _fnum(iv, 20.0, 2.0, 3600.0)
                name, spec = body.split('=>', 1) if '=>' in body else ('动作', body)
                act = _parse_fidget_action(spec)
                if act is None:
                    log('WARN 挂机动作.txt 有一行看不懂, 已跳过: %s' % t[:60])
                    continue
                act['name'] = (name.strip() or '动作')[:20]
                act['interval'] = interval
                rules.append(act)
        except Exception as e:
            log('WARN 读 挂机动作.txt 失败: %s' % e)
            rules = []
    _FIDGET_RULES_CACHE['mtime'], _FIDGET_RULES_CACHE['rules'] = mt, rules
    return rules


def load_fidget_switches():
    """内置反挂机串 A/B/D 的开关(设置.txt: fidget_a / fidget_b / fidget_d = 1/0)"""
    sw = {'a': True, 'b': True, 'd': True}
    try:
        for ln in io.open(data_file('设置.txt'), encoding='utf-8-sig', errors='replace'):
            ln = ln.strip()
            if '=' not in ln or ln.startswith('#'):
                continue
            k, v = ln.split('=', 1)
            k = k.strip().lower()
            if k in ('fidget_a', 'fidget_b', 'fidget_d'):
                sw[k[-1]] = v.strip() not in ('0', 'off', 'false', '否', '关')
    except Exception:
        pass
    return sw
# ══ v1.12.5 挂机机制 结束 ═══════════════════════════════════════════════


BAD_CLICK = ('装备', '精灵', '仓库', '商城', '通行证', '任务', '生涯', '赛事',
             '商店', '充值', '购买', '背包', '设置', '好友', '工会', '社交')


def clickable(line):
    if not line:
        return False
    t = line.get('norm') or ''
    return not any(b in t for b in BAD_CLICK)


def find_clickable(lines, *kws, **kw):
    hit = find_line(lines, *kws, **kw)
    if hit and clickable(hit):
        return hit
    # 同一关键词可能出现在多行: 逐行找第一个"不是黑名单"的
    for l in lines:
        if l['y'] < kw.get('ymin', 0) or l['y'] > kw.get('ymax', 10 ** 9):
            continue
        if l['x'] < kw.get('xmin', 0) or l['x'] > kw.get('xmax', 10 ** 9):
            continue
        if any(k in l['norm'] for k in kws) and clickable(l):
            return l
    return None


def find_line(lines, *kws, ymin=0, ymax=10 ** 9, xmin=0, xmax=10 ** 9):
    for l in lines:
        if l['y'] < ymin or l['y'] > ymax or l['x'] < xmin or l['x'] > xmax:
            continue
        for kw in kws:
            if kw in l['norm']:
                return l
    return None


# 罗盘方向字对应的角度(北=0, 顺时针)
COMPASS_DEG = {'北': 0, '东北': 45, '东': 90, '东南': 135,
               '南': 180, '西南': 225, '西': 270, '西北': 315}


def compass_labels(img):
    """OCR 屏幕顶部罗盘, 返回 [(角度, x像素, 原文)], 用于算 px/度。
    罗盘刻度每 15° 一个数字, 方向字在 45° 的整数倍上。"""
    import re
    try:
        from PIL import Image
    except Exception:
        return []
    W, H = img.size
    box = (int(W * 0.06), 0, int(W * 0.76), int(H * 0.09))
    try:
        c = img.crop(box)
        c = c.resize((c.width * 3, c.height * 3), Image.LANCZOS)
        sub = ocr_lines(c)
    except Exception:
        return []
    out = []
    for ln in sub:
        t = (ln.get('text') or '').replace(' ', '')
        x = box[0] + (ln.get('x') or 0) / 3.0
        if t in COMPASS_DEG:
            out.append((COMPASS_DEG[t], x, t))
            continue
        mm = re.fullmatch(r'(\d{1,3})/?', t)
        if mm:
            v = int(mm.group(1))
            if 0 <= v <= 345 and v % 15 == 0:
                out.append((v, x, str(v)))
    return out


def px_per_deg(img):
    """用一帧画面里的罗盘刻度算「每度多少像素」。返回 (px_per_deg, 样本数)"""
    lab = compass_labels(img)
    ks = []
    for i in range(len(lab)):
        for j in range(i + 1, len(lab)):
            d1, x1, _ = lab[i]
            d2, x2, _ = lab[j]
            dd = abs(d2 - d1)
            dx = abs(x2 - x1)
            # 只信 15°~180° 之间、间距合理的组合(避免 0/360 绕圈和误识别)
            if 15 <= dd <= 180 and dx > 40:
                ks.append(dx / float(dd))
    if not ks:
        return None, 0
    ks.sort()
    med = ks[len(ks) // 2]
    good = [k for k in ks if abs(k - med) <= med * 0.12]   # 去掉明显错的
    if good:
        med = sum(good) / len(good)
    return med, len(good) if good else len(ks)


COUNTS_PER_DEG = 12.16     # 每转 1 度要发多少鼠标计数(实测 900 计数 ≈ 74°)
PX_PER_DEG = 5.9          # 默认值(本机 15% 灵敏度实测), 会被 设置.txt 覆盖


def load_counts_per_deg():
    """从 设置.txt 读 counts_per_deg(每度多少鼠标计数); 没有就用默认值"""
    global COUNTS_PER_DEG
    path = data_file('设置.txt')
    try:
        if os.path.exists(path):
            for ln in io.open(path, encoding='utf-8-sig').read().splitlines():
                m = re.match(r'\s*counts_per_deg\s*=\s*([0-9.]+)', ln)
                if m:
                    v = float(m.group(1))
                    if 2.0 <= v <= 200.0:
                        COUNTS_PER_DEG = v
                        return COUNTS_PER_DEG
    except Exception:
        pass
    return COUNTS_PER_DEG


def load_px_per_deg():
    """从 设置.txt 读校准值(每度多少像素); 没有就用默认值"""
    global PX_PER_DEG
    path = data_file('设置.txt')
    try:
        if os.path.exists(path):
            for ln in io.open(path, encoding='utf-8-sig').read().splitlines():
                m = re.match(r'\s*px_per_deg\s*=\s*([0-9.]+)', ln)
                if m:
                    v = float(m.group(1))
                    if 1.0 <= v <= 100.0:
                        PX_PER_DEG = v
                        return PX_PER_DEG
    except Exception:
        pass
    return PX_PER_DEG


def compass_heading(labels, ppd, cx=None):
    """由罗盘刻度反推「屏幕正中央对着的朝向(度)」。
    每个刻度 a 出现在屏幕 x 处, 说明朝向 = a + (cx - x)/ppd。
    同一帧里所有刻度算出来的朝向应该一致; 取一致票数最多的那个(容忍 OCR 误读)。"""
    if not labels or ppd <= 0:
        return None
    if cx is None:
        cx = grab().size[0] / 2.0
    heads = []
    for a, x, _ in labels:
        h = (a + (cx - x) / ppd) % 360.0
        heads.append(h)
    # 以每个候选为中心数 3 度内的赞同票, 取票最多者
    best, best_votes = None, 0
    for h in heads:
        votes = 0
        for h2 in heads:
            d = abs((h2 - h + 180.0) % 360.0 - 180.0)
            if d <= 3.0:
                votes += 1
        if votes > best_votes:
            best, best_votes = h, votes
    if best is None or best_votes < 1:
        return None
    # 用赞同的刻度取平均, 更准
    sel = [h2 for h2 in heads if abs((h2 - best + 180.0) % 360.0 - 180.0) <= 3.0]
    x = sum(sel) / len(sel)
    return x % 360.0


def angdiff(a, b):
    """a-b 的最短角度差(-180~180)"""
    return (a - b + 180.0) % 360.0 - 180.0


def counts_per_deg_from_labels(before, after, ppd, dx):
    """已知转了 dx 个鼠标计数, 用罗盘刻度在屏幕上的位移反推「每度多少计数」"""
    b = {a: x for a, x, _ in before}
    shifts = []
    for a, x, _ in after:
        if a in b:
            shifts.append(abs(x - b[a]))
    if not shifts:
        return None
    shifts.sort()
    dpx = shifts[len(shifts) // 2]                 # 中位数位移(屏幕像素)
    ddeg = dpx / ppd                               # 换算成角度
    if ddeg < 3.0:
        return None
    v = abs(float(dx)) / ddeg
    return v if 2.0 <= v <= 200.0 else None


def save_calib(v):
    """把 px/度 写进 设置.txt(保留其它键)"""
    path = data_file('设置.txt')
    lines = []
    if os.path.exists(path):
        try:
            lines = [ln for ln in io.open(path, encoding='utf-8-sig').read().splitlines()
                     if not ln.strip().startswith('px_per_deg')]
        except Exception:
            lines = []
    lines.append('px_per_deg=%.4f' % v)
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')


def save_counts_calib(v):
    """把 counts_per_deg 写进 设置.txt(其余键保留)"""
    path = data_file('设置.txt')
    lines = []
    try:
        if os.path.exists(path):
            lines = io.open(path, encoding='utf-8-sig').read().splitlines()
    except Exception:
        lines = []
    out, done = [], False
    for ln in lines:
        if re.match(r'\s*counts_per_deg\s*=', ln):
            out.append('counts_per_deg=%.4f' % v)
            done = True
        else:
            out.append(ln)
    if not done:
        out.append('counts_per_deg=%.4f' % v)
    io.open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')


def find_compass(lines):
    """顶部方位条: 屏幕上方中部出现「东北/东南/北/东...」这类方位词。
    这栏只在「对局中且自己还没死」时才有, 所以是很强的局内佐证。"""
    for l in lines:
        if l['y'] < 0 or l['y'] > 140:
            continue
        if l['x'] < 330 or l['x'] > 1800:
            continue
        for kw in COMPASS_DIRS:
            if kw in l['norm']:
                return l
    return None


def center(l):
    return int(l['x'] + l['w'] / 2), int(l['y'] + l['h'] / 2)


def find_confirm(lines):
    """在弹窗里挑"最像按钮"的确认文字: 越短、越像整体按钮文字, 优先级越高。
    否则会把说明文字(如"代币将在领取后计入进度")当成按钮点。"""
    best = None
    for l in lines:
        t = l['norm']
        for kw in CONFIRM_KWS:
            if kw in t:
                score = 0
                if t == kw:
                    score += 100
                if t.startswith(kw) or t.endswith(kw):
                    score += 40
                score += max(0, 20 - len(t))       # 越短越像按钮
                if best is None or score > best[0]:
                    best = (score, l)
                break
    return best[1] if best else None


# ---------------------------------------------------------------- 状态分类
def is_bar_color(r, g, b):
    """血条(绿) / 护盾(蓝) / 残血(红橙) 三种 UI 纯色"""
    return ((g > 140 and g - r > 45 and g - b > 45) or
            (b > 150 and b - r > 50 and b - g > 20) or
            (r > 150 and r - g > 60 and r - b > 60))


def hud_bar_visible(img):
    """左下血条/护盾条: 局内(且活着)才有。

    在 BAR_SEARCH_REL 区域里逐行判断, 返回"最长连续纯色行数"。
    >=10 行即认为血条在场。这样 UI 比例调大/调小都还能命中。
    """
    if img is None:
        return 0
    W, H = img.size
    x1, y1, x2, y2 = BAR_SEARCH_REL
    c = img.crop((int(x1 * W), int(y1 * H), int(x2 * W), int(y2 * H))).convert('RGB')
    best = cur = 0
    for y in range(c.height):
        r, g, b = ImageStat.Stat(c.crop((0, y, c.width, y + 1))).mean
        if is_bar_color(r, g, b):
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def cjk_only(t):
    return ''.join(ch for ch in t if '\u4e00' <= ch <= '\u9fff')


def name_like(t, name):
    """OCR 常把中文名漏字/错字, 这里做宽松匹配。"""
    t2, n2 = cjk_only(t), cjk_only(name)
    if not t2 or not n2:
        return False
    if n2 in t2 or t2 in n2:
        return True
    for frag in (n2[:2], n2[1:3], n2[-3:], n2[-2:]):
        if len(frag) >= 2 and frag in t2:
            return True
    import difflib
    for i in range(0, max(1, len(t2) - len(n2) + 1)):
        seg = t2[i:i + len(n2)]
        if difflib.SequenceMatcher(None, seg, n2).ratio() >= 0.55:
            return True
    return False


def self_name_visible(lines, img):
    """自己名字是否出现在屏幕左下角(局内且活着才有)。返回命中的行或 None。"""
    if img is None:
        return None
    W, H = img.size
    x1, y1, x2, y2 = SELF_BOX_REL
    for L in lines:
        if not (x1 * W <= L['x'] <= x2 * W and y1 * H <= L['y'] <= y2 * H):
            continue
        if name_like(L['text'], PLAYER_NAME):
            return L
    return None


def classify(lines, img=None):
    """返回 (state, evidence, target_line)"""
    j = joined(lines)

    # 弹窗(宝藏代币结算 / 奖励领取 / 断线重连) 放最前面:
    # 弹窗会盖住下面的界面(半透明遮罩), 先点掉它, 否则后面的"返回大厅"永远点不到。
    # 必须"确认词 + 弹窗线索"同时出现, 否则局内菜单(也有返回大厅)、聊天里的"好的"会误判。
    # 休眠模式(游戏闲置会自动进睡眠, 屏幕显示「已进入休眠模式 / 继续游戏」)
    # 不先点掉它, 后面的大厅按钮根本点不到。
    sleepbtn = find_line(lines, '继续游戏')
    if sleepbtn and find_line(lines, '已进入休眠模式', '休眠模式'):
        return 'SLEEP', '休眠模式:' + sleepbtn['text'], sleepbtn

    cbtn = find_confirm(lines)
    chint = find_line(lines, *MODAL_HINTS)
    if cbtn and chint:
        return 'MODAL', '确认弹窗:%s / %s' % (cbtn['text'], chint['text']), cbtn
    # 单独一个「领取」按钮(宝藏代币/活动奖励弹窗): 用户要求——扫到「领取」就直接点它
    if cbtn and '领取' in cbtn['norm']:
        return 'MODAL', '领取按钮:%s' % cbtn['text'], cbtn

    # 任意"有跳过按钮"的浮层: 玩家问卷 / 过场动画 / 新手指引 / 活动介绍 / 更新公告 …
    # 不管是什么弹出来的, 只要屏幕上有「跳过」(或英文 SKIP) 就先点掉它。
    # 这类浮层会盖住下面所有界面(不点掉的话, 后面的开始/返回大厅全点不到), 所以放最前面。
    skipbtn = find_line(lines, '跳过', '略过')
    if skipbtn is None:
        # 万一系统没装中文 OCR(退到英文识别), 跳过按钮会是 "SKIP"
        for _l in lines:
            if (_l.get('norm') or '').strip() in ('skip', 'skip>>', 'skip>', '>>skip'):
                skipbtn = _l
                break
    if skipbtn is not None:
        return 'SURVEY', '跳过浮层:' + skipbtn['text'], skipbtn

    # 局内菜单(手动打开)的特征: 返回大厅 + 进入睡眠模式. 先算出来, 后面要排除它
    inmenu = bool(find_line(lines, '返回大厅')) and bool(find_line(lines, '进入睡眠模式'))

    # 死亡/观战判定必须放在"结算页"之前:
    # 被淘汰的面板(尤其小队模式)上同样有「返回大厅」按钮, 如果先按结算页处理,
    # 观察 8 秒后就会点它 —— 那就违反了用户的规则(死了要观战队友, 队友全死才回大厅)。
    # 用户实测: 死了会自动进入观战, 「返回大厅」只有在整队都死光后才出现。
    # 所以这里只负责"识别成观战/已死"(不点任何东西), 绝不抢在结算页之前点按钮:
    #   · 有观战/旁观字样 -> DEAD (此时即使有返回大厅也不点, 等小队全灭)
    #   · 只有死亡横幅、且没有任何按钮 -> DEAD
    #   · 一旦看到「返回大厅」-> 交给下面的结算页逻辑(那才是真的结束了)
    deadmark = find_line(lines, '被淘汰', '已淘汰', '你已阵亡', '阵亡', '被风暴吞噬', '迷失在风暴中')
    # 用户建议: 屏幕上方「正在观战 XXX」+ 底部「上一个队友/下一个队友」+「等待重启」
    # 全都是「自己已死在看队友」的专属文字 -> 出现任一个就判 DEAD, 绝不点「返回大厅」
    watchmark = find_line(lines, *SPECTATE_KWS)
    endmark = find_line(lines, '比赛回顾', '你的排名', '继续和此小队', '胜利王冠', '再来一局', '再玩一次')
    backbtn = find_line(lines, '返回大厅')
    # v1.11.3: 死亡/淘汰面板上往往同时有「返回大厅」按钮(小队模式还会带「观战」),
    # 旧逻辑因 backbtn 存在把 DEAD 让给结算页 -> 命令框显示"结算页面", 且队友还活着时
    # 就把「返回大厅」点掉提前退局。现在只要看到「被淘汰/阵亡/观战」字样一律先判 DEAD
    # (不点任何按钮); 只有真正出现结算页标记才算这一局结束。
    if watchmark and not endmark:
        return 'DEAD', '观战中:' + (watchmark['text'])[:20], None
    if deadmark and not endmark:
        return 'DEAD', '被淘汰:' + (deadmark['text'])[:20], None

    # 结束判定(用户指定的简化规则): 屏幕上出现「返回大厅 / 再玩一次」= 这局已经结束了,
    # 不管是被打死、被风暴吞、还是队友带着赢/输, 一律直接点它回大厅再开一把。
    rl_lobby = find_line(lines, '返回大厅')
    rl_again = find_line(lines, *AGAIN_WORDS)
    if AFTER_MATCH == 'again':          # 默认「再玩一局」: 优先点它, 找不到才退回大厅
        rl = rl_again or rl_lobby
    else:                               # 用户选的是「返回大厅」, 找不到才退回再玩一次
        rl = rl_lobby or rl_again
    # v1.8.3: 结算页已经出现、但 ESC 菜单/侧栏同时盖在上面(用户实测过这个卡死场景)
    # 这时不能当 MENU(会什么都不做 -> 永远卡住), 也不能盲点(按钮被面板挡住),
    # 先返回 ENDMENU, 由 act() 按一次 ESC 把菜单关掉, 下一轮就能正常点「返回大厅」
    if endmark is not None and inmenu:
        return 'ENDMENU', '结算页被菜单挡住: 先关菜单', find_line(lines, '返回大厅')

    if rl and not inmenu:
        marker = find_line(lines, '比赛回顾', '你的排名', '经验值', '继续和此小队', '胜利王冠')
        if DEATH_RULE == 'team' and not marker and not rl['norm'].startswith('再'):
            return 'RESULTS?', '只有「%s」但没结算标记(可能在观战/中途)' % rl['text'], rl
        return 'RESULTS', '结束页面:' + rl['text'], rl

    # 死亡横幅已在前面(结算页之前)判定; 这里再兜一次底(极少数只剩横幅、没有其它字的画面)
    if find_line(lines, '被淘汰', '已淘汰', '你已阵亡', '阵亡'):
        return 'DEAD', '被淘汰/阵亡', None

    if inmenu:
        return 'MENU', '局内菜单', find_line(lines, '返回大厅')

    # 等待区(巴士前的准备区)
    wait = find_line(lines, '正在等待玩家')
    if wait:
        return 'WAITING', wait['text'], None

    # 战斗巴士(气球船): 必须靠"船长是/司机平稳驾驶"这类独有文本, 不能用"空格跳出"
    bus = find_line(lines, '船长是', '司机平稳', '空中巴士', '空格跳出')
    if bus:
        return 'BUS', bus['text'], bus

    # 跳伞/滑翔中(巴士跳出后, 不需要任何操作)
    sky = find_line(lines, '部署卓耿', '空格跳过', '空格跳伞')
    if sky:
        return 'SKYDIVE', sky['text'], None

    # 匹配中
    if find_line(lines, '搜索服务器', '取消匹配') or (find_line(lines, '取消') and find_line(lines, '匹配')):
        return 'MATCHING', '搜索服务器', None

    # 加载
    if find_line(lines, '连接中') or find_line(lines, '正在加载') or (find_line(lines, '创作者') and find_line(lines, 'epic')):
        return 'LOADING', '连接中', None

    # 大厅: 顶部导航(商城/仓库/通行证) + 左下"开始"大按钮
    start_btn = find_line(lines, '开始', ymin=1100, xmax=1000)
    # v1.11.0: 只要左侧那个大「开始」按钮在, 再看到任意一个大厅专属词, 就当大厅
    # (宁可把画面判成大厅一整轮不动, 也不能把大厅误判成局内去做反挂机)
    nav = find_line(lines, '商城', '仓库', '通行证', '生涯', '零建造', '装备一个精灵',
                    '任务', '赛事', '宝库')
    # v1.11.0: 「开始」按钮就是大厅的硬判据(前面的守卫已排除结算页/局内菜单/观战),
    # 大厅专属词也算; 宁可整轮判成大厅不动, 也不要把大厅误判成局内去做反挂机
    if start_btn or nav:
        return 'LOBBY', (start_btn['text'] if start_btn else '开始(坐标兜底)'), start_btn

    # 局内 HUD: 三重条件, 任何一条不满足都不算局内
    # ① 命中 HUD 词; ② 屏幕上没有任何局外界面特征词;
    # ③ 位置/共现约束: HUD 词出现在左上任务条区, 或者两个以上不同 HUD 词同时出现
    # (第四局实测: 任务面板/社交侧栏也带任务文字, 光看 HUD 词会误判成局内)
    hud = find_line(lines, *INGAME_HUD)
    outside = find_line(lines, *NOT_INGAME)
    selfhit = self_name_visible(lines, img)  # 左下角自己的名字(局内活着才有, 用户实测最准)
    compass0 = find_compass(lines)           # v1.11.1: 顶部方位条也是"局内"铁证(大厅/加载/结算都没有)
    if not outside and (hud is not None or selfhit is not None or compass0 is not None):
        hits = set()
        hud_line = None
        for l in lines:
            t = norm(l['text'])
            for kw in INGAME_HUD:
                if kw in t:
                    hits.add(kw)
                    if hud_line is None:
                        hud_line = l
                    break
        top_left = (hud_line is not None and hud_line['x'] < INGAME_REGION_W
                    and hud_line['y'] < INGAME_REGION_H)
        compass = compass0                     # v1.11.1: 复用外面算好的, 省一次遍历
        if top_left or len(hits) >= 2 or compass is not None or selfhit is not None:
            if hud is not None:
                ev = hud['text']
            else:
                ev = '自己名字:%s' % (selfhit or {}).get('text', '')[:16]
            if compass is not None:
                ev += ' | 方位:' + compass['text'][:26]
            if selfhit is not None:
                ev += ' | 自己名字'
            return 'INGAME', ev, None
        return 'UNKNOWN', '只有 1 个 HUD 词且不在任务条区: ' + (hud['text'][:40] if hud else '(无 HUD 词)'), None

    if find_line(lines, '大逃杀') and find_line(lines, '创作者'):
        return 'LOADING', '大逃杀加载页', None

    return 'UNKNOWN', j[:120], None


def bus_seconds(lines):
    """从巴士界面读出倒计时剩余秒数, 读不到返回 None"""
    for l in lines:
        if l['x'] < 1850:
            continue
        m = re.search(r'(\d{1,2})\s*[:：]\s*(\d{2})', l['text'])
        if m:
            return int(m.group(1)) * 60 + int(m.group(2))
    return None


# ---------------------------------------------------------------- 主循环
class Bot:
    def __init__(self, interval=5.0, jump_at=20, dry=False, save_shots=True):
        self.interval = interval
        self.jump_at = jump_at
        self.dry = dry
        self.save_shots = save_shots
        self.last_state = None
        self.hwnd = None
        self.last_click = 0.0
        self.start_attempts = 0
        self.lobby_since = 0.0        # 在大厅停留的起始时间(看门狗)
        self.force_click = False      # 大厅卡住时强制点一次
        self.bus_first_seen = None
        self.jumped = False
        self.stop_requested = False
        self.require_foreground = True     # True: 游戏不在前台就暂停, 绝不抢焦点
        self.index = 0
        self.results_clicks = 0            # 结算页连点计数(卡住检测用)
        self.match_total = 0               # 已完成的局数(点「返回大厅」的次数, 持久化在 stats.txt)
        self.modal_clicks = 0              # 弹窗连点计数
        self.unknown_cycles = 0            # 连续认不出画面的轮数(卡死兜底)
        self.first_secs = None             # 巴士上首次读到的倒计时(合理性检查用)
        self.jump_attempts = 0             # 本局已尝试按空格跳伞的次数
        self.jump_sent_at = 0.0            # 上次按空格跳伞的时间
        self.resultsq_cycles = 0           # RESULTS? 连续轮数(先观察再动手)
        self.resultsq_wait_rounds = self.load_int_conf('resultsq_wait_rounds', RESULTSQ_WAIT_ROUNDS, 0, 10)
        self.spectating = False            # 自己已阵亡、正在观战队友: 期间绝不点"返回大厅"
        self.paused_logged = 0.0            # 前台失焦的提示: 每 15 秒记一次(带前台窗口标题)
        load_key_bindings()          # 读 设置.txt 里的按键绑定(没改就用默认)
        self.interact_cd = 0.0        # 交互键冷却(画面出现「搜索」时按 C)
        self.interact_count = 0
        self.squad_mode = load_squad_mode()   # 'alive'=全程检查 / 'start'=只在开局前
        self.squad_bad = 0
        self.plate_seen = {}                 # v1.8.4: 队友名字 -> 最后一次在画面里看见他的时间
        self.teammate_alive_sec = self.load_teammate_alive_sec()
        self.lobby_click_cd = self.load_click_cd('lobby_click_cd', LOBBY_CLICK_CD)
        self.results_click_cd = self.load_click_cd('results_click_cd', RESULTS_CLICK_CD)
        self.react_min = self.load_click_cd('click_react_min', CLICK_REACT_MIN)
        self.react_max = self.load_click_cd('click_react_max', CLICK_REACT_MAX)            # 连续几轮判定「队伍人数不足」
        self.leave_done_match = False # 本局是否已经因为人数不足退过一次
        self.action_rules = load_action_rules()   # v1.12.4: 关键词 -> 动作(可自定义)
        self.action_cd = {}                        # 每条规则自己的冷却
        self.action_count = 0
        self.custom_cd = {}                        # v1.12.5: 自定义挂机动作各自的计时
        self.action_hold_until = 0.0               # 救援/召回后要停 15 秒
        self._urgent_next = 0.0                    # 动作串里"快速扫一眼"的节流
        self.fidget_scan = False                   # 正在做反挂机动作串(允许被交互键打断)
        self._interrupt_fidget = False             # 本串已被交互键打断
        self.fidget = True                  # 反挂机小动作开关
        self.last_fidget = 0.0
        self.next_fidget = time.time() + random.uniform(3.0, 8.0)
        self.fidget_count = 0
        self.ingame_streak = 0              # 连续几轮判成 INGAME(双重确认, 防单帧误判)
        self.alive = False                  # 左下角能看到自己名字 = 在局内且还活着
        self.self_box = None                # v1.10.8: 记住自己名字在左下角的位置(相对坐标)
        self._last_fidget_skip = None      # v1.10.9: 反挂机跳过原因(只在变化时记日志)
        self.name_miss = 0                  # 局内连续几轮没认到自己的名字
        self.name_warned = False            # 名字识别失败提示只打一次
        self.name_relax_warned = False       # v1.11.1: 名字长期认不到时放宽反挂机, 只提示一次
        # v1.12.9: 名字监测 —— 进局内后连续 NAME_WATCH_SEC 秒看不到自己的名字就退局重排
        self.ingame_since = 0.0              # 双重确认进局内的时刻(计时起点)
        self.name_seen_at = 0.0              # 最近一次在左下角认到自己名字的时刻
        self.name_watch_warned = False       # 「已经 N 秒没看到名字」提示只打一次
        self.name_leave_at = 0.0             # 上次因找不到名字退局的时刻(冷却)
        # v1.12.10: 画面出现「空格」字样时, 挂机动作里带空格的(跳/按空格)一律不做
        self.space_block_until = 0.0
        self.space_block_logged = False
        self.teammates = []
        self.squad_panel = None        # v1.9.1: 进局后自己学到的队伍栏区域 rel(x0,y0,x1,y1)
        self.follow_turn_dir = 0.0     # v1.9.1: 记住上次转身方向, 避免左右来回抖
        self.aim_pitch_total = 0.0     # v1.9.5: 瞄准累计俯仰(负=抬头), 防止抬到看天
        self.view_pitch_bias = 0.0     # v1.9.6: 跨动作串保留的俯仰偏置(负=抬头)
        self.squad_seen_max = 0        # v1.9.7: 本局队伍栏最多看到过几个人                 # 从左下角队伍栏学到的队友名字
        self.follow_name = None             # 当前锁定的队友(自动朝队友走)
        self.follow_miss = 0                # 连续几次没在画面里找到他名字
        self.squad_boxes = []               # 队伍栏里每个队友名字所在的矩形(相对坐标), 用来排除
        self.squad_logged = False           # "已记住队伍栏位置" 只记一次
        os.makedirs(SHOT_DIR, exist_ok=True)

    # --- 工具 ---
    def game_is_foreground(self):
        if self.hwnd is None or not user32.IsWindow(self.hwnd):
            self.hwnd = find_game_window()
        return bool(self.hwnd) and hwnd_int(user32.GetForegroundWindow()) == self.hwnd

    def focus(self):
        if self.hwnd is None or not user32.IsWindow(self.hwnd):
            self.hwnd = find_game_window()
        if not self.hwnd:
            log('WARN 找不到 Fortnite 窗口')
            return False
        ok = focus_game(self.hwnd)
        if not ok:
            log('WARN 前台切换失败 hwnd=%s' % self.hwnd)
        return ok

    def _nap(self, seconds):
        """分片睡眠, 期间轮询 F9/F10; 返回 False 表示收到停止"""
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            hk = poll_control()
            if hk == 'stop':
                self.stop_requested = True
                log('=== 收到 F9: 停止自动打 ===')
                return False
            # v1.12.4: 反挂机动作串期间每 0.6 秒扫一眼画面, 出现交互关键词
            # 就立刻按键并打断这串动作(用户要求: 交互键优先, 能打断防挂机)
            if self.fidget_scan and time.time() >= self._urgent_next:
                if self._urgent_interact_scan():
                    self._interrupt_fidget = True
                    raise _FidgetInterrupt()
            time.sleep(0.1)
        return True

    def _urgent_interact_scan(self):
        """反挂机动作串中途的一次快速扫描: 有交互关键词就立刻按键, 返回是否按了。"""
        self._urgent_next = time.time() + 0.6
        if time.time() < self.action_hold_until:
            return False
        try:
            img = grab()
            lines = ocr_lines(img)
        except Exception:
            return False
        act = self.check_actions(lines, 'INGAME')
        if act:
            log('    ' + act)
            return True
        return False

    # ---------------- 反挂机: 每 20 秒来一串"像人"的动作 ----------------
    def _walk_look(self, ms, key=None, pan_deg=None):
        """按住方向键一直走, 同时平滑地左右转视角(抬头平视, 绝不低头), 像真人边走边看。
        pan_deg: 这段路总共转多少度(符号 = 左右); 不给就按 100~200° 随机。"""
        if key is None:
            key = VK_W
        if not self._nap(0.02):
            return False
        dur = max(0.3, ms / 1000.0)
        if pan_deg is None:
            pan_deg = random.uniform(100.0, 200.0)
        pan_deg *= random.choice((-1.0, 1.0))
        pan_cnt = pan_deg * COUNTS_PER_DEG          # 度 → 鼠标计数(校准值, 不是屏幕像素)
        segs = 2 if dur < 1.6 else random.randint(2, 3)
        key_down_scan(key)
        try:
            left = pan_cnt
            for i in range(segs):
                if self.stop_requested:
                    return False
                part = left / float(segs - i)
                spd = random.uniform(2600.0, 4200.0)      # 计数/秒, 不再被走路时长限制
                seg_dur = max(0.12, min(1.0, abs(part) / spd))
                # dy 恒等于 0: 只左右转, 不上下看(之前会误低头)
                if not mouse_look_smooth(part, 0.0, dur=seg_dur,
                                         halt=lambda: self.stop_requested):
                    return False
                left -= part
                if i < segs - 1 and not self._nap(random.uniform(0.10, 0.30)):
                    return False
        finally:
            key_up_scan(key)
        return not self.stop_requested

    def _jump(self):
        """原地/移动中跳一下(空格)"""
        if self.stop_requested:
            return False
        if time.time() < self.space_block_until:
            return False          # v1.12.10: 画面有「空格」字样, 不抢空格
        press_scan(key_vk('跳跃'))
        return True

    def _hold_move_jump(self, mod_vk, hold_s=5.0, after_s=3.0, pan_deg=0.0):
        """按住 W + 修饰键(Shift 冲刺 / Ctrl 蹲走) 走 hold_s 秒,
           中间不松手, 按一下空格跳, 再继续按住走 after_s 秒, 最后才松开。
           pan_deg 不为 0 时顺带很慢地左右看一眼(平视, 不低头)。"""
        key_down_scan(VK_W)
        if mod_vk:
            key_down_scan(mod_vk)
        try:
            if not self._nap(hold_s):
                return False
            if not self._jump():
                return False
            if abs(pan_deg) > 1:
                mouse_look_smooth(pan_deg * COUNTS_PER_DEG, 0.0,
                                  dur=min(1.2, after_s * 0.5),
                                  speed=random.uniform(1800.0, 2600.0),
                                  halt=lambda: self.stop_requested)
            if not self._nap(after_s):
                return False
        finally:
            key_up_scan(VK_W)
            if mod_vk:
                key_up_scan(mod_vk)
        return not self.stop_requested

    def _fire(self):
        """点按左键几下, 或者长按左键一段时间"""
        if random.random() < 0.5:
            for _ in range(random.randint(1, 3)):
                mouse_left()
                if not self._nap(random.uniform(0.12, 0.30)):
                    return False
        else:
            mouse_left_hold(random.randint(250, 900))
        return True

    def _switch_item(self):
        """换物品: 按数字键切武器/道具, 偶尔滚轮, 像真人在换枪"""
        press_scan(random.choice((VK_1, VK_2, VK_3, VK_4, VK_5, VK_1, VK_2)))
        if random.random() < 0.3:
            user32.mouse_event(0x0800, 0, 0, 120, None)   # 滚轮切物品
        return True

    def _emote(self):
        """按 B 打开表情轮盘, 再按 1 选第一个表情(真正跳/挥手);
        之后由调用方等 10 秒, 让表情播完再继续。"""
        press_scan(key_vk('动作'))
        if not self._nap(0.9):
            return False
        press_scan(VK_1)
        return True

    def run_fidget_series(self):
        """一串连续动作, 四种轮着来(A/B/D 可在 [17] 挂机机制 里关掉):
           A 综合串(少移动): 走+左右看 -> 左键 -> 换物品 -> (跳跃) -> (表情)
           B 冲刺串: W+Shift 5s -> 空格跳 -> W+Shift 3s
           D 互动串: F -> 等 1 秒 -> 长按左键 10 秒
           E 朝队友走(高频): 锁定左下角一个队友, 按他名牌的方位转向并前进"""
        self.fidget_count += 1
        n = self.fidget_count
        parts = []
        t_start = time.time()          # 节奏以「开始时刻」为准: 每 20 秒一串
        # v1.12.5: A/B/D 可以在 [17] 挂机机制 里关掉(设置.txt 的 fidget_a/b/d);
        # 追踪队友(E)是内置核心, 恒可选、不可关
        _sw = load_fidget_switches()
        _w = {'A': 20, 'B': 8, 'D': 18, 'E': 54}
        _kinds = [k for k in ('A', 'B', 'D', 'E') if k == 'E' or _sw.get(k.lower(), True)]
        if time.time() < self.space_block_until:
            # v1.12.10: 画面有「空格」字样 -> 只做不含空格的 D 串(A/B/E 都带跳)
            _kinds = [k for k in _kinds if k == 'D']
            if not _kinds:
                self.next_fidget = t_start + FIDGET_INTERVAL
                log('    FIDGET 跳过: 画面有「空格」字样(带空格的串先不做)')
                return None
        kind = random.choices(_kinds, weights=[_w[k] for k in _kinds])[0]
        if self.dry:
            self.next_fidget = t_start + FIDGET_INTERVAL
            return '[DRY] 系列#%d(%s)' % (n, kind)
        if not self.game_is_foreground():
            log('    FIDGET 跳过: 游戏不在前台')
            self.next_fidget = time.time() + 5.0
            return None
        self.fidget_scan = True            # v1.12.4: 允许被交互关键词打断
        self._interrupt_fidget = False
        try:
            if kind == 'A':
                deg1 = random.uniform(100.0, 200.0)
                if self._walk_look(random.randint(3000, 4000), VK_W, pan_deg=deg1):
                    parts.append('走+看%.0f°' % deg1)
                if not self.stop_requested and random.random() < 0.8:
                    if self._fire():
                        parts.append('左键')
                if not self.stop_requested:
                    self._switch_item()
                    parts.append('换物品')
                if not self.stop_requested and self.game_is_foreground() and random.random() < 0.5:
                    key = random.choice((VK_W, VK_W, VK_A, VK_D))
                    deg2 = random.uniform(120.0, 240.0)
                    if self._walk_look(random.randint(1500, 2200), key, pan_deg=deg2):
                        parts.append('走+甩%.0f°' % deg2)
                if not self.stop_requested and random.random() < 0.45:
                    if self._walk_look(random.randint(700, 1100), VK_W, pan_deg=random.uniform(40.0, 90.0)):
                        parts.append('走')
                    if self._jump():
                        parts.append('跳')
                if not self.stop_requested and random.random() < 0.65:
                    if self._fire():
                        parts.append('左键2')
                if not self.stop_requested and random.random() < 0.5:
                    self._switch_item()
                    parts.append('换物品2')
                if not self.stop_requested and random.random() < 0.3:
                    if self._emote():
                        parts.append('表情B')
                        log('    FIDGET 表情中, 等 %.0f 秒' % FIDGET_EMOTE_WAIT)
                        self._nap(FIDGET_EMOTE_WAIT)
            elif kind == 'D':
                # 切稿子(F) -> 等 1 秒 -> 长按左键 10 秒(挖矿/挥击) -> 再按一次 F 切回武器
                # v1.12.6: 切回武器放进 finally —— 被交互键打断时也要按回 F,
                # 否则下一串会拎着镐子跑(以前的 except 直接跳过了这一下)
                if not self.stop_requested and self._nap(0.2):
                    switched = press_scan(key_vk('稿子'))
                    if switched:
                        parts.append('切稿子')
                    try:
                        self._nap(1.0)
                        if not self.stop_requested:
                            user32.mouse_event(0x0002, 0, 0, 0, None)
                            try:
                                log('    FIDGET 长按左键 10 秒(挖矿)')
                                if self._nap(10.0):
                                    parts.append('长按左键10s')
                            finally:
                                user32.mouse_event(0x0004, 0, 0, 0, None)
                    finally:
                        if switched and not self.stop_requested and press_scan(key_vk('稿子')):
                            parts.append('切回武器')
            elif kind == 'E':
                got = self.follow_teammate_sustained()
                if got and got != '-':
                    parts.append('朝队友: ' + got)
            elif kind == 'B':
                # v1.12.6: 只有真做成了才记日志(以前先 append, 没做也写「冲刺 5s」)
                if self._hold_move_jump(key_vk('奔跑'), 5.0, 3.0, pan_deg=random.uniform(20.0, 60.0)):
                    parts.append('冲刺: W+Shift 5s')
                    parts.append('空格跳')
                    parts.append('W+Shift 3s')
        except _FidgetInterrupt:
            parts.append('被交互键打断')
        except Exception as e:
            log('FIDGET ERROR %r' % (e,))
        finally:
            self.fidget_scan = False
            self._interrupt_fidget = False
            # v1.12.6: 至少留 2 秒喘息; 长串(表情等 10 秒)做完不会立刻接下一串
            self.next_fidget = max(t_start + FIDGET_INTERVAL, time.time() + 2.0)
        return '%s系列#%d(%s, %.1fs): %s' % ('[DRY] ' if self.dry else '', n, kind,
                                             time.time() - t_start,
                                             '+'.join(parts) if parts else '-')

    # --- 队伍人数检测: 不足 3 人自动退出重排 ---
    def squad_count(self, lines):
        """当前队伍人数(含自己): 左下角队伍栏里读到的队友数 + 1

        直接数"看起来像名字的行", 不做模糊合并(队友名字相近时模糊合并会少算)。
        上限 3 个队友(小队最多 4 人)。
        """
        self.learn_teammates(lines)          # 顺便维护 teammates/squad_boxes
        seen = []
        for l in lines:
            t = l.get('text') or ''
            x, y = l.get('x', 0), l.get('y', 0)
            if not (x < SQ_LIST_RELX * SCREEN_W and y > SQ_LIST_RELY * SCREEN_H):
                continue
            nm = plate_name(t)
            if len(nm) < 2 or len(nm) > 16:
                continue
            # v1.9.6: 血条/护盾数字(50/95/100)和符号不能被当成队友名字
            _cjk = sum(1 for ch in nm if is_cjk(ch))
            _alpha = sum(1 for ch in nm if ch.isalpha() and not is_cjk(ch))
            if _cjk < 2 and _alpha < 3:
                continue
            if not any(ch.isalpha() or is_cjk(ch) for ch in nm):
                continue                      # v1.9.0: 纯数字(血条数字)不是名字
            cjk = sum(1 for ch in nm if is_cjk(ch))
            alpha = sum(1 for ch in nm if 'a' <= ch <= 'z')
            if cjk < 2 and alpha < 3:
                continue
            if any(k in nm for k in ('队伍', '好友', '任务', '语音', '聊天', '返回',
                                     '开始', '金币', '等级', '商城', '仓库')):
                continue
            if name_match(nm, PLAYER_NAME):
                continue
            if nm not in seen:
                seen.append(nm)
        return 1 + min(3, len(seen))

    def _click_rel(self, rx, ry, why=''):
        x, y = int(SCREEN_W * rx), int(SCREEN_H * ry)
        if self.dry:
            return '[DRY] 点(%d,%d) %s' % (x, y, why)
        click(x, y)
        return '点(%d,%d) %s' % (x, y, why)

    def _ocr_click_short(self, want, words, maxlen=6):
        """只点"整行就是按钮文字"的短行(确认框按钮), 避免误点含这些字的说明文字"""
        img = grab()
        lines = ocr_lines(img)
        for w in words:
            best = None
            for l in lines:
                n = (l.get('norm') or l.get('text') or '')
                if n == w and len(n) <= maxlen:
                    best = l
                    break
            if best is None:
                continue
            cx = best['x'] + (best.get('w') or 0) // 2
            cy = best['y'] + (best.get('h') or 0) // 2
            if self.dry:
                log('    [DRY] %s: 找到「%s」-> 点(%d,%d)' % (want, best['text'], cx, cy))
                return True
            click(cx, cy)
            log('    %s: 点「%s」(%d,%d)' % (want, best['text'], cx, cy))
            return True
        return False

    def _ocr_click(self, want, words, why=''):
        """截图 -> OCR -> 找 words 里的字 -> 点它; 成功返回 True"""
        img = grab()
        lines = ocr_lines(img)
        hit = find_line(lines, *words)
        if not hit:
            return False
        cx = hit['x'] + (hit.get('w') or 0) // 2
        cy = hit['y'] + (hit.get('h') or 0) // 2
        if self.dry:
            log('    [DRY] %s: 找到「%s」-> 点(%d,%d)' % (want, hit['text'], cx, cy))
            return True
        click(cx, cy)
        log('    %s: 点「%s」(%d,%d)' % (want, hit['text'], cx, cy))
        return True

    # ---------- v1.9.2: 不靠固定坐标, 用画面里的文字当锚点自动找位置 ----------
    def _anchor_lines(self, lines, kws):
        return [l for l in lines if any(k in (l.get('text') or '') for k in kws)]

    def _menu_anchor_box(self, lines):
        """用 ESC 侧栏里能 OCR 到的文字(社交/队伍成员/好友..)推出侧栏的框"""
        hits = self._anchor_lines(lines, ('社交', '队伍成员', '可加入的队伍', '好友', '群聊'))
        if not hits:
            return None
        x0 = min(l['x'] for l in hits); x1 = max(l['x'] + l.get('w', 0) for l in hits)
        y0 = min(l['y'] for l in hits); y1 = max(l['y'] + l.get('h', 0) for l in hits)
        return (x0, y0, x1, y1)

    def click_menu_hamburger_verified(self, why='☰'):
        """点 ESC 侧栏右上角的 ☰, 但不写死坐标:

        先根据画面里"社交/队伍成员/好友"这些字推出侧栏大致位置, 再在侧栏顶部
        图标行上按候选点逐个试探; 每点一次就重新 OCR, 看到「返回大厅/进入睡眠模式」
        才算点中(说明菜单打开了), 没点中继续试下一个候选点。
        实在一个都没中, 才退回旧的比例位置(并写日志)。
        """
        if self.dry:
            return '[DRY] 点菜单 %s(自动探测位置)' % why
        img = grab()
        lines = ocr_lines(img)
        box = self._menu_anchor_box(lines)
        cands = []
        if box is not None:
            x0, y0, x1, y1 = box
            panel_w = max(80, x1 - x0)
            panel_h = max(80, y1 - y0)
            top_y = max(8, y0 - int(panel_h * 0.06))
            right_x = min(SCREEN_W - 12, x1 + int(panel_w * 0.10))
            for dy in (0, -int(panel_h * 0.03), int(panel_h * 0.03)):
                for dx in (0, -int(panel_w * 0.06), -int(panel_w * 0.12)):
                    cands.append((right_x + dx, top_y + dy))
        cands.append((int(SCREEN_W * MENU_HAMBURGER_REL[0]),
                      int(SCREEN_H * MENU_HAMBURGER_REL[1])))
        seen = set()
        uniq = []
        for c in cands:
            key = (int(c[0] // 8), int(c[1] // 8))
            if key in seen:
                continue
            seen.add(key); uniq.append(c)
        for (cx, cy) in uniq:
            if self.stop_requested:
                return '停止了'
            click(cx, cy)
            if not self._nap(0.55):
                return '停止了'
            l2 = ocr_lines(grab())
            if find_line(l2, '返回大厅', '进入睡眠模式', '法务', '举报'):
                return '点开菜单 (%d,%d)' % (cx, cy)
        return '没点开菜单(候选 %d 个都试过)' % len(uniq)

    def _lobby_start_candidates(self, lines):
        """v1.9.2: 认不出「开始」时, 用画面里其它字当锚点推按钮位置(不写死比例)"""
        # 比例是拿 2560x1600 真实大厅量出来的:
        #   「零建造,小队,填充」行 y≈1114..1144 -> 「开始」按钮中心 y≈1378 (Δ≈0.156H)
        #   「装备一个精灵」行   y≈1226..1275 -> 「开始」按钮中心 y≈1378 (Δ≈0.080H)
        out = []
        for kws, dy_ratio in ((('零建造', '小队', '填充', '模式'), 0.156),
                              (('装备一个精灵', '精灵'), 0.080),
                              (('通行证', '任务', '生涯'), 0.30)):
            hits = self._anchor_lines(lines, kws)
            if not hits:
                continue
            x0 = min(l['x'] for l in hits); x1 = max(l['x'] + l.get('w', 0) for l in hits)
            y1 = max(l['y'] + l.get('h', 0) for l in hits)
            mid = int((x0 + x1) / 2.0)
            cy = int(y1 + dy_ratio * SCREEN_H)
            for dx in (44, -44, 0, 100):
                out.append((mid + dx, cy))
        return out

    def leave_bad_squad(self, lines=None, reason=None):
        """ESC -> 侧栏 ☰ -> 返回大厅 -> 确认: 退出这局, 回大厅重新匹配

        v1.8.3: 动手之前先看一眼画面 —— 如果已经出现「返回大厅 / 比赛回顾」,
        说明这局已经结束(或结算页已经出来了), 交给结算流程, 绝不再按 ESC。
        """
        log('    ' + (reason or '队友全都倒了/被淘汰, 队里没有活着的队友: 自动返回大厅重新匹配'))
        steps = []
        if lines:
            if find_line(lines, '比赛回顾', '你的排名', '继续和此小队'):
                log('    画面上已经是结算页了 -> 不按 ESC, 交给结算流程')
                return None
            if find_line(lines, '返回大厅'):
                log('    画面上已经有「返回大厅」-> 不按 ESC, 交给结算流程')
                return None
        if self.dry:
            return '[DRY] 退出对局(ESC -> ☰ -> 返回大厅 -> 确认)'
        press_scan(VK_ESC)
        steps.append('ESC')
        if not self._nap(1.0):
            return '退出中断(F9)'
        steps.append(self.click_menu_hamburger_verified(why='☰ 菜单'))
        if not self._nap(1.0):
            return '退出中断(F9)'
        steps.append('OCR 返回大厅')
        if self._ocr_click('返回大厅', LEAVE_WORDS):
            if not self._nap(1.0):
                return '退出中断(F9)'
            # 可能弹确认框(确认/是/离开), 没有就直接回大厅了
            if self._ocr_click_short('确认退出', CONFIRM_WORDS):
                steps.append('确认')
            else:
                steps.append('无确认框')
        else:
            steps.append('没找到「返回大厅」')
            press_scan(VK_ESC)      # 关掉菜单, 下一轮再试
            self._nap(1.0)
            self.squad_bad = 0
            return '没找到「返回大厅」, 已关掉菜单等下轮再试'
        self._nap(1.5)
        self.leave_done_match = True
        self.squad_bad = 0
        return '已退出: ' + ' + '.join(steps)

    def load_int_conf(self, name, default, lo, hi):
        """从 设置.txt 读一个整数(带上下限), 读不到就用默认值。"""
        try:
            conf = {}
            path = data_file('设置.txt')
            if os.path.exists(path):
                for ln in io.open(path, encoding='utf-8-sig').read().splitlines():
                    if '=' in ln and not ln.strip().startswith('#'):
                        k, v = ln.split('=', 1)
                        conf[k.strip()] = v.strip()
            v = int(float(conf.get(name, default)))
            return max(lo, min(hi, v))
        except Exception:
            return default

    def load_click_cd(self, name, default):
        """从 设置.txt 读 lobby_click_cd / results_click_cd / click_react_min|max"""
        try:
            path = data_file('设置.txt')
            if os.path.exists(path):
                for line in io.open(path, encoding='utf-8-sig'):
                    t = line.strip()
                    if t.startswith(name):
                        v = float(t.split('=', 1)[-1].strip())
                        if 0.0 <= v <= 600.0:
                            return v
        except Exception:
            pass
        return default

    def load_teammate_alive_sec(self):
        """从 设置.txt 读 teammate_alive_sec(默认 45 秒)"""
        try:
            path = data_file('设置.txt')
            if os.path.exists(path):
                for line in io.open(path, encoding='utf-8-sig'):
                    t = line.strip()
                    if t.startswith('teammate_alive_sec'):
                        v = t.split('=', 1)[-1].strip()
                        v = float(v)
                        if 5 <= v <= 600:
                            return v
        except Exception:
            pass
        return TEAMMATE_ALIVE_SEC

    @staticmethod
    def _name_color_state(img, box):
        """按名字所在区域的像素颜色判断死活: 白=活, 红/灰=死, 其余=None。"""
        try:
            W, H = img.size
            x0 = max(0, int(box[0])); y0 = max(0, int(box[1]))
            x1 = min(W, int(box[2])); y1 = min(H, int(box[3]))
            if x1 - x0 < 8 or y1 - y0 < 6:
                return None
            px = list(img.crop((x0, y0, x1, y1)).getdata())
            n = len(px) or 1
            # v1.11.2: HUD 会被环境光染色(沙漠橙/风暴紫/冰面蓝), 绝对 RGB 判"白/红/灰"
            # 会把整片橙色背景都算成红 -> 误判"我死了" -> 反挂机全停(用户截图复现)。
            # 改为只认铁证: 整块很红(>45%)且几乎没有亮文字 -> dead; 有亮文字 -> alive;
            # 灰不再判死(半透明底板本来就是灰); 其余不表态。
            white = sum(1 for r, g, b in px if min(r, g, b) > 155 and max(r, g, b) - min(r, g, b) < 60)
            red = sum(1 for r, g, b in px if r > 120 and r - g > 70 and r - b > 70)
            white, red = white / n, red / n
            if red >= 0.45 and white < 0.10:
                return 'dead'
            if white >= 0.06:
                return 'alive'
            return None
        except Exception:
            return None
        return None

    def self_name_state(self, lines, img):
        """判断「我是否还活着」, 两条路:

        1) OCR 在左下角找到自己那行名字 -> 按该行颜色判(白=活, 红/灰=死), 并记住位置;
        2) OCR 读不到(字变红后 Windows OCR 常读不出) -> 用上次记住的位置直接看颜色。

        用户思路: 自己名字白色=还活着; 变红/变灰=已经死了(在观战队友)。
        """
        if img is None:
            return None
        W, H = img.size
        if lines:
            for l in lines:
                if l['y'] < H * 0.78 or l['x'] > W * 0.42:
                    continue      # 只找左下角队伍栏最后那行(自己的)
                nm = plate_name(l['text'])
                if not (name_match(nm, PLAYER_NAME) or (PLAYER_NAME[:3] and PLAYER_NAME[:3] in l['norm'])):
                    continue
                st = self._name_color_state(img, (l['x'] - 2, l['y'] - 2, l['x'] + l['w'] + 2, l['y'] + l['h'] + 2))
                # 只要这行确实是"我的名字"且读到了, 就先记住位置(下次读不到时按位置看颜色)
                self.self_box = (l['x'] / W, l['y'] / H, (l['x'] + l['w']) / W, (l['y'] + l['h']) / H)
                if st:
                    return st
        # 兜底: 用本局记住的位置看颜色(名字变红时 OCR 读不出来, 但位置没变)
        if self.self_box:
            bx = self.self_box
            st = self._name_color_state(img, (bx[0] * W - 3, bx[1] * H - 3, bx[2] * W + 3, bx[3] * H + 3))
            if st:
                return st
        return None

    def squad_names(self, lines, img):
        """v1.8.8: 直接看左下角队伍栏里"队友名字的颜色"判断死活（不再看血条）

        用户规则: 名字是白色 = 还活着; 名字是红色 = 已死(倒地/淘汰)。
        做法: 把 OCR 到的名字行框裁出来, 统计"接近白色"和"偏红"的像素占比:
          白: min(r,g,b) > 135 且 max-min < 50  (抗锯齿的灰白也算)
          红: r > 120 且 r-g > 55 且 r-b > 55
        返回 (总人数含自己, [(名字, 'alive'|'dead'), ...]); 读不到返回 None。
        """
        if img is None:
            return None
        rows = []
        W, H = SCREEN_W, SCREEN_H
        for l in lines:
            t = l.get('text') or ''
            x, y = l.get('x', 0), l.get('y', 0)
            w, h = l.get('w', 0), l.get('h', 0)
            if not (x < SQ_LIST_RELX * W and y > SQ_LIST_RELY * H):
                continue
            nm = plate_name(t)
            if len(nm) < 2 or len(nm) > 16:
                continue
            # v1.9.6: 血条/护盾数字(50/95/100)和符号不能被当成队友名字
            _cjk = sum(1 for ch in nm if is_cjk(ch))
            _alpha = sum(1 for ch in nm if ch.isalpha() and not is_cjk(ch))
            if _cjk < 2 and _alpha < 3:
                continue
            if any(k in nm for k in ('队伍', '好友', '任务', '语音', '聊天', '返回',
                                     '开始', '金币', '等级', '商城', '仓库')):
                continue
            if w < 30 or h < 8:
                continue
            x0 = max(0, int(x) - 2); x1 = min(W, int(x + w) + 2)
            y0 = max(0, int(y) - 2); y1 = min(H, int(y + h) + 2)
            if x1 - x0 < 20 or y1 - y0 < 6:
                continue
            try:
                crop = img.crop((x0, y0, x1, y1)).convert('RGB')
            except Exception:
                continue
            px = crop.load()
            total = crop.width * crop.height
            white = red = grey = 0
            for yy in range(crop.height):
                for xx in range(crop.width):
                    r, g, b = px[xx, yy]
                    mx, mn = max(r, g, b), min(r, g, b)
                    mean = (r + g + b) / 3.0
                    if mn > 170 and mx - mn < 45:
                        white += 1                    # 亮白 = 活着
                    elif r > 120 and r - g > 55 and r - b > 55:
                        red += 1                      # 偏红 = 死亡
                    elif 115 <= mean <= 172 and mx - mn < 38:
                        grey += 1                     # 灰暗中性 = 死亡(变色灰)
            fw = white / float(max(1, total))
            fr = red / float(max(1, total))
            fg = grey / float(max(1, total))
            if fw >= 0.10:
                st = 'alive'                  # 白名字 = 还活着
            elif fr >= 0.08 or fg >= 0.12:
                st = 'dead'                   # 红名字 / 灰名字 = 死了
            else:
                st = 'unknown'                # 认不出颜色 -> 不瞎猜, 交给上层保守处理
            rows.append((nm, st))
        if not rows:
            return None
        mates = [(nm, st) for nm, st in rows if not name_match(nm, PLAYER_NAME)]
        return 1 + len(mates), mates

    def squad_pixel_rows(self, img):
        """v1.9.0: 不靠 OCR, 直接按像素在左下角队伍栏里"分行 + 分颜色"

        做法: 裁左下角 (0.038W~0.30W, 0.66H~0.90H), 逐行统计"白名字像素"和
        "红名字像素", 连续行合并成一条, 间距 <=14px 的也合并; 过滤太窄/太短的碎片。
        每行按 白像素占比 判定: >=0.75 活着 / <=0.55 死亡 / 中间值当"认不出"。
        返回 [('alive'|'dead'|'unknown'), ...] 从上到下; 最后一行通常是"自己"。
        """
        if img is None:
            return None
        W, H = SCREEN_W, SCREEN_H
        if self.squad_panel is not None:
            # v1.9.1: 用进局后自己学到的队伍栏区域(适配不同分辨率/UI 比例), 四周留一点余量
            px0, py0, px1, py1 = self.squad_panel
            x0 = int(max(0.0, px0 - 0.010) * W)
            x1 = int(min(1.0, px1 + 0.060) * W)     # 名字后面还有平台图标, 多留一点
            y0 = int(max(0.0, py0 - 0.012) * H)
            y1 = int(min(1.0, py1 + 0.012) * H)
        else:
            x0, x1 = int(0.038 * W), int(0.30 * W)
            y0, y1 = int(0.66 * H), int(0.90 * H)
        if x1 - x0 < 60 or y1 - y0 < 30:
            return None
        try:
            crop = img.crop((x0, y0, x1, y1)).convert('RGB')
        except Exception:
            return None
        px = crop.load()
        cw, ch = crop.width, crop.height
        white = [0] * ch
        red = [0] * ch
        xmin_all = [10 ** 9] * ch
        xmax_all = [-1] * ch
        for yy in range(ch):
            w = r = 0
            xmn, xmx = 10 ** 9, -1
            for xx in range(cw):
                R, G, B = px[xx, yy]
                mx, mn = max(R, G, B), min(R, G, B)
                if mn > 170 and mx - mn < 45:
                    w += 1
                elif R > 120 and R - G > 55 and R - B > 55:
                    r += 1
                else:
                    continue
                if xx < xmn: xmn = xx
                if xx > xmx: xmx = xx
            white[yy] = w
            red[yy] = r
            xmin_all[yy] = xmn
            xmax_all[yy] = xmx
        bands = []
        cur = None
        for yy in range(ch):
            hit = (white[yy] + red[yy]) >= 4
            if hit and cur is None:
                cur = [yy, yy]
            elif hit:
                cur[1] = yy
            elif cur is not None:
                bands.append(tuple(cur))
                cur = None
        if cur is not None:
            bands.append(tuple(cur))
        merged = []
        for a, b in bands:
            if merged and a - merged[-1][1] <= 14:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        rows = []
        for a, b in merged:
            tw = sum(white[a:b + 1]); tr = sum(red[a:b + 1])
            tot = tw + tr
            if b - a < 10 or tot < 350:
                continue
            wd = max(xmax_all[a:b + 1]) - min(xmin_all[a:b + 1])
            if wd < 90:
                continue
            wp = tw / float(tot)
            if wp >= 0.75:
                st = 'alive'
            elif wp <= 0.55:
                st = 'dead'
            else:
                st = 'unknown'
            rows.append(st)
        return rows or None

    def note_plates(self, lines):
        """v1.8.4: 每轮 OCR 记录一下"画面里看得见的队友名字牌" = 这些队友还活着"""
        try:
            pos = self._plate_pos(lines)
        except Exception:
            pos = None
        if pos:
            nm = plate_name(pos[0])
            if nm:
                self.plate_seen[nm] = time.time()

    def alive_teammates(self):
        """返回 window 秒内看见过名字牌的队友列表(队里还有活人 -> 不该退出)"""
        now = time.time()
        win = max(10.0, float(self.teammate_alive_sec))
        out = []
        for nm, ts in list(self.plate_seen.items()):
            if now - ts <= win:
                out.append(nm)
        return out

    def team_info(self, lines, img=None):
        """v1.9.9: 两种机制合并 -> 队伍到底几个人

        机制1 名字行(squad_names): OCR 读队伍栏每行名字 + 名字颜色(白=活/红灰=死)
        机制2 像素分行(squad_pixel_rows): 不依赖 OCR, 按行切带再判白/红
        合并规则: 人数取两者最大值(封顶 4), 死活优先信"名字行", 名字行没读到时用像素行兜底。
        返回 dict: total(含自己) / mates / alive / dead / unknown / by_names / by_px
        """
        names = self.squad_names(lines, img) if lines is not None else None
        bands = None
        if img is not None:
            try:
                bands = self.squad_pixel_rows(img)
            except Exception:
                bands = None
        by_names = None if not names else len(names[1]) + 1
        by_px = None if not bands else len(bands)
        total = max([v for v in (by_names, by_px) if v] + [1])
        total = min(total, 4)
        alive = dead = unknown = 0
        if names:
            alive = sum(1 for _, st in names[1] if st == 'alive')
            dead = sum(1 for _, st in names[1] if st == 'dead')
            unknown = sum(1 for _, st in names[1] if st == 'unknown')
        if bands and alive == 0:
            mates = list(bands)[:-1]          # 最后一行是自己
            alive = sum(1 for st in mates if st == 'alive')
            dead = max(dead, sum(1 for st in mates if st == 'dead'))
        if total >= 2:
            self.squad_seen_max = max(self.squad_seen_max, total)
        return {'total': total, 'mates': total - 1, 'alive': alive, 'dead': dead,
                'unknown': unknown, 'by_names': by_names, 'by_px': by_px}

    def maybe_check_squad(self, lines, state, img=None):
        """开局前(等待区/巴士)检查队伍人数; 连续 2 轮不足 3 人就退出重排"""
        if state == 'LOBBY':
            self.leave_done_match = False
        # v1.8.3: 只有"我自己还活着"时才会因为队伍不足而退;
        # 我死了(观战)就老老实实等着看队友, 绝不按 ESC —— 这是用户明确要求的规则,
        # 也修掉了"结算页 + ESC 菜单同时出现 -> 一直卡住"的 bug。
        if state == 'DEAD':
            self.squad_bad = 0
            return None
        states = SQUAD_ALL_STATES if self.squad_mode == 'alive' else SQUAD_CHECK_STATES
        if state not in states:
            self.squad_bad = 0
            return None
        # v1.9.4 修 bug: 战斗巴士上不显示自己的名字/血量, 队伍栏也看不到,
        # 以前这里会把"读不到"当成"只有我一个人" -> 4 人满编也被退对局。
        if state in ('BUS', 'SKYDIVE'):
            self.squad_bad = 0
            return None
        if state in ('SKYDIVE', 'INGAME') and not self.alive:
            self.squad_bad = 0
            return None
        # 屏幕上已经有「返回大厅 / 比赛回顾」= 这局已经结束, 交给结算流程, 不要按 ESC
        if find_line(lines, '返回大厅') or find_line(lines, '比赛回顾', '你的排名'):
            self.squad_bad = 0
            return None
        if self.leave_done_match:
            return None
        total = self.squad_count(lines)

        if state == 'WAITING':
            # 开局前: 必须凑满 SQUAD_MIN(3) 人, 否则退出重排
            # v1.10.0: 以前只看"名字行", 读少一行(4 人读成 2 人)就会把满编队退掉;
            #          现在两套机制一起看, 只要有一套数到 >= SQUAD_MIN 就不退。
            px = self.squad_pixel_rows(img)
            by_px = len(px) if px else None
            best = max([v for v in (total, by_px) if v] or [1])
            log('    等待区人数: 名字行 %d / 像素行 %s -> 取 %d' % (total, by_px, best))
            if best >= SQUAD_MIN:
                self.squad_bad = 0
                return None
            if best <= 1:
                self.squad_bad = 0
                log('    等待区两套机制都没读到人(可能还没刷出来), 先不动')
                return None
            total = best
        else:
            # 局内(v1.8.4 按用户规则):
            #   只要 45 秒内还看见过某个队友的名字牌 = 他还活着 -> 不退出(他能复活我/其他队员)
            #   队友全都倒地/淘汰(很长时间没看见任何人) 且我还活着 -> 连续 3 轮 -> 回大厅
            info = self.team_info(lines, img)
            log('    队伍 %d 人 (名字行 %s / 像素行 %s, 活 %d 倒 %d 认不出 %d)'
                % (info['total'], info['by_names'], info['by_px'],
                   info['alive'], info['dead'], info['unknown']))
            if info['alive'] > 0:
                # v1.9.9: 综合两种机制后只要还有活着的队友 -> 绝不退
                self.squad_bad = 0
                return None
            roster = self.squad_names(lines, img)
            name_info = None
            pixel_decided = False
            if roster is None or len(roster[1]) < 2:
                # v1.9.0: OCR 没读全(名字太小/日文/紫雾) -> 用像素分行兜底
                prow = self.squad_pixel_rows(img)
                if prow and len(prow) >= 2:
                    mates_px = prow[:-1]          # 最后一行通常是自己
                    alive_px = [i for i, st in enumerate(mates_px) if st == 'alive']
                    dead_px = [i for i, st in enumerate(mates_px) if st == 'dead']
                    pixel_decided = True
                    if alive_px:
                        self.squad_bad = 0
                        return None
                    if dead_px:
                        name_info = (len(prow), len(dead_px))
                        log('    像素分行: %d 行, 队友 %s -> 没人能复活我了'
                            % (len(prow), '/'.join(mates_px)))
                    else:
                        self.squad_bad = 0
                        log('    像素分行: 颜色都认不出(%s), 先不动' % '/'.join(mates_px))
                        return None
                    roster = None
            if not pixel_decided and roster is not None:
                total = roster[0]                   # 以队伍栏行数为准
                if total >= 2:
                    self.squad_seen_max = max(self.squad_seen_max, total)
                if total <= 1 and self.squad_seen_max >= 2:
                    # v1.9.7: 队伍栏只剩自己一行 -> 队友都没了(倒地被淘汰/退出),
                    # 连续 2 轮就回大厅; 否则会一直站在那不动
                    self.squad_bad += 1
                    log('    队伍栏只剩我自己了(本局最多见过 %d 人), 连续 %d 轮'
                        % (self.squad_seen_max, self.squad_bad))
                    if self.squad_bad >= 2:
                        return self.leave_bad_squad(lines)
                    return None
                standing = [nm for nm, st in roster[1] if st == 'alive']
                dead = [nm for nm, st in roster[1] if st == 'dead']
                unknown = [nm for nm, st in roster[1] if st == 'unknown']
                if standing:
                    self.squad_bad = 0
                    return None
                if not dead:
                    # 全是"认不出颜色"(紫雾/被挡/名字太小) -> 保守不动, 免得误退
                    self.squad_bad = 0
                    log('    队伍栏名字颜色没认出来(%d 个), 先不动' % len(unknown))
                    return None
                name_info = (len(roster[1]), len(dead))
                log('    队友 %d 个: 白名字(活着) 0 / 红或灰名字(死了) %d -> 没人能复活我了'
                    % name_info)
                # 掉线/没读到的行不算; 连确认 need 次就走
            elif not pixel_decided:
                # 队伍栏没读到 -> 退回名字牌 45 秒窗口的老办法
                plate_alive = self.alive_teammates()
                if plate_alive:
                    self.squad_bad = 0
                    return None
                if len(self.plate_seen) == 0:
                    self.squad_bad = 0
                    return None              # 从没见过名牌: 保守不动
        self.squad_bad += 1
        need = 2          # 连续 2 轮确认(~4-6 秒)就动, 免得手抖
        if state in ('WAITING', 'BUS'):
            log('    队伍人数 %d/%d (连续 %d 轮, %s, 模式 %s)' % (total, SQUAD_MIN, self.squad_bad, state, self.squad_mode))
        elif name_info is not None:
            log('    队友 %d 个名字全红(连续 %d 轮), 我 1 人还活着 -> 准备返回大厅'
                % (name_info[0], self.squad_bad))
        else:
            log('    队友 %d 秒内都没露头(多半都倒了/被淘汰), 我 1 人还活着 (连续 %d 轮)'
                % (int(self.teammate_alive_sec), self.squad_bad))
        if self.squad_bad < need:
            return None
        return self.leave_bad_squad(lines)

    # --- 自动朝队友走 ---
    def _learn_squad_panel(self, boxes):
        """v1.9.1: 学出队伍栏在屏幕上的区域(相对坐标), 供像素分行使用."""
        if not boxes:
            return
        W = float(SCREEN_W or 1); H = float(SCREEN_H or 1)
        xs0 = [b[0] / W for b in boxes]; ys0 = [b[1] / H for b in boxes]
        xs1 = [b[2] / W for b in boxes]; ys1 = [b[3] / H for b in boxes]
        cur = (min(xs0), min(ys0), max(xs1), max(ys1))
        if self.squad_panel is None:
            self.squad_panel = cur
        else:
            p = self.squad_panel
            self.squad_panel = (min(p[0], cur[0]), min(p[1], cur[1]),
                                max(p[2], cur[2]), max(p[3], cur[3]))

    def learn_teammates(self, lines):
        """从左下角队伍栏读队友名字(会被记下来, 之后用来在画面里找他们的名牌)"""
        found = []
        for l in lines:
            t = l.get('text') or ''
            x, y = l.get('x', 0), l.get('y', 0)
            if x < SQ_LIST_RELX * SCREEN_W and y > SQ_LIST_RELY * SCREEN_H:
                nm = plate_name(t)
                if len(nm) < 2 or len(nm) > 16:
                    continue
                cjk = sum(1 for ch in nm if is_cjk(ch))
                alpha = sum(1 for ch in nm if 'a' <= ch <= 'z')
                digit = sum(1 for ch in nm if ch.isdigit())
                # 至少 2 个中日文字, 或 2 个字母, 或"字母+数字"混排; 纯数字/"12天" 之类不算队友
                if not (cjk >= 2 or alpha >= 2 or (alpha >= 1 and digit >= 1)):
                    continue
                if any(k in nm for k in ('队伍', '好友', '任务', '语音', '聊天', '返回',
                                         '开始', '金币', '等级', '商城', '仓库')):
                    continue
                if name_match(nm, PLAYER_NAME):
                    continue
                found.append(nm)
                # 记住"这个队友名字在队伍栏里的位置"(相对坐标), 之后画面里这一块不算名牌,
                # 免得追踪的时候把左下角队伍栏里的名单当成队友在屏幕上的方向
                import sys
                w = l.get('w') or 0
                h = l.get('h') or 0
                rel = (x / float(SCREEN_W or 1), y / float(SCREEN_H or 1),
                       (x + w) / float(SCREEN_W or 1), (y + h) / float(SCREEN_H or 1))
                self._learn_squad_panel([rel])
                for b in self.squad_boxes:
                    if name_match(b.get('name') or '', nm):
                        b.update(x0=rel[0], y0=rel[1], x1=rel[2], y1=rel[3], name=nm)
                        break
                else:
                    self.squad_boxes.append({'name': nm, 'x0': rel[0], 'y0': rel[1],
                                             'x1': rel[2], 'y1': rel[3]})
                    if len(self.squad_boxes) > 8:
                        self.squad_boxes = self.squad_boxes[-8:]
        if found:
            merged = list(self.teammates)
            for nm in found:
                if not any(name_match(nm, x) for x in merged):
                    merged.append(nm)
            self.teammates = merged[:6]
        return self.teammates

    def _squad_box(self):
        """左下角队伍栏的区域(按屏幕比例算, 适配不同 UI 缩放/分辨率): (x_max, y_min)"""
        return SQ_LIST_RELX * SCREEN_W, SQ_LIST_RELY * SCREEN_H

    def _in_squad_boxes(self, l):
        """这一行是不是落在"队伍栏里某个队友名字"的那块区域里(那是指名单, 不是屏幕方向)"""
        if not self.squad_boxes:
            return False
        x = l.get('x', 0)
        y = l.get('y', 0)
        w = l.get('w') or 0
        h = l.get('h') or 0
        cx, cy = x + w / 2.0, y + h / 2.0
        for b in self.squad_boxes:
            bx0, by0 = b['x0'] * SCREEN_W, b['y0'] * SCREEN_H
            bx1, by1 = b['x1'] * SCREEN_W, b['y1'] * SCREEN_H
            # 中心点落进这个框(外扩几像素容错) 就认为是队伍栏那一条
            if bx0 - 6 <= cx <= bx1 + 6 and by0 - 6 <= cy <= by1 + 6:
                return True
        return False

    def looks_like_plate(self, l):
        """v1.10.4: 不依赖名字匹配, 判断这一行"像不像屏幕上的队友名牌"

        背景: 屏幕边缘的名牌字小 + 描边 + 彩色, OCR 经常读成乱码
        (实测 softty_warbie3 -> ". SO 孬 儿 wa 刎 e3 >"), 跟左下角队伍栏
        学到的名字对不上, 结果永远找不到队友、只会原地扫视。
        所以再加一层"长得像名字"的兜底判定。
        """
        t = l.get('text') or ''
        nm = plate_name(t)
        if len(nm) < 3 or len(nm) > 16:
            return ''
        x, y = l.get('x', 0), l.get('y', 0)
        rely = y / float(SCREEN_H or 1)
        relx = x / float(SCREEN_W or 1)
        if rely < 0.04 or rely > 0.94:          # 最顶/最底: HUD 区
            return ''
        if relx < 0.30 and rely < 0.16:         # 左上角任务/血条
            return ''
        if relx > 0.88 and rely < 0.10:         # 右上角小地图
            return ''
        cjk = sum(1 for ch in nm if is_cjk(ch))
        alpha = sum(1 for ch in nm if 'a' <= ch <= 'z')
        if cjk < 2 and alpha < 2:
            return ''
        bad = ('收集', '地图', '碎片', '宝箱', '任务', '经验', '弹药', '普通',
               '召回', '淘汰', '击倒', '我们', '在哪', '血', '护盾', '米',
               '秒', '等级', '金币', '队伍', '好友', '聊天', '语音', '返回',
               '开始', '商城', '仓库', '通行证', '生涯', '赛事', '设置')
        if any(k in nm for k in bad):
            return ''
        if name_match(nm, PLAYER_NAME):         # 自己的名字不算
            return ''
        return nm

    def _plate_pos(self, lines):
        """在画面上找队友名牌, 返回 (名字, x, y)

        排除三类东西, 免得把"左下角队伍栏里的名字"当成"画面里队友的位置":
          1. 自己的名字 (PLAYER_NAME) —— 那是玩家自己的, 不参与追踪
          2. 左下角队伍栏区域 —— 那里是固定名单, 不是屏幕上的位置
          3. 最上方 HUD / 最下方快捷栏 —— 不会出现名牌
        """
        if not self.teammates:
            return None
        x_max, y_min = self._squad_box()
        cands = []
        for l in lines:
            t = l.get('text') or ''
            x, y = l.get('x', 0), l.get('y', 0)
            rely = y / float(SCREEN_H or 1)
            if rely < 0.02 or rely > 0.97:        # v1.9.8: 只排除最顶/最底一条, 边缘名牌也要用
                continue
            if x < x_max and y > y_min:           # 左下角队伍栏: 名单, 不是位置
                continue
            if self._in_squad_boxes(l):           # 之前记下的队伍栏名字位置, 也不算画面方向
                continue
            if name_match(t, PLAYER_NAME):        # 自己的名字: 跳过
                continue
            for nm in self.teammates:
                if name_match(t, nm):
                    cands.append((nm, x, y))
                    break
        if not cands:
            # v1.10.4 兜底: 名牌字太小/太花时 OCR 会读成乱码, 名字对不上,
            # 这时退一步——只要"长得像队友名牌"就当队友 (画面里不会显示敌人名字)
            fallback = []
            for l in lines:
                x, y = l.get('x', 0), l.get('y', 0)
                rely = y / float(SCREEN_H or 1)
                if rely < 0.02 or rely > 0.97:
                    continue
                if x < x_max and y > y_min:
                    continue
                if self._in_squad_boxes(l):
                    continue
                nm = self.looks_like_plate(l)
                if nm:
                    fallback.append((l.get('text') or '', x, y))
            if not fallback:
                return None
            fallback.sort(key=lambda c: abs(c[1] - SCREEN_W / 2.0))
            return fallback[0]
        # 优先用锁定的队友; 否则取最靠近屏幕水平中心的一个
        for nm, x, y in cands:
            if self.follow_name and name_match(nm, self.follow_name):
                return (nm, x, y)
        cands.sort(key=lambda c: abs(c[1] - SCREEN_W / 2.0))
        return cands[0]

    # ---------- v1.9.3: 用"闭环瞄准"把队友名字转到屏幕正中 ----------
    def _plate_center_step(self, lines, img):
        """算这一帧该怎么转, 才能把队友名字转到屏幕正中

        v1.9.3 按用户规则:
          · 名字在屏幕上方 -> 抬头(pitch 向上) + 左右转
          · 名字在屏幕下方/左下/右下 -> 只左右转身, 绝不低头(落地默认已经低头)
          · 名字贴左右边缘 -> 往那一侧大角度转
          · 目标: 名字落在屏幕正中(水平 ±6%宽, 垂直 ±6%高)
        返回 (名字, x, y, yaw_deg, pitch_deg, centered) 或 None
        """
        pos = self._plate_pos(lines)
        if pos is None:
            return None
        nm, px, py = pos
        W = float(SCREEN_W or 1); H = float(SCREEN_H or 1)
        dx = px - W / 2.0
        dy = py - H / 2.0
        ppd = max(1.0, view_px_per_deg())
        yaw = 0.0
        pitch = 0.0
        centered = (abs(dx) <= W * 0.06) and (abs(dy) <= H * 0.06)
        if abs(dx) > W * 0.02:
            if abs(dx) > W * 0.40:
                # v1.9.8: 名字贴在左右边缘 -> 大角度转过去(避免只转一点点看不到)
                yaw = max(-85.0, min(85.0, 1.30 * dx / ppd))
            else:
                yaw = max(-90.0, min(90.0, 0.85 * dx / ppd))
        if dy < -H * 0.08:
            # 名字在上方: 抬头 + 左右转
            pitch = max(-32.0, min(0.0, 0.55 * dy / ppd))
        elif dy > H * 0.08:
            # 名字在下方(人在背后/侧后): 主要转身, 不盯着地面
            side = 1.0 if dx > 0 else (-1.0 if dx < 0 else (self.follow_turn_dir or random.choice((-1.0, 1.0))))
            self.follow_turn_dir = side
            yaw = max(-125.0, min(125.0, side * max(50.0, abs(yaw))))
            pitch = 0.0
            # v1.9.5: 抬头抬过头(在看天)时名字会被顶到屏幕下方 -> 先低头回水平
            if self.aim_pitch_total < -8.0:
                pitch = min(14.0, -self.aim_pitch_total - 2.0)
                log('    抬头过头(累计%.0f°), 先低头%.0f°回水平' % (self.aim_pitch_total, pitch))
        return nm, px, py, yaw, pitch, centered

    def aim_at_teammate(self, max_steps=8):
        """闭环转视角, 直到队友名字出现在屏幕正中 (找不到就大角度扫视)"""
        parts = []
        # v1.9.6: 上一串结束时视线可能已经仰起来了, 这里继承, 不再每次清零
        self.aim_pitch_total = self.view_pitch_bias
        if self.aim_pitch_total <= -14.0:
            back = min(16.0, -self.aim_pitch_total - 2.0)
            mouse_look_smooth(0.0, back * COUNTS_PER_DEG, dur=0.22)
            self.aim_pitch_total += back
            parts.append('回水平%.0f°' % back)
        for i in range(max_steps):
            if self.stop_requested or not self.game_is_foreground():
                return '瞄准中断'
            img = grab()
            lines = ocr_lines(img)
            self.learn_teammates(lines)
            st = self._plate_center_step(lines, img)
            if st is None:
                side = self.follow_turn_dir or random.choice((-1.0, 1.0))
                self.follow_turn_dir = side
                turn = side * random.uniform(70.0, 115.0)
                # v1.9.5: 落地默认低头, 需要抬一点头才看得到名字牌;
                # 但必须有上限 —— 以前每轮都抬, 结果一路抬到看天上,
                # 队友名字反而被顶到屏幕下方, 永远找不到。
                if self.aim_pitch_total <= -18.0:
                    lift = 12.0                       # 抬太高了 -> 低回来
                elif self.aim_pitch_total >= 10.0:
                    lift = -8.0                       # 低太多(看地面) -> 抬一点
                elif i % 3 == 2:
                    lift = -8.0
                else:
                    lift = 0.0
                mouse_look_smooth(turn * COUNTS_PER_DEG, lift * COUNTS_PER_DEG,
                                  dur=max(0.30, min(0.75, abs(turn) / 220.0)))
                self.aim_pitch_total += lift
                parts.append('扫视%.0f°%s' % (turn, ('+抬头%.0f°' % -lift) if lift < 0 else ('+低头%.0f°' % lift if lift > 0 else '')))
                if not self._nap(0.15):
                    return '瞄准中断'
                continue
            nm, px, py, yaw, pitch, centered = st
            short = plate_name(nm)[:6]
            if centered:
                parts.append('%s已居中' % short)
                self.view_pitch_bias = self.aim_pitch_total
                return '瞄准成功(%s, x=%d,y=%d): %s' % (short, px, py, '+'.join(parts[-4:]) or '一开始就居中')
            if self.aim_pitch_total + pitch < -28.0:
                pitch = -28.0 - self.aim_pitch_total      # 硬上限: 最多抬头 28°
            elif self.aim_pitch_total + pitch > 20.0:
                pitch = 20.0 - self.aim_pitch_total
            mouse_look_smooth(yaw * COUNTS_PER_DEG, pitch * COUNTS_PER_DEG,
                              dur=max(0.14, min(0.55, (abs(yaw) + abs(pitch)) / 240.0)))
            self.aim_pitch_total += pitch
            parts.append('%s yaw%.0f/pitch%.0f' % (short, yaw, pitch))
            if not self._nap(0.20):
                return '瞄准中断'
        self.view_pitch_bias = self.aim_pitch_total
        return '没完全居中(试了%d步): %s' % (max_steps, '+'.join(parts[-3:]))

    def follow_teammate_sustained(self, seconds=None):
        """朝锁定的队友连续冲刺 10~20 秒 (v1.8.0)

        要求:
          · 每次开始往队友方向移动时, 先按一下「稿子」键(默认 F)
          · W 全程按住不松手 —— 中间跳跃也只是在按住 W 的状态下点一下空格
          · 期间持续 OCR 修正朝向(只动鼠标, 不动 W)
        """
        total = seconds if seconds else random.uniform(10.0, 20.0)
        t0 = time.time()
        parts = []
        jump_vk = key_vk('跳跃')
        pick_vk = key_vk('稿子')

        # 1) 开始移动前先按一下稿子键(用户要求: 每次往队友方向移动都按 F)
        if not self.stop_requested:
            press_scan(pick_vk)
            parts.append('按镐子')
            # 关键: 等镐子真的切出来, 再开始按 W 往前走(用户要求"按一下镐子, 然后再开始按 W")
            if not self._nap(0.45):
                return '朝队友冲刺 0.0s(刚按完镐子就收到停止)'

        # 2) v1.9.3: 先用"闭环瞄准"把队友名字转到屏幕正中(会抬头/转身),
        #    对准了再按住 W 往前走
        try:
            aim = self.aim_at_teammate(6)
            parts.append('瞄准:' + aim[:38])
        except Exception:
            parts.append('瞄准跳过')

        # 3) W + 奔跑 全程按住, 直到这串结束才松开
        key_down_scan(VK_W)
        key_down_scan(key_vk('奔跑'))
        parts.append('按住W')
        walked_t = time.time()
        jumps = 0
        jump_plan = 5 if random.random() < 0.5 else 6
        next_jump = time.time() + random.uniform(1.8, 3.2)
        chunk_i = 0
        try:
            # 起跑先连跳 3 下(W 一直按着, 跳跃只是点一下空格)
            for _ in range(3):
                if self.stop_requested:
                    break
                press_scan(jump_vk)
                jumps += 1
                if not self._nap(0.5):
                    break
            if jumps:
                parts.append('连跳%d' % jumps)

            while time.time() - t0 < total:
                if self.stop_requested or not self.game_is_foreground():
                    break
                img = grab()
                lines = ocr_lines(img)
                self.learn_teammates(lines)
                st = self._plate_center_step(lines, img)
                pos = None if st is None else (st[0], st[1], st[2])
                if st is not None:
                    nm, px, py, yaw_deg, pitch_deg, centered = st
                    self.follow_name = nm
                    self.follow_miss = 0
                    if not centered:
                        mouse_look_smooth(yaw_deg * COUNTS_PER_DEG, pitch_deg * COUNTS_PER_DEG,
                                          dur=max(0.14, min(0.50, (abs(yaw_deg) + abs(pitch_deg)) / 240.0)))
                        self.aim_pitch_total += pitch_deg
                        self.view_pitch_bias = max(-26.0, min(14.0, self.aim_pitch_total))
                        parts.append('%s修正%.0f/%.0f' % (plate_name(nm)[:6], yaw_deg, pitch_deg))
                    else:
                        parts.append(plate_name(nm)[:6] + '居中')
                    # v1.11.1: 这里原本是 v1.9.1 遗留的「再算一次转向」代码块，与上面的闭环瞄准
                    # 重复(会转两次)，而且引用了未定义的 ppd/W -> 一进这个分支就 NameError，
                    # 整串 E(朝队友跑) 直接崩掉。用户症状正是「永远不往队友走」。整块删除。
                else:
                    self.follow_miss += 1
                    # v1.9.1: 看不见人时朝同一侧持续大角度扫视(转过 180° 能拍到背后),
                    # 而不是随机小角度左右抖
                    side = self.follow_turn_dir or random.choice((-1.0, 1.0))
                    self.follow_turn_dir = side
                    turn = side * random.uniform(70.0, 110.0)
                    # v1.10.4: 扫视期间 W 一直按着, 所以人不会被"卡在原地不动";
                    # 这里只补一句日志, 让用户能从日志看出"没找到人但在往前走"
                    parts.append('找人边走%.0f°' % turn)
                    mouse_look_smooth(turn * COUNTS_PER_DEG, 0.0,
                                      dur=max(0.30, min(0.80, abs(turn) / 220.0)))
                    parts.append('找人%.0f°' % turn)

                # 跑动 + 跳跃(W 始终按着)
                chunk_i += 1
                chunk = min(random.uniform(1.2, 2.4), max(0.2, total - (time.time() - t0)))
                if (jumps < jump_plan and time.time() >= self.space_block_until
                        and (time.time() >= next_jump or chunk_i % 2 == 1)):
                    press_scan(jump_vk)          # W 不松, 只点一下空格
                    jumps += 1
                    next_jump = time.time() + random.uniform(2.2, 4.0)
                    parts.append('跑跳%d' % jumps)
                if not self._nap(chunk):
                    break
        finally:
            key_up_scan(key_vk('奔跑'))
            key_up_scan(VK_W)
        real = time.time() - t0
        walked = max(0.0, time.time() - walked_t)
        return '朝队友冲刺 %.1fs(W 全程按住, 跳 %d 下): %s' % (
            real, jumps, '+'.join(parts[:10]) if parts else '没走')

    def follow_teammate(self):
        """转向 + 前进: 让锁定的队友名字跑到屏幕正中, 然后一直往那个方向走"""
        parts = []
        swapped = False          # 一串里最多换一次人, 免得两个都看不见时来回弹
        tried = set()
        for step in range(3):
            if self.stop_requested or not self.game_is_foreground():
                break
            img = grab()
            lines = ocr_lines(img)
            self.learn_teammates(lines)
            pos = self._plate_pos(lines)
            if pos is None:
                self.follow_miss += 1
                if self.follow_miss >= 2 and not swapped:
                    old = self.follow_name
                    if old:
                        tried.add(plate_name(old))
                    pool = [n for n in self.teammates if plate_name(n) not in tried]
                    if not pool:                    # 都试过了: 从头再来
                        tried.clear()
                        pool = [n for n in self.teammates
                                if not (old and name_match(n, old))]
                    self.follow_name = random.choice(pool) if pool else None
                    self.follow_miss = 0
                    swapped = True
                    if old:
                        parts.append('换队友')
                # 看不见人: 左右扫一眼(幅度大一点, 好把名牌扫进画面), 再朝刚才的方向小步走
                mouse_look_smooth(random.choice((-1.0, 1.0)) * random.uniform(40.0, 100.0) * COUNTS_PER_DEG,
                                  0.0, dur=random.uniform(0.30, 0.55))
                if not self._nap(0.25):
                    break
                key_down_scan(VK_W)
                try:
                    self._nap(random.uniform(0.7, 1.2))
                finally:
                    key_up_scan(VK_W)
                parts.append('找人')
                continue
            nm, px, py = pos
            self.follow_name = nm
            self.follow_miss = 0
            ppd = view_px_per_deg()
            cx, cy = SCREEN_W / 2.0, SCREEN_H / 2.0
            dx = px - cx
            rely = py / float(SCREEN_H or 1)
            center_band = SCREEN_W * 0.015          # 死区: 名字离中线这么近就算"对准了"
            walk_s = random.uniform(FIDGET_FOLLOW_WAIT * 0.8, FIDGET_FOLLOW_WAIT * 1.4)
            # ── 名字在屏幕下方: 队友在"背后/身下". 绝不低头, 只左右转, 而且不往前冲 ──
            if rely > 0.58:
                if abs(dx) < SCREEN_W * 0.10:
                    side = random.choice((-1.0, 1.0))   # 正下方 -> 不知道在左还是在右, 先转一大圈找
                    deg = side * random.uniform(100.0, 160.0)
                else:
                    deg = max(-110.0, min(110.0, 1.2 * dx / ppd))
                mouse_look_smooth(deg * COUNTS_PER_DEG, 0.0,      # dy 恒为 0: 不低头
                                  dur=max(0.26, min(0.7, abs(deg) / 190.0)))
                parts.append('%s在下方:背后转%.0f°' % (nm[:6], abs(deg)))
                walk_s = random.uniform(0.25, 0.5)               # 只小挪一步, 主要靠转身找
            # ── 名字在屏幕上方: 要么我正低着头(队友在前面), 要么队友在空中; 也只左右转 ──
            elif rely < 0.30:
                if abs(dx) > center_band:
                    deg = max(-75.0, min(75.0, 0.8 * dx / ppd))
                    mouse_look_smooth(deg * COUNTS_PER_DEG, 0.0,
                                      dur=max(0.22, min(0.6, abs(deg) / 180.0)))
                    parts.append('%s在上方:%s转%.0f°' % (nm[:6], '右' if deg > 0 else '左', abs(deg)))
                else:
                    parts.append('%s在上方:已对准' % nm[:6])
            # ── 名字在中间带: 正常水平对准 ──
            elif abs(dx) > center_band:
                deg = max(-75.0, min(75.0, 0.8 * dx / ppd))
                mouse_look_smooth(deg * COUNTS_PER_DEG, 0.0,
                                  dur=max(0.22, min(0.6, abs(deg) / 180.0)))
                parts.append('%s在%s转%.0f°' % (nm[:6], '右' if deg > 0 else '左', abs(deg)))
            if not self._nap(0.22):
                break
            key_down_scan(VK_W)                     # 朝那个方向走过去
            try:
                self._nap(walk_s)
            finally:
                key_up_scan(VK_W)
            parts.append('前进')
        return '+'.join(parts) if parts else '-'

    def _stats_files(self):
        """计数写两处: 程序目录 stats.txt(看得见) + 用户目录(换版本/重装也不丢)"""
        out = [os.path.join(ROOT, 'stats.txt')]
        try:
            base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
            d = os.path.join(base, 'FortniteAutoBot')
            os.makedirs(d, exist_ok=True)
            out.append(os.path.join(d, 'matches.txt'))
        except Exception:
            pass
        return out

    def _match_total(self):
        """总局数: 取两个计数文件里行数较多的那个(v1.12.6)

        以前是「两个文件行数之和 ÷ 2」, 只要有一边缺文件/写失败,
        日志里的累计局数就直接少一半(5 局显示成 2 局)。
        两个文件正常情况下内容一致(每次记两处), 取较大值就是真实局数;
        某一边写失败时也不会被拉低。
        """
        try:
            counts = []
            for p in self._stats_files():
                if not os.path.isfile(p):
                    continue
                n = 0
                for ln in io.open(p, encoding='utf-8-sig', errors='ignore'):
                    if ln.strip():
                        n += 1
                counts.append(n)
            return max(counts) if counts else 0
        except Exception:
            return 0

    def record_match(self, tag='返回大厅'):
        """完成一局: 每点一次「返回大厅」(或从结算页回到大厅)就 +1"""
        try:
            t = time.strftime('%Y-%m-%d %H:%M:%S')
            for p in self._stats_files():
                try:
                    with open(p, 'a', encoding='utf-8') as f:
                        f.write('%s\t%s\n' % (t, tag))
                except Exception as e:
                    log('    WARN 写对局统计失败(%s): %s' % (p, e))
            self.match_counted = True
            self.match_total = self._match_total()
            log('    MATCH 记一局: 点了一次「返回大厅」 -> 累计 %d 局' % self.match_total)
        except Exception:
            pass

    def maybe_interact(self, lines, state):
        """旧路径(兼容保留): 画面里出现交互提示就按一次交互键。v1.12.4 起由 check_actions 接管。"""
        return self.check_actions(lines, state)

    # 任务栏/说明文字里的这些词不是交互提示
    ACTION_SKIP_WORDS = ('宝箱', '地图碎片', '经验值', '任务', '图鉴')

    def _action_taskbar(self, hit):
        """判断这行字是不是左上角任务条/说明文字(不是交互提示)"""
        cx = hit['x'] + hit['w'] // 2
        cy = hit['y'] + hit['h'] // 2
        if cx < SCREEN_W * 0.40 and cy < SCREEN_H * 0.22:   # 左上角任务条
            return True
        nm = hit.get('norm') or ''
        if len(nm) > 8:                                     # 提示都很短, 长的是说明文字
            return True
        return any(k in nm for k in self.ACTION_SKIP_WORDS)

    def _find_action_line(self, lines, words):
        """v1.12.6: 逐行找第一个「含关键词且不是任务条」的行。

        以前用 find_line 只取 OCR 顺序第一行: 那一行如果正好是左上角的长句
        (例如「620/1000 打开宝箱」进度), 整条规则就被 continue 掉,
        屏幕下方真正的提示(例如「召回货车」)反而被漏掉。
        """
        for l in lines:
            nm = l.get('norm') or ''
            if not nm:
                continue
            if not any(w in nm for w in words):
                continue
            if self._action_taskbar(l):
                continue
            return l
        return None

    def _send_combo(self, vks):
        """同时按住一组键再依次松开(组合键)"""
        sent = []
        try:
            for vk in vks:
                if key_down_scan(vk):
                    sent.append(vk)
                    time.sleep(0.03)
        finally:
            for vk in reversed(sent):
                key_up_scan(vk)
        return bool(sent)

    def _do_action(self, rule, hit, hold=0.0):
        kind = rule['kind']
        if kind == 'click':
            cx, cy = center(hit)
            click(cx, cy)
            return True
        if kind == 'combo':
            return self._send_combo(rule['vks'])
        vk = rule['vk'] if kind == 'key' else key_vk('交互')
        if hold > 0:
            # v1.12.6: 救援/召回这类提示要按住才生效, 轻点一下游戏不认
            if not key_down_scan(vk):
                return False
            try:
                return self._nap(hold)
            finally:
                key_up_scan(vk)
        return press_scan(vk)

    def check_actions(self, lines, state):
        """v1.12.4: 交互关键词优先(v1.12.6 修了两处逻辑).

        画面出现「打开/搜索/上升/救援/召回(内置)」或 识别动作.txt 里的自定义字眼时:
          立刻按一次(默认交互键 E / 点击 / 自定义按键 / 组合键), 优先级高于防挂机;
          按完等 5 秒再识别, 还在就再按一次;
          「救援/召回」是按住型提示 -> 按住 0.8 秒, 然后停 15 秒(期间反挂机让路)。
        仅对局内生效; 左上角任务条/长句说明文字会被排除(逐行找, 长句不会挡住真提示)。
        """
        if state != 'INGAME':
            return None
        # 允许挂机中途改 识别动作.txt: 只多一次 stat, 内容变了才重新解析
        try:
            self.action_rules = load_action_rules()
        except Exception:
            pass
        now = time.time()
        for r in self.action_rules:
            hit = self._find_action_line(lines, r['words'])
            if not hit:
                continue
            nm = hit.get('norm') or ''
            long_kind = any(w in nm for w in LONG_ACTION_WORDS)
            wait = ACTION_HOLD_SEC if long_kind else ACTION_RETRY_SEC
            key = (r['words'], r['kind'], r['label'])
            if now < self.action_cd.get(key, 0.0):
                continue
            if now < self.action_hold_until:        # 救援/召回后的 15 秒静默
                return None
            # v1.12.6: 先确认游戏在前台再记冷却 —— 没按出去的那次不该白等 5 秒
            if not self.dry and not self.game_is_foreground():
                return None
            self.action_cd[key] = now + wait
            if long_kind:
                self.action_hold_until = now + ACTION_HOLD_SEC
            word = next((w for w in r['words'] if w in nm), r['words'][0])
            cx, cy = center(hit)
            extra = (' (救援/召回, 按住 %.1f 秒, 停 %.0f 秒)'
                     % (ACTION_PRESS_HOLD, ACTION_HOLD_SEC)) if long_kind else ''
            if self.dry:
                return '[DRY] 交互 「%s」@%d,%d -> %s%s' % (word, cx, cy, r['label'], extra)
            self._do_action(r, hit, hold=ACTION_PRESS_HOLD if long_kind else 0.0)
            self.action_count += 1
            return '交互 画面出现「%s」→ %s (第%d次)%s' % (
                word, r['label'], self.action_count, extra)
        return None

    # 只有「真正活着在局内」才允许做反挂机动作。用户要求: 大厅/结算页/观战/死亡都不能串进来。
    RESULT_VETO = ('返回大厅', '比赛回顾', '你的排名', '继续和此小队', '再玩一次', '再匹配',
                   '胜利王冠', '再来一局')
    # 大厅专属字样: 只要画面里出现任意一个, 就算"可能在选人/选模式", 绝不做反挂机
    LOBBY_VETO = ('开始', '准备开始', '零建造', '装备一个精灵', '商城', '仓库', '通行证',
                  '生涯', '赛事', '任务', '宝库')

    def alive_for_fidget(self, lines, img, state):
        """反挂机的唯一开关: 返回 (能不能动, 原因)。

        互不串通的 5 道门:
          1) 画面必须是 INGAME(大厅/结算/匹配/巴士/跳伞/菜单/弹窗/休眠 一律不做)
          2) 画面里不能有观战字样(正在观战/上一个队友/等待重启...) -> 那说明我死了在看队友
          3) 画面里不能有结算页字样(返回大厅/比赛回顾/你的排名...) -> 那说明这局结束了
          4) 只有 self.alive 为真才算活着(左下角自己名字是白的; 红/灰=死)
          5) 游戏必须在前台
        """
        if not self.fidget:
            return False, '反挂机开关关闭'
        if state != 'INGAME':
            return False, '不在局内(%s)' % state
        if find_line(lines, *SPECTATE_KWS):
            return False, '画面有观战字样(我死了在看队友)'
        if find_line(lines, *self.LOBBY_VETO):
            return False, '画面有大厅字样(在选人/选模式)'
        if find_line(lines, *self.RESULT_VETO):
            return False, '画面有结算页字样(这局结束了)'
        if self.spectating:
            return False, '正在观战(已判定死亡)'
        if not self.alive:
            # v1.11.1: 名字可能被水面/遮挡/UI 缩放挡掉而读不到。连续 name_miss 轮
            # 都认不到名字时, 靠"方位条/HUD 已确认是局内"放宽, 否则永远不动。
            if self.name_miss < 5:
                return False, '左下角自己名字不是白色(死了/不在局内)'
            if not self.name_relax_warned:
                self.name_relax_warned = True
                log('    WARN 连续 %d 轮没认到自己的名字 -> 已确认在局内, 仍照常做反挂机(请按 4 检查游戏ID)' % self.name_miss)
        if self.ingame_streak < 2:
            return False, '还没连续两轮确认是局内'
        if not self.game_is_foreground():
            return False, '游戏不在前台'
        return True, '活着且在局内'

    def maybe_fidget(self, lines, state, img=None):
        """局内每约 20 秒做一串动作(走/转视角/左键/表情), 免得被判定挂机。
        只在「真正活着且在局内」时做; 绝不点任何按钮。"""
        if time.time() < self.action_hold_until:
            return None                    # v1.12.4: 救援/召回后 15 秒让路
        ok, why = self.alive_for_fidget(lines, img, state)
        if not ok:
            if self._last_fidget_skip != why and state == 'INGAME':
                self._last_fidget_skip = why
                log('    反挂机跳过: %s' % why)
            return None
        self._last_fidget_skip = None
        if time.time() < self.next_fidget:
            return None
        # v1.11.1: 动作串里出任何异常都要留下痕迹(以前 NameError 被静默吞掉,
        # 表现就是"一直不往队友走"), 绝不能让它悄悄停掉反挂机
        try:
            return self.run_fidget_series()
        except Exception as _e:
            import traceback as _tb
            log('    WARN 反挂机动作出错(%s): %s' % (type(_e).__name__, _e))
            for _ln in _tb.format_exc().strip().splitlines()[-6:]:
                log('      ' + _ln[:160])
            return None

    def _fidget_mouse(self, r):
        """鼠标动作: 长按(hold>0) 或 连点(count 次, 每次隔 gap 秒)"""
        down = 0x0002 if r['button'] == 'left' else 0x0008
        up = 0x0004 if r['button'] == 'left' else 0x0010
        if r['hold'] > 0:
            user32.mouse_event(down, 0, 0, 0, None)
            try:
                return self._nap(r['hold'])      # v1.12.6: 按 F9 停止时返回 False
            finally:
                user32.mouse_event(up, 0, 0, 0, None)
        for i in range(max(1, r['count'])):
            if self.stop_requested:
                return False
            user32.mouse_event(down, 0, 0, 0, None)
            user32.mouse_event(up, 0, 0, 0, None)
            if i + 1 < r['count'] and r['gap'] > 0:
                if not self._nap(r['gap']):
                    return False
        return True

    def _fidget_do(self, r):
        """执行一条自定义挂机动作"""
        if r['kind'] == 'mouse':
            return self._fidget_mouse(r)
        if r['kind'] == 'combo':
            return self._send_combo(r['vks'])
        vk = r['vk']
        if time.time() < self.space_block_until and (vk == VK_SPACE or VK_SPACE in (r.get('vks') or ())):
            return False          # v1.12.10: 画面有「空格」字样, 含空格的自定义动作先不做
        if r['hold'] > 0:
            key_down_scan(vk)
            try:
                return self._nap(r['hold'])      # v1.12.6: 按 F9 停止时返回 False
            finally:
                key_up_scan(vk)
        for i in range(max(1, r['count'])):
            if self.stop_requested:
                return False
            press_scan(vk)
            if i + 1 < r['count'] and r['gap'] > 0:
                if not self._nap(r['gap']):
                    return False
        return True

    def maybe_custom_fidget(self, lines, state, img=None):
        """v1.12.5: 用户自定义挂机动作, 每条各自按「间隔」计时(不等 15 秒那一串)"""
        if time.time() < self.action_hold_until:
            return None                    # 救援/召回后的静默期
        rules = load_fidget_rules()
        if not rules:
            return None
        ok, _why = self.alive_for_fidget(lines, img, state)
        if not ok:
            return None
        now = time.time()
        # v1.12.6: 冷却键不再带 interval(改间隔不该立刻执行一次); 顺手清掉已删动作的旧计时
        keep = {(r['name'], r['label']) for r in rules}
        self.custom_cd = {k: v for k, v in self.custom_cd.items() if k in keep}
        for r in rules:
            key = (r['name'], r['label'])
            if now < self.custom_cd.get(key, 0.0):
                continue
            self.custom_cd[key] = now + r['interval']
            if self.dry:
                return '[DRY] 挂机动作 「%s」→ %s (每 %.0f 秒)' % (r['name'], r['label'], r['interval'])
            if not self.game_is_foreground():
                return None
            self.fidget_scan = True        # 做自定义动作时也能被交互关键词打断
            self._interrupt_fidget = False
            done = True
            try:
                done = bool(self._fidget_do(r))
            except _FidgetInterrupt:
                return '挂机动作 「%s」被交互键打断' % r['name']
            except Exception as e:
                log('    WARN 自定义挂机动作出错(%s): %r' % (r['name'], e))
                return None
            finally:
                self.fidget_scan = False
                self._interrupt_fidget = False
            if not done:
                return '挂机动作 「%s」→ %s 没做完(按了 F9, 或画面有「空格」字样)' % (r['name'], r['label'])
            return '挂机动作 「%s」→ %s (每 %.0f 秒)' % (r['name'], r['label'], r['interval'])
        return None

    def maybe_shot(self, img, state):
        if not self.save_shots:
            return
        path = os.path.join(SHOT_DIR, '%s_%s.png' % (datetime.now().strftime('%H%M%S'), state))
        try:
            img.save(path)
            log('SHOT %s' % path)
            # 只保留最近 300 张, 免得长期挂在后台把磁盘写满
            shots = [os.path.join(SHOT_DIR, f) for f in os.listdir(SHOT_DIR)
                     if f.endswith('.png') and not f.startswith('mock')]
            if len(shots) > 300:
                shots.sort(key=lambda p: os.path.getmtime(p))
                for old in shots[:len(shots) - 300]:
                    try:
                        os.remove(old)
                    except Exception:
                        pass
        except Exception as e:
            log('SHOT_FAIL %s' % e)

    def cycle(self, index=0):
        # 结算后行为允许中途改: 每轮重新读一次 设置.txt, 在启动器按 [15] 改完不用重启
        global AFTER_MATCH
        try:
            AFTER_MATCH = load_after_match()
        except Exception:
            pass
        # ── 堡垒之夜没开 / 被关掉: 明确报告, 不截图不点击
        if not game_window_alive(self.hwnd):
            self.hwnd = find_game_window()
        if not self.hwnd:
            # 窗口没找到: 再看进程 —— 游戏开着但窗口可能最小化/还没加载完/标题被改
            pids = game_process_pids()
            if pids:
                if self.last_state != 'NOWIN':
                    log('#%d state=NOWIN ev=堡垒之夜进程在运行(pid=%s), 但没找到它的窗口(可能最小化了), 会自动重试'
                        % (index, ','.join(str(x) for x in sorted(pids))[:40]))
                self.last_state = 'NOWIN'
                self.paused_logged = 0.0
                return 'NOWIN'
            if self.last_state != 'NOGAME':
                log('#%d state=NOGAME ev=没找到堡垒之夜窗口, 也没找到进程(游戏没开?) 打开游戏后会自动继续' % index)
            self.last_state = 'NOGAME'
            self.paused_logged = 0.0
            return 'NOGAME'
        if self.require_foreground and not self.game_is_foreground():
            now_p = time.time()
            if now_p - self.paused_logged > 15:   # 每 15 秒提醒一次, 并写清是谁抢了前台
                self.paused_logged = now_p
                log('#%d PAUSED 游戏不在前台(前台窗口: %s) —— 切回游戏会自动继续'
                    % (index, (foreground_title() or '未知')[:40]))
            return 'PAUSED'
        self.paused_logged = 0.0
        img = grab()
        lines = ocr_lines(img)
        state, ev, target = classify(lines, img)
        # v1.12.10: 画面出现「空格」字样(跳出/跳伞/上升等提示) -> 挂机动作里带空格的先不做,
        # 免得抢在游戏提示前面按空格把跳伞/交互搞乱
        if find_line(lines, *SPACE_WORDS):
            if not self.space_block_logged:
                self.space_block_logged = True
                log('    画面出现「空格」字样 -> 挂机动作里带空格的(跳/按空格)先不做')
            self.space_block_until = time.time() + SPACE_BLOCK_SEC
        elif time.time() >= self.space_block_until:
            self.space_block_logged = False
        extra = ''
        if self.last_state == 'MATCHING' and state == 'LOBBY':
            # 匹配被取消(用户手动点了「取消」) → 别跟用户抢, 先安静 6 秒再点
            self.last_click = time.time()
            self.lobby_since = time.time()
            # 用户手动取消匹配后, 别马上又点: 让「开始」冷却只剩 6 秒
            # (而不是立刻点, 也不是硬等满 lobby_click_cd 那么久)
            self._cd_lobby = time.time()   # 从此刻重新算 3 秒冷却, 不让用户一取消就被立刻抢点
            log('检测到匹配被取消(回到大厅), 先等 %.0f 秒再重新点「开始」' % MAX_CLICK_CD)
        if state == 'LOBBY':          # 回到大厅, 本局的「退过一次」标记清掉
            self.leave_done_match = False
            self.plate_seen.clear()
            self.squad_panel = None
            now_l = time.time()
            if not self.lobby_since or state != self.last_state:
                self.lobby_since = now_l
            elif (now_l - self.lobby_since > 25) and (now_l - self.last_click > 25) \
                    and not self.force_click:
                self.force_click = True
                log('WARN 在大厅停留超过 25 秒还没点成功(可能被窗口挡住/OCR 没认到), 本轮强制点一次')
        else:
            self.lobby_since = 0.0
        # v1.9.6: 新一局(回大厅/上巴士)时把俯仰记忆清零, 否则会带着上一局的仰角继续看天
        if state in ('LOBBY', 'BUS'):
            self.view_pitch_bias = 0.0
            self.aim_pitch_total = 0.0
            self.squad_seen_max = 0
        # 完成一局 = 从"结算页"回到大厅/下一局(不管是谁点的「返回大厅」)
        # 注意: 这段要在下面 LOBBY 重置 match_counted 之前, 且只认 RESULTS -> LOBBY 这一次跳变
        if self.last_state in ('RESULTS', 'RESULTS?') and state in ('LOBBY', 'MATCHING', 'LOADING'):
            self.record_match('返回大厅')
        # v1.8.4: 记录这一帧看得见的队友名字牌(用来判断"队友里还有没有活人")
        if state in ('INGAME', 'SKYDIVE', 'DEAD'):
            self.note_plates(lines)
        # v1.8.3: 先把"我还活着吗"更新掉, 免得队伍人数检查用的是上一轮的旧值
        # v1.10.8: 优先看"左下角自己那行名字的颜色"(白=活, 红/灰=死了在观战)
        if state in ('INGAME', 'SKYDIVE'):
            _selfst = None
            try:
                _selfst = self.self_name_state(lines, img)
            except Exception as e:
                log('    WARN 判断"我是否活着"出错: %s' % e)
            if _selfst == 'dead':
                if self.alive:
                    log('    自己在队伍栏的名字变红/变灰 -> 我已经死了, 进入观战')
                self.alive = False
                self.spectating = True
            elif _selfst == 'alive':
                self.alive = True
                self.spectating = False
            else:
                self.alive = self_name_visible(lines, img) is not None
        elif state == 'DEAD':
            self.alive = False           # 观战/被淘汰时明确置 False
        sq = self.maybe_check_squad(lines, state, img)
        if sq:
            log('    ' + sq)
        if state == 'BUS':
            secs = bus_seconds(lines)
            extra = ' countdown=%s' % secs
            now = time.time()
            if self.bus_first_seen is None:
                self.bus_first_seen = now
            elapsed = now - self.bus_first_seen
            extra += ' bus_elapsed=%.1fs' % elapsed
            if secs is not None and self.first_secs is None:
                self.first_secs = secs
            # 合理性: 刚上巴士时倒计时应在 20~40 秒. 首次读到个位数多半是 OCR 噪声,
            # 这时不走"倒计时<=jump_at"的路径, 改用纯时间兜底
            reliable = secs is not None and (self.first_secs or 0) >= 8
            due = (reliable and secs <= self.jump_at) or (not reliable and elapsed >= 18)
            can_press = self.jump_attempts < 3 and (now - self.jump_sent_at) >= 2.5
            if not self.jumped and due and can_press and elapsed >= 2.0:
                if self.dry:
                    extra += ' [DRY] 该按空格跳伞'
                else:
                    press_scan(VK_SPACE)
                    self.jump_attempts += 1
                    self.jump_sent_at = now
                    extra += ' ACTION jump=SPACE 第%d次(待确认)' % self.jump_attempts
        if state == 'SKYDIVE' and not self.jumped:
            self.jumped = True             # 以"画面真的变成跳伞"为准, 不再只信按键
        if state == 'DEAD':
            self.spectating = True         # 之后一律等队友打完, 不许提前退
        elif state in ('RESULTS', 'RESULTS?', 'LOBBY', 'MATCHING'):
            self.spectating = False
        if state in ('LOBBY', 'MATCHING'):
            # 只有回到大厅/重新匹配才算新的一局, 重置跳伞标记
            self.teammates = []              # 上一局的队友名单作废
            self.follow_name = None
            self.follow_miss = 0
            self.squad_boxes = []            # 上一局的队伍栏位置也作废
            self.squad_logged = False
            self.self_box = None
            self.bus_first_seen = None
            self.jumped = False
            self.first_secs = None
            self.jump_attempts = 0
            self.jump_sent_at = 0.0
            self.match_counted = False       # 新一局可以再计一次
            self.start_attempts = 0          # 新一局重新数"认不出开始"的次数
        elif state != 'BUS':
            self.bus_first_seen = None

        from PIL import Image as _I  # noqa: F401
        if state == 'INGAME':
            self.ingame_streak += 1
            _selfst = self.self_name_state(lines, img)
            if _selfst == 'dead':
                if self.alive:
                    log('    自己在队伍栏的名字变红/变灰 -> 我已经死了, 进入观战')
                self.alive = False
                self.spectating = True
            elif _selfst == 'alive':
                self.alive = True
                self.spectating = False
            else:
                self.alive = self_name_visible(lines, img) is not None
            if self.alive:
                self.name_miss = 0
            else:
                self.name_miss += 1
                if self.name_miss == 5 and not self.name_warned:
                    self.name_warned = True
                    log('WARN 连续5轮局内都没看到名字(%s), 请按 4 检查游戏ID; 也可开宽松模式' % PLAYER_NAME)
            # v1.12.9: 名字监测 —— 连续 NAME_WATCH_SEC 秒没在左下角认到自己的名字, 就退回大厅重排
            _now = time.time()
            _name_hit = self_name_visible(lines, img)
            if self.ingame_streak == 2:
                self.ingame_since = _now          # 双重确认进局内: 从这一刻起算 60 秒
            if _name_hit is not None or _selfst == 'alive':
                self.name_seen_at = _now
                self.name_watch_warned = False
            elif (NAME_WATCH_SEC > 0 and self.ingame_since
                  and not self.spectating
                  and not find_line(lines, *SPECTATE_KWS)
                  and not find_line(lines, *self.RESULT_VETO)
                  and not find_line(lines, *self.LOBBY_VETO)
                  and _now - max(self.ingame_since, self.name_seen_at) >= NAME_WATCH_SEC
                  and _now - self.name_leave_at >= NAME_WATCH_SEC):
                log('WARN 进局内后连续 %.0f 秒没在左下角看到自己的名字(%s) -> 自动返回大厅重新匹配'
                    % (_now - max(self.ingame_since, self.name_seen_at), PLAYER_NAME))
                self.name_leave_at = _now
                _leave = self.leave_bad_squad(lines, reason='名字监测: 左下角一直没出现自己的名字, 退回大厅重排')
                if _leave:
                    log('    ' + str(_leave))
            elif (NAME_WATCH_SEC > 0 and self.ingame_since and not self.name_watch_warned
                  and _now - max(self.ingame_since, self.name_seen_at) >= NAME_WATCH_SEC * 0.5):
                self.name_watch_warned = True
                log('WARN 已 %.0f 秒没看到自己的名字(%s), 到 %.0f 秒还没看到就自动返回大厅'
                    % (_now - max(self.ingame_since, self.name_seen_at), PLAYER_NAME, NAME_WATCH_SEC))
        else:
            self.ingame_streak = 0
            self.alive = False
            self.ingame_since = 0.0           # v1.12.9: 离开局内就重置名字监测
            self.name_seen_at = 0.0
            self.name_watch_warned = False
        if state == 'UNKNOWN':
            self.unknown_cycles += 1
        else:
            self.unknown_cycles = 0
        if state == 'RESULTS?':
            self.resultsq_cycles += 1
        else:
            self.resultsq_cycles = 0
        changed = state != self.last_state
        if changed:                        # 换了界面: 重置卡住检测计数
            self.results_clicks = 0
            self.modal_clicks = 0
        log('#%d state=%s ev=%s%s' % (index, state, ev, extra))
        if changed:
            self.maybe_shot(img, state)

        if state == 'INGAME':
            try:
                self.learn_teammates(lines)
                if self.squad_boxes and not self.squad_logged:
                    self.squad_logged = True
                    log('    已记住 %d 个队友名字在队伍栏的位置(自动朝队友时这些位置不算方向): %s'
                        % (len(self.squad_boxes),
                           ', '.join('%s@(%.0f%%,%.0f%%)' % (b['name'][:6], b['x0'] * 100, b['y0'] * 100)
                                     for b in self.squad_boxes[:4])))
            except Exception:
                pass
        # v1.12.4: 交互关键词优先 —— 先看有没有「打开/搜索/上升/救援/召回」等字眼,
        # 有就立刻按交互键, 再轮别的动作; 救援/召回后 15 秒内不抢任何动作
        act_hit = self.check_actions(lines, state)
        if act_hit:
            log('    ' + act_hit)
        if time.time() < self.action_hold_until and state == 'INGAME':
            # 救援/召回后的 15 秒静默: 局内防挂机/自定义动作全部让路。
            # v1.12.6: 只拦局内 —— 大厅/结算/弹窗该点的还要点, 别被静默拖 15 秒
            self.last_state = state
            return state
        act = self.act(state, target, lines, img)
        if act:
            log('    ACTION %s' % act)
        fid = self.maybe_fidget(lines, state, img)
        if fid:
            log('    FIDGET %s' % fid)
        cf = self.maybe_custom_fidget(lines, state, img)
        if cf:
            log('    FIDGET %s' % cf)
        self.last_state = state
        return state

    def ensure_game_front(self):
        """点之前把游戏切到前台(只在要点的时候切一次, 不长期抢焦点)"""
        hwnd = self.hwnd or find_game_window()
        if not hwnd:
            return False
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
                self._nap(0.5)
                log('游戏窗口是最小化状态, 已还原')
        except Exception:
            pass
        try:
            fg = user32.GetForegroundWindow()
        except Exception:
            fg = 0
        if fg == hwnd:
            return True
        ok = focus_game(hwnd)
        self._nap(0.5)
        log('点击前把游戏切到前台: %s' % ('成功' if ok else '失败(可能被其它窗口挡住)'))
        return ok

    def click_start_verified(self, x, y, attempts=3):
        """点完 0.7 秒回来看一眼: 还在大厅就说明没点到, 重试(默认最多 3 次, 联动切前台)

        attempts=1 时只点一下、只校验一次 —— 大厅现在用这个模式:
        点一次 → 没生效就走 3 秒冷却 → 3 秒后再点一次(用户要求的"每 3 秒点一次")。"""
        for attempt in range(1, max(1, attempts) + 1):
            self.ensure_game_front()
            click(x, y)
            self._nap(0.7)
            try:
                img2 = grab()
                lines2 = ocr_lines(img2)
            except Exception:
                return attempt
            cancel = find_line(lines2, '取消')
            start_btn = find_line(lines2, '开始', ymin=1100, xmax=1000)
            if cancel or start_btn is None:
                return attempt
            log('WARN 点了「开始」但画面还在大厅(第%d次), 可能被别的窗口挡住, 重试' % attempt)
            y += 7 * attempt
        return 0

    # --- v1.10.0: 每个动作独立冷却(以前共用一个 last_click, 点完跳过/领取会让"开始"白等 6 秒) ---
    def cd_ok(self, key, seconds, ignore=False):
        if ignore:
            return True
        return (time.time() - getattr(self, '_cd_' + key, 0.0)) >= seconds

    def cd_left(self, key, seconds):
        return max(0.0, seconds - (time.time() - getattr(self, '_cd_' + key, 0.0)))

    def cd_mark(self, key):
        setattr(self, '_cd_' + key, time.time())
        self.last_click = time.time()

    def act(self, state, target, lines, img):
        now = time.time()
        if state not in ('LOBBY', 'RESULTS', 'RESULTS?', 'ENDMENU', 'MENU', 'MODAL', 'UNKNOWN', 'SLEEP', 'SURVEY'):
            return None
        if not self.game_is_foreground():
            return '跳过(游戏不在前台)'

        # ── 休眠模式: 点「继续游戏」把游戏唤醒
        if state == 'SLEEP':
            if target is None:
                return '休眠弹窗没找到继续游戏'
            if now - self.last_click < 3:
                return '冷却中'
            x, y = center(target)
            if self.dry:
                return '[DRY] 点继续游戏 (%d,%d)' % (x, y)
            click(x, y)
            self.last_click = now
            return 'click_resume(%d,%d)' % (x, y)

        # ── 弹窗: 先点"确定/领取", 否则后面的"返回大厅"点不到
        if state == 'MODAL':
            if target is None:
                return '弹窗没找到确认按钮'
            if now - self.last_click < 2:
                return '冷却中'
            x, y = center(target)
            if self.dry:
                return '[DRY] 点弹窗确认 %s (%d,%d)' % (target['text'], x, y)
            click(x, y)
            self.last_click = now
            self.modal_clicks += 1
            self.log_target = '「%s」@%d,%d-%d,%d' % (
                target['text'], target['x'], target['y'],
                target['x'] + target['w'], target['y'] + target['h'])
            return 'click_modal(%s @%d,%d 矩形 %s) 第%d次' % (target['text'], x, y, self.modal_clicks)

        # ── 问卷/过场: 点「跳过」, 点完问卷就消失了
        if state == 'SURVEY':
            if target is None:
                return '提到跳过但没找到按钮位置'
            if now - self.last_click < 2:
                return '冷却中'
            x, y = center(target)
            if self.dry:
                return '[DRY] 点跳过 %s (%d,%d)' % (target['text'], x, y)
            click(x, y)
            self.last_click = now
            return 'click_skip(%s @%d,%d)' % (target['text'], x, y)

        # ── 用户自定义点击词: 出现在界面上就点它(仅限非对局画面, 免得在局内乱开枪)
        SAFE_USER_STATES = ('LOBBY', 'RESULTS', 'RESULTS?', 'MENU', 'MODAL',
                            'UNKNOWN', 'WAITING', 'BUS', 'SKYDIVE', 'LOADING')
        if state in SAFE_USER_STATES:
            uw = find_user_word(lines)
            if uw is not None and self.cd_ok('user_word', 1.5):
                ux, uy = center(uw)
                if not self.dry:
                    click(ux, uy)
                    self.cd_mark('user_word')
                self.log_target = '「%s」@%d,%d-%d,%d' % (
                    uw['text'], uw['x'], uw['y'], uw['x'] + uw['w'], uw['y'] + uw['h'])
                return ('%s 点自定义词 %s (%d,%d)' % ('[DRY]' if self.dry else 'click_user', uw['text'], ux, uy))

        # ── 大厅: 点识！别！到！的！那个「开始」(文字在哪就点哪, 位置不写死)
        if state == 'LOBBY':
            _lobby_cd = min(MAX_CLICK_CD, self.lobby_click_cd)
            if not self.cd_ok('lobby', _lobby_cd, ignore=self.force_click):
                return '等待点「开始」(冷却还剩 %.1f 秒)' % self.cd_left('lobby', _lobby_cd)
            if target is not None:
                x, y = center(target)                      # ← OCR 框中心
            else:
                # 连续 6 轮(~18 秒)都认不出「开始」才兜底点一次, 并在日志里说清楚
                if self.start_attempts < 2:
                    self.start_attempts += 1
                    return ('未识别到「开始」, 本轮不盲点(第%d次); 等下一轮重新识别'
                            % self.start_attempts)
                # v1.9.2 兜底: 先用画面里「零建造/小队/填充」「BATTLE ROYALE」
                # 「装备一个精灵」这些字当锚点推出按钮位置(不写死比例);
                # 连锚点都没有, 才用最后的老比例
                anchor_c = self._lobby_start_candidates(lines)
                if anchor_c:
                    # 逐个候选点试, 每个都带"点完看画面有没有变"的校验
                    for (cx, cy) in anchor_c[:4]:
                        n = self.click_start_verified(cx, cy, attempts=1)
                        if n:
                            self.force_click = False
                            self.cd_mark('lobby')
                            self.log_target = '锚点兜底点中(%d,%d)' % (cx, cy)
                            return '[DRY] 点击开始 (%d,%d) %s' % (cx, cy, self.log_target) \
                                if self.dry else '锚点兜底 点开始 (%d,%d) 第%d次生效' % (cx, cy, n)
                    x, y = int(SCREEN_W * 0.1332), int(SCREEN_H * 0.8625)
                    self.log_target = '比例兜底(锚点候选都没生效)'
                else:
                    x, y = int(SCREEN_W * 0.1332), int(SCREEN_H * 0.8625)
                    self.log_target = '比例兜底(画面里连锚点文字都没认到)'
            tgt_desc = ('「%s」@%d,%d-%d,%d' % (target['text'], target['x'], target['y'],
                                              target['x'] + target['w'], target['y'] + target['h'])
                        if target is not None else '百分比兜底(未识别到「开始」)')
            self.log_target = tgt_desc
            if self.dry:
                self.force_click = False
                return '[DRY] 点击开始 (%d,%d) %s' % (x, y, tgt_desc)
            # 看到按钮后先"反应"一会儿再点, 别像机器一样瞬间点下去
            react = random.uniform(self.react_min, self.react_max)
            log('    看到「开始」, 反应 %.1f 秒后点击' % react)
            if not self._nap(react):
                return '反应期间收到停止'
            n = self.click_start_verified(x, y, attempts=1)   # 只点一次, 失败就等 3 秒再来
            self.force_click = False
            self.cd_mark('lobby')
            self.start_attempts = 0 if n else self.start_attempts + 1
            if n == 0:
                return ('点了「开始」没生效: %.0f 秒后再点一次' % self.lobby_click_cd)
            return 'click_start(%d,%d) 第%d次生效 %s' % (x, y, n, self.log_target)

        # ── 结算页: 只做一件事——鼠标点「返回大厅」(不按任何键)
        if state in ('RESULTS', 'RESULTS?'):
            # RESULTS? 意味着少了"比赛回顾/你的排名/经验值"等标记, 可能其实是观战页;
            # 先观察 4 轮(≈8 秒), 确认它一直不动再动手, 避免把观战页当结算页提前离开
            # v1.11.3: "只有返回大厅、没有结算标记"的画面, 如果知道自己在观战, 就绝不点
            # (否则队友还活着时会被提前带回大厅)
            if state == 'RESULTS?' and self.spectating:
                return '疑似结算页, 但我正在观战 -> 不点(等小队全部结束)'
            if state == 'RESULTS?' and self.resultsq_cycles < self.resultsq_wait_rounds:
                return '疑似结算页, 先观察(%d/%d)' % (self.resultsq_cycles, self.resultsq_wait_rounds)
            # 用户要求: 所有点击冷却不超过 3 秒; RESULTS? 只靠 resultsq_cycles 多观察, 不再加长冷却
            res_cd = min(MAX_CLICK_CD, self.results_click_cd)
            if not self.cd_ok('results', res_cd):
                return '冷却中(还剩 %.0f 秒, 两次结算点击至少隔 %.0f 秒)' % (
                    self.cd_left('results', res_cd), res_cd)
            if target is not None:
                x, y = center(target)
                if not self.dry:
                    react = random.uniform(self.react_min, self.react_max)
                    log('    看到「%s」, 反应 %.1f 秒后点击' % (target['text'], react))
                    if not self._nap(react):
                        return '反应期间收到停止'
                    click(x, y)
                self.cd_mark('results')
                self.results_clicks += 1
                if self.dry:
                    return '[DRY] 点击 %s (%d,%d)' % (target['text'], x, y)
                return 'click_results(%s @%d,%d) 第%d次' % (
                    target['text'], x, y, self.results_clicks)
            # 没找到按钮文字: 什么都不做, 等下一轮重新识别(绝不按键、绝不乱点)
            return '结算页未看清按钮, 等下一轮'

        # ── 局内菜单(只可能是用户自己按 Esc 打开的): 不做任何操作
        # 菜单里也有「返回大厅」, 但这时对局还没结束, 绝不能点; 也不替用户关菜单。
        if state == 'ENDMENU':
            # 结算页被菜单/侧栏挡住: 按 ESC 关掉, 下一轮再点「返回大厅」
            if not self.cd_ok('endmenu', 2.0):
                return None
            self.cd_mark('endmenu')
            log('    结算页被菜单挡住 -> 按 ESC 关菜单')
            press_scan(VK_ESC)
            return '关掉盖在结算页上的菜单'

        if state == 'MENU':
            return '检测到局内菜单(尚未结束对局): 不操作'

        # ── 认不出来的画面: 什么都不做, 只等下一轮 (不按键、不点击)
        if state == 'UNKNOWN':
            if self.spectating:
                return None   # 观战画面文字本来就少, 认不出属正常: 等结算页
            return None
        return None

    def run(self, max_cycles=0):
        start_hotkey_thread()
        log('=== BOT START interval=%.1fs jump_at=%ss dry=%s  (F9=停止 / F10=开始) ==='
            % (self.interval, self.jump_at, self.dry))
        self.stop_requested = False
        self.index = 0
        while not self.stop_requested:
            if poll_control() == 'stop':
                self.stop_requested = True
                log('=== 收到 F9/控制文件: 停止自动打 ===')
                break
            self.index += 1
            try:
                t0 = time.time()
                st = self.cycle(self.index)
                # 需要动手的界面(弹窗/结算/大厅)用 2 秒快速复查, 免得弹窗一条条等 5 秒
                wait = min(self.interval, 2.0) if st in (
                    'MODAL', 'LOBBY', 'RESULTS', 'RESULTS?', 'MENU', 'SLEEP',
                    'SURVEY') else self.interval
                dt = time.time() - t0
                if not self._nap(max(0.0, wait - dt)):
                    break
            except KeyboardInterrupt:
                log('=== BOT STOP (KeyboardInterrupt) ===')
                break
            except Exception as e:
                log('ERROR %r' % (e,))
                if not self._nap(self.interval):
                    break
            if max_cycles and self.index >= max_cycles:
                log('=== BOT DONE max_cycles=%d ===' % max_cycles)
                break


def daemon(interval=5.0, jump_at=20, dry=False, require_fg=True, autostart=False, fidget=True):
    start_hotkey_thread()
    """常驻待命: autostart/按 F10 开始自动打, F9 停止(待命时再按 F9 退出脚本)"""
    if autostart:
        log('=== 自动挂机已启动 (F9=停止 / F10=再次开始) ===')
    else:
        log('=== 待命中: 按 F10 开始自动打; F9 停止 ===')
    _hk_prev.clear()
    idle_stop = 0.0
    pending = bool(autostart)
    while True:
        try:
            hk = poll_control()
            if hk == 'stop' and pending:
                log('=== 收到 F9: 取消本次自动挂机 ===')
                pending = False
                continue
            if pending or hk == 'start':
                log('=== 收到 F10: 开始自动打 ===' if not pending else '=== 开始自动挂机 ===')
                pending = False
                bot = Bot(interval=interval, jump_at=jump_at, dry=dry)
                bot.require_foreground = require_fg
                bot.fidget = fidget
                bot.run()
                _hk_queue.clear()            # 丢掉"停止"的尾巴事件, 免得待命状态又吃到一次
                _hk_last.clear()
                time.sleep(0.6)
                idle_stop = 0.0
                log('=== 已停止, 待命中: F10 开始 / F9 再按一次退出脚本 ===')
                continue
            if hk == 'stop':
                now2 = time.time()
                if now2 - idle_stop < 3.0:
                    log('=== 空闲时连按两次 F9: 退出脚本 ===')
                    return
                idle_stop = now2
                log('=== 现在是待命状态: 按 F10 开始自动打; 想退出脚本请再按一次 F9 ===')
            time.sleep(0.05)
        except KeyboardInterrupt:
            log('=== 脚本退出 (Ctrl+C) ===')
            return
        except Exception as e:
            log('DAEMON ERROR %r' % (e,))
            time.sleep(1.0)


def replay(paths):
    for p in paths:
        try:
            from PIL import Image
            img = Image.open(p)
            lines = ocr_lines(img)
            state, ev, target = classify(lines, img)
            extra = ''
            if state == 'BUS':
                extra = ' countdown=%s' % bus_seconds(lines)
            tgt = ''
            if target:
                tgt = ' target=%s@%s,%s' % (target['text'], *center(target))
            print('%s -> %s | %s%s%s' % (os.path.basename(p), state, ev, extra, tgt))
        except Exception as e:
            print('%s -> ERROR %r' % (p, e))


def screen_report():
    try:
        img = ImageGrab.grab(bbox=(0, 0, SCREEN_W, SCREEN_H))
        return '截图 %dx%d / 系统 %dx%d / DPI=%s' % (img.size[0], img.size[1], SCREEN_W, SCREEN_H, DPI_MODE)
    except Exception:
        return '截图失败'


def follow_test():
    """--follow-test: 只看不点, 打印"它现在看到的队友/名牌/建议转角" (局内用)"""
    hwnd = find_game_window()
    print('游戏窗口:', hwnd or '没找到')
    img = grab()
    lines = ocr_lines(img)
    state, ev, target = classify(lines, img)
    print('画面状态:', state, '|', ev)
    b = Bot(dry=True, save_shots=False)
    team = b.learn_teammates(lines)
    print('队伍栏里读到的名字:', team or '(没读到)')
    print('记住的队伍栏位置:', [(x['name'], round(x['y0'], 2)) for x in b.squad_boxes] or '(无)')
    plate = b._plate_pos(lines)
    if plate:
        name, x, y = plate
        ppd = view_px_per_deg()
        dx = x - SCREEN_W / 2.0
        deg = max(-110.0, min(110.0, 0.8 * dx / ppd))
        zone = '上方(只水平转)' if y < SCREEN_H * 0.30 else ('下方=背后(只转身不低头)' if y > SCREEN_H * 0.58 else '中间')
        print('画面里找到名牌: %s @(%d,%d) 区域=%s' % (name, x, y, zone))
        print('计算: dx=%.0fpx  ppd=%.2f  建议转 %.1f°' % (dx, ppd, deg))
    else:
        print('画面里没找到队友名牌(队友不在视野/被遮挡/名字认不出)')
    print('OCR 前 12 行:', [(l.get('text') or '')[:14] for l in lines[:12]])


def main():
    log('屏幕: %s' % screen_report())
    global AFTER_MATCH
    AFTER_MATCH = load_after_match()
    log('结算后行为: %s' % ('再玩一局(点「再玩一次」)' if AFTER_MATCH == 'again'
                          else '返回大厅'))
    log('名字监测: 局内连续 %.0f 秒看不到自己的名字 -> 自动返回大厅重新匹配' % NAME_WATCH_SEC)
    log('空格保护: 画面出现「空格」字样时, 挂机动作里带空格的(跳/按空格)一律不做')
    try:
        _rules = load_action_rules()
        log('交互关键词: %s | 交互键=%s' % (
            '; '.join('%s→%s' % ('/'.join(r['words']), r['label']) for r in _rules),
            vk_label(key_vk('交互'))))
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay', nargs='*')
    ap.add_argument('--loop', action='store_true', help='立即开始循环(不等 F10)')
    ap.add_argument('--daemon', action='store_true', help='待命: F10 开始自动打, F9 停止/退出')
    ap.add_argument('--autostart', action='store_true', help='配合 --daemon: 启动后立刻开始打')
    ap.add_argument('--hotkeys', type=int, default=0,
                    help='诊断: 监听 N 秒, 按 F9/F10 看能不能被识别')
    ap.add_argument('--follow-test', dest='follow_test', action='store_true',
                    help='诊断: 打印它看到的队友名字/名牌位置/建议转角(不做任何操作)')
    ap.add_argument('--press', default=None, help='测试按键: 例如 --press 跳跃')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--interval', type=float, default=3.0)
    ap.add_argument('--jump-at', type=int, default=20)
    ap.add_argument('--cycles', type=int, default=0)
    ap.add_argument('--force-focus', action='store_true',
                    help='游戏不在前台也强行切前台(旧行为, 会抢焦点)')
    ap.add_argument('--no-fidget', action='store_true',
                    help='关闭反挂机小动作(默认开着: 局内随机走动/转视角/左键/B表情)')
    ap.add_argument('--player-name', default=None,
                    help='自己的游戏ID(默认 YOUR_GAME_NAME), 用于判断左下角名字是否出现')
    ap.add_argument('--lax-name', action='store_true',
                    help='宽松模式: 左下角认不到自己的名字时也继续做反挂机动作')
    ap.add_argument('--install-ocr', action='store_true',
                    help='尝试安装 Windows 中文 OCR 语言功能(会弹 UAC)')
    ap.add_argument('--calibrate', action='store_true',
                    help='局内测一次视角灵敏度(每度多少像素)并写入 设置.txt')
    ap.add_argument('--check-ocr', action='store_true',
                    help='只打印本机可用的 Windows OCR 语言, 然后退出')
    ap.add_argument('--focus', action='store_true',
                    help='只把堡垒之夜窗口切到前台一次(给启动器用), 然后退出')
    a = ap.parse_args()
    load_px_per_deg()
    load_counts_per_deg()
    load_key_bindings()          # 设置.txt 里的按键绑定
    if a.hotkeys:
        print('监听 F9/F10 %d 秒, 请按一下 F9 或 F10 ...' % a.hotkeys)
        end = time.time() + a.hotkeys
        _hk_prev.clear()
        while time.time() < end:
            hk = poll_hotkeys()
            if hk:
                print('  %s  识别到 %s' % (time.strftime('%H:%M:%S'), 'F10(开始)' if hk == 'start' else 'F9(停止)'))
            time.sleep(0.05)
        print('监听结束。如果一个都没识别到, 说明键盘事件被别的程序拦截了(比如以管理员运行的视频工具/宏软件)。')
        return 0
    if getattr(a, 'follow_test', False):
        follow_test()
        return 0
    if a.press:
        vk = key_vk(a.press)
        if vk is None:
            print('不认识的按键: %s (可用: 下蹲/跳跃/奔跑/稿子, 或 VK 名字)' % a.press)
            return 2
        ok = press_scan(vk)
        ev = '按了 %s (vk=0x%02X)' % (a.press, vk)
        print(('PRESS ' + ev) if ok else ('PRESS FAILED ' + ev))
        return 0 if ok else 1
    if a.calibrate:
        global PX_PER_DEG, COUNTS_PER_DEG
        img = grab()
        lines = ocr_lines(img)
        cls = classify(lines, img)
        st = cls[0] if isinstance(cls, tuple) else cls
        print('当前画面: ' + st)
        # 多帧取中位数(每帧轻微转一点, 让刻度落到不同位置, 结果更稳)
        samples = []
        for k in range(3):
            if k:
                mouse_look_smooth(140, 0, dur=0.18)
                time.sleep(0.25)
            img = grab()
            v1, n1 = px_per_deg(img)
            if v1:
                samples.append(v1)
        v = sorted(samples)[len(samples)//2] if samples else None
        n = len(samples)
        if not v:
            print('没测到: 需要在「局内、角色活着」时测(顶部有罗盘刻度)')
            return 2
        print('校准结果: %.2f 像素/度  (%.4f 度/像素, 共 %d 个刻度样本)' % (v, 1.0 / v, n))
        print('  转一整圈(360°) 在屏幕上占 %.0f 像素' % (v * 360))
        save_calib(v)
        PX_PER_DEG = v

        # 再测「每度多少鼠标计数」: 故意转 40 度左右, 看罗盘刻度挪了多少像素
        cpd = None
        try:
            img_a = grab()
            lb0 = compass_labels(img_a)
            h0 = compass_heading(lb0, v, img_a.size[0] / 2.0)
            dx = int(round(40.0 * COUNTS_PER_DEG))
            if h0 is not None and dx >= 100:
                mouse_look_smooth(dx, 0.0, dur=0.3)
                time.sleep(0.5)
                img_b = grab()
                lb1 = compass_labels(img_b)
                h1 = compass_heading(lb1, v, img_b.size[0] / 2.0)
                if h1 is not None:
                    d = abs(angdiff(h1, h0))
                    if 3.0 <= d <= 180.0:
                        cpd = abs(dx) / d
                        print('  朝向 %.1f° -> %.1f° (转了 %.1f°), 反推 %s 计数/度'
                              % (h0, h1, d, '%.2f' % cpd if 2.0 <= cpd <= 200.0 else '异常'))
                        if not (2.0 <= cpd <= 200.0):
                            cpd = None
                else:
                    print('  转动后没读到罗盘朝向, 跳过计数校准')
            else:
                print('  没读到罗盘朝向, 跳过计数校准')
        except Exception as e:
            print('  计数校准出错: %r' % (e,))

        if cpd:
            COUNTS_PER_DEG = cpd
            save_counts_calib(cpd)
            print('已写入 设置.txt: px_per_deg=%.4f  counts_per_deg=%.4f' % (v, cpd))
        else:
            print('已写入 设置.txt: px_per_deg=%.4f (计数用默认 %.2f)' % (v, COUNTS_PER_DEG))
        print('  反挂机每串转: 第一段 100~200°, 第二段 150~300°')
        print('  速度: %.0f~%.0f 计数/秒 ≈ %.0f~%.0f 度/秒'
              % (2600, 4200, 2600 / COUNTS_PER_DEG, 4200 / COUNTS_PER_DEG))
        return 0
    if a.install_ocr:
        langs = available_ocr_langs()
        zh = [x for x in langs if x.startswith('zh')]
        if zh:
            print('本机已有中文OCR: ' + ','.join(zh) + ' —— 不用装')
            return 0
        ok, msg = install_ocr(dry=a.dry)
        print(('安装成功: ' if ok else '未完成: ') + msg)
        return 0 if ok else 1
    if a.check_ocr:
        langs = available_ocr_langs()
        print('OCR 语言: ' + (', '.join(langs) if langs else '(调用失败)'))
        print('将使用: ' + (pick_ocr_lang(langs) or '(无)'))
        zh = [x for x in langs if x.startswith('zh')]
        print('中文可用: ' + ('是' if zh else '否'))
        # 下面三行是给启动器解析的 ASCII 标记(不依赖控制台编码)
        print('CHINESE_OCR=%d' % (1 if zh else 0))
        print('ZH_LANGS=' + (','.join(zh) or '-'))
        print('ALL_LANGS=' + (','.join(langs) or '-'))
        return 0

    require_fg = not a.force_focus

    global PLAYER_NAME, LAX_NAME
    LAX_NAME = bool(a.lax_name)
    if not a.player_name:                      # 没在命令行指定就看 玩家名字.txt
        try:
            nf = data_file('玩家名字.txt')
            if os.path.exists(nf):
                with open(nf, encoding='utf-8-sig') as fh:
                    first = fh.readline().strip()
                if first:
                    a.player_name = first
        except Exception:
            pass
    if a.player_name:
        PLAYER_NAME = a.player_name
        print('PLAYER_NAME ->', PLAYER_NAME)

    if a.focus:
        hwnd = find_game_window()
        ok = focus_game(hwnd)
        print('FOCUS', 'OK' if ok else 'FAIL', hwnd)
        sys.exit(0 if ok else 2)
    if a.replay is not None:
        replay(a.replay)
        return
    if a.daemon:
        daemon(interval=a.interval, jump_at=a.jump_at, dry=a.dry, require_fg=require_fg,
               autostart=a.autostart, fidget=not a.no_fidget)
        return
    bot = Bot(interval=a.interval, jump_at=a.jump_at, dry=a.dry)
    bot.require_foreground = require_fg
    bot.fidget = not a.no_fidget
    if a.once:
        bot.focus()
        st = bot.cycle(0)
        print('STATE', st)
        return
    bot.focus()
    bot.run(a.cycles)


if __name__ == '__main__':
    main()
