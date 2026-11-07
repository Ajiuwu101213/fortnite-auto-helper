#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""堡垒之夜 · 自动挂机助手  ——  控制台界面
作者 QQ: 2395019665        （有 bug / 报错请加这个 QQ 反馈）
"""
import ctypes
import io
import os
import re
import subprocess

try:
    import splash_art as _art
except Exception:      # 艺术文件缺失也不影响主功能
    _art = None

try:
    import splash_braille as _braille      # 盲文点阵大图(优先用)
except Exception:
    _braille = None
import sys
import shutil
import time
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    r"""设置类文件放 %LOCALAPPDATA%\FortniteAutoBot —— 换新版本/重新解压也不会被覆盖"""
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        d = os.path.join(base, 'FortniteAutoBot')
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return BASE


DATA_DIR = _data_dir()


def data_file(name):
    new = os.path.join(DATA_DIR, name)
    old = os.path.join(BASE, name)
    if not os.path.isfile(new) and os.path.isfile(old):
        try:
            import shutil
            shutil.copyfile(old, new)
        except Exception:
            return old
    return new
BOT = os.path.join(BASE, 'fortnite_bot.py')
LOG = os.path.join(BASE, 'bot_log.txt')
CONSOLE_LOG = os.path.join(BASE, 'bot_console.log')
NAME_FILE = data_file('玩家名字.txt')
CONF_FILE = data_file('设置.txt')
DEFAULT_NAME = 'YOUR_GAME_NAME'

AUTHOR_NAME = '阿舸AG'
AUTHOR_QQ = '2395019665'
VERSION = 'v1.12.10'
REPO_URL = 'https://github.com/Ajiuwu101213/fortnite-auto-helper'
try:
    import updater
except Exception:
    updater = None   # 项目主页(更新地址)
W = 64          # 界面宽度(显示字符数)

STATE_CN = {
    'LOBBY': ('大厅', '准备点开始'),
    'MATCHING': ('匹配中', '搜索服务器'),
    'LOADING': ('加载中', '连接中/大逃杀'),
    'WAITING': ('等待区', '等玩家入齐'),
    'BUS': ('战斗巴士', '读倒计时准备跳伞'),
    'SKYDIVE': ('跳伞/滑翔', '已跳伞'),
    'INGAME': ('战斗', '原地待机等死'),
    'DEAD': ('已阵亡', '观战队友'),
    'RESULTS': ('结算页', '点返回大厅'),
    'RESULTS?': ('疑似结算页', '只有返回大厅, 观察中'),
    'ENDMENU': ('结算页被菜单挡住', '按一次 ESC 关菜单'),
    'MENU': ('局内菜单', '关掉菜单'),
    'MODAL': ('弹窗', '点确认'),
    'SURVEY': ('问卷', '点跳过'),
    'SLEEP': ('休眠', '点继续游戏'),
    'UNKNOWN': ('识别中', '画面不认识'),
    'PAUSED': ('已暂停', '游戏不在前台'),
    'NOGAME': ('未开游戏', '请打开游戏'),
    'NOWIN': ('游戏在跑', '但找不到窗口'),
}

COLORS = {'reset': '\033[0m', 'dim': '\033[2m', 'bold': '\033[1m',
          'cyan': '\033[96m', 'green': '\033[92m', 'yellow': '\033[93m',
          'red': '\033[91m', 'magenta': '\033[95m', 'white': '\033[97m',
          'blue': '\033[94m'}
USE_COLOR = True


# ---------------------------------------------------------------- 终端工具
def enable_ansi():
    """打开 Windows 控制台的 ANSI 颜色支持"""
    global USE_COLOR
    try:
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            USE_COLOR = False
            return
        if not k.SetConsoleMode(h, mode.value | 0x0004):   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            USE_COLOR = False
    except Exception:
        USE_COLOR = False


def c(s, color):
    if not USE_COLOR:
        return s
    return COLORS.get(color, '') + s + COLORS['reset']


def dwidth(s):
    """显示宽度: 中日韩全角算 2"""
    n = 0
    for ch in s:
        n += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return n


def pad(s, width):
    return s + ' ' * max(0, width - dwidth(s))


def clear():
    os.system('cls' if os.name == 'nt' else 'clear')


def set_title():
    try:
        ctypes.windll.kernel32.SetConsoleTitleW('堡垒之夜自动挂机助手 | QQ ' + AUTHOR_QQ)
    except Exception:
        pass


def hide_cursor(hide=True):
    try:
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        info = ctypes.create_string_buffer(24)
        k.GetConsoleCursorInfo(h, info)
        struct = (ctypes.c_uint32 * 2).from_buffer(info)
        struct[0] = 0 if hide else 1
        k.SetConsoleCursorInfo(h, info)
    except Exception:
        pass


# ---------------------------------------------------------------- 读机器人状态
def my_name():
    """当前设置的游戏ID（玩家名字.txt 优先，其次默认值）"""
    try:
        if os.path.exists(NAME_FILE):
            with open(NAME_FILE, encoding='utf-8-sig') as fh:
                n = fh.readline().strip()
            if n:
                return n
    except Exception:
        pass
    return DEFAULT_NAME


def lax_mode():
    """宽松模式: 名字认不到时也继续做反挂机(给名字特殊/OCR 认不出的朋友用)。"""
    try:
        if os.path.exists(CONF_FILE):
            with open(CONF_FILE, encoding='utf-8-sig') as fh:
                return 'lax=1' in fh.read().replace(' ', '')
    except Exception:
        pass
    return False


def save_lax(on):
    """只改 lax 一项, 保留 设置.txt 里的 px_per_deg/counts_per_deg/key_* (旧写法会整份覆盖)"""
    try:
        conf = read_conf(strict=True)
        conf['lax'] = '1' if on else '0'
        if not write_conf(conf):
            return False
        return lax_mode() == bool(on)          # 回读校验
    except Exception as e:
        _last_action[0] = '保存宽松模式失败: %r' % (e,)
        return False


# ---------------------------------------------------------------- 按键绑定
DEFAULT_KEYS = [('跳跃', '0x20'), ('奔跑', '0xA0'), ('稿子', '0x31'), ('动作', '0x42'), ('交互', '0x45')]

VK_FRIENDLY = {
    0x20: '空格', 0x0D: '回车', 0x1B: 'Esc', 0x09: 'Tab', 0x08: '退格',
    0xA0: '左Shift', 0xA1: '右Shift', 0xA2: '左Ctrl', 0xA3: '右Ctrl', 0xA4: '左Alt', 0xA5: '右Alt',
    0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt',
    0x25: '左方向', 0x26: '上方向', 0x27: '右方向', 0x28: '下方向',
}
for _i in range(1, 13):
    VK_FRIENDLY[0x6F + _i] = 'F%d' % _i           # F1=0x70
for _i in range(10):
    VK_FRIENDLY[0x30 + _i] = str(_i)              # 0-9
for _c in range(26):
    VK_FRIENDLY[0x41 + _c] = chr(0x41 + _c)       # A-Z

NAME_TO_KEY = {
    '空格': '0x20', 'space': '0x20', '空格键': '0x20',
    '回车': '0x0D', 'enter': '0x0D', 'return': '0x0D',
    'esc': '0x1B', 'escape': '0x1B', 'tab': '0x09',
    'ctrl': '0xA2', 'control': '0xA2', '控制': '0xA2', '左ctrl': '0xA2', '右ctrl': '0xA3',
    'shift': '0xA0', '左shift': '0xA0', '右shift': '0xA1',
    'alt': '0xA4', '左alt': '0xA4', '右alt': '0xA5', '控制键': '0xA2',
}
for _i in range(1, 13):                       # f1~f12: 帮助里写了, 以前解析不了
    NAME_TO_KEY['f%d' % _i] = '0x%02X' % (0x6F + _i)


def read_conf(strict=False):
    """把 设置.txt 读成 {小写键: 值}

    strict=True 时, 文件存在但读失败会抛异常(避免"读失败 -> 空 dict -> 整份覆盖"丢配置)
    """
    conf = {}
    if not os.path.exists(CONF_FILE):
        return conf
    try:
        for line in io.open(CONF_FILE, encoding='utf-8-sig', errors='ignore'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            conf[k.strip().lower()] = v.strip()
    except Exception:
        if strict:
            raise
    return conf


def write_conf(conf):
    """原子写: 先写 .tmp 再 os.replace, 中断也不会把 设置.txt 清空"""
    tmp = CONF_FILE + '.tmp'
    try:
        lines = ['# 程序设置(自动生成, 可手动改)', '']
        for k, v in conf.items():
            lines.append('%s=%s' % (k, v))
        with io.open(tmp, 'w', encoding='utf-8', newline='') as fh:
            fh.write('\r\n'.join(lines) + '\r\n')
        os.replace(tmp, CONF_FILE)
        return True
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        _last_action[0] = '写 设置.txt 失败: %r' % (e,)
        return False


def set_conf_one(key, value):
    try:
        conf = read_conf(strict=True)
    except Exception as e:
        _last_action[0] = '读 设置.txt 失败(文件被占用?), 没敢写: %r' % (e,)
        return False
    conf[key.lower()] = value
    return write_conf(conf)


def save_conf_or_report(key, value, ok_msg, bad_msg=None):
    """v1.12.6: 写一项设置并如实反馈结果(以前忽略返回值, 写失败也说「已保存」)"""
    if set_conf_one(key, value):
        _last_action[0] = ok_msg
        return True
    _last_action[0] = bad_msg or ('写 设置.txt 失败(文件被占用?)：%s' % key)
    return False


def parse_key_text(txt):
    """把用户输入变成 VK 字符串(0xNN); 认不出返回 None"""
    t = (txt or '').strip()
    if not t:
        return None
    low = t.lower()
    if low in NAME_TO_KEY:
        return NAME_TO_KEY[low]
    if low.startswith('0x'):
        try:
            v = int(low, 16)
            return '0x%02X' % v if 1 <= v <= 0xFF else None
        except Exception:
            return None
    if len(low) == 1 and low.isdigit():          # 单个数字 = 数字键(3 -> 0x33)
        return '0x%02X' % (0x30 + int(low))
    if t.isdigit():
        v = int(t)                              # 多位数 = 十进制 VK(160 -> 0xA0)
        if 1 <= v <= 0xFF:
            return '0x%02X' % v
        return None
    if len(t) == 1 and t.isascii():
        return '0x%02X' % ord(t.upper())
    return None


def key_label(vk_str):
    try:
        v = int(str(vk_str), 16) if str(vk_str).lower().startswith('0x') else int(vk_str)
    except Exception:
        return str(vk_str)
    return VK_FRIENDLY.get(v, '0x%02X' % v)


def read_keys():
    """返回 [(名字, '0xNN')], 顺序固定"""
    conf = read_conf()
    out = []
    for name, dv in DEFAULT_KEYS:
        out.append((name, conf.get('key_' + name.lower(), dv)))
    return out


def save_key(name, vk_str):
    set_conf_one('key_' + name.lower(), vk_str)


def keys_lines(width=50):
    """键位分行显示(项变多了, 一行放不下)"""
    parts = keys_line().split(' ')
    out, cur = [], ''
    for p in parts:
        cand = (cur + ' ' + p) if cur else p
        if dwidth(cand) > width and cur:
            out.append(cur)
            cur = p
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def keys_line():
    """一行显示键位(短标签, 免得被状态栏截断)"""
    short = {'左Ctrl': 'Ctrl', '左Shift': 'Shift', '右Ctrl': 'Ctrl', '左Alt': 'Alt',
             '右Alt': 'Alt', '左方向': '←', '右方向': '→', '上方向': '↑', '下方向': '↓'}
    parts = []
    for n, v in read_keys():
        parts.append('%s=%s' % (n, short.get(key_label(v), key_label(v))))
    return ' '.join(parts)


def squad_mode_label():
    v = (read_conf().get('squad_mode') or 'alive').strip().lower()
    return '全程' if v != 'start' else '开局前'


def squad_mode_menu():
    print()
    print('  当前策略: ' + c(squad_mode_label(), 'yellow'))
    print('  [1] 全程 —— 队伍栏少于 3 人(含自己)就退出重排；局内也生效(连续 3 轮确认)')
    print('  [2] 开局前 —— 只在等待区/巴士检查(局内绝不主动退出, 打完自然结算)')
    print('  [0] 返回')
    a = _ask('  选择: ')
    if a == '1':
        save_conf_or_report('squad_mode', 'alive',
                            '退出策略 = 全程（局内只剩 2 人也会退出重排）')
    elif a == '2':
        save_conf_or_report('squad_mode', 'start',
                            '退出策略 = 只在开局前（局内不会主动退出）')
    else:
        _last_action[0] = '没改'


def after_match_label():
    v = (read_conf().get('after_match') or 'again').strip().lower()
    return '再玩一局' if v == 'again' else '返回大厅'


def after_match_menu():
    print()
    print('  当前: ' + c(after_match_label(), 'yellow'))
    print('  [1] 再玩一局 —— 结算后直接点「再玩一次」, 省掉回大厅的时间(默认)')
    print('  [2] 返回大厅 —— 结算后回大厅, 再点「开始」重新匹配')
    print('  (改完下一次结算页就生效, 不用重启)')
    print('  [0] 返回')
    a = _ask('  选择: ')
    if a == '1':
        save_conf_or_report('after_match', 'again', '结算后行为 = 再玩一局（点「再玩一次」）')
    elif a == '2':
        save_conf_or_report('after_match', 'lobby', '结算后行为 = 返回大厅')
    else:
        _last_action[0] = '没改'


def keys_menu():
    """[10] 按键绑定: 单独设置 跳跃/奔跑/稿子/动作/交互 对应键盘哪个键"""
    while True:
        clear()
        print()
        print(c('  ┌' + '─' * W + '┐', 'cyan'))
        boxrow('按键绑定：告诉程序这几个动作在键盘上是哪个键', 'cyan', W - 1)
        boxrow('值可以是单字字母/数字，或 空格 / Ctrl / Shift / Tab / F1~F12，或 0x20 这种', 'dim', W - 1)
        print(c('  └' + '─' * W + '┘', 'cyan'))
        print()
        for i, (name, vk) in enumerate(read_keys(), 1):
            print('  ' + c('[%d]' % i, 'cyan') + ' ' + pad(name, 10)
                  + c(pad(key_label(vk), 10), 'yellow') + c(vk, 'dim'))
        print()
        print(c('  用在：奔跑=冲刺 动作=B跳舞 交互=E搜索/开箱/救援/召回；跳跃/稿子=备用', 'dim'))
        print(c('  [0] 返回      [t] 测试「跳跃」能不能按出来', 'dim'))
        print()
        ans = _ask('  输入序号改键，0 返回：').strip()
        if ans in ('0', ''):
            return
        if ans.lower() == 't':
            _last_action[0] = '已发送一次「跳跃」，看看游戏里角色有没有跳'
            try:
                subprocess.Popen([sys.executable, BOT, '--press', '跳跃'], cwd=BASE,
                                 creationflags=0x08000000, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except Exception as e:
                _last_action[0] = '测试失败: %s' % e
            time.sleep(0.8)
            continue
        if not ans.isdigit() or not (1 <= int(ans) <= len(DEFAULT_KEYS)):
            _last_action[0] = '没有这个序号：%s' % ans
            time.sleep(0.5)
            continue
        idx = int(ans) - 1
        name, cur = read_keys()[idx]
        print()
        print(c('  「%s」现在绑的是 %s。' % (name, key_label(cur)), 'white'))
        txt = _ask('  输入新按键（直接敲字母/数字，或 空格/ctrl/shift/tab/f1，或 0xNN）：')
        vk = parse_key_text(txt)
        if not vk:
            _last_action[0] = '认不出这个按键："%s"' % txt
            time.sleep(0.8)
            continue
        save_key(name, vk)
        log_ui('按键绑定: %s -> %s (%s)' % (name, key_label(vk), vk))
        _last_action[0] = '已把「%s」绑到 %s' % (name, key_label(vk))
        time.sleep(0.6)


def save_name(name):
    try:
        with open(NAME_FILE, 'w', encoding='utf-8') as fh:
            fh.write(name.strip() + '\n')
        return True
    except Exception as e:
        _last_action[0] = '保存游戏ID失败: %r (文件被占用?)' % (e,)
        return False


def log_ui(msg):
    """把界面上发生的事(按键/启动/报错)也写进 bot_log.txt, 方便排查问题"""
    try:
        line = '[%s] [UI] %s' % (time.strftime('%H:%M:%S'), msg)
        with open(os.path.join(BASE, 'bot_log.txt'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _match_files():
    """计数文件: 程序目录 stats.txt + 用户目录 matches.txt(换版本也不丢)"""
    out = [os.path.join(BASE, 'stats.txt')]
    try:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        out.append(os.path.join(base, 'FortniteAutoBot', 'matches.txt'))
    except Exception:
        pass
    return out


def read_matches():
    """已完成对局 = 每一次点「返回大厅」算一局(两处文件按行去重后统计)"""
    total = today = 0
    today_s = time.strftime('%Y-%m-%d')
    seen = set()
    try:
        for path in _match_files():
            if not os.path.exists(path):
                continue
            for ln in io.open(path, encoding='utf-8-sig', errors='replace').read().splitlines():
                ln = ln.lstrip('\ufeff').strip()
                if not ln:
                    continue
                if ln in seen:
                    continue
                seen.add(ln)
                total += 1
                if ln[:10] == today_s:
                    today += 1
    except Exception:
        pass
    return total, today


_SESSION_BASE = None


def session_mark():
    """记下启动器启动那一刻的总局数; 「本次对局」= 当前总数 - 它"""
    global _SESSION_BASE
    try:
        _SESSION_BASE = read_matches()[0]
    except Exception:
        _SESSION_BASE = None


def session_rounds():
    """本次启动器开着期间完成的局数: 关掉再开就从头算(总局数不受影响)"""
    global _SESSION_BASE
    try:
        total, _ = read_matches()
    except Exception:
        return 0
    if _SESSION_BASE is None:
        session_mark()
        return 0
    return max(0, total - _SESSION_BASE)


def read_status():
    """从 bot_log.txt 尾部读: 当前状态 / 已完成局数 / 最近动作"""
    state, action, rounds = None, '', 0
    try:
        with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()[-400:]
    except Exception:
        return None, '', 0
    for ln in lines:
        m = re.search(r'state=([A-Za-z_?]+)', ln)
        if m:
            state = m.group(1)
        if 'ACTION' in ln:
            action = ln.split('ACTION', 1)[1].strip()[:36]
        if 'FIDGET' in ln:
            action = '反挂机:' + ln.split('FIDGET', 1)[1].strip()[:30]
        if '结束页面' in ln or 'click_results' in ln:
            rounds += 1
    return state, action, rounds


_OCR_INFO = {}


def child_env():
    """让子进程按 utf-8 输出, 免得中文在管道里变成乱码"""
    e = dict(os.environ)
    e['PYTHONIOENCODING'] = 'utf-8'
    e['PYTHONUTF8'] = '1'
    return e


def read_counts():
    """读 设置.txt 里的 counts_per_deg(每度多少鼠标计数), 默认 11.92"""
    try:
        path = data_file('设置.txt')
        if os.path.exists(path):
            for ln in io.open(path, encoding='utf-8-sig').read().splitlines():
                mm = re.match(r'\s*counts_per_deg\s*=\s*([0-9.]+)', ln)
                if mm:
                    v = float(mm.group(1))
                    if 2.0 <= v <= 200.0:
                        return v
    except Exception:
        pass
    return 11.92


def read_calib():
    """读 设置.txt 里的 px_per_deg(每度多少像素), 没有就返回默认 5.9"""
    try:
        path = data_file('设置.txt')
        if os.path.exists(path):
            for ln in io.open(path, encoding='utf-8-sig').read().splitlines():
                m = re.match(r'\s*px_per_deg\s*=\s*([0-9.]+)', ln)
                if m:
                    v = float(m.group(1))
                    if 1.0 <= v <= 100.0:
                        return v
    except Exception:
        pass
    return 5.9


def ocr_info():
    """问打包的 python: 这台机器装了哪些 Windows OCR 语言(只查一次)"""
    global _OCR_INFO
    if _OCR_INFO:
        return _OCR_INFO
    info = {'langs': [], 'zh': False, 'ok': False}
    try:
        r = subprocess.run([sys.executable, BOT, '--check-ocr'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=child_env(),
                           creationflags=0x08000000, timeout=25)
        for ln in (r.stdout or '').splitlines():
            ln = ln.strip()
            if ln.startswith('CHINESE_OCR='):          # ASCII 标记最可靠
                info['zh'] = ln.split('=', 1)[1].strip() == '1'
                info['ok'] = True
            elif ln.startswith('ZH_LANGS='):
                v = ln.split('=', 1)[1].strip()
                if v and v != '-':
                    info['langs'] = [x.strip() for x in v.split(',') if x.strip()]
            elif ln.startswith('ALL_LANGS='):
                if not info['langs']:
                    v = ln.split('=', 1)[1].strip()
                    info['langs'] = [x.strip() for x in v.split(',') if x.strip() and x.strip() != '-']
            elif ln.startswith('OCR 语言:'):            # 兜底(旧版/英文系统)
                info['langs'] = [x.strip() for x in ln.split(':', 1)[1].split(',') if x.strip()]
                info['ok'] = True
            elif ln.startswith('中文可用:'):
                info['zh'] = ln.endswith('是')
    except Exception as e:
        info['err'] = str(e)
    if info.get('ok'):
        _OCR_INFO = info          # v1.10.1: 只有成功才缓存, 否则下次还能自己重查
    return info


def bot_alive(proc):
    return proc is not None and proc.poll() is None


def bot_running(proc):
    """本进程句柄 + bot.pid 任一说在跑就算在跑(关掉启动器再开也能认出来)"""
    return bot_alive(proc) or bool(existing_bot_pid())


def _as_stoppable(proc):
    if bot_alive(proc):
        return proc
    pid = existing_bot_pid()
    return _ExtProc(pid) if pid else proc


def kill_stray_bots():
    """把跟本程序相关的 python 进程全部结束掉(命令行里含 fortnite_bot.py 的)。
    覆盖: 本启动器起的子进程、上次启动器遗留的 daemon、手动跑的 --daemon。
    返回被结束的 pid 列表。"""
    killed = []
    try:
        # 只找 python 解释器(排除 powershell 自己: 它的命令行里也带 fortnite_bot.py 字样)
        ps = ("Get-CimInstance Win32_Process | "
              "Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*fortnite_bot.py*' } | "
              "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; "
              "[string]$_.ProcessId } catch {} }")
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                           capture_output=True, text=True, timeout=20,
                           creationflags=(0x08000000 if os.name == 'nt' else 0))
        for tok in (r.stdout or '').split():
            if tok.strip().isdigit():
                killed.append(int(tok.strip()))
    except Exception as e:
        try:
            log_ui('清理残留进程时出错: %s' % e)
        except Exception:
            pass
    if killed:
        try:
            log_ui('已结束残留的挂机进程: %s' % ', '.join(str(x) for x in killed))
        except Exception:
            pass
    return killed


def stop_everything(proc):
    """[2]/[3]/Ctrl+C/EOF/退出: 把机器人真正停掉——不只是本进程的子进程,
    还包括上次启动器留下的 daemon / 手动起的 --daemon(一律按命令行匹配结束)。"""
    p = _as_stoppable(proc)
    if p is not None:
        stop_bot(p)
    clear_pid_file()
    kill_stray_bots()


# ---------------------------------------------------------------- 界面
def hr(ch='─'):
    return ch * W


def line(text='', color=None):
    s = pad(text, W)
    print(c('  ' + s, color) if color else '  ' + s)


def fit(s, width):
    """按显示宽度截断到 width 以内(超出补 …), 保证边框不歪。"""
    if dwidth(s) <= width:
        return s
    out, n = '', 0
    for ch in s:
        w = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if n + w > width - 1:
            break
        out += ch
        n += w
    return out + '…'


def boxrow(text, color, inner):
    print(c('  │ ', color) + c(pad(fit(text, inner), inner), color) + c('│', color))


def menu_group(title):
    """菜单分组标题: 左边标题, 右边一条淡色横线"""
    print('  ' + c(title, 'dim') + ' ' + c('─' * max(1, W - dwidth(title) - 1), 'dim'))


def menu_row(num, name, value='', value_color='dim'):
    """菜单一行: 序号/名称/当前值三列, 列宽固定, 纵向一定对齐"""
    head = c(pad('[%s]' % num, 7), 'cyan')
    body = c(pad(fit(name, 16), 16), 'white')
    if value:
        print(('  ' + head + body + '  ' + c(fit(value, 42), value_color)).rstrip())
    else:
        print(('  ' + head + body).rstrip())


def kv(pairs):
    """一行内放多组「标签 值」，例如 kv([('画面','战斗','white'), ('对局','3 局','dim')])"""
    out = []
    for label, value, color in pairs:
        out.append(c(pad(label, 8), 'dim'))
        out.append(c(pad(fit(value, 22), 22), color))
    print(('  ' + ''.join(out)).rstrip())



# ---------------------------------------------------------------- 开场动画
_SPLASH_ROWS = []


def _splash_key_hit():
    """有按键就返回 True(用于"按任意键跳过开场动画")"""
    try:
        import msvcrt
    except ImportError:
        return False
    hit = False
    while msvcrt.kbhit():
        msvcrt.getwch()
        hit = True
    return hit


def _row_ansi(top, bot):
    """一行 40 格: 上像素当前景、下像素当背景, 用半块 ▀ 画出来"""
    alpha = _art.ALPHA
    out, cur = [], None
    for ct, cb in zip(top, bot):
        fg = _art.XTERM[alpha.index(ct)]
        bg = _art.XTERM[alpha.index(cb)]
        if fg == bg:
            ch, key = '█', (fg, None)
        else:
            ch, key = '▀', (fg, bg)
        if key != cur:
            if key[1] is None:
                out.append('\x1b[38;5;%dm' % key[0])
            else:
                out.append('\x1b[38;5;%d;48;5;%dm' % key)
            cur = key
        out.append(ch)
    out.append('\x1b[0m')
    return ''.join(out)


def _splash_rows():
    global _SPLASH_ROWS
    if not _SPLASH_ROWS and _art is not None:
        _SPLASH_ROWS = [_row_ansi(t, b) for t, b in zip(_art.TOP, _art.BOT)]
    return _SPLASH_ROWS


def _console_cells():
    """取控制台窗口当前可见区域的字符列数/行数"""
    try:
        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [('X', wintypes.SHORT), ('Y', wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [('Left', wintypes.SHORT), ('Top', wintypes.SHORT),
                        ('Right', wintypes.SHORT), ('Bottom', wintypes.SHORT)]

        class CSBI(ctypes.Structure):
            _fields_ = [('dwSize', COORD), ('dwCursorPosition', COORD),
                        ('wAttributes', wintypes.WORD), ('srWindow', SMALL_RECT),
                        ('dwMaximumWindowSize', COORD)]

        k = ctypes.windll.kernel32
        info = CSBI()
        if not k.GetConsoleScreenBufferInfo(k.GetStdHandle(-11), ctypes.byref(info)):
            return None
        r = info.srWindow
        return (r.Right - r.Left + 1, r.Bottom - r.Top + 1)
    except Exception:
        return None


def _console_window():
    try:
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return 0


def maximize_console():
    """把控制台窗口最大化(只调 ShowWindow, 不动缓冲区/尺寸 API)"""
    hwnd = _console_window()
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.ShowWindow(hwnd, 3)      # SW_MAXIMIZE
        return True
    except Exception:
        return False


def restore_console():
    hwnd = _console_window()
    if not hwnd:
        return
    try:
        ctypes.windll.user32.ShowWindow(hwnd, 9)      # SW_RESTORE
    except Exception:
        pass


def _art_rows(mod, PAT, COL, order):
    """把点阵画转成带颜色的终端行"""
    rows = []
    alpha = mod.ALPHA
    for y in range(len(PAT)):
        out, cur = [], None
        for ch, cc in zip(PAT[y], COL[y]):
            if ch == ' ':
                out.append(' ')
                continue
            xi = order[alpha.index(cc)]
            if xi != cur:
                out.append('\x1b[38;5;%dm' % xi)
                cur = xi
            out.append(ch)
        out.append('\x1b[0m')
        rows.append(''.join(out))
    return rows


def _half_rows(TOP, BOT, order, mod):
    """把"上下两个实心方块"画转成终端行: 每个字符用 ▀ + 前景/背景色, 格子 100% 填满"""
    rows = []
    alpha = mod.ALPHA
    for y in range(len(TOP)):
        top, bot = TOP[y], BOT[y]
        out = []
        cur = None
        for x in range(len(top)):
            pair = (order[alpha.index(top[x])], order[alpha.index(bot[x])])
            if pair != cur:
                out.append('\x1b[38;5;%d;48;5;%dm' % pair)
                cur = pair
            out.append('▀')
        out.append('\x1b[0m')
        rows.append(''.join(out))
    return rows


def _quad_rows(PAT, FG, BG, order, mod):
    """高分辨率版: 每格 = 2x4 点(盲文) + 实心背景色, 格子 100% 填满"""
    rows = []
    alpha = mod.ALPHA
    for y in range(len(PAT)):
        out = []
        cur = None
        for x in range(len(PAT[y])):
            pair = (order[alpha.index(FG[y][x])], order[alpha.index(BG[y][x])])
            if pair != cur:
                out.append('\x1b[38;5;%d;48;5;%dm' % pair)
                cur = pair
            out.append(PAT[y][x])
        out.append('\x1b[0m')
        rows.append(''.join(out))
    return rows


def play_intro(skipable=True):
    """开场: 控制台先最大化一次 -> 按全屏尺寸现场生成点阵大图 -> 播放 -> 还原窗口"""
    force = os.environ.get('FNBOT_FORCE_ANIM') == '1'
    if not force and not sys.stdout.isatty():
        return False
    try:
        import splash_gen
    except Exception:
        splash_gen = None
    logo = os.path.join(BASE, 'logo.png')
    if splash_gen is None or not os.path.exists(logo) or _braille is None:
        return play_intro_static(skipable)

    try:
        maximize_console()
        time.sleep(0.5)
        cols, rows = (_console_cells() or shutil.get_terminal_size((120, 30)))
        cols = max(20, cols - 1)          # 留 1 列余量, 免得最后几格被窗口右边缘吃掉
        art_rows = max(8, min(rows - 6, 70))
        clear()
        mode = (os.environ.get('FNBOT_SPLASH_MODE') or 'quad').lower()
        if mode == 'braille':
            PAT, COL, order = splash_gen.build_braille(
                logo, cols, art_rows, '堡垒之夜', '自动挂机助手', VERSION, AUTHOR_QQ)
            out = _art_rows(splash_gen, PAT, COL, order)
        elif mode == 'quad':
            PAT, FG, BG, order = splash_gen.build_quad(
                logo, cols, art_rows, '堡垒之夜', '自动挂机助手', VERSION,
                AUTHOR_QQ, AUTHOR_NAME, with_text=False)
            out = _quad_rows(PAT, FG, BG, order, splash_gen)
        else:
            TOP, BOT, order = splash_gen.build_half(
                logo, cols, art_rows, '堡垒之夜', '自动挂机助手', VERSION, AUTHOR_QQ)
            out = _half_rows(TOP, BOT, order, splash_gen)
        for i, r in enumerate(out):
            sys.stdout.write(r + '\n')
            sys.stdout.flush()
            if skipable and _splash_key_hit():
                for r2 in out[i + 1:]:
                    sys.stdout.write(r2 + '\n')
                sys.stdout.flush()
                break
            time.sleep(0.004 if i < 3 else 0.008)

        # 徽章下面用正常字符输出标题(比点阵清晰得多)
        def _center(txt):
            ind = max(0, (cols - dwidth(txt)) // 2)
            return ' ' * ind + txt

        print()
        print(c(_center('堡垒之夜 · 自动挂机助手 ' + VERSION), 'bold'))
        print(c(_center('作者 ' + AUTHOR_NAME + '   ·   QQ ' + AUTHOR_QQ), 'dim'))
        print(c(_center('项目主页 ' + REPO_URL), 'cyan'))

        steps, label = 36, '正在初始化 OCR 引擎 '
        bind = max(0, (cols - steps - 6 - dwidth(label)) // 2)
        for i in range(steps + 1):
            bar = '█' * i + '░' * (steps - i)
            sys.stdout.write('\r' + ' ' * bind + '\x1b[38;5;178m' + label
                             + '\x1b[38;5;220m' + bar + '\x1b[0m %3d%%' % (i * 100 // steps))
            sys.stdout.flush()
            if skipable and _splash_key_hit():
                sys.stdout.write('\r' + ' ' * bind + '\x1b[38;5;178m' + label
                                 + '\x1b[38;5;220m' + '█' * steps + '\x1b[0m 100%')
                break
            time.sleep(0.012)
        sys.stdout.write('\n')
        sys.stdout.flush()
        time.sleep(0.8)
        return True
    except Exception:
        return False
    finally:
        restore_console()
        time.sleep(0.3)
        clear()


def play_intro_static(skipable=True):
    """回退: 旧版固定尺寸半块 ▀ 图案(没有 logo.png / 没有 splash_gen 时)"""
    force = os.environ.get('FNBOT_FORCE_ANIM') == '1'
    if not force and not sys.stdout.isatty():
        return False
    try:
        sz = shutil.get_terminal_size((120, 40))
        cols, lines = sz.columns, sz.lines
    except Exception:
        cols, lines = 120, 40

    clear()
    rows_out = []
    if _braille is not None and cols >= _braille.W + 2 and lines >= _braille.H + 1:
        rows_out = _art_rows(_braille, _braille.PAT, _braille.COL, _braille.XTERM)
        art_w = _braille.W
    elif _art is not None:
        rows_out = _splash_rows()
        art_w = getattr(_art, 'W', 48)
    if not rows_out:
        return False
    ind = max(0, (cols - art_w) // 2)
    pad = ' ' * ind
    for i, r in enumerate(rows_out):
        sys.stdout.write(pad + r + '\n')
        sys.stdout.flush()
        if skipable and _splash_key_hit():
            for r2 in rows_out[i + 1:]:
                sys.stdout.write(pad + r2 + '\n')
            sys.stdout.flush()
            break
        time.sleep(0.012)
    title = '堡垒之夜 · 自动挂机助手 ' + VERSION
    print('\n' + c(pad_str(title, cols), 'bold'))
    print(c(pad_str('作者 QQ ' + AUTHOR_QQ + '   ·   OCR 自动对局助手', cols), 'dim'))
    time.sleep(0.5)
    return True


def pad_str(text, cols):
    ind = max(0, (cols - dwidth(text)) // 2)
    return ' ' * ind + text


def banner():
    inner = W - 1
    print(c('  ╭' + '─' * W + '╮', 'cyan'))
    title, ver = '堡垒之夜 · 自动挂机助手', VERSION
    gap = max(1, inner - dwidth(title) - dwidth(ver))
    print(c('  │ ', 'cyan') + c(pad(title, inner - dwidth(ver) - 1) + ver + ' ', 'bold') + c('│', 'cyan'))
    sub = fit('作者 ' + AUTHOR_NAME + '   ·   QQ ' + AUTHOR_QQ + '   ·   bug / 报错请加 QQ 并附 bot_log.txt', inner)
    print(c('  │ ', 'cyan') + c(pad(sub, inner), 'dim') + c('│', 'cyan'))
    print(c('  ╰' + '─' * W + '╯', 'cyan'))


def render(proc):
    state, action, rounds = read_status()
    _mt, _md = read_matches()
    rounds, rounds_today = _mt, _md        # 只认 stats.txt(日志行数会重复计数)
    alive = bot_running(proc)
    clear()
    banner()
    print()

    # ── 状态(两列对齐)
    st_txt, st_col = ('● 运行中', 'green') if alive else ('○ 未运行', 'dim')
    print(('  ' + c(pad('状态', 8), 'dim') + c(pad(st_txt, 18), st_col)).rstrip())

    if state:
        cn, tip = STATE_CN.get(state, (state, ''))
        screen = cn + ('·' + tip if tip else '')
    else:
        screen = '等待启动'
    kv([('词库', ('%d 个自定义词（按 9 增删）' % len(read_words())) if read_words() else '空（按 9 添加）', 'dim')])
    _sess = session_rounds()
    kv([('画面', screen, 'white'),
        ('本次对局', ' %d 局' % _sess, 'dim')])
    kv([('总局数', ('%d 局 (今天 %d)' % (rounds, rounds_today)) if rounds
         else '0 局 (还没点过「返回大厅」)', 'dim')])

    _nm = my_name()
    _lax = lax_mode()
    _oi = ocr_info()
    _ocr_txt = '中文OCR正常' if _oi.get('zh') else '中文OCR缺失'
    kv([('识别', ('宽松' if _lax else '严格') + '·' + _ocr_txt,
         'yellow' if _lax else ('dim' if _oi.get('zh') else 'red')),
        ('我的ID', _nm, 'red' if _nm == DEFAULT_NAME else 'white')])

    _cal = read_calib()
    print(('  ' + c(pad('视角', 8), 'dim')
           + c(pad('%.1f px/度' % _cal, 14), 'white')
           + c('≈ %.3f 度/px · 转一圈 %.0f px · %.1f 计数/度'
               % (1.0 / _cal, _cal * 360, read_counts()), 'dim')).rstrip())
    for _i, _ln in enumerate(keys_lines()):
        print(('  ' + c(pad('键位' if _i == 0 else '', 8), 'dim') + c(_ln, 'dim')).rstrip())
    if action:
        print(('  ' + c(pad('动作', 8), 'dim') + c(fit(action, 40), 'dim')).rstrip())
    print()

    # ── 只在「还没填自己的名字」时出现的警告块
    if _nm == DEFAULT_NAME:
        print(c('  ┌' + '─' * W + '┐', 'red'))
        boxrow('现在用的是作者测试号 “%s”' % DEFAULT_NAME, 'red', W - 1)
        boxrow('先按 4 填名字，再按 1 开始。填错 → 认不到你 → 防挂机不启动：', 'red', W - 1)
        boxrow('局内干站可能被当成挂机踢掉，也可能触发游戏的「休眠模式」。', 'red', W - 1)
        if not _oi.get('zh'):
            boxrow('这台电脑没装中文OCR：装「中文(简体)」的「光学字符识别」。', 'red', W - 1)
        print(c('  └' + '─' * W + '┘', 'red'))
        print()

    # ── 菜单: 分组 + 三列对齐(序号 / 名称 / 当前值)
    print(c('  ' + hr('─'), 'dim'))
    print()

    menu_group('开始 / 停止')
    menu_row('1', '开始挂机')
    menu_row('2', '停止挂机')
    menu_row('3', '退出程序')
    print()

    menu_group('我的游戏 / 识别')
    menu_row('4', '改我的游戏ID', _nm, 'red' if _nm == DEFAULT_NAME else 'white')
    menu_row('5', '名字识别', '宽松（容易认错）' if _lax else '严格（推荐）',
             'yellow' if _lax else 'white')
    menu_row('6', '中文 OCR', '正常' if _oi.get('zh') else '缺失（按 6 安装）',
             'white' if _oi.get('zh') else 'red')
    print()

    menu_group('手感 / 按键')
    menu_row('7', '校准视角灵敏度', '%.1f px/度（局内、活着时按 7）' % _cal)
    menu_row('8', '重播开场动画')
    menu_row('9', '自动点击词', '%d 个' % len(read_words()))
    menu_row('10', '按键绑定')
    menu_row('11', '热键自检')
    print()

    menu_group('自动行为')
    menu_row('12', '退出策略', squad_mode_label())
    menu_row('15', '结算后', after_match_label())
    menu_row('16', '识别动作',
             ('%d 条自定义' % len(read_actions())) if read_actions()
             else '默认（内置 7 词→交互键）')
    menu_row('17', '挂机机制', fidget_label())
    print()

    menu_group('程序 / 更新')
    menu_row('13', '打开项目主页', '看有没有新版本')
    menu_row('14', '检查更新', '自动下载到「下载」文件夹')
    print()
    print()

    # ── 反馈 / 快捷键 / 当前提示
    print(('  ' + c(pad('上次操作', 10), 'dim')
           + c(fit(_last_action[0] or '还没有输入过', 46), 'yellow')).rstrip())
    print(('  ' + c(pad('快捷键', 10), 'dim')
           + c('F10 开始 / 重开    F9 停止    （输入数字后按回车执行）', 'dim')).rstrip())
    if alive:
        print('  ' + c('★ 正在挂机：Alt+Tab 回游戏即可，它会自己跑图、自己重开。', 'green'))
    else:
        if state == 'NOWIN':
            print('  ' + c('● 游戏进程在跑，但没找到它的窗口：把游戏窗口还原（别最小化），脚本会自动接上。', 'yellow'))
        elif state == 'NOGAME':
            print('  ' + c('● 还没开堡垒之夜：先启动游戏，挂机助手会自动开始点「开始」。', 'yellow'))
        else:
            print('  ' + c('● 未运行：输入 1 回车即可开始（会自动切到游戏窗口）。', 'yellow'))
    print()
    print(c('  ' + hr('─'), 'dim'))
    print('  ' + c(pad('项目主页', 10), 'dim') + c(REPO_URL, 'cyan') + c('   ← 更新地址', 'dim'))
    print('  ' + c('有 bug / 报错 → 加 QQ ' + AUTHOR_QQ + '，把 bot_log.txt 一起发我', 'magenta'))
    print()


# ---------------------------------------------------------------- 机器人进程
def focus_game_once():
    """把堡垒之夜窗口切到前台(只在按 1 开始挂机时切一次, 之后绝不再抢焦点)。"""
    if not os.path.isfile(BOT):
        return False
    try:
        flags = 0x08000000 if os.name == 'nt' else 0
        r = subprocess.run([sys.executable, BOT, '--focus'],
                           cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=flags, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


PID_FILE = os.path.join(BASE, 'bot.pid')


def _pid_alive(pid):
    """OpenProcess 探测进程是否还活着(不依赖 subprocess 句柄)"""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == 'nt':
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong(0)
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _proc_create_time(pid):
    """进程创建时间(Windows FILETIME 整数), 用来防止 PID 复用误判/误杀"""
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x0400, False, int(pid))       # PROCESS_QUERY_INFORMATION
        if not h:
            return 0
        ct, et, kt, ut = (wintypes.FILETIME() for _ in range(4))
        ok = k.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                               ctypes.byref(kt), ctypes.byref(ut))
        k.CloseHandle(h)
        if not ok:
            return 0
        return (ct.dwHighDateTime << 32) | ct.dwLowDateTime
    except Exception:
        return 0


def write_pid_file(pid):
    try:
        with io.open(PID_FILE, 'w', encoding='utf-8') as fh:
            fh.write('%d|%d' % (int(pid), _proc_create_time(pid)))
        return True
    except Exception:
        return False


def clear_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def existing_bot_pid():
    """读 bot.pid: 活着 + 创建时间一致才算(否则当过期文件删掉)"""
    try:
        if os.path.exists(PID_FILE):
            raw = (io.open(PID_FILE, encoding='utf-8', errors='ignore').read().strip() or '')
            parts = raw.split('|')
            pid = int(parts[0] or 0)
            ct = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            if not _pid_alive(pid):
                clear_pid_file()
                return 0
            if ct and _proc_create_time(pid) != ct:
                clear_pid_file()                      # PID 被复用了, 不是我们的机器人
                return 0
            return pid
    except Exception:
        pass
    return 0


class _ExtProc(object):
    """上次启动器留下的机器人: 让界面显示运行中, 且 [2] 能停掉"""
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None if _pid_alive(self.pid) else 0

    def terminate(self):
        try:
            subprocess.run(['taskkill', '/PID', str(self.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=(0x08000000 if os.name == 'nt' else 0))
        except Exception:
            pass


def start_bot():
    if not os.path.isfile(BOT):
        print('\n  找不到 fortnite_bot.py，请确认文件和启动.bat 在同一个文件夹里。')
        time.sleep(2.5)
        return None
    try:
        old = existing_bot_pid()
        if old:
            write_control('start')
            _last_action[0] = '检测到上次的机器人还在跑(pid=%s), 已复用它并发了「开始」' % old
            log_ui('复用已在运行的机器人 pid=%s (避免双开), 已写 控制.txt=start' % old)
            return _ExtProc(old)
        write_control('start')          # 清掉上次残留的 stop, 免得新进程一启动就把自己关掉
        f = open(CONSOLE_LOG, 'a', encoding='utf-8', errors='replace')
        flags = 0x08000000 if os.name == 'nt' else 0      # CREATE_NO_WINDOW: 不弹第二个黑框
        args = [sys.executable, BOT, '--daemon', '--autostart']
        if lax_mode():
            args.append('--lax-name')
        log_ui('启动机器人进程: %s' % ' '.join(args[1:]))
        proc = subprocess.Popen(args, cwd=BASE, stdout=f, stderr=f, creationflags=flags)
        write_pid_file(proc.pid)        # 立刻占坑, 免得 2.5 秒内连点两次起两个 daemon
        try:
            f.close()                   # 父进程别再拿着这个句柄
        except Exception:
            pass
        time.sleep(2.5)
        if proc.poll() is not None:
            tail = ''
            try:
                with open(CONSOLE_LOG, 'r', encoding='utf-8', errors='replace') as cf:
                    tail = ''.join(cf.readlines()[-12:])
            except Exception:
                pass
            log_ui('机器人启动后立刻退出了(exit=%s), 控制台最后几行:\n%s' % (proc.returncode, tail))
            clear_pid_file()
            _last_action[0] = '机器人启动失败(详情已写入 bot_log.txt), 常见原因: 游戏没开/被杀软拦了'
            return None
        log_ui('机器人已启动(pid=%s)' % proc.pid)
        write_pid_file(proc.pid)
        return proc
    except Exception as e:
        print('\n  启动失败：%r' % (e,))
        print('  请把这条信息发给作者 QQ ' + AUTHOR_QQ)
        time.sleep(3)
        return None


CONTROL_FILE = os.path.join(BASE, '控制.txt')


def write_control(cmd):
    """给机器人传命令(键盘 F9/F10 万一被拦截, 这条通道也能停/开始)"""
    try:
        io.open(CONTROL_FILE, 'w', encoding='utf-8').write(cmd)
        return True
    except Exception:
        return False


def stop_bot(proc):
    if proc is None:
        return
    try:
        proc.terminate()
        for _ in range(20):             # 最多等 4 秒, 让它自己收尾(别直接 kill 丢日志)
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
    except Exception:
        pass
    clear_pid_file()


# 用列表做可变引用: 让按键等待里的状态刷新能看到当前进程
_proc_holder = [None]
_last_state = [None]
_last_action = ['']      # 最近一次按键反馈(显示在界面上, 让你看得见自己按了什么)


def install_ocr_flow():
    """调 --install-ocr(内部弹 UAC 安装); 没成就打开 Windows 设置页"""
    try:
        r = subprocess.run([sys.executable, BOT, '--install-ocr'],
                           cwd=BASE, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=child_env(),
                           timeout=900, creationflags=0x08000000)
        out = ((r.stdout or '') + (r.stderr or '')).strip()
        if out:
            print(c('  ' + out.replace('\n', '\n  '), 'dim'))
        if r.returncode == 0:
            return True
    except Exception as e:
        print(c('  安装过程出错: %s' % e, 'red'))
    try:
        os.startfile('ms-settings:regionlanguage')
        print(c('  已打开 Windows「语言和区域」设置页，按说明手动装即可。', 'yellow'))
    except Exception:
        pass
    return False


WORDS_FILE = data_file('点击词.txt')
WORDS_HEADER = [
    '# 自定义点击词: 一行一个, 只要游戏画面上出现这段文字, 就点它的位置',
    '# 以 # 开头的是注释行; 增删请用启动器菜单 [9]',
]


def read_words():
    words = []
    try:
        if os.path.exists(WORDS_FILE):
            for ln in io.open(WORDS_FILE, encoding='utf-8-sig', errors='replace').read().splitlines():
                t = ln.strip()
                if t and not t.startswith('#'):
                    words.append(t)
    except Exception:
        pass
    return words


def write_words(words):
    try:
        with io.open(WORDS_FILE, 'w', encoding='utf-8', newline='') as f:
            f.write('\r\n'.join(WORDS_HEADER) + '\r\n')
            for w in words:
                f.write(w + '\r\n')
        return True
    except Exception:
        return False


ACTIONS_FILE = data_file('识别动作.txt')
ACTION_HEADER = [
    '# 识别动作: 一行一条, 格式  字眼 => 动作',
    '# 字眼可以写多个, 用 / 或 , 分开; 例: 打开/搜索/上升 => 交互',
    '# 动作可以写: 点击 / 交互 / 按键:E / 组合键:Ctrl+E',
    '# 例: 领取 => 点击',
    '# 例: 召回 => 按键:G',
    '# 一行只写词(没写 =>)时, 等同「点击」; # 开头是注释',
]
ACTION_KINDS = ('点击', '交互')


def read_actions():
    """读 识别动作.txt, 返回有效行(不含注释/空行)"""
    out = []
    try:
        if os.path.exists(ACTIONS_FILE):
            for ln in io.open(ACTIONS_FILE, encoding='utf-8-sig', errors='replace').read().splitlines():
                t = ln.strip()
                if t and not t.startswith('#'):
                    out.append(t)
    except Exception:
        pass
    return out


def _write_rows_atomic(path, header, rows):
    """v1.12.6: 先写 .tmp 再 os.replace —— 写一半崩溃也不会留下半截文件"""
    tmp = path + '.tmp'
    try:
        with io.open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write('\r\n'.join(header) + '\r\n')
            for ln in rows:
                f.write(ln + '\r\n')
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def write_actions(rows):
    return _write_rows_atomic(ACTIONS_FILE, ACTION_HEADER, rows)


def action_ok(txt):
    """检查动作写法: 点击 / 交互 / 按键:E / 组合键:Ctrl+E"""
    t = (txt or '').strip().lower().replace(' ', '').replace('＋', '+')
    if not t or t in ('点击', '交互'):
        return True
    if t.startswith('按键:') or t.startswith('键:'):
        return bool(parse_key_text(t.split(':', 1)[1]))
    if t.startswith('组合键:') or t.startswith('组合:'):
        parts = [p for p in t.split(':', 1)[1].split('+') if p][:4]
        return bool(parts) and all(parse_key_text(p) for p in parts)
    return False


def _action_words(row):
    if '=>' in row:
        row = row.split('=>', 1)[0]
    return [w.strip() for w in row.replace('，', ',').replace('、', ',')
            .replace('/', ',').split(',') if w.strip()]


def actions_menu():
    """[16] 识别动作: 画面出现某些字眼 -> 按键 / 点击 / 组合键"""
    while True:
        clear()
        hide_cursor(True)
        rows = read_actions()
        print()
        print(c('  ┌──────────────────────────────────────────────────────────┐', 'cyan'))
        print(c('  │  识别动作：画面出现这些字 → 立刻按键 / 点击 / 组合键        │', 'cyan'))
        print(c('  │  优先级高于防挂机；按完等 5 秒再看，「救援/召回」停 15 秒    │', 'cyan'))
        print(c('  └──────────────────────────────────────────────────────────┘', 'cyan'))
        print()
        if rows:
            for i, r in enumerate(rows, 1):
                print('  ' + c('%d.' % i, 'cyan') + '  ' + r)
        else:
            print(c('  （没有自定义规则；内置 7 词始终生效）', 'dim'))
            print(c('  默认：打开/搜索/上升/救援/召回/拾取/互动 → 交互键（默认 E）', 'dim'))
        print()
        print('  ' + c('[1]', 'cyan') + ' 添加/修改一条规则   '
              + c('[2]', 'cyan') + ' 删除一条   '
              + c('[3]', 'cyan') + ' 恢复默认   '
              + c('[4]', 'cyan') + ' 用记事本打开   '
              + c('[0]', 'cyan') + ' 返回')
        print()
        ans = _ask('  请输入序号后按回车（0~4）：').strip()
        if ans in ('0', ''):
            hide_cursor(False)
            _last_action[0] = ('已返回主菜单（识别动作：%d 条自定义）' % len(rows) if rows
                               else '已返回主菜单（识别动作：内置默认）')
            return
        if ans == '1':
            print()
            w = _ask('  输入字眼（多个用 / 分开，例如 打开/搜索/救援）：').strip()
            if not w:
                _last_action[0] = '没输入内容，已取消'
                time.sleep(0.5)
                continue
            a = _ask('  输入动作（点击 / 交互 / 按键:E / 组合键:Ctrl+E，直接回车=交互）：').strip() or '交互'
            if not action_ok(a):
                _last_action[0] = '动作写法看不懂："%s"（例：点击 / 交互 / 按键:E / 组合键:Ctrl+E）' % a
                time.sleep(1.0)
                continue
            new_words = [x.strip() for x in w.replace('，', ',').replace('、', ',')
                         .replace('/', ',').split(',') if x.strip()]
            # v1.12.6: 只有字眼完全一样才替换; 以前有一个词重叠就整条删掉,
            # 已有「打开/搜索」时再加「打开」会把「搜索」一起弄丢
            rows = [r for r in rows if set(_action_words(r)) != set(new_words)]
            rows.append('%s => %s' % ('/'.join(new_words), a))
            if not write_actions(rows):
                _last_action[0] = '写入 识别动作.txt 失败(文件被占用?)'
                time.sleep(0.8)
                continue
            _last_action[0] = '已保存「%s => %s」，下次 OCR 到就生效（不用重启机器人）' % ('/'.join(new_words), a)
            time.sleep(0.6)
        elif ans == '2':
            if not rows:
                _last_action[0] = '没有自定义规则可删'
                time.sleep(0.6)
                continue
            idx = _ask('  输入要删除的序号（直接回车=取消）：').strip()
            if not idx.isdigit() or not (1 <= int(idx) <= len(rows)):
                _last_action[0] = '序号不对，已取消'
                time.sleep(0.6)
                continue
            gone = rows.pop(int(idx) - 1)
            if not write_actions(rows):
                _last_action[0] = '写入 识别动作.txt 失败(文件被占用?)'
                time.sleep(0.8)
                continue
            _last_action[0] = '已删除「%s」' % gone
            time.sleep(0.6)
        elif ans == '3':
            try:
                if os.path.exists(ACTIONS_FILE):
                    os.remove(ACTIONS_FILE)
                _last_action[0] = ('已恢复默认：删掉自定义规则'
                                   '（内置 打开/搜索/上升/救援/召回/拾取/互动 → 交互键 始终生效）')
            except Exception as e:
                _last_action[0] = '删除 识别动作.txt 失败: %r' % (e,)
            time.sleep(0.6)
        elif ans == '4':
            try:
                if not os.path.exists(ACTIONS_FILE):
                    write_actions([])
                os.startfile(ACTIONS_FILE)
                _last_action[0] = '已用记事本打开 识别动作.txt（改完保存，机器人每轮会自动读）'
            except Exception as e:
                _last_action[0] = '打不开文件: %r（路径：%s）' % (e, ACTIONS_FILE)
            time.sleep(0.8)


# ── v1.12.5 挂机机制 [17] ──────────────────────────────────────────────
FIDGET_FILE = data_file('挂机动作.txt')
FIDGET_HEADER = [
    '# 挂机动作(自定义): 一行一条, 格式  名称 => 动作 @ 间隔秒',
    '# 动作可以写: 按:E / 长按:E:2 / 连点:E:3 / 连点:E:3:0.4',
    '#            点击 / 点击:左键:3 / 长按左键:10 / 组合键:Ctrl+E',
    '# 间隔不写默认 20 秒(最少 2 秒)',
    '# 例: 点左键 => 点击:左键:3 @ 6',
    '# 例: 按住W  => 长按:W:2 @ 15',
    '# 追踪队友(自动朝队友走)是内置核心动作, 不在这个文件里, 也不能关',
]
FIDGET_BUILTIN = [
    ('A', '综合串', '走两步+看视角+左键+换物品+跳/表情'),
    ('B', '冲刺串', 'W+Shift 冲刺 5 秒 + 空格跳'),
    ('D', '互动串', 'F 切稿子 + 长按左键 10 秒 + 切回'),
]
FIDGET_DETAIL = [
    ('A', '综合串', [
        '按住 W 往前走 3~4 秒，边走边平滑转视角 100~200°（只左右转，不低头）',
        '80% 概率点鼠标左键 1~3 下（或长按左键 0.25~0.9 秒）',
        '按一次「稿子」键换物品（键位见主菜单 [10] 键位设置）',
        '50% 概率换个方向（W/A/D 随机）再走 1.5~2.2 秒，同时甩 120~240°',
        '45% 概率再走 0.7~1.1 秒，小转 40~90°，然后跳一下',
        '65% 概率再点一次左键',
        '50% 概率再换一次物品',
        '30% 概率做个表情，然后等 10 秒',
        '整串一般 8~20 秒；被交互键打断会立刻收尾并松开所有按键',
    ]),
    ('B', '冲刺串', [
        '按住 W + 奔跑键（默认左 Shift）冲刺 5 秒',
        '冲的过程中按一次空格跳（W 和 Shift 一直按着不松）',
        '继续按住再走 3 秒，顺便慢慢转 20~60°',
        '最后才松开 W 和 Shift',
        '整串约 8~9 秒',
    ]),
    ('D', '互动串', [
        '按一次「稿子」键切出镐子',
        '等 1 秒（等镐子真的切出来）',
        '按住鼠标左键 10 秒（挖矿 / 挥击）',
        '再按一次「稿子」键切回武器（被交互键打断也会切回）',
        '整串约 11~12 秒',
    ]),
    ('E', '追踪队友', [
        '读左下角队友名牌，先闭环瞄准把队友转到屏幕正中',
        '移动前先按一下「稿子」键，然后按住 W + 奔跑 10~20 秒',
        '起跑先连跳 3 下，之后每 1.8~3.2 秒点一下空格跳（一共约 5~6 跳）',
        '期间持续 OCR 修正朝向（只动鼠标，W 一直按着）',
        '固定开启，不能改、不能删',
    ]),
]


def fidget_detail(k):
    """[17] -> [7] 查看某一串内置动作的具体步骤"""
    name = ''
    steps = []
    for key, nm, lines in FIDGET_DETAIL:
        if key == k:
            name, steps = nm, lines
            break
    if not steps:
        _last_action[0] = '没有这个内置动作: %s' % k
        time.sleep(0.8)
        return
    clear()
    hide_cursor(True)
    tag = '固定开启' if k == 'E' else ('开' if fidget_switch(FIDGET_SWITCH_KEYS[k]) else '关')
    title = '内置动作 %s %s 的详细步骤' % (k, name)
    title += ' ' * max(0, 56 - sum(2 if ord(ch) > 127 else 1 for ch in title))
    print()
    print(c('  ┌──────────────────────────────────────────────────────────┐', 'cyan'))
    print(c('  │ ' + title + ' │', 'cyan'))
    print(c('  └──────────────────────────────────────────────────────────┘', 'cyan'))
    print('  ' + c('当前状态：', 'white') + c(tag, 'green' if tag != '关' else 'dim'))
    print()
    for i, s in enumerate(steps, 1):
        print('   ' + c('%d.' % i, 'cyan') + ' ' + s)
    print()
    print(c('  抽中概率：A 20% · B 8% · D 18% · E 54%（关掉的动作不参与抽取）', 'dim'))
    if k != 'E':
        print(c('  用 [1] 可以开关这一串；它是内置动作，不能改里面每一步。', 'dim'))
    print()
    _ask('  按回车返回挂机机制：')


FIDGET_SWITCH_KEYS = {'A': 'fidget_a', 'B': 'fidget_b', 'D': 'fidget_d'}
FIDGET_MOUSE = ('左键', '左', '右键', '右', 'left', 'right', 'lmb', 'rmb')


def fidget_switch(key):
    """内置串开关: 设置.txt 里 fidget_a/b/d, 缺省=开"""
    return (read_conf().get(key, '1') or '1').strip().lower() not in ('0', 'off', 'false', '否', '关')


def read_fidget():
    """读 挂机动作.txt 里的有效行"""
    out = []
    try:
        if os.path.exists(FIDGET_FILE):
            for ln in io.open(FIDGET_FILE, encoding='utf-8-sig', errors='replace').read().splitlines():
                t = ln.strip()
                if t and not t.startswith('#'):
                    out.append(t)
    except Exception:
        pass
    return out


def write_fidget(rows):
    return _write_rows_atomic(FIDGET_FILE, FIDGET_HEADER, rows)


def _fnum_ok(txt, lo, hi):
    try:
        v = float(str(txt).strip())
        return lo <= v <= hi
    except Exception:
        return False


def fidget_ok(spec):
    """检查自定义动作写法; 返回 (ok, 原因)"""
    t = (spec or '').strip().lower().replace(' ', '').replace('＋', '+')
    if not t:
        return False, '动作不能空'
    verb, _, rest = t.partition(':')
    parts = [p for p in rest.split(':') if p] if rest else []
    if verb in ('按', '按键'):
        if parts and parse_key_text(parts[0]):
            return True, ''
        return False, '要写成 按:E'
    if verb in ('长按左键', '长按右键', '按住左键', '按住右键'):
        return (True, '') if not parts or _fnum_ok(parts[0], 0.1, 30) else (False, '秒数要 0.1~30')
    if verb in ('长按', '按住'):
        if not parts:
            return False, '要写成 长按:E:2 或 长按左键:10'
        if parts[0] in FIDGET_MOUSE:
            return (True, '') if len(parts) < 2 or _fnum_ok(parts[1], 0.1, 30) else (False, '秒数要 0.1~30')
        if not parse_key_text(parts[0]):
            return False, '按键名看不懂：%s' % parts[0]
        return (True, '') if len(parts) < 2 or _fnum_ok(parts[1], 0.1, 30) else (False, '秒数要 0.1~30')
    if verb in ('连点', '多点', '连续点击'):
        if not parts or not (parse_key_text(parts[0]) or parts[0] in FIDGET_MOUSE):
            return False, '要写成 连点:E:3 或 连点:左键:3'
        if len(parts) > 1 and not (parts[1].isdigit() and 1 <= int(parts[1]) <= 50):
            return False, '次数要 1~50'
        if len(parts) > 2 and not _fnum_ok(parts[2], 0.05, 5):
            return False, '间隔要 0.05~5 秒'
        return True, ''
    if verb in ('点击', '单击'):
        tgt = parts[0] if parts else '左键'
        if tgt not in FIDGET_MOUSE:
            return False, '要写成 点击 / 点击:左键:3'
        if len(parts) > 1 and not (parts[1].isdigit() and 1 <= int(parts[1]) <= 50):
            return False, '次数要 1~50'
        return True, ''
    if verb in ('组合键', '组合'):
        ps = [p for p in rest.split('+') if p][:4]
        if ps and all(parse_key_text(p) for p in ps):
            return True, ''
        return False, '要写成 组合键:Ctrl+E'
    return False, '动作开头只支持: 按 / 长按 / 连点 / 点击 / 长按左键 / 组合键'


def _fidget_line_ok(line):
    """v1.12.6: 这一行是否真能被机器人执行(名称 => 动作 @ 间隔)"""
    if '=>' not in line:
        return False
    name, rest = line.split('=>', 1)
    if not name.strip() or '@' not in rest:
        return False
    spec, iv = rest.rsplit('@', 1)
    ok, _why = fidget_ok(spec)
    if not ok:
        return False
    try:
        v = float(iv.strip())
    except Exception:
        return False
    return 2 <= v <= 3600


def fidget_label():
    on = [k for k in ('A', 'B', 'D') if fidget_switch(FIDGET_SWITCH_KEYS[k])]
    rows = read_fidget()
    n = sum(1 for r in rows if _fidget_line_ok(r))
    bad = len(rows) - n
    builtin = ''.join(on) if on else 'A/B/D全关'
    return '内置%s·追踪队友·自定义%d条%s' % (builtin, n, '·坏行%d' % bad if bad else '')


def fidget_menu():
    """[17] 挂机机制: 内置串开关 + 自定义动作(增删改)"""
    while True:
        clear()
        hide_cursor(True)
        rows = read_fidget()
        print()
        print(c('  ┌──────────────────────────────────────────────────────────┐', 'cyan'))
        print(c('  │  挂机机制：内置反挂机动作可开关，也可以加自己的动作        │', 'cyan'))
        print(c('  │  追踪队友是内置核心动作，不能改、不能删                    │', 'cyan'))
        print(c('  └──────────────────────────────────────────────────────────┘', 'cyan'))
        print()
        if _last_action[0]:
            # v1.12.7: 子菜单里也要能看到「上次操作/出错提示」, 以前只有回主菜单才显示
            print('  ' + c(fit(_last_action[0], 54), 'yellow'))
            print()
            _last_action[0] = ''
        print('  ' + c('内置动作（局内每隔约 15 秒轮着做一条）', 'white'))
        for k, name, desc in FIDGET_BUILTIN:
            on = fidget_switch(FIDGET_SWITCH_KEYS[k])
            print('   ' + c(k, 'cyan') + ' ' + pad(name, 8)
                  + c('开 ' if on else '关 ', 'green' if on else 'dim') + c(desc, 'dim'))
        print('   ' + c('E', 'cyan') + ' ' + pad('追踪队友', 8)
              + c('固定开启', 'yellow') + c('自动朝队友走（内置核心，不可修改）', 'dim'))
        print(c('   （想看 A/B/D 里具体做哪些动作？按 [7]，或直接输入字母 A / B / D / E）', 'dim'))
        print()
        print('  ' + c('自定义动作（各自按自己的间隔执行，改完下一轮就生效）', 'white'))
        if rows:
            for i, r in enumerate(rows, 1):
                print('   ' + c('%d.' % i, 'cyan') + '  ' + r)
        else:
            print(c('   （还没有自定义动作，按 [2] 可以添加）', 'dim'))
        print()
        print('  ' + c('[1]', 'cyan') + ' 开关内置动作   '
              + c('[2]', 'cyan') + ' 添加   '
              + c('[3]', 'cyan') + ' 修改   '
              + c('[4]', 'cyan') + ' 删除   '
              + c('[5]', 'cyan') + ' 恢复默认   '
              + c('[6]', 'cyan') + ' 用记事本打开   '
              + c('[7]', 'cyan') + ' 看内置动作详情   '
              + c('[0]', 'cyan') + ' 返回')
        print()
        ans = _ask('  请输入序号后按回车（0~7）：').strip()
        if ans in ('0', ''):
            hide_cursor(False)
            _last_action[0] = '已返回主菜单（挂机机制：%s）' % fidget_label()
            return
        if len(ans) == 1 and ans.upper() in ('A', 'B', 'D', 'E'):
            fidget_detail(ans.upper())
            continue
        if ans == '7':
            k = _ask('  想看哪个内置动作？输入 A / B / D / E（回车=取消）：').strip().upper()
            if k not in ('A', 'B', 'D', 'E'):
                _last_action[0] = '只支持 A / B / D / E'
                time.sleep(0.8)
                continue
            fidget_detail(k)
            continue
        if ans == '1':
            k = _ask('  要开关哪个内置动作？输入 A / B / D（回车=取消）：').strip().upper()
            if k not in FIDGET_SWITCH_KEYS:
                _last_action[0] = '只支持 A / B / D（追踪队友 E 是核心动作，不能关）'
                time.sleep(0.9)
                continue
            key = FIDGET_SWITCH_KEYS[k]
            now_on = fidget_switch(key)
            save_conf_or_report(key, '0' if now_on else '1',
                                '内置动作 %s 已%s（下一串动作起生效）'
                                % (k, '关掉' if now_on else '打开'))
            time.sleep(0.6 if not now_on else 1.0)
            continue
        if ans in ('2', '3'):
            idx = None
            if ans == '3':
                if not rows:
                    _last_action[0] = '还没有自定义动作可改，用 [2] 添加'
                    time.sleep(0.8)
                    continue
                s = _ask('  输入要修改的序号（回车=取消）：').strip()
                if not s.isdigit() or not (1 <= int(s) <= len(rows)):
                    _last_action[0] = '序号不对，已取消'
                    time.sleep(0.6)
                    continue
                idx = int(s) - 1
            old = rows[idx] if idx is not None else ''
            old_name = old.split('=>')[0].strip() if '=>' in old else ''
            nm = _ask('  动作名称（例如 点左键）%s：' % ('，回车=不改' if old_name else '')).strip() or old_name
            if not nm:
                _last_action[0] = '名称不能空，已取消'
                time.sleep(0.6)
                continue
            old_spec = old.split('=>', 1)[1].split('@')[0].strip() if '=>' in old else ''
            spec = _ask('  动作（按:E / 长按:E:2 / 连点:E:3 / 点击:左键:3 / 长按左键:10 / 组合键:Ctrl+E）%s：'
                        % ('，回车=不改' if old_spec else '')).strip() or old_spec
            ok, why = fidget_ok(spec)
            if not ok:
                _last_action[0] = '动作写法不对：%s（例：连点:左键:3 / 长按:W:2）' % why
                time.sleep(1.3)
                continue
            old_iv = old.rsplit('@', 1)[1].strip() if '@' in old else ''
            iv = _ask('  每隔多少秒做一次？%s：' % ('[%s]' % old_iv if old_iv else '[20]')).strip() or old_iv or '20'
            try:
                iv_f = float(iv)
                if not (2 <= iv_f <= 3600):
                    raise ValueError
            except Exception:
                _last_action[0] = '间隔要写 2~3600 之间的秒数，已取消'
                time.sleep(1.1)
                continue
            line = '%s => %s @ %g' % (nm, spec, iv_f)
            if idx is None:
                rows = [r for r in rows if r.split('=>')[0].strip().lower() != nm.lower()]
                rows.append(line)
            else:
                rows[idx] = line
            if not write_fidget(rows):
                _last_action[0] = '写入 挂机动作.txt 失败(文件被占用?)'
                time.sleep(0.9)
                continue
            _last_action[0] = '已保存「%s」：%s，每 %g 秒做一次（下一轮生效，不用重启）' % (nm, spec, iv_f)
            time.sleep(0.9)
        elif ans == '4':
            if not rows:
                _last_action[0] = '没有自定义动作可删'
                time.sleep(0.6)
                continue
            s = _ask('  输入要删除的序号（回车=取消）：').strip()
            if not s.isdigit() or not (1 <= int(s) <= len(rows)):
                _last_action[0] = '序号不对，已取消'
                time.sleep(0.6)
                continue
            gone = rows.pop(int(s) - 1)
            if not write_fidget(rows):
                _last_action[0] = '写入 挂机动作.txt 失败(文件被占用?)'
                time.sleep(0.9)
                continue
            _last_action[0] = '已删除「%s」' % gone
            time.sleep(0.6)
        elif ans == '5':
            # v1.12.6: 恢复默认连内置开关一起复位(以前只删文件, 提示却说「A/B/D 全开」)
            try:
                if os.path.exists(FIDGET_FILE):
                    os.remove(FIDGET_FILE)
                failed = [k for k in FIDGET_SWITCH_KEYS
                          if not set_conf_one(FIDGET_SWITCH_KEYS[k], '1')]
                if failed:
                    _last_action[0] = '已清空自定义动作，但内置开关没复位成功: %s' % '/'.join(failed)
                else:
                    _last_action[0] = '已恢复默认：自定义动作清空，内置 A/B/D 全开 + 追踪队友'
            except Exception as e:
                _last_action[0] = '删除 挂机动作.txt 失败: %r' % (e,)
            time.sleep(0.9)
        elif ans == '6':
            try:
                if not os.path.exists(FIDGET_FILE):
                    write_fidget([])
                os.startfile(FIDGET_FILE)
                _last_action[0] = '已用记事本打开 挂机动作.txt（改完保存，机器人下一轮自动读）'
            except Exception as e:
                _last_action[0] = '打不开文件: %r（路径：%s）' % (e, FIDGET_FILE)
            time.sleep(0.9)


def words_menu():
    """[9] 自动点击词: 添加 / 删除 / 查看"""
    while True:
        clear()
        hide_cursor(True)
        words = read_words()
        print()
        print(c('  ┌──────────────────────────────────────────────────────┐', 'cyan'))
        print(c('  │  自动点击词：游戏里出现这些字，就点它所在的位置       │', 'cyan'))
        print(c('  └──────────────────────────────────────────────────────┘', 'cyan'))
        print()
        if words:
            for i, w in enumerate(words, 1):
                print('  ' + c('%d.' % i, 'cyan') + '  ' + w)
        else:
            print(c('  （现在是空的。比如可以添加：领取 / 跳过 / 开始）', 'dim'))
        print()
        print('  ' + c('[1]', 'cyan') + ' 添加一个字/一句话      '
              + c('[2]', 'cyan') + ' 删除一个      '
              + c('[0]', 'cyan') + ' 返回')
        print()
        ans = _ask('  请输入序号后按回车（0~2）：').strip()
        if ans in ('0', ''):
            hide_cursor(False)
            _last_action[0] = '已返回主菜单（自动点击词：%d 个）' % len(words)
            return
        if ans == '1':
            print()
            w = _ask('  输入要点击的文字（会整行匹配，例如 领取）：').strip()
            if not w:
                _last_action[0] = '没输入内容，已取消'
                time.sleep(0.5)
                continue
            if w in words:
                _last_action[0] = '「%s」已经在列表里了' % w
                time.sleep(0.6)
                continue
            if w.startswith('#'):
                _last_action[0] = '不能以 # 开头(会被当成注释)，换个词吧'
                time.sleep(0.6)
                continue
            words.append(w)
            if not write_words(words):
                words.pop()
                _last_action[0] = '写入 点击词.txt 失败(文件被占用?)'
                time.sleep(0.8)
                continue
            _last_action[0] = '已添加「%s」，现在就生效（不用重启机器人）' % w
            time.sleep(0.6)
        elif ans == '2':
            if not words:
                _last_action[0] = '列表是空的，没有可删除的词'
                time.sleep(0.6)
                continue
            print()
            idx = _ask('  输入要删除的序号（直接回车=取消）：').strip()
            if not idx.isdigit() or not (1 <= int(idx) <= len(words)):
                _last_action[0] = '序号不对，已取消'
                time.sleep(0.6)
                continue
            gone = words.pop(int(idx) - 1)
            if not write_words(words):
                words.append(gone)
                _last_action[0] = '写入 点击词.txt 失败(文件被占用?)'
                time.sleep(0.8)
                continue
            _last_action[0] = '已删除「%s」' % gone
            time.sleep(0.6)


def hotkey_check(seconds=10):
    """[11] 热键自检: 监听 10 秒, 看 F9/F10 能不能被系统读到(不开始/不停止挂机)"""
    print()
    print('  ' + c('热键自检', 'bold') + ' — 现在请连续按几次 ' + c('F10', 'yellow')
          + ' 和 ' + c('F9', 'yellow') + '（%d 秒）' % seconds)
    print('  ' + c('只监听, 不会开始/停止挂机；用来确认按键能不能被系统读到', 'dim'))
    got = False
    try:
        r = subprocess.run([sys.executable, BOT, '--hotkeys', str(int(seconds))],
                           cwd=BASE, capture_output=True, timeout=seconds + 20,
                           env=child_env(), creationflags=(0x08000000 if os.name == 'nt' else 0))
        out2 = (r.stdout or b'').decode('utf-8', 'ignore')
        for line in out2.splitlines():
            if ('识别到 F' in line) or ('HOTKEY' in line):
                print('  ' + c(line.strip(), 'green'))
                got = True
        if not got:
            print('  ' + c('十秒内没识别到 F9/F10。', 'yellow'))
            print('  ' + c('常见原因: ①按得太短(按住 0.2 秒再松) ②被输入法/加速器/录屏软件占用 ③笔记本 Fn 组合键', 'dim'))
    except Exception as e:
        print('  ' + c('自检失败: %r' % (e,), 'red'))
    print()


_INPUT_EOF = [False]


def _ask(prompt='  请输入序号后按回车（1~17）：'):
    """行输入: 你打的字会原样显示在屏幕上, 按回车才算数(不会误触)。"""
    try:
        sys.stdout.write(c(prompt, 'bold'))
        sys.stdout.flush()
        return input().strip()
    except EOFError:
        _INPUT_EOF[0] = True
        return ''
    except KeyboardInterrupt:
        raise


# ---------------------------------------------------------------- 检查更新
def _conf_flag(name, default=1):
    try:
        v = (read_conf() or {}).get(name)
        return int(v) if v not in (None, '') else default
    except Exception:
        return default


def _fmt_mb(n):
    try:
        return '%.1f MB' % (float(n) / 1048576.0)
    except Exception:
        return '?'


def auto_update_check(verbose=False):
    """启动时查一下 GitHub 最新 Release，有新版本就提示下载。
    成功返回提示字符串，失败/跳过返回 None（绝不阻塞启动超过 ~8 秒）。"""
    if updater is None:
        return None
    if _conf_flag('auto_update', 1) != 1:
        return None
    try:
        info = updater.latest(6)
    except Exception as e:
        if verbose:
            return '检查更新失败（网络/接口）：%s' % str(e)[:60]
        return None
    if not info or not info.get('version'):
        return None
    if not updater.is_newer(info['version'], VERSION):
        if verbose:
            return '已经是最新版本（%s）' % VERSION
        return None

    # 有新版本 → 提示
    print()
    print(c('  ╭' + '─' * 62 + '╮', 'yellow'))
    print(c('  │ ' + pad('发现新版本 ' + info['version'], 60) + ' │', 'yellow'))
    print(c('  ╰' + '─' * 62 + '╯', 'yellow'))
    print('  ' + c('当前版本 %s  →  最新版本 %s' % (VERSION, info['version']), 'white'))
    if info.get('asset'):
        print('  ' + c('安装包：%s（%s）' % (info['asset'], _fmt_mb(info.get('size'))), 'dim'))
    print('  ' + c('项目主页：' + (info.get('page') or REPO_URL), 'dim'))
    ans = _ask('  输入 y 回车 = 下载到「下载」文件夹并打开；直接回车 = 先不更新：').strip().lower()
    if ans not in ('y', 'yes', '是', '1'):
        _last_action[0] = '发现新版本 %s，已跳过（[14] 可再来一次）' % info['version']
        return None
    if not info.get('url'):
        return '新版本没有可下载的安装包，请手动去主页下载'
    return _do_download(info)


def _do_download(info):
    """带进度条下载 + 打开下载文件夹。返回提示字符串。"""
    total = int(info.get('size') or 0)
    last = [0]

    def cb(got, tot):
        tot = tot or total
        if not tot:
            return
        pct = int(got * 100 / tot)
        if pct == last[0]:
            return
        last[0] = pct
        bars = int(pct * 30 / 100)
        sys.stdout.write('\r  ' + c('下载中 [', 'dim') + '█' * bars + '░' * (30 - bars)
                         + c('] %3d%%  %s / %s' % (pct, _fmt_mb(got), _fmt_mb(tot)), 'dim'))
        sys.stdout.flush()

    print()
    try:
        path = updater.download(info['url'], info.get('asset') or 'fortnite-auto-helper_new.zip', progress=cb)
    except Exception as e:
        print()
        return '下载失败：%s（可去主页手动下载：%s）' % (str(e)[:60], info.get('page') or REPO_URL)
    print()
    ok = updater.verify_zip(path)
    d = os.path.dirname(path)
    try:
        if os.name == 'nt':
            os.startfile(d)          # 自动打开「下载」文件夹
    except Exception:
        pass
    _last_action[0] = ('已下载 %s（%s）→ 已打开下载文件夹' % (os.path.basename(path), '校验通过' if ok else '但校验没通过，请谨慎'))
    if not ok:
        return '下载完成但校验没通过：%s（建议重新下载或手动去主页下载）' % path
    return ('已下载到：%s\n  已帮你打开「下载」文件夹；退出本程序后解压覆盖即可（设置/按键不会丢）。'
            % path)


def update_menu():
    """菜单 [14]：手动检查更新。"""
    print()
    print(c('  正在检查 GitHub 最新版本…', 'dim'))
    msg = auto_update_check(verbose=True)
    print()
    if msg:
        print('  ' + c(msg, 'green'))
    print('  ' + c('项目主页：' + REPO_URL, 'dim'))
    time.sleep(1.0)


def main():
    # 这里不再改控制台缓冲区/窗口尺寸(以前那样会把窗口搞坏: 无法最小化/关闭/点击);
    # 开场画面会自己 ShowWindow 最大化一次, 播完立刻还原。
    session_mark()      # 「本次对局」从 0 开始算(总局数照旧累计)
    ocr_info()          # 启动时问一次本机 OCR 语言
    try:
        log_ui('===== 启动器启动 %s =====' % VERSION)
        log_ui('目录: %s' % BASE)
        log_ui('python: %s' % sys.executable)
        log_ui('日志文件: %s' % os.path.join(BASE, 'bot_log.txt'))
        log_ui('OCR: %r' % (ocr_info(),))
    except Exception:
        pass
    global _proc_holder
    if '--demo' in sys.argv:          # 只渲染一次界面(用于自检/截图)
        enable_ansi()
        set_title()
        render(_proc_holder[0])
        print()
        return
    enable_ansi()
    set_title()
    if '--anim' in sys.argv:            # 只看开场动画(自检/分享用)
        hide_cursor(True)
        play_intro(skipable=False)
        hide_cursor(False)
        print()
        return
    if '--no-anim' not in sys.argv:
        hide_cursor(True)
        play_intro()
        hide_cursor(False)
    if '--no-update-check' not in sys.argv:
        _um = auto_update_check()
        if _um:
            print('  ' + c(_um, 'green'))
    proc = None
    _proc_holder[0] = proc
    try:
        while True:
            render(proc)
            ans = _ask()
            if _INPUT_EOF[0]:
                _last_action[0] = '输入流已结束, 正在停止挂机并退出'
                stop_everything(proc)
                return
            if ans == '':
                _last_action[0] = '已刷新状态（回车可刷新；1 开始 / 2 停止 / 3 退出 / 4 改ID / 6 装OCR）'
                continue
            _last_action[0] = '收到输入 "%s"' % ans
            log_ui('用户输入: %s' % ans)
            if ans == '1':
                if bot_running(proc):
                    write_control('start')          # 键盘 F10 万一只被拦截, 走文件通道也能开始
                    _last_action[0] = '已经在运行了：已给机器人发「开始」命令'
                    continue
                if my_name() == DEFAULT_NAME:
                    print()
                    print(c('  你现在用的是作者测试号 “%s”，不是你的游戏名字。' % DEFAULT_NAME, 'red'))
                    print(c('  名字不对 → 认不到「你自己在局内还活着」→ 防挂机不会启动。', 'red'))
                    print(c('  结果：局内站着不动 → 可能被当成挂机踢掉，也可能触发游戏的休眠机制。', 'red'))
                    print(c('  建议先按 2 停、按 4 改成你的名字。仍要现在开始？输入 y 回车 = 开始，其它 = 取消', 'yellow'))
                    yn = _ask('  y/N：').strip().lower()
                    if yn != 'y':
                        _last_action[0] = '已取消：请先按 4 填写你自己的游戏ID'
                        time.sleep(0.4)
                        continue
                focused = focus_game_once()      # 只在这里切一次前台
                proc = start_bot()
                _proc_holder[0] = proc
                if proc is not None:
                    if focused:
                        _last_action[0] = '已开始挂机，已自动切到堡垒之夜窗口'
                    else:
                        _last_action[0] = '已开始挂机，但没找到游戏窗口（请手动点一下游戏）'
                    time.sleep(1.2)
                else:
                    _last_action[0] = '启动失败，看上面的提示'
            elif ans == '2':
                if bot_running(proc):
                    write_control('stop')           # 先礼貌停机(键盘 F9 失灵的备份通道)
                    p = _as_stoppable(proc)
                    for _ in range(25):             # 最多等 5 秒, 让它在动作串里也能读到 stop
                        if p is None or p.poll() is not None:
                            break
                        time.sleep(0.2)
                    stop_everything(proc)
                    _last_action[0] = '已停止挂机（窗口不用关，按 1 可再开）'
                else:
                    _last_action[0] = '当前没有在挂机，无需停止'
                proc = None
                _proc_holder[0] = proc
                time.sleep(0.6)
            elif ans == '4':
                print()
                print(c('  现在用的 ID：%s' % my_name(), 'white'))
                print(c('  输入你的游戏ID（堡垒之夜里的名字，要和左上角/左下角显示的完全一致）：', 'yellow'))
                nm = _ask('  新ID：').strip()
                if nm:
                    if save_name(nm):
                        _last_action[0] = '已把游戏ID改成 "%s"（已写入 玩家名字.txt；下次按 1 生效）' % nm
                    else:
                        _last_action[0] = '保存游戏ID失败(看上面的红字)，ID 没改成功'
                else:
                    _last_action[0] = '没输入内容，ID 保持不变'
                time.sleep(0.4)
            elif ans == '5':
                on = not lax_mode()
                if not save_lax(on):
                    _last_action[0] = '改宽松/严格模式失败(设置.txt 被占用?)，这次没生效'
                    time.sleep(0.6)
                    continue
                if on:
                    _last_action[0] = ('已切到宽松模式：认不到名字也照做反挂机（必要时用；正常请保持严格）。'
                               '对正在挂机的进程要按 2 停、再按 1 开始才生效')
                else:
                    _last_action[0] = ('已切回严格模式：必须看到左下角名字才做反挂机。'
                               '对正在挂机的进程要按 2 停、再按 1 开始才生效')
                time.sleep(0.4)
            elif ans == '7':
                print()
                print(c('  正在测「视角灵敏度」：需要游戏在局内、角色活着（顶部有罗盘刻度）。', 'yellow'))
                print(c('  它会自己把游戏切到前台、轻轻转两下视角再切回来。', 'dim'))
                try:
                    r = subprocess.run([sys.executable, BOT, '--calibrate'],
                                       cwd=BASE, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace',
                                       env=child_env(), timeout=120,
                                       creationflags=0x08000000)
                    out = ((r.stdout or '') + (r.stderr or '')).strip()
                    for ln in out.splitlines():
                        print(c('  ' + ln, 'white'))
                    _last_action[0] = ('视角校准完成：每度 %.1f 像素' % read_calib()
                                       if r.returncode == 0 else '校准没成功：需要在局内且活着时再试')
                except Exception as e:
                    print(c('  校准出错: %s' % e, 'red'))
                    _last_action[0] = '校准出错'
                time.sleep(1.0)
            elif ans == '6':
                print()
                _OCR_INFO.clear()                 # 每次都重查, 不吃上次的缓存
                _oi2 = ocr_info()
                if _oi2.get('zh'):
                    _last_action[0] = '本机已有中文OCR（%s），不用装' % (','.join(_oi2.get('langs') or []),)
                    time.sleep(0.5)
                    continue
                print(c('  这台电脑缺「中文OCR」，现在尝试一键安装：', 'yellow'))
                print(c('    · 马上会弹「用户账户控制(UAC)」→ 请点「是」', 'white'))
                print(c('    · 需要联网下载（几十 MB），黑窗口里能看到进度', 'white'))
                print(c('    · 装完可能需要重启程序才生效', 'white'))
                ok = install_ocr_flow()
                _OCR_INFO.clear()                 # 重新检测
                ocr_info()
                _last_action[0] = ('中文OCR 安装完成（若仍显示缺失，重启程序/重启电脑）'
                                   if ok else '没装成：已打开设置页，请按里面的说明手动安装')
                time.sleep(0.8)
            elif ans == '9':
                words_menu()
                continue
            elif ans == '10':
                keys_menu()
                continue
            elif ans == '12':
                squad_mode_menu()
            elif ans == '8':
                clear()
                hide_cursor(True)
                play_intro()
                hide_cursor(False)
                _last_action[0] = '开场动画播完了（按 8 可以再看一遍）'
                time.sleep(0.4)
            elif ans == '14':
                update_menu()
            elif ans == '15':
                after_match_menu()
            elif ans == '16':
                actions_menu()
                continue
            elif ans == '17':
                fidget_menu()
                continue
            elif ans == '13':
                import webbrowser
                try:
                    webbrowser.open(REPO_URL)
                    _last_action[0] = '已用浏览器打开项目主页：' + REPO_URL
                except Exception:
                    _last_action[0] = '打不开浏览器，请手动访问：' + REPO_URL
                time.sleep(0.6)
            elif ans == '3':
                # v1.11.4: 用户要求——按 3 立刻退出, 不再二次确认
                stop_everything(proc)
                clear()
                print()
                print(c('  已退出（已同时停掉挂机进程）。感谢使用', 'cyan'))
                print(c('  有问题联系 QQ ' + AUTHOR_QQ, 'cyan'))
                print()
                time.sleep(1.5)
                return
            else:
                if ans == '11':
                    hotkey_check()
                    _last_action[0] = '已做过热键自检'
                else:
                    _last_action[0] = '没有这个选项："%s"（请输入 1~17）' % ans
    except KeyboardInterrupt:
        stop_everything(proc)
        clear()
        print()
        print(c('  已退出（Ctrl+C）。有问题联系 QQ ' + AUTHOR_QQ, 'cyan'))
        print()
        time.sleep(1.5)
    finally:
        try:
            if bot_running(proc):
                stop_everything(proc)         # 兜底: 不管怎么退出, 都别把机器人单独留在后台
        except Exception:
            pass
        print()
        print(c('  窗口即将关闭；这是正常退出。想继续挂机就重新双击 启动.bat。', 'dim'))


def _wait_key():
    try:
        import msvcrt
    except ImportError:
        return input().strip()[:1]
    while True:
        time.sleep(0.4)
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch == '\x03':
                raise KeyboardInterrupt
            if ch in '123':
                return ch
        # 状态变了就刷新一下界面(比如从"已暂停"变成"战斗中")
        st = read_status()[0]
        if st != _last_state[0]:
            _last_state[0] = st
            render(_proc_holder[0])


_last_state = [None]


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n  已取消')
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            with open(os.path.join(BASE, 'bot_log.txt'), 'a', encoding='utf-8') as f:
                f.write('[%s] [UI] ***** 启动器异常 *****\n%s\n' % (time.strftime('%H:%M:%S'), tb))
        except Exception:
            pass
        print('\n  程序出错了, 详情已写入 bot_log.txt:')
        print(tb)
        try:
            input('  按回车退出...')
        except Exception:
            pass
