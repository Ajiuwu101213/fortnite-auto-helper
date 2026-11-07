# -*- coding: utf-8 -*-
"""运行时生成"开场大图"(全屏控制台尺寸), 供 launcher 播放。

   为什么运行时生成: 不同人的控制台全屏后列数/行数都不一样, 预生成死尺寸必然浪费屏幕。
   这里按当前控制台格子数现场画:
       braille 模式: 每字符 = 2x4 点  -> 分辨率是半块 ▀ 的 4 倍(推荐, 全屏很清晰)
       半边块模式:   每字符 = 1x2 像素 -> 方块更"实", 适合小窗口
   只用 Pillow(包里自带), 不需联网。
"""
import io
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

PAL = [(10, 10, 14), (34, 22, 10), (62, 40, 14), (92, 62, 20), (124, 86, 26),
       (156, 110, 30), (186, 138, 36), (210, 162, 44), (232, 184, 54),
       (248, 204, 74), (255, 222, 110), (255, 238, 168),
       (232, 232, 236), (206, 206, 214), (176, 176, 186), (140, 140, 150)]
BG = PAL[0]
GOLD, CREAM, GOLD2, DIM = PAL[9], PAL[11], PAL[7], PAL[15]

ALPHA = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUV"
DOT_BITS = [(0, 0, 0), (0, 1, 1), (0, 2, 2), (1, 0, 3), (1, 1, 4), (1, 2, 5), (0, 3, 6), (1, 3, 7)]


def _font(size):
    for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(r"C:\Windows\Fonts\\" + name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _load_logo(path, size):
    """读 logo(优先用自带透明通道抠底), 居中放进正方形黑底, 放大到 size²"""
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    alpha = im.getchannel("A")
    use_alpha = alpha.getextrema()[0] < 16      # 有透明像素 -> 直接当蒙版

    if use_alpha:
        m = max(w, h)
        sq = Image.new("RGBA", (m, m), (0, 0, 0, 0))
        sq.paste(im, ((m - w) // 2, (m - h) // 2))
        sq = sq.resize((size, size), Image.LANCZOS)
        out = Image.new("RGB", (size, size), BG)
        out.paste(sq, (0, 0), sq.split()[3])
        return ImageEnhance.Contrast(out).enhance(1.12)

    px = im.load()
    corners = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
    br = sum(c[0] for c in corners) // 4
    bgg = sum(c[1] for c in corners) // 4
    bb = sum(c[2] for c in corners) // 4
    out = Image.new("RGB", (w, h), BG)
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 40:
                continue
            if abs(r - br) + abs(g - bgg) + abs(b - bb) < 60:
                continue
            op[x, y] = (r, g, b)
    m = max(w, h)
    sq = Image.new("RGB", (m, m), BG)
    sq.paste(out, ((m - w) // 2, (m - h) // 2))
    return sq.resize((size, size), Image.LANCZOS)


def _fit_font(text, max_w, size, min_size=9):
    """从 size 往下试, 找能放进 max_w 的字号"""
    while size > min_size:
        f = _font(size)
        try:
            w = f.getlength(text)
        except Exception:
            w = len(text) * size * 0.6
        if w <= max_w:
            return f, size
        size -= 1
    return _font(min_size), min_size


def _compose(logo_path, px_w, px_h, title, subtitle, version, qq, author='阿舸AG', with_text=True):
    """拼一张 px_w x px_h 的图: 徽章 + 右侧/下方文字"""
    canvas = Image.new("RGB", (px_w, px_h), BG)
    dr = ImageDraw.Draw(canvas)
    if not with_text:                     # 只要徽章, 文字交给控制台真字输出(更清晰)
        emb = int(min(px_h * 0.96, px_w * 0.60))
        canvas.paste(_load_logo(logo_path, emb), ((px_w - emb) // 2, (px_h - emb) // 2))
        return canvas
    emb = int(min(px_h * 0.90, px_w * 0.46))
    x0 = max(0, int(px_w * 0.02))
    canvas.paste(_load_logo(logo_path, emb), (x0, int((px_h - emb) / 2)))

    tx = x0 + emb + int(px_w * 0.035)
    avail = px_w - tx - int(px_w * 0.02)
    if avail > px_h * 0.40:                      # 右侧竖排文字(宽屏)
        s1 = int(px_h * 0.20)
        f1, s1 = _fit_font(title, avail * 0.98, s1)
        s2 = max(9, int(s1 * 0.62))
        f2, s2 = _fit_font(subtitle, avail * 0.98, s2)
        s3 = max(8, int(s1 * 0.46))
        f3, s3 = _fit_font(version, avail * 0.60, s3)
        f4, s4 = _fit_font("作者 " + author, avail * 0.90, max(8, int(s1 * 0.40)))
        y = int(px_h * 0.09)
        dr.text((tx, y), title, font=f1, fill=GOLD)
        y += int(s1 * 1.20)
        dr.text((tx, y), subtitle, font=f2, fill=CREAM)
        y += int(s2 * 1.30)
        dr.text((tx, y), version, font=f3, fill=GOLD2)
        y += int(s3 * 1.35)
        dr.text((tx, y), "作者 " + author, font=f4, fill=CREAM)
        y += int(s4 * 1.35)
        f5, s5 = _fit_font("QQ " + qq, avail * 0.90, max(8, int(s1 * 0.34)))
        if y + s5 < px_h * 0.98:
            dr.text((tx, y), "QQ " + qq, font=f5, fill=DIM)
    else:                                        # 窄屏: 文字放徽章下面
        f1, s1 = _fit_font(title, px_w * 0.92, max(10, int(px_h * 0.12)))
        dr.text((int(px_w * 0.04), px_h - int(px_h * 0.20)), title, font=f1, fill=GOLD)
        f2, _ = _fit_font("v" + version + "   QQ " + qq, px_w * 0.92, max(8, int(s1 * 0.6)))
        dr.text((int(px_w * 0.04), px_h - int(px_h * 0.09)), "v" + version + "   QQ " + qq, font=f2, fill=DIM)
    return canvas


def _near(c):
    best, bi = 1 << 30, 0
    for i, (pr, pg, pb) in enumerate(PAL):
        d = (c[0] - pr) ** 2 + (c[1] - pg) ** 2 + (c[2] - pb) ** 2
        if d < best:
            best, bi = d, i
    return bi


def _xterm(c):
    r, g, b = c
    best, bd = 16, 1 << 30
    for i in range(16, 256):
        if i < 232:
            n = i - 16
            cr, cg, cb = (n // 36) * 51, ((n % 36) // 6) * 51, (n % 6) * 51
        else:
            cr = cg = cb = 8 + (i - 232) * 10
        d = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if d < bd:
            bd, best = d, i
    return best


XTERM = [_xterm(c) for c in PAL]


def build_braille(logo_path, cols, rows, title, subtitle, version, qq):
    """返回 (PAT, COL): 每行 cols 个盲文字符 + 每格颜色代号"""
    px_w, px_h = cols * 2, rows * 4
    canvas = _compose(logo_path, px_w, px_h, title, subtitle, version, qq)
    px = canvas.load()
    # 先把颜色归到 12 色调色板(去掉压缩噪色)
    grid = [[_near(px[x, y]) for x in range(px_w)] for y in range(px_h)]
    palette = {}
    PAT, COL = [], []
    for cy in range(rows):
        pat, col = [], []
        for cx in range(cols):
            bits = 0
            seen = {}
            for dx, dy, bn in DOT_BITS:
                X, Y = cx * 2 + dx, cy * 4 + dy
                if X >= px_w or Y >= px_h:
                    continue
                ci = grid[Y][X]
                if ci != 0 and sum(PAL[ci]) > 40:
                    bits |= (1 << bn)
                    seen[ci] = seen.get(ci, 0) + 1
            if bits == 0:
                pat.append(' ')
                col.append(ALPHA[0])
            else:
                dom = max(seen.items(), key=lambda kv: kv[1])[0]
                if dom not in palette:
                    palette[dom] = len(palette)
                pat.append(chr(0x2800 + bits))
                col.append(ALPHA[palette[dom]])
        PAT.append(''.join(pat))
        COL.append(''.join(col))
    order = [None] * len(palette)
    for ci, idx in palette.items():
        order[idx] = XTERM[ci]
    return PAT, COL, order


def build_blocks(logo_path, cols, rows, title, subtitle, version, qq):
    """半块 ▀ 版: 返回 (TOP, BOT, order)"""
    px_w, px_h = cols, rows * 2
    canvas = _compose(logo_path, px_w, px_h, title, subtitle, version, qq)
    px = canvas.load()
    grid = [[_near(px[x, y]) for x in range(px_w)] for y in range(px_h)]
    used = sorted({ci for row in grid for ci in row})
    mapping = {ci: i for i, ci in enumerate(used)}
    TOP, BOT = [], []
    for cy in range(rows):
        TOP.append(''.join(ALPHA[mapping[grid[cy * 2][cx]]] for cx in range(px_w)))
        BOT.append(''.join(ALPHA[mapping[grid[cy * 2 + 1][cx]]] for cx in range(px_w)))
    order = [XTERM[ci] for ci in used]
    return TOP, BOT, order


def build_half(logo_path, cols, rows, title, subtitle, version, qq):
    """方格子版: 每字符 = 上下两个实心方块(用 ▀ + 前景/背景色), 单元格 100% 填满

    返回 (TOP, BOT, order):
      TOP[y][x] / BOT[y][x] 是色板代号(用 ALPHA 表示), order 是色板 -> xterm256
      渲染: \x1b[38;5;<top>;48;5;<bot>m▀
    """
    px_w, px_h = cols, rows * 2
    canvas = _compose(logo_path, px_w, px_h, title, subtitle, version, qq)
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.5)
    px = canvas.load()
    grid = [[_near(px[x, y]) for x in range(px_w)] for y in range(px_h)]
    used = sorted({ci for row in grid for ci in row})
    mapping = {ci: i for i, ci in enumerate(used)}
    TOP, BOT = [], []
    for cy in range(rows):
        TOP.append(''.join(ALPHA[mapping[grid[cy * 2][cx]]] for cx in range(px_w)))
        BOT.append(''.join(ALPHA[mapping[grid[cy * 2 + 1][cx]]] for cx in range(px_w)))
    order = [XTERM[ci] for ci in used]
    return TOP, BOT, order


def build_quad(logo_path, cols, rows, title, subtitle, version, qq, author='阿舸AG', with_text=True):
    """高分辨率版(相当于给图案换"1080p 材质包"):

    每个字符 = 2x4 个点(盲文), **再加一层实心背景色** —— 所以既没有缝隙(格子 100% 填满),
    分辨率又是方块版的 8 倍(2 宽 x 4 高)。
    返回 (PAT, FG, BG, order):
        PAT[y][x] 盲文字符(' ' 表示这格没有细节点)
        FG/BG[y][x] 前景/背景色板代号
    """
    px_w, px_h = cols * 2, rows * 4
    canvas = _compose(logo_path, px_w, px_h, title, subtitle, version, qq, author, with_text)
    px = canvas.load()
    grid = [[_near(px[x, y]) for x in range(px_w)] for y in range(px_h)]

    used = set()
    cells = []
    for cy in range(rows):
        row = []
        for cx in range(cols):
            sub = [grid[cy * 4 + dy][cx * 2 + dx] for dx, dy, _bn in DOT_BITS]
            cnt = {}
            for ci in sub:
                cnt[ci] = cnt.get(ci, 0) + 1
            base = max(cnt.items(), key=lambda kv: kv[1])[0]
            bits = 0
            fgcnt = {}
            for (dx, dy, bn), ci in zip(DOT_BITS, sub):
                if ci != base:
                    bits |= (1 << bn)
                    fgcnt[ci] = fgcnt.get(ci, 0) + 1
            fg = max(fgcnt.items(), key=lambda kv: kv[1])[0] if fgcnt else base
            row.append((bits, fg, base))
            used.add(fg)
            used.add(base)
        cells.append(row)

    used = sorted(used)
    mapping = {ci: i for i, ci in enumerate(used)}
    PAT, FG, BG = [], [], []
    for row in cells:
        PAT.append(''.join(' ' if bits == 0 else chr(0x2800 + bits) for bits, _fg, _bg in row))
        FG.append(''.join(ALPHA[mapping[fg]] for _bits, fg, _bg in row))
        BG.append(''.join(ALPHA[mapping[bg]] for _bits, _fg, bg in row))
    order = [XTERM[ci] for ci in used]
    return PAT, FG, BG, order
