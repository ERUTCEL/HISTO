"""기록물 연구 데스크톱용 공통 테마.

밝은 회백 바탕 · 흰 사이드바 · 인디고 강조 · 낙관(붉은 도장) 로고.
Tkinter로 그릴 수 있는 평면 카드, 1px 선, 알약형 배지만 사용합니다.
"""
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ── 색상 토큰 ─────────────────────────────────────────────
# 절제된 따뜻한 중성색. 창 바탕은 맑은 미색 회색, 작업 패널은 흰색, 원문은 아주 옅은 종이색.
# 강조색(먹청)은 핵심 동작과 선택 상태에만. 판정은 녹(관련)·주(무관).
BG = '#F6F5F2'            # 창 바탕
SURFACE_ALT = '#EFEEEA'   # 상태 표시줄
CARD = '#FFFFFF'          # 작업 패널·목록·입력칸
PAPER = '#FFFEFB'         # 원문 읽기
FIELD = '#FFFFFF'
HEAD = '#FAF9F7'          # 표 머리·패널 머리줄
ANNOT = '#F5F2EB'         # 주석·안내 단
ROW_ALT = '#FAF9F6'       # 표 줄무늬
LINE = '#E5E2DC'          # 패널 테두리·구분선
LINE_SOFT = '#EFEDE8'
LINE_STRONG = '#CDC8BF'   # 입력칸 테두리
INK = '#1E1D1A'
MUTED = '#6A665F'
FAINT = '#9A968E'
NAV = '#EFEEEA'           # 사이드바
NAV_HOVER = '#E6E4DF'
NAV_ACTIVE = '#FFFFFF'
NAV_TEXT = '#4A4741'
NAV_MUTED = '#8C887F'
NAV_LINE = '#E1DED8'
NAV_STRONG = '#1E1D1A'
ACCENT = '#2C4F86'        # 먹청
ACCENT_HOVER = '#244373'
ACCENT_PRESS = '#1B355D'
ACCENT_PALE = '#EBF0F7'
ACCENT_SOFT = '#DCE5F2'   # 목록 선택 줄
GOLD = ACCENT
SEAL = '#B0442E'          # 주색 — 무관 판정·경고·로고
OK = '#3F8455'
OK_PALE = '#E8F2EA'
OK_INK = '#2E6A41'
WARN_PALE = '#F7EEDC'
WARN_INK = '#8A5A12'
BUSY = '#C08325'
NEUTRAL_PALE = '#F0EEEA'
HIT = '#F6E7A8'           # 찾은 낱말

# 이전 버전 이름 호환
GREEN = ACCENT
PALE = ACCENT_PALE
BORDER = LINE

BADGES = {
    'full': (OK_PALE, OK_INK),
    'catalog': (ACCENT_PALE, ACCENT),
    'official': (WARN_PALE, WARN_INK),
    'neutral': (NEUTRAL_PALE, MUTED),
    'complete': (OK_PALE, OK_INK),
    'pending': (NEUTRAL_PALE, MUTED),
    'failed': ('#F5E3DE', SEAL),
}

SCALE = 1.0
FONTS = {}
FAMILY = '맑은 고딕'
FAMILY_SEMI = None
MONO = 'Consolas'
# 제목·원문 읽기용 부리 글꼴(마루부리, 앱에 내장). (글꼴 이름, 굵기)
SERIF = (None, 'normal')
SERIF_BOLD = (None, 'bold')


def resource_dir():
    """PyInstaller 실행파일이면 압축이 풀린 폴더, 아니면 이 파일 폴더."""
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def _prepare_platform():
    """Tk 창을 만들기 전에 한 번: 선명한 고해상도 표시와 내장 글꼴 등록."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        for path in sorted((resource_dir() / 'fonts').glob('*.otf')):
            ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)  # FR_PRIVATE
    except Exception:
        pass


_prepare_platform()


def px(value):
    """96dpi 기준 픽셀을 현재 화면 배율에 맞춥니다."""
    return int(round(value * SCALE))


def fit_geometry(window, width, height, min_width, min_height):
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    w, h = min(px(width), sw - 40), min(px(height), sh - 90)
    window.geometry(f'{w}x{h}')
    window.minsize(min(px(min_width), w), min(px(min_height), h))


def _font(root, name, family, size, weight='normal'):
    full = 'rc_' + name
    # Windows에서 마루부리 굵은 글씨는 따로 이름이 있는 글꼴('MaruBuriOTF Bold') — 가짜 굵게 대신 진짜 굵은 글꼴
    if weight == 'bold' and family == SERIF[0] and SERIF_BOLD[0] and SERIF_BOLD[1] == 'normal':
        family, weight = SERIF_BOLD[0], 'normal'
    try:
        f = tkfont.Font(root=root, name=full, family=family, size=size, weight=weight, exists=False)
    except tk.TclError:
        f = tkfont.nametofont(full, root=root)
        f.configure(family=family, size=size, weight=weight)
    FONTS[name] = f
    return f


def _pick_family(root):
    global FAMILY, FAMILY_SEMI, MONO, SERIF, SERIF_BOLD
    families = set(tkfont.families(root))
    # 마루부리 이름: Windows는 굵기마다 다른 이름이고, 한국어 Windows는 한국어 이름으로 알려 줍니다
    # ('마루 부리OTF 중간 / 조금굵은 / 굵은'). 리눅스·맥은 한 이름('MaruBuriOTF') + 굵기.
    def first(*names):
        return next((n for n in names if n in families), None)
    regular = first('MaruBuriOTF Regular', '마루 부리OTF 중간')
    semi_name = first('MaruBuriOTF SemiBold', '마루 부리OTF 조금굵은')
    bold = first('MaruBuriOTF Bold', '마루 부리OTF 굵은')
    single = first('MaruBuriOTF', '마루 부리OTF')
    if regular:
        SERIF = (regular, 'normal')
    elif single:
        SERIF = (single, 'normal')
    if bold:
        SERIF_BOLD = (bold, 'normal')
    elif single:
        SERIF_BOLD = (single, 'bold')
    for name in ('Pretendard', 'Pretendard Variable', '맑은 고딕', 'Malgun Gothic', 'Noto Sans CJK KR', 'Noto Sans KR'):
        if name in families:
            FAMILY = name
            break
    FAMILY_SEMI = 'Pretendard SemiBold' if 'Pretendard SemiBold' in families else None
    if SERIF[0]:   # 마루부리가 있으면 앱 전체(버튼·표·입력칸 포함)를 마루부리로
        FAMILY = SERIF[0]
        FAMILY_SEMI = semi_name
    for name in ('D2Coding', 'Consolas', 'Cascadia Mono', 'DejaVu Sans Mono'):
        if name in families:
            MONO = name
            break


_CHECK_IMAGES = {}


def _mix(a, b, t):
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _check_image(root, size, gap, fill, border, mark):
    """체크박스 상자를 직접 그립니다(4x4 표본으로 부드럽게). 오른쪽 gap은 투명."""
    img = tk.PhotoImage(master=root, width=size + gap, height=size)
    pts = [(0.24, 0.52), (0.43, 0.71), (0.77, 0.32)]
    thick = max(1.6, size * 0.13)

    def dist(px_, py_, a, b):
        ax, ay, bx, by = a[0] * size, a[1] * size, b[0] * size, b[1] * size
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px_ - ax) * dx + (py_ - ay) * dy) / (dx * dx + dy * dy)))
        return ((px_ - ax - t * dx) ** 2 + (py_ - ay - t * dy) ** 2) ** 0.5

    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            color = border if x in (0, size - 1) or y in (0, size - 1) else fill
            if mark and 0 < x < size - 1 and 0 < y < size - 1:
                hit = 0
                for sy in range(4):
                    for sx in range(4):
                        X, Y = x + (sx + 0.5) / 4, y + (sy + 0.5) / 4
                        if min(dist(X, Y, pts[0], pts[1]), dist(X, Y, pts[1], pts[2])) <= thick / 2:
                            hit += 1
                color = _mix(fill, mark, hit / 16)
            row.append(color)
        rows.append('{' + ' '.join(row) + '}')
    img.put(' '.join(rows), to=(0, 0))
    return img


def _tab_image(root, fill, border, left, top, right, size=24, bottom=0):
    """탭 바탕(9칸 늘리기용)."""
    img = tk.PhotoImage(master=root, width=size, height=size)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            edge = y < top or x < left or x >= size - right or y >= size - bottom
            row.append(border if edge else fill)
        rows.append('{' + ' '.join(row) + '}')
    img.put(' '.join(rows), to=(0, 0))
    return img


_TAB_IMAGES = {}


def _install_tabs(root, style):
    """밑줄 탭: 고른 탭은 파란 굵은 밑줄(2px), 나머지는 회색 1px 밑줄."""
    thick = max(2, px(2))
    imgs = _TAB_IMAGES.setdefault(str(root.tk), {
        'normal': _tab_image(root, BG, LINE, 0, 0, 0, bottom=1),
        'active': _tab_image(root, BG, LINE_STRONG, 0, 0, 0, bottom=1),
        'selected': _tab_image(root, BG, ACCENT, 0, 0, 0, bottom=thick),
    })
    try:
        style.element_create('Histro.Notebook.tab', 'image', imgs['normal'],
                             ('selected', imgs['selected']), ('active', imgs['active']),
                             border=(1, 1, 1, thick + 1), sticky='nsew')
    except tk.TclError:
        return   # 이미 만든 경우
    style.layout('TNotebook.Tab', [('Histro.Notebook.tab', {'sticky': 'nswe', 'children': [
        ('Notebook.padding', {'side': 'top', 'sticky': 'nswe', 'children': [
            ('Notebook.label', {'side': 'top', 'sticky': ''})]})]})])



# ── 둥근 모서리(작은 반경) — 버튼·입력칸·카드 ───────────────────
# Tk에는 둥근 모서리가 없어 PNG(투명도 포함)를 직접 만들어 9칸 늘리기 이미지로 씁니다.
RADIUS = 5          # 컨트롤 모서리(패널은 8)
_RR = {}


def _png(w, h, pixel):
    import zlib
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(pixel(x, y))

    def chunk(tag, data):
        return len(data).to_bytes(4, 'big') + tag + data + (zlib.crc32(tag + data) & 0xffffffff).to_bytes(4, 'big')
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', w.to_bytes(4, 'big') + h.to_bytes(4, 'big') + bytes((8, 6, 0, 0, 0)))
            + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b''))


def _rgb(c):
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


def _round_rect(root, w, h, r, fill, border=None, width=1, outside=None):
    """둥근 사각형 이미지. outside가 없으면 모서리 바깥은 투명."""
    key = (str(root.tk), w, h, r, fill, border, width, outside)
    if key in _RR:
        return _RR[key]
    import base64
    f, b = _rgb(fill), _rgb(border or fill)
    o = _rgb(outside) if outside else None
    n = 4

    def pixel(x, y):
        cov = edge = 0
        for sy in range(n):
            for sx in range(n):
                X, Y = x + (sx + .5) / n, y + (sy + .5) / n
                cx, cy = min(max(X, r), w - r), min(max(Y, r), h - r)
                d = ((X - cx) ** 2 + (Y - cy) ** 2) ** .5
                if d <= r:
                    cov += 1
                    inner = r - width
                    dx, dy = min(X, w - X), min(Y, h - Y)
                    if d > inner or dx < width or dy < width:
                        edge += 1
        t = edge / cov if cov else 0
        col = [f[i] + (b[i] - f[i]) * t for i in range(3)]
        a = cov / (n * n)
        if o:
            return [round(o[i] + (col[i] - o[i]) * a) for i in range(3)] + [255]
        return [round(v) for v in col] + [round(255 * a)]
    img = tk.PhotoImage(master=root, data=base64.b64encode(_png(w, h, pixel)))
    _RR[key] = img
    return img


def _chevron(root, fill, color, w=22, h=16):
    key = (str(root.tk), 'chev', fill, color)
    if key in _RR:
        return _RR[key]
    import base64
    f, c = _rgb(fill), _rgb(color)
    pts = [(w * .30, h * .38), (w * .48, h * .62), (w * .66, h * .38)]

    def dist(X, Y, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((X - a[0]) * dx + (Y - a[1]) * dy) / (dx * dx + dy * dy)))
        return ((X - a[0] - t * dx) ** 2 + (Y - a[1] - t * dy) ** 2) ** .5

    def pixel(x, y):
        hit = 0
        for sy in range(4):
            for sx in range(4):
                X, Y = x + (sx + .5) / 4, y + (sy + .5) / 4
                if min(dist(X, Y, pts[0], pts[1]), dist(X, Y, pts[1], pts[2])) <= 0.8:
                    hit += 1
        t = hit / 16
        return [round(f[i] + (c[i] - f[i]) * t) for i in range(3)] + [255]
    img = tk.PhotoImage(master=root, data=base64.b64encode(_png(w, h, pixel)))
    _RR[key] = img
    return img


# 버튼·입력칸은 모서리 바깥을 '스타일 바탕색'으로 칠합니다. 그래서 흰 카드 위의 위젯은
# 'Card.' 스타일로 자동으로 바꿔 모서리가 카드 색과 맞게 합니다.
_LABEL_KIDS = [('Button.padding', {'sticky': 'nswe', 'children': [('Button.label', {'sticky': 'nswe'})]})]
_TOOL_KIDS = [('Toolbutton.padding', {'sticky': 'nswe', 'children': [('Toolbutton.label', {'sticky': 'nswe'})]})]
# 그림 테두리를 쓰는 스타일 → (테두리 그림 이름, 안쪽)
_SHAPES = {
    'TButton': ('RC.Button.border', _LABEL_KIDS), 'Large.TButton': ('RC.Button.border', _LABEL_KIDS),
    'Outline.TButton': ('RC.Button.border', _LABEL_KIDS),
    'Primary.TButton': ('RC.Primary.border', _LABEL_KIDS), 'PrimarySm.TButton': ('RC.Primary.border', _LABEL_KIDS),
    'Tool.TButton': ('RC.Tool.border', _LABEL_KIDS),
    'Seg.Toolbutton': ('RC.Seg.border', _TOOL_KIDS), 'Judge.Toolbutton': ('RC.JudgeOk.border', _TOOL_KIDS),
    'JudgeNo.Toolbutton': ('RC.JudgeNo.border', _TOOL_KIDS),
}
_VARIANTS = set()


def _variant(root, style, bg, prefix):
    """바탕색 bg 위에 놓일 버튼·입력칸용 그림 묶음과 스타일(prefix + 원래 이름)을 만듭니다."""
    key = (str(root.tk), bg)
    suffix = '' if not prefix else '.' + bg[1:]
    R, S = px(RADIUS), px(RADIUS) * 2 + 6
    rr = lambda fill, border=None: _round_rect(root, S, S, R, fill, border, 1, bg)
    if 'RC.Combo.arrow' not in style.element_names():
        style.element_create('RC.Combo.arrow', 'image', _chevron(root, CARD, MUTED),
                             ('disabled', _chevron(root, FIELD, LINE_STRONG)), sticky='')
    if key not in _VARIANTS:
        _VARIANTS.add(key)
        elements = {
            'RC.Button.border': (rr(CARD, LINE_STRONG), [('disabled', rr(NEUTRAL_PALE, LINE)), ('pressed', rr(LINE_SOFT, MUTED)),
                                 ('focus', rr(CARD, ACCENT)), ('active', rr(HEAD, MUTED))]),
            'RC.Primary.border': (rr(ACCENT), [('disabled', rr('#9DAAC0')), ('pressed', rr(ACCENT_PRESS)),
                                  ('focus', rr(ACCENT, ACCENT_PRESS)), ('active', rr(ACCENT_HOVER))]),
            'RC.Seg.border': (rr(CARD, LINE_STRONG), [('disabled', rr(NEUTRAL_PALE, LINE)), ('selected', rr(ACCENT_PALE, ACCENT)),
                              ('active', rr(HEAD, MUTED))]),
            'RC.JudgeOk.border': (rr(CARD, LINE_STRONG), [('selected', rr(OK_INK, OK_INK)), ('active', rr(OK_PALE, OK_INK))]),
            'RC.JudgeNo.border': (rr(CARD, LINE_STRONG), [('selected', rr(SEAL, SEAL)), ('active', rr('#F5E3DE', SEAL))]),
            'RC.Tool.border': (rr(bg, bg), [('pressed', rr(LINE, LINE)), ('active', rr(NEUTRAL_PALE, LINE))]),
            'RC.Entry.field': (rr(CARD, LINE_STRONG), [('disabled', rr(FIELD, LINE)), ('readonly', rr(FIELD, LINE)),
                               ('focus', rr(CARD, ACCENT)), ('hover', rr(CARD, MUTED))]),
            'RC.Combo.field': (rr(CARD, LINE_STRONG), [('disabled', rr(FIELD, LINE)), ('focus', rr(CARD, ACCENT)),
                               ('hover', rr(CARD, MUTED))]),
        }
        for name, (img, states) in elements.items():
            if name + suffix not in style.element_names():
                style.element_create(name + suffix, 'image', img, *states, border=R + 2, padding=(px(2), px(1)), sticky='nsew')
    for base, (el, kids) in _SHAPES.items():
        style.layout(prefix + base, [(el + suffix, {'sticky': 'nswe', 'children': kids})])
    entry = [('RC.Entry.field' + suffix, {'sticky': 'nswe', 'children': [
        ('Entry.padding', {'sticky': 'nswe', 'children': [('Entry.textarea', {'sticky': 'nswe'})]})]})]
    style.layout(prefix + 'TEntry', entry)
    style.layout(prefix + 'Large.TEntry', entry)
    style.layout(prefix + 'TCombobox', [('RC.Combo.field' + suffix, {'sticky': 'nswe', 'children': [
        ('RC.Combo.arrow', {'side': 'right', 'sticky': 'e'}),
        ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
    for base in ('Link.TButton', 'Quiet.TButton'):
        style.layout(prefix + base, _LABEL_KIDS)


FITTED = ('TButton', 'Primary.TButton', 'PrimarySm.TButton', 'Large.TButton', 'Outline.TButton', 'Link.TButton', 'Quiet.TButton', 'Tool.TButton',
          'Seg.Toolbutton', 'Judge.Toolbutton', 'JudgeNo.Toolbutton', 'TEntry', 'Large.TEntry', 'TCombobox', 'TCheckbutton', 'Source.TCheckbutton')


def _parent_bg(widget):
    parent = widget.master
    while parent is not None:
        try:
            return str(parent.cget('bg'))
        except tk.TclError:
            try:
                st = parent.cget('style') or parent.winfo_class()
                bg = ttk.Style(widget).lookup(st, 'background')
                if bg:
                    return str(bg)
            except tk.TclError:
                pass
        parent = parent.master
    return BG


_FITTED_DONE = set()


def _fit(event):
    """위젯 모서리 바깥 색을 부모 바탕색에 맞춥니다(BG가 아니면 'BgRRGGBB.' 스타일). 위젯마다 한 번만."""
    w = event.widget
    if getattr(w, '_fitted', False):
        return
    try:
        w._fitted = True
        style = str(w.cget('style')) or w.winfo_class()
    except (tk.TclError, AttributeError):
        return
    base = style.split('.', 1)[1] if style.startswith(('Card.', 'Bg')) and '.' in style else style
    if base not in FITTED:
        return
    bg = _parent_bg(w).upper()
    if not bg.startswith('#') or len(bg) != 7:
        return
    want = base if bg == BG.upper() else f'Bg{bg[1:]}.{base}'
    if want != base and bg not in _FITTED_DONE:
        st = ttk.Style(w)
        _variant(w._root(), st, bg, f'Bg{bg[1:]}.')
        for b in FITTED:
            st.configure(f'Bg{bg[1:]}.{b}', background=bg)
        _FITTED_DONE.add(bg)
    if want != style:
        w.configure(style=want)


def semi(root, name, size):
    """세미볼드 글꼴(없으면 굵게)."""
    if FAMILY_SEMI:
        return _font(root, name, FAMILY_SEMI, size)
    return _font(root, name, FAMILY, size, 'bold')


_THEMED = set()


def apply_theme(root):
    # 스타일·글꼴은 프로그램 전체가 공유합니다. 결과 창 등 새 창을 열 때마다 다시 만들지 않습니다.
    if str(root.tk) in _THEMED:
        root.configure(bg=BG)
        return ttk.Style(root)
    global SCALE
    try:
        SCALE = max(1.0, root.winfo_fpixels('1i') / 96.0) if os.name == 'nt' else 1.0
    except tk.TclError:
        SCALE = 1.0
    _pick_family(root)
    root.configure(bg=BG)

    for named in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont', 'TkCaptionFont', 'TkTooltipFont'):
        try:
            tkfont.nametofont(named, root=root).configure(family=FAMILY, size=10)
        except tk.TclError:
            pass
    body = _font(root, 'body', FAMILY, 10)
    small = _font(root, 'small', FAMILY, 9)
    serif, serif_w = SERIF if SERIF[0] else (FAMILY, 'normal')
    serif_b, serif_bw = SERIF_BOLD if SERIF_BOLD[0] else (FAMILY, 'bold')
    _font(root, 'reading', serif, 11, serif_w)
    _font(root, 'reading_b', serif_b, 11, serif_bw)
    _font(root, 'small_b', FAMILY, 9, 'bold')
    _font(root, 'input', FAMILY, 11)
    _font(root, 'input_lg', FAMILY, 14)
    _font(root, 'h1', serif_b, 19, serif_bw)
    _font(root, 'h2', FAMILY, 12, 'bold')
    _font(root, 'h3', FAMILY, 11, 'bold')
    _font(root, 'title', serif_b, 15, serif_bw)
    _font(root, 'metric', FAMILY, 19, 'bold')
    _font(root, 'brand', FAMILY, 13, 'bold')
    _font(root, 'seal', FAMILY, 17, 'bold')
    _font(root, 'mono', MONO, 9)
    label = semi(root, 'label', 10)
    semi(root, 'label_sm', 9)
    btn = semi(root, 'button', 10)
    root.option_add('*TCombobox*Listbox.font', body)
    root.option_add('*TCombobox*Listbox.background', CARD)
    root.option_add('*TCombobox*Listbox.foreground', INK)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', '#FFFFFF')
    root.option_add('*TCombobox*Listbox.borderWidth', 0)

    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=body, background=BG, foreground=INK, bordercolor=LINE,
                    lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground='#FFFFFF', insertcolor=INK)
    style.configure('TFrame', background=BG)
    style.configure('Card.TFrame', background=CARD)
    style.configure('Field.TFrame', background=FIELD)
    style.configure('TLabel', background=BG, foreground=INK)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Small.TLabel', foreground=MUTED, font=small)
    style.configure('Card.TLabel', background=CARD)
    style.configure('CardMuted.TLabel', background=CARD, foreground=MUTED, font=small)
    style.configure('CardLabel.TLabel', background=CARD, foreground=INK, font=label)
    style.configure('Title.TLabel', font=FONTS['h1'])
    style.configure('Section.TLabel', font=FONTS['h2'], background=CARD)
    style.configure('Number.TLabel', font=FONTS['metric'], background=CARD, foreground=INK)
    style.configure('NumberAccent.TLabel', font=FONTS['metric'], background=CARD, foreground=ACCENT)

    # 명령의 위계: 주요(먹청 채움, 화면에 하나) · 보조(종이 + 1px 선, 작게) · 도구(상자 없음, 도구줄) · 글자 링크.
    # 모서리는 반경 2 — 웹 카드가 아니라 데스크톱 컨트롤.
    # 둥근 모서리 그림은 '불투명'하게(모서리 바깥을 바탕색으로 칠해서) 만듭니다. 반투명 그림은 Tk가 그릴 때마다
    # 바탕과 섞느라 아주 느려집니다(앱 시작 6초 → 0.9초). 그래서 바탕색마다 그림 묶음을 따로 둡니다(_variant).
    _variant(root, style, BG, '')
    if 'RC.Combo.arrow' not in style.element_names():
        style.element_create('RC.Combo.arrow', 'image', _chevron(root, CARD, MUTED),
                             ('disabled', _chevron(root, FIELD, LINE_STRONG)), sticky='')

    pad = (px(14), px(6))
    style.configure('TButton', padding=pad, background=BG, foreground=INK, font=btn, anchor='center')
    style.map('TButton', foreground=[('disabled', FAINT)])
    style.configure('Primary.TButton', padding=(px(26), px(10)), foreground='#FFFFFF',
                    font=_font(root, 'button_lg', FAMILY, 10, 'bold'))
    style.map('Primary.TButton', foreground=[('disabled', '#EEF1F5')])
    style.configure('Large.TButton', padding=(px(18), px(8)))
    style.configure('PrimarySm.TButton', padding=(px(14), px(5)), foreground='#FFFFFF', font=FONTS['button_lg'])
    style.map('PrimarySm.TButton', foreground=[('disabled', '#EEF1F5')])
    style.configure('Tool.TButton', padding=(px(10), px(6)), foreground=INK, font=body, width=0)
    style.map('Tool.TButton', foreground=[('disabled', FAINT), ('active', ACCENT)])
    style.configure('Outline.TButton')          # 보조 버튼과 같은 모양(버튼 종류를 세 가지로만)
    style.configure('Link.TButton', padding=(px(6), px(4)), foreground=ACCENT, font=label, width=0)
    style.configure('Quiet.TButton', padding=(px(6), px(4)), foreground=ACCENT, font=label, width=0)
    for name in ('Link.TButton', 'Quiet.TButton'):
        style.map(name, foreground=[('disabled', FAINT), ('pressed', ACCENT_PRESS), ('active', ACCENT_HOVER)])
    style.configure('Seg.Toolbutton', padding=(px(14), px(9)), background=BG, foreground=INK, font=body, anchor='center')
    style.map('Seg.Toolbutton', foreground=[('disabled', FAINT), ('selected', ACCENT)],
              font=[('selected', label)])
    # 판정 단추: 지금 판정이 눌린 상태로 보임(관련 = 녹색 먹, 무관 = 주색)
    for name, el in (('Judge.Toolbutton', 'RC.JudgeOk.border'), ('JudgeNo.Toolbutton', 'RC.JudgeNo.border')):
        style.configure(name, padding=(px(10), px(3)), background=BG, foreground=INK, font=label, anchor='center')
        style.map(name, foreground=[('selected', '#FFFFFF')])

    # 입력칸: 둥근 테두리, 고르면 파란 테두리
    style.configure('TEntry', padding=(px(10), px(6)), background=BG, fieldbackground=CARD, foreground=INK, insertcolor=INK)
    style.map('TEntry', fieldbackground=[('disabled', FIELD), ('readonly', FIELD)], foreground=[('disabled', MUTED)])
    style.configure('Large.TEntry', padding=(px(12), px(6)))
    style.configure('TCombobox', padding=(px(10), px(5), px(4), px(5)), background=BG, fieldbackground=CARD, foreground=INK)
    style.map('TCombobox', fieldbackground=[('readonly', CARD), ('disabled', FIELD)],
              foreground=[('readonly', INK), ('disabled', MUTED)],
              selectbackground=[('readonly', CARD)], selectforeground=[('readonly', INK)])

    # 체크박스
    for name, bg in (('TCheckbutton', BG), ('Source.TCheckbutton', BG)):
        style.configure(name, background=bg, foreground=INK, padding=(0, px(2)), indicatormargin=(0, 0, px(10), 0),
                        indicatorsize=px(16), indicatorbackground=CARD, indicatorforeground='#FFFFFF',
                        upperbordercolor=LINE_STRONG, lowerbordercolor=LINE_STRONG, focuscolor=bg)
        style.map(name, indicatorbackground=[('disabled', NEUTRAL_PALE), ('selected', ACCENT), ('active', ACCENT_PALE)],
                  upperbordercolor=[('selected', ACCENT)], lowerbordercolor=[('selected', ACCENT)],
                  foreground=[('disabled', MUTED)])
    style.configure('Source.TCheckbutton', font=FONTS['h3'])
    if 'RC.Check.indicator' not in style.element_names():
        size, gap = px(18), px(10)
        imgs = _CHECK_IMAGES.setdefault(str(root.tk), {
            'off': _check_image(root, size, gap, CARD, LINE_STRONG, None),
            'hover': _check_image(root, size, gap, ACCENT_PALE, ACCENT, None),
            'on': _check_image(root, size, gap, ACCENT, ACCENT, '#FFFFFF'),
            'off_dis': _check_image(root, size, gap, NEUTRAL_PALE, LINE, None),
            'on_dis': _check_image(root, size, gap, '#B4C0F0', '#B4C0F0', '#FFFFFF'),
        })
        style.element_create('RC.Check.indicator', 'image', imgs['off'],
                             ('disabled', 'selected', imgs['on_dis']), ('selected', imgs['on']),
                             ('disabled', imgs['off_dis']), ('active', imgs['hover']), sticky='')
    for name in ('TCheckbutton', 'Source.TCheckbutton'):
        style.layout(name, [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('RC.Check.indicator', {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])

    # 스크롤바: 화살표 없는 얇은 막대
    for orient, trough, thumb in (('Vertical', 'ns', 'nswe'), ('Horizontal', 'we', 'nswe')):
        style.layout(f'{orient}.TScrollbar', [
            (f'{orient}.Scrollbar.trough', {'sticky': trough, 'children': [
                (f'{orient}.Scrollbar.thumb', {'expand': '1', 'sticky': thumb})]})])
        style.configure(f'{orient}.TScrollbar', troughcolor=BG, background='#CBC7BE', bordercolor=BG,
                        lightcolor='#CBC7BE', darkcolor='#CBC7BE', arrowsize=px(10), gripcount=0, relief='flat')
        style.map(f'{orient}.TScrollbar', background=[('pressed', FAINT), ('active', '#AFAAA0')],
                  lightcolor=[('pressed', FAINT), ('active', '#AFAAA0')],
                  darkcolor=[('pressed', FAINT), ('active', '#AFAAA0')])
    style.configure('Card.Vertical.TScrollbar', troughcolor=CARD, bordercolor=CARD)

    # 탭, 표
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', padding=(px(18), px(10)), background=BG, foreground=MUTED, font=label)
    style.map('TNotebook.Tab', foreground=[('selected', ACCENT), ('active', INK)])
    _install_tabs(root, style)
    style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=INK, rowheight=px(30),
                    borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, font=body)
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
    style.configure('Treeview.Heading', background=HEAD, foreground=MUTED, font=FONTS['label_sm'],
                    padding=(px(8), px(6)), borderwidth=0, relief='flat',
                    bordercolor=LINE, lightcolor=HEAD, darkcolor=HEAD)
    style.map('Treeview.Heading', background=[('active', LINE_SOFT)], lightcolor=[('active', LINE_SOFT)],
              darkcolor=[('active', LINE_SOFT)], relief=[('pressed', 'flat')])
    style.map('Treeview', background=[('selected', ACCENT_SOFT)], foreground=[('selected', INK)])

    style.configure('Horizontal.TProgressbar', troughcolor=LINE, background=ACCENT, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(4), borderwidth=0)
    style.configure('TPanedwindow', background=BG)
    style.configure('Sash', sashthickness=px(5), background=LINE, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, gripcount=0)
    for cls in ('TButton', 'TRadiobutton', 'TEntry', 'TCombobox', 'TCheckbutton'):
        root.bind_class(cls, '<Map>', _fit, add='+')
    for base in FITTED:
        style.configure('Card.' + base, background=CARD)
    _variant(root, style, CARD, 'Card.')
    _install_ime_font(root)
    _THEMED.add(str(root.tk))
    return style


# ── 한글 입력(IME) 조합 글꼴 ─────────────────────────────────
# Windows IME는 조합 중인 글자를 시스템 기본 글꼴(작은 크기)로 그리고, 완성된 글자만
# 입력칸 글꼴로 넣습니다. 그래서 '작게 쳐지다가 커지는' 현상이 생깁니다.
# 입력칸에 포커스가 갈 때 IME 조합 글꼴을 그 칸의 글꼴·크기로 맞춥니다.

_IME_READY = set()


def _ime_font_for(widget):
    try:
        spec = widget.cget('font')
    except tk.TclError:
        spec = ''
    try:
        return tkfont.Font(root=widget, font=spec or 'TkTextFont').actual()
    except tk.TclError:
        return tkfont.nametofont('TkTextFont', root=widget).actual()


def _set_ime_font(widget):
    import ctypes

    class LOGFONTW(ctypes.Structure):
        _fields_ = [('lfHeight', ctypes.c_long), ('lfWidth', ctypes.c_long),
                    ('lfEscapement', ctypes.c_long), ('lfOrientation', ctypes.c_long),
                    ('lfWeight', ctypes.c_long), ('lfItalic', ctypes.c_ubyte),
                    ('lfUnderline', ctypes.c_ubyte), ('lfStrikeOut', ctypes.c_ubyte),
                    ('lfCharSet', ctypes.c_ubyte), ('lfOutPrecision', ctypes.c_ubyte),
                    ('lfClipPrecision', ctypes.c_ubyte), ('lfQuality', ctypes.c_ubyte),
                    ('lfPitchAndFamily', ctypes.c_ubyte), ('lfFaceName', ctypes.c_wchar * 32)]

    actual = _ime_font_for(widget)
    size = actual.get('size', 10)
    height = -abs(int(round(widget.winfo_fpixels(f'{size}p')))) if size > 0 else size
    lf = LOGFONTW()
    lf.lfHeight = height
    lf.lfWeight = 700 if actual.get('weight') == 'bold' else 400
    lf.lfCharSet = 1          # DEFAULT_CHARSET
    lf.lfQuality = 5          # CLEARTYPE_QUALITY
    lf.lfFaceName = actual.get('family', FAMILY)[:31]

    imm, user = ctypes.windll.imm32, ctypes.windll.user32
    imm.ImmGetContext.restype = ctypes.c_void_p
    imm.ImmGetContext.argtypes = [ctypes.c_void_p]
    imm.ImmSetCompositionFontW.argtypes = [ctypes.c_void_p, ctypes.POINTER(LOGFONTW)]
    imm.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user.GetFocus.restype = ctypes.c_void_p
    done = []
    for hwnd in {user.GetFocus(), widget.winfo_id()}:
        if not hwnd:
            continue
        himc = imm.ImmGetContext(hwnd)
        if himc:
            done.append(bool(imm.ImmSetCompositionFontW(himc, ctypes.byref(lf))))
            imm.ImmReleaseContext(hwnd, himc)
    log = os.environ.get('HISTO_IME_LOG')
    if log:
        with open(log, 'a', encoding='utf-8') as f:
            f.write(f'{widget.winfo_class()} {lf.lfFaceName} {lf.lfHeight} {done}\n')


def _install_ime_font(root):
    if os.name != 'nt' or str(root.tk) in _IME_READY:
        return
    _IME_READY.add(str(root.tk))

    def on_focus(event):
        widget = event.widget
        def apply():
            try:
                _set_ime_font(widget)
            except Exception:
                pass
        widget.after_idle(apply)

    for cls in ('TEntry', 'Entry', 'Text', 'TCombobox'):
        root.bind_class(cls, '<FocusIn>', on_focus, add='+')


# ── 화면 조립 도우미 ─────────────────────────────────────────

def round_corners(frame, radius=8, border=LINE, inner=None):
    """평면 상자의 네 모서리를 둥글게: 모서리마다 (바깥 = 부모 바탕색) 작은 그림을 덮습니다."""
    def paint(_=None, force=False):
        if getattr(frame, '_corners', None):
            if not force:
                return
            for lab in frame._corners:
                lab.destroy()
        try:
            outside = str(frame.master.cget('bg'))
        except tk.TclError:
            outside = BG
        fill = inner or str(frame.cget('bg'))
        r = px(radius)
        full = _round_rect(frame, r * 2 + 2, r * 2 + 2, r, fill, border or fill, 1, outside)
        frame._corners = []
        for i, (relx, rely, anchor) in enumerate(((0, 0, 'nw'), (1, 0, 'ne'), (0, 1, 'sw'), (1, 1, 'se'))):
            part = tk.PhotoImage(master=frame)
            x0 = 0 if relx == 0 else r + 2
            y0 = 0 if rely == 0 else r + 2
            part.tk.call(part, 'copy', full, '-from', x0, y0, x0 + r, y0 + r)
            lab = tk.Label(frame, image=part, bd=0, highlightthickness=0, bg=fill)
            lab.image = part
            lab.place(relx=relx, rely=rely, anchor=anchor, bordermode='outside')
            frame._corners.append(lab)
    frame.bind('<Map>', paint, add='+')
    frame.refresh_corners = lambda: paint(force=True) if frame.winfo_ismapped() or getattr(frame, '_corners', None) else None
    return frame


def card(parent, padx=22, pady=20, bg=CARD):
    """흰 카드: 1px 테두리 + 둥근 모서리(반경 8)."""
    box = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE,
                   padx=px(padx), pady=px(pady))
    return round_corners(box)


def section(parent, title, bg=None, hint=None):
    """패널 안 입력 묶음의 이름: 굵은 작은 제목 + (오른쪽) 짧은 도움말·명령 자리."""
    bg = bg or parent.cget('bg')
    head = tk.Frame(parent, bg=bg)
    tk.Label(head, text=title, bg=bg, fg=INK, font=FONTS['label']).pack(side='left')
    head.right = tk.Frame(head, bg=bg)
    head.right.pack(side='right')
    if hint:
        tk.Label(head, text=hint, bg=bg, fg=FAINT, font=FONTS['small']).pack(side='left', padx=(px(8), 0))
    return head


def keycap(parent, key, label, bg=None):
    """단축키 안내: [F9] 한자 변환 — 작은 키 모양 + 설명."""
    bg = bg or parent.cget('bg')
    box = tk.Frame(parent, bg=bg)
    tk.Label(box, text=key, bg=NEUTRAL_PALE, fg=MUTED, font=FONTS['label_sm'], padx=px(5), pady=0,
             highlightthickness=1, highlightbackground=LINE).pack(side='left')
    tk.Label(box, text=label, bg=bg, fg=MUTED, font=FONTS['small']).pack(side='left', padx=(px(5), 0))
    return box


def hline(parent, color=LINE_SOFT, bg=None):
    return tk.Frame(parent, height=1, bg=color)


def vline(parent, color=LINE_SOFT):
    return tk.Frame(parent, width=1, bg=color)


def dot(parent, color, bg):
    box = tk.Frame(parent, bg=bg)
    sq = tk.Frame(box, width=px(8), height=px(8), bg=color)
    sq.pack(pady=(px(1), 0))
    box.mark = sq
    return box


def badge(parent, text, kind='neutral'):
    bg, fg = BADGES.get(kind, BADGES['neutral'])
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=FONTS['label_sm'], padx=px(8), pady=px(1), bd=0)


def placeholder(widget, text, variable=None):
    """입력칸 안의 흐린 안내 문구. 값이 아니라 위에 겹친 글자라서, 입력하거나 칸을 누르면 사라집니다."""
    is_text = isinstance(widget, tk.Text)
    bg = widget.cget('bg') if is_text else CARD
    hint = tk.Label(widget.master, text=text, bg=bg, fg=FAINT, justify='left', anchor='nw',
                    font=widget.cget('font') if is_text else FONTS['input_lg'], cursor='xterm')

    def empty():
        if variable is not None:
            return not variable.get()
        return not widget.get('1.0', 'end-1c')

    def update(_=None):
        if empty() and widget.focus_get() is not widget:
            if is_text:
                hint.place(in_=widget, x=int(widget.cget('padx')) + 1, y=int(widget.cget('pady')) + 1)
            else:
                hint.place(in_=widget, x=px(14), rely=0.5, anchor='w')
            hint.lift()
        else:
            hint.place_forget()

    hint.bind('<Button-1>', lambda e: (widget.focus_set(), update()))
    widget.bind('<FocusIn>', update, add='+')
    widget.bind('<FocusOut>', update, add='+')
    if variable is not None:
        variable.trace_add('write', lambda *a: update())
    else:
        widget.bind('<<Modified>>', lambda e: (widget.edit_modified(False), update()), add='+')
    widget.after_idle(update)
    return hint


class ScrollFrame(tk.Frame):
    """세로 스크롤 영역. 내용은 self.body에 넣습니다."""

    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.bind('<Enter>', self._bind_wheel)
        self.bind('<Leave>', self._unbind_wheel)

    def _on_scroll(self, first, last):
        self.scroll.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scroll.pack_forget()
        elif not self.scroll.winfo_ismapped():
            self.scroll.pack(side='right', fill='y', before=self.canvas)

    def _wheel(self, event):
        if self.scroll.winfo_ismapped():
            step = -1 if (event.num == 4 or event.delta > 0) else 1
            self.canvas.yview_scroll(step * 3, 'units')

    def _bind_wheel(self, _):
        self.bind_all('<MouseWheel>', self._wheel)
        self.bind_all('<Button-4>', self._wheel)
        self.bind_all('<Button-5>', self._wheel)

    def _unbind_wheel(self, _):
        self.unbind_all('<MouseWheel>')
        self.unbind_all('<Button-4>')
        self.unbind_all('<Button-5>')


# ── 마우스를 올리면 나오는 설명(툴팁) ─────────────────────────
# 화면에 늘 깔려 있던 회색 부연 설명 대신, 버튼·입력칸에 마우스를 잠시 올리면 설명을 보여 줍니다.
class _Tip:
    DELAY = 350

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.win = self.job = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self._hide, add='+')
        widget.bind('<ButtonPress>', self._hide, add='+')

    def _schedule(self, event=None):
        self._cancel()
        self.job = self.widget.after(self.DELAY, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _show(self):
        self.job = None
        text = self.text() if callable(self.text) else self.text
        if not text or self.win is not None:
            return
        try:
            x = self.widget.winfo_pointerx() + px(14)
            y = self.widget.winfo_pointery() + px(18)
            win = tk.Toplevel(self.widget)
        except tk.TclError:
            return
        win.wm_overrideredirect(True)
        try:
            win.wm_attributes('-topmost', True)
        except tk.TclError:
            pass
        tk.Label(win, text=text, bg='#2A2925', fg='#F4F2EC', font=FONTS.get('small'), justify='left', anchor='w',
                 wraplength=px(380), padx=px(9), pady=px(5), bd=0).pack()
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        x = min(x, sw - win.winfo_reqwidth() - px(8))
        win.wm_geometry(f'+{x}+{y}')
        self.win = win

    def _hide(self, event=None):
        self._cancel()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None


def tip(widget, text):
    """widget에 마우스를 올리면 text(문자열 또는 문자열을 돌려주는 함수)를 보여 줍니다."""
    widget._tip = _Tip(widget, text)
    return widget


def info_icon(parent, text, bg=None):
    """긴 설명을 대신하는 작은 'ⓘ' 표시. 마우스를 올리면 설명이 나옵니다."""
    label = tk.Label(parent, text='ⓘ 설명', bg=bg or BG, fg=ACCENT, font=FONTS.get('small'), cursor='question_arrow', anchor='w')
    return tip(label, text)
