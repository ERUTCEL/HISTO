"""기록물 연구 데스크톱용 공통 테마 (v3.1 디자인 개선).

한지 바탕 · 먹색 사이드바 · 쪽빛 강조 · 낙관(붉은 도장) 로고.
Tkinter로 그릴 수 있는 평면 카드, 1px 선, 알약형 배지만 사용합니다.
"""
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ── 색상 토큰 ───────────────────────────────────────────────
BG = '#F4F1EA'            # 한지 바탕
SURFACE_ALT = '#EFEBE2'   # 상태 표시줄
CARD = '#FFFFFF'
FIELD = '#FAF8F3'         # 입력 영역 바탕
HEAD = '#F7F4ED'          # 표 머리
LINE = '#E3DDD0'
LINE_SOFT = '#EEE9DF'
LINE_STRONG = '#CFC7B7'
INK = '#1F2328'
MUTED = '#5F646B'
NAV = '#1E2630'           # 먹색 사이드바
NAV_HOVER = '#252E3A'
NAV_ACTIVE = '#2B3542'
NAV_TEXT = '#C9D1DA'
NAV_MUTED = '#8E99A6'
NAV_LINE = '#333E4B'
ACCENT = '#27507A'        # 쪽빛
ACCENT_HOVER = '#1E3F62'
ACCENT_PRESS = '#173250'
ACCENT_PALE = '#E6EDF5'
ACCENT_SOFT = '#DCE6F2'
GOLD = '#C9A45C'
SEAL = '#B23A26'          # 낙관
OK = '#2E7D5B'
OK_PALE = '#E3F0E9'
OK_INK = '#1E6B52'
WARN_PALE = '#F5ECDA'
WARN_INK = '#7A5210'
BUSY = '#C27C0E'
NEUTRAL_PALE = '#F1EEE7'

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
    'failed': ('#F7E3DF', '#9A2F1F'),
}

SCALE = 1.0
FONTS = {}
FAMILY = '맑은 고딕'
FAMILY_SEMI = None
MONO = 'Consolas'


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
    try:
        f = tkfont.Font(root=root, name=full, family=family, size=size, weight=weight, exists=False)
    except tk.TclError:
        f = tkfont.nametofont(full, root=root)
        f.configure(family=family, size=size, weight=weight)
    FONTS[name] = f
    return f


def _pick_family(root):
    global FAMILY, FAMILY_SEMI, MONO
    families = set(tkfont.families(root))
    for name in ('Pretendard', 'Pretendard Variable', '맑은 고딕', 'Malgun Gothic', 'Noto Sans CJK KR', 'Noto Sans KR'):
        if name in families:
            FAMILY = name
            break
    FAMILY_SEMI = 'Pretendard SemiBold' if 'Pretendard SemiBold' in families else None
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
    _font(root, 'reading', FAMILY, 11)
    _font(root, 'input', FAMILY, 11)
    _font(root, 'input_lg', FAMILY, 14)
    _font(root, 'h1', FAMILY, 19, 'bold')
    _font(root, 'h2', FAMILY, 12, 'bold')
    _font(root, 'h3', FAMILY, 11, 'bold')
    _font(root, 'title', FAMILY, 15, 'bold')
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

    # 버튼: 기본(흰 바탕 + 선), 주요(쪽빛), 강조선, 링크
    pad = (px(16), px(8))
    style.configure('TButton', padding=pad, background=CARD, foreground=INK, font=btn,
                    bordercolor=LINE_STRONG, lightcolor=CARD, darkcolor=CARD,
                    borderwidth=1, relief='raised', focusthickness=0, anchor='center')
    style.map('TButton',
              background=[('disabled', NEUTRAL_PALE), ('pressed', LINE_SOFT), ('active', HEAD)],
              lightcolor=[('disabled', NEUTRAL_PALE), ('pressed', LINE_SOFT), ('active', HEAD)],
              darkcolor=[('disabled', NEUTRAL_PALE), ('pressed', LINE_SOFT), ('active', HEAD)],
              bordercolor=[('focus', ACCENT), ('active', '#B9B09D')],
              foreground=[('disabled', '#9C9FA4')])
    style.configure('Primary.TButton', padding=(px(22), px(10)), background=ACCENT, foreground='#FFFFFF',
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, font=_font(root, 'button_lg', FAMILY, 10, 'bold'))
    style.map('Primary.TButton',
              background=[('disabled', '#AEBBCB'), ('pressed', ACCENT_PRESS), ('active', ACCENT_HOVER)],
              lightcolor=[('disabled', '#AEBBCB'), ('pressed', ACCENT_PRESS), ('active', ACCENT_HOVER)],
              darkcolor=[('disabled', '#AEBBCB'), ('pressed', ACCENT_PRESS), ('active', ACCENT_HOVER)],
              bordercolor=[('disabled', '#AEBBCB'), ('focus', ACCENT_PRESS), ('active', ACCENT_HOVER)],
              foreground=[('disabled', '#F4F6F9')])
    style.configure('Large.TButton', padding=(px(20), px(10)))
    style.configure('Outline.TButton', foreground=ACCENT, bordercolor=ACCENT)
    style.map('Outline.TButton', background=[('disabled', NEUTRAL_PALE), ('pressed', ACCENT_SOFT), ('active', ACCENT_PALE)],
              lightcolor=[('pressed', ACCENT_SOFT), ('active', ACCENT_PALE)],
              darkcolor=[('pressed', ACCENT_SOFT), ('active', ACCENT_PALE)],
              bordercolor=[('disabled', LINE_STRONG), ('focus', ACCENT_PRESS)],
              foreground=[('disabled', '#9C9FA4')])
    for name, bg in (('Link.TButton', BG), ('Quiet.TButton', CARD)):
        style.configure(name, padding=(px(6), px(4)), background=bg, foreground=ACCENT,
                        bordercolor=bg, lightcolor=bg, darkcolor=bg, font=label)
        style.map(name, background=[('active', bg), ('pressed', bg)],
                  lightcolor=[('active', bg)], darkcolor=[('active', bg)],
                  bordercolor=[('focus', ACCENT_SOFT), ('active', bg)],
                  foreground=[('disabled', '#9C9FA4'), ('pressed', ACCENT_PRESS), ('active', ACCENT_HOVER)])

    # 점선 포커스 대신 쪽빛 테두리(bordercolor focus)로 키보드 포커스를 표시합니다.
    style.layout('TButton', [('Button.border', {'sticky': 'nswe', 'border': '1', 'children': [
        ('Button.padding', {'sticky': 'nswe', 'children': [('Button.label', {'sticky': 'nswe'})]})]})])
    style.layout('Seg.Toolbutton', [('Toolbutton.border', {'sticky': 'nswe', 'children': [
        ('Toolbutton.padding', {'sticky': 'nswe', 'children': [('Toolbutton.label', {'sticky': 'nswe'})]})]})])

    # 검색 방식 세그먼트
    style.configure('Seg.Toolbutton', padding=(px(12), px(7)), background=CARD, foreground=INK, font=body,
                    bordercolor=LINE_STRONG, lightcolor=CARD, darkcolor=CARD, borderwidth=1, relief='raised', anchor='center')
    style.map('Seg.Toolbutton',
              background=[('disabled', NEUTRAL_PALE), ('selected', ACCENT), ('active', HEAD)],
              lightcolor=[('disabled', NEUTRAL_PALE), ('selected', ACCENT), ('active', HEAD)],
              darkcolor=[('disabled', NEUTRAL_PALE), ('selected', ACCENT), ('active', HEAD)],
              bordercolor=[('selected', ACCENT), ('focus', ACCENT)],
              foreground=[('disabled', '#9C9FA4'), ('selected', '#FFFFFF')],
              relief=[('selected', 'raised'), ('pressed', 'raised')])

    # 입력칸
    style.configure('TEntry', padding=(px(10), px(7)), fieldbackground=CARD, foreground=INK,
                    bordercolor=LINE_STRONG, lightcolor=CARD, darkcolor=CARD, insertcolor=INK)
    style.map('TEntry', bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT_PALE)],
              fieldbackground=[('disabled', FIELD), ('readonly', FIELD)], foreground=[('disabled', MUTED)])
    style.configure('Large.TEntry', padding=(px(14), px(10)))
    style.configure('TCombobox', padding=(px(10), px(6)), fieldbackground=CARD, background=CARD, foreground=INK,
                    bordercolor=LINE_STRONG, lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED, arrowsize=px(12))
    style.map('TCombobox', fieldbackground=[('readonly', CARD), ('disabled', FIELD)],
              background=[('active', HEAD), ('readonly', CARD)],
              bordercolor=[('focus', ACCENT), ('active', '#B9B09D')],
              foreground=[('readonly', INK), ('disabled', MUTED)],
              selectbackground=[('readonly', CARD)], selectforeground=[('readonly', INK)],
              arrowcolor=[('disabled', LINE_STRONG)])

    # 체크박스
    for name, bg in (('TCheckbutton', CARD), ('Source.TCheckbutton', CARD)):
        style.configure(name, background=bg, foreground=INK, padding=(0, px(2)), indicatormargin=(0, 0, px(10), 0),
                        indicatorsize=px(16), indicatorbackground=CARD, indicatorforeground='#FFFFFF',
                        upperbordercolor=LINE_STRONG, lowerbordercolor=LINE_STRONG, focuscolor=bg)
        style.map(name, background=[('active', bg)],
                  indicatorbackground=[('disabled', NEUTRAL_PALE), ('selected', ACCENT), ('active', ACCENT_PALE)],
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
            'on_dis': _check_image(root, size, gap, '#AEBBCB', '#AEBBCB', '#FFFFFF'),
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
        style.configure(f'{orient}.TScrollbar', troughcolor=BG, background='#D3CCBD', bordercolor=BG,
                        lightcolor='#D3CCBD', darkcolor='#D3CCBD', arrowsize=px(10), gripcount=0, relief='flat')
        style.map(f'{orient}.TScrollbar', background=[('pressed', '#A99F8A'), ('active', '#BDB4A2')],
                  lightcolor=[('pressed', '#A99F8A'), ('active', '#BDB4A2')],
                  darkcolor=[('pressed', '#A99F8A'), ('active', '#BDB4A2')])
    style.configure('Card.Vertical.TScrollbar', troughcolor=CARD, bordercolor=CARD)

    # 탭, 표
    style.configure('TNotebook', background=BG, borderwidth=0)
    style.configure('TNotebook.Tab', padding=(px(18), px(9)), background=NEUTRAL_PALE, bordercolor=LINE)
    style.map('TNotebook.Tab', background=[('selected', CARD)], foreground=[('selected', ACCENT)])
    style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=INK, rowheight=px(34),
                    borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, font=body)
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
    style.configure('Treeview.Heading', background=HEAD, foreground=MUTED, font=FONTS['label_sm'],
                    padding=(px(10), px(9)), borderwidth=0, relief='flat',
                    bordercolor=LINE, lightcolor=HEAD, darkcolor=HEAD)
    style.map('Treeview.Heading', background=[('active', LINE_SOFT)], lightcolor=[('active', LINE_SOFT)],
              darkcolor=[('active', LINE_SOFT)], relief=[('pressed', 'flat')])
    style.map('Treeview', background=[('selected', ACCENT)], foreground=[('selected', '#FFFFFF')])

    style.configure('Horizontal.TProgressbar', troughcolor=LINE, background=ACCENT, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=px(4), borderwidth=0)
    style.configure('TPanedwindow', background=BG)
    style.configure('Sash', sashthickness=px(12), background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG, gripcount=0)
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

def card(parent, padx=22, pady=20, bg=CARD):
    """흰 카드: 1px 테두리를 가진 평면 상자."""
    return tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE,
                    padx=px(padx), pady=px(pady))


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
    hint = tk.Label(widget.master, text=text, bg=bg, fg='#9C9FA4', justify='left', anchor='nw',
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
