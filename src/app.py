"""독립 실행파일 또는 python app.py로 시작."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback
import sqlite3
import time
import collector
from analysis_engine import default_profile, profile_for_target
from result_viewer import ResultsWindow, RelationsWindow
from source_connectors import SOURCES, BY_ID, AUTO_IDS
from browser_links import open_web_url
import ui_theme as ui
from ui_theme import (apply_theme, px, BG, CARD, INK, MUTED, NAV, GOLD, LINE, LINE_SOFT, LINE_STRONG,
                      FIELD, ACCENT, OK, BUSY, SEAL, SURFACE_ALT, NAV_ACTIVE, NAV_HOVER, NAV_TEXT,
                      NAV_MUTED, NAV_LINE)

ROOT = Path(__file__).resolve().parent
APP_HOME = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT
VERSION = '3.1'

MODES = (
    ('빠른 검색 (목록 우선)', '빠른 검색'),
    ('우선 본문 (최대 30건)', '우선 본문 30건'),
    ('전체 수집 / 이어받기', '전체 수집'),
    ('검색 목록만', '목록만'),
    ('본문 이어받기', '본문 이어받기'),
)
MODE_HINTS = {
    '빠른 검색 (목록 우선)': '최대 20회 새 요청으로 서지·요약과 첫 목록을 모읍니다.',
    '우선 본문 (최대 30건)': '저장된 목록에서 60점 이상 후보를 점수순으로 최대 30건 받습니다.',
    '전체 수집 / 이어받기': '요청 제한 없이 모든 후보 본문을 받습니다. 오래 걸릴 수 있습니다.',
    '검색 목록만': '본문 없이 검색 목록만 저장합니다.',
    '본문 이어받기': '저장된 목록에서 아직 받지 않은 본문을 이어서 받습니다.',
}
PAGES = (
    ('search', '통합 검색', '흩어진 기록을 하나의 연구로. 공식 자료원의 기록을 모으고 근거를 따라 살펴보세요.'),
    ('sources', '자료원 연결', '7곳의 서지·목록을 앱에 통합하고, 3곳은 공식 검색 화면으로 연결합니다.'),
    ('library', '내 문헌', '다시 찾을 수 있는 연구 기록. 이전 버전의 수집 결과도 그대로 읽을 수 있습니다.'),
    ('activity', '진행 기록', '자료원 응답과 수집 상태를 기록합니다. 첫 목록이 저장되면 결과 창에서 바로 확인하세요.'),
)


class QueueStream:

    def __init__(self, events):
        self.events = events

    def write(self, value):
        if value:
            self.events.put(('log', value))
        return len(value)

    def flush(self):
        pass


class NavItem(tk.Frame):
    """사이드바 메뉴 한 줄: 왼쪽 금색 표시선 + 이름."""

    def __init__(self, parent, text, command):
        super().__init__(parent, bg=NAV, cursor='hand2', height=px(44))
        self.pack_propagate(False)
        self.active = False
        self.command = command
        self.bar = tk.Frame(self, width=px(3), bg=NAV)
        self.bar.pack(side='left', fill='y', pady=px(12))
        self.label = tk.Label(self, text=text, bg=NAV, fg=NAV_TEXT, font=ui.FONTS['body'], anchor='w', padx=px(12))
        self.label.pack(side='left', fill='both', expand=True)
        for widget in (self, self.label, self.bar):
            widget.bind('<Button-1>', lambda e: self.command())
            widget.bind('<Enter>', lambda e: self._hover(True))
            widget.bind('<Leave>', lambda e: self._hover(False))

    def _hover(self, inside):
        if not self.active:
            self._paint(NAV_HOVER if inside else NAV)

    def _paint(self, bg):
        self.configure(bg=bg)
        self.label.configure(bg=bg)
        if not self.active:
            self.bar.configure(bg=bg)

    def set_active(self, active):
        self.active = active
        self._paint(NAV_ACTIVE if active else NAV)
        self.bar.configure(bg=GOLD if active else NAV)
        self.label.configure(fg='white' if active else NAV_TEXT,
                             font=ui.FONTS['label'] if active else ui.FONTS['body'])


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f'역사자료 연구실 {VERSION} — 통합 탐색과 근거 중심 분석')
        apply_theme(self)
        ui.fit_geometry(self, 1280, 860, 1080, 720)
        self._set_icon()
        self.worker = None
        self.running = False
        self.events = queue.Queue()
        self.output = tk.StringVar(value=str(APP_HOME / '수집결과'))
        self.target = tk.StringVar(value='숙빈 최씨')
        self.mode = tk.StringVar(value='빠른 검색 (목록 우선)')
        self.mode_hint = tk.StringVar(value=MODE_HINTS[self.mode.get()])
        self.state = tk.StringVar(value='검색할 인물이나 주제를 입력하세요. 저장된 문헌은 ‘내 문헌’에서 바로 읽을 수 있습니다.')
        self.source_summary = tk.StringVar()
        self.source_vars = {s.id: tk.BooleanVar(value=s.mode != 'official') for s in SOURCES}
        self.source_checks = []
        self.stats = {key: tk.StringVar(value='0') for key in ('sites', 'selected', 'records', 'bodies')}
        self.stats['sites'].set(str(len(SOURCES)))
        self.stats['selected'].set(str(len(AUTO_IDS)))
        self._build_sidebar()

        self.main = tk.Frame(self, bg=BG)
        self.main.pack(side='left', fill='both', expand=True)
        self._build_statusbar()
        header = tk.Frame(self.main, bg=BG)
        header.pack(fill='x', padx=px(40), pady=(px(30), px(18)))
        self.page_title = tk.StringVar(value='통합 검색')
        self.page_lead = tk.StringVar()
        heads = tk.Frame(header, bg=BG)
        heads.pack(side='left', fill='x', expand=True)
        title_row = tk.Frame(heads, bg=BG)
        title_row.pack(anchor='w')
        self.history = []
        self.current_page = None
        self.back_button = ttk.Button(title_row, text='←  뒤로', style='Link.TButton', command=self.go_back, cursor='hand2')
        tk.Label(title_row, textvariable=self.page_title, bg=BG, fg=INK, font=ui.FONTS['h1'], anchor='w').pack(side='left')
        self.lead_label = tk.Label(heads, textvariable=self.page_lead, bg=BG, fg=MUTED, font=ui.FONTS['body'],
                                   anchor='w', justify='left')
        self.lead_label.pack(anchor='w', pady=(px(4), 0))
        heads.bind('<Configure>', lambda e: self.lead_label.configure(wraplength=max(px(300), e.width - px(10))))
        chip = tk.Frame(header, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=px(12), pady=px(5))
        chip.pack(side='right', anchor='s', pady=(0, px(4)))
        self.chip_dot = ui.dot(chip, OK, CARD)
        self.chip_dot.pack(side='left', padx=(0, px(8)))
        self.chip_text = tk.Label(chip, text='대기 중', bg=CARD, fg=MUTED, font=ui.FONTS['small'])
        self.chip_text.pack(side='left')

        self.pages_box = tk.Frame(self.main, bg=BG)
        self.pages_box.pack(fill='both', expand=True, padx=px(40), pady=(0, px(20)))
        self.pages = {name: tk.Frame(self.pages_box, bg=BG) for name, _, _ in PAGES}
        self._build_search(self.pages['search'])
        self._build_sources(self.pages['sources'])
        self._build_library(self.pages['library'])
        self._build_activity(self.pages['activity'])
        self.sources_changed()
        self.show_page('search')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Alt-Left>', lambda e: self.go_back())
        self.after(150, self.poll)
        self.after(250, self.refresh_counts)

    def _set_icon(self):
        try:
            icon = ui.resource_dir() / 'app.ico'
            if os.name == 'nt' and icon.exists():
                self.iconbitmap(default=str(icon))
        except tk.TclError:
            pass

    # ── 사이드바 · 상태 표시줄 ─────────────────────────────

    def _build_sidebar(self):
        side = tk.Frame(self, bg=NAV, width=px(224))
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=NAV)
        brand.pack(fill='x', padx=px(24), pady=(px(30), 0))
        seal = tk.Frame(brand, bg=SEAL, width=px(44), height=px(44))
        seal.pack(side='left')
        seal.pack_propagate(False)
        tk.Label(seal, text='史', bg=SEAL, fg='white', font=ui.FONTS['seal']).pack(expand=True)
        names = tk.Frame(brand, bg=NAV)
        names.pack(side='left', padx=(px(12), 0))
        tk.Label(names, text='역사자료 연구실', bg=NAV, fg='white', font=ui.FONTS['brand']).pack(anchor='w')
        tk.Label(names, text=f'HISTORY DESK · v{VERSION}', bg=NAV, fg=NAV_MUTED, font=ui.FONTS['small']).pack(anchor='w')

        menu = tk.Frame(side, bg=NAV)
        menu.pack(fill='x', padx=px(14), pady=(px(40), 0))
        self.nav_buttons = {}
        for key, label, _ in PAGES:
            item = NavItem(menu, label, lambda k=key: self.show_page(k))
            item.pack(fill='x', pady=(0, px(4)))
            self.nav_buttons[key] = item

        bottom = tk.Frame(side, bg=NAV)
        bottom.pack(side='bottom', fill='x', padx=px(24), pady=px(24))
        ui.hline(bottom, NAV_LINE).pack(fill='x', pady=(0, px(14)))
        tk.Label(bottom, text=f'{len(SOURCES)}개 공개 자료원 · {len(AUTO_IDS)}곳 통합', bg=NAV, fg=NAV_TEXT,
                 font=ui.FONTS['small']).pack(anchor='w')
        tk.Label(bottom, text='정치사 · 사회사 · 제도사\n연대와 관계 후보 탐색', bg=NAV, fg=NAV_MUTED, justify='left',
                 font=ui.FONTS['small']).pack(anchor='w', pady=(px(6), 0))

    def _build_statusbar(self):
        bar = tk.Frame(self.main, bg=SURFACE_ALT, height=px(40))
        bar.pack(side='bottom', fill='x')
        bar.pack_propagate(False)
        ui.hline(bar, LINE).pack(side='top', fill='x')
        inner = tk.Frame(bar, bg=SURFACE_ALT)
        inner.pack(fill='both', expand=True, padx=px(40))
        self.status_mark = ui.dot(inner, OK, SURFACE_ALT)
        self.status_mark.pack(side='left', padx=(0, px(10)))
        self.progress = ttk.Progressbar(inner, mode='indeterminate', length=px(180))
        self.status_label = tk.Label(inner, textvariable=self.state, bg=SURFACE_ALT, fg=MUTED,
                                     font=ui.FONTS['small'], anchor='w')
        self.status_label.pack(side='left', fill='x', expand=True)

    def _set_busy(self, busy):
        color = BUSY if busy else OK
        self.status_mark.mark.configure(bg=color)
        self.chip_dot.mark.configure(bg=color)
        self.chip_text.configure(text='수집 중' if busy else '대기 중', fg=INK if busy else MUTED)
        if busy:
            self.progress.pack(side='right', before=self.status_label)
            self.progress.start(18)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # ── 통합 검색 ──────────────────────────────────────────

    def _build_search(self, page):
        scroller = ui.ScrollFrame(page)
        scroller.pack(fill='both', expand=True)
        page = scroller.body

        metrics = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        metrics.pack(fill='x', pady=(0, px(16)))
        items = [('sites', '연결 자료원'), ('selected', '통합 검색 선택'), ('records', '저장된 문헌'), ('bodies', '저장된 본문')]
        for i, (key, label) in enumerate(items):
            metrics.columnconfigure(i * 2, weight=1, uniform='metric')
            cell = tk.Frame(metrics, bg=CARD, padx=px(20), pady=px(14))
            cell.grid(row=0, column=i * 2, sticky='nsew')
            tk.Label(cell, text=label, bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w')
            tk.Label(cell, textvariable=self.stats[key], bg=CARD, fg=ACCENT if key == 'selected' else INK,
                     font=ui.FONTS['metric']).pack(anchor='w')
            if i < len(items) - 1:
                ui.vline(metrics).grid(row=0, column=i * 2 + 1, sticky='ns', pady=px(12))

        card = ui.card(page, 28, 24)
        card.pack(fill='x')
        tk.Label(card, text='연구 대상', bg=CARD, fg=INK, font=ui.FONTS['h2']).pack(anchor='w')
        row = tk.Frame(card, bg=CARD)
        row.pack(fill='x', pady=(px(8), px(6)))
        self.target_entry = ttk.Entry(row, textvariable=self.target, font=ui.FONTS['input_lg'], style='Large.TEntry')
        self.target_entry.pack(side='left', fill='x', expand=True)
        self.target_entry.bind('<Return>', lambda e: self.apply_target())
        self.target_button = ttk.Button(row, text='대상 적용', style='Outline.TButton', command=self.apply_target, cursor='hand2')
        self.target_button.pack(side='left', fill='y', padx=(px(10), 0))
        tk.Label(card, text='인물·사건·제도 이름을 입력하면 알려진 별칭을 검색어에 채웁니다.', bg=CARD, fg=MUTED,
                 font=ui.FONTS['small']).pack(anchor='w')

        cols = tk.Frame(card, bg=CARD)
        cols.pack(fill='x', pady=(px(20), 0))
        cols.columnconfigure(0, weight=1, uniform='col')
        cols.columnconfigure(1, weight=1, uniform='col')
        left = tk.Frame(cols, bg=CARD)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, px(14)))
        head = tk.Frame(left, bg=CARD)
        head.pack(fill='x')
        tk.Label(head, text='검색어와 별칭', bg=CARD, fg=INK, font=ui.FONTS['label']).pack(side='left')
        tk.Label(head, text='  ·  한 줄에 하나', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left')
        self.terms = tk.Text(left, height=4, font=ui.FONTS['input'], wrap='word', relief='flat', bg=FIELD, fg=INK,
                             insertbackground=INK, padx=px(12), pady=px(9), spacing1=px(2), spacing3=px(2),
                             highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                             selectbackground=ACCENT, selectforeground='white')
        self.terms.pack(fill='x', pady=(px(8), 0))
        self.terms.insert('1.0', '\n'.join(default_profile()['quick_terms']))

        right = tk.Frame(cols, bg=CARD)
        right.grid(row=0, column=1, sticky='nsew', padx=(px(14), 0))
        tk.Label(right, text='검색 방식', bg=CARD, fg=INK, font=ui.FONTS['label']).pack(anchor='w')
        seg = tk.Frame(right, bg=CARD)
        seg.pack(fill='x', pady=(px(8), px(8)))
        self.mode_buttons = []
        for i, (value, label) in enumerate(MODES):
            seg.columnconfigure(i % 3, weight=1, uniform='seg')
            b = ttk.Radiobutton(seg, text=label, value=value, variable=self.mode, style='Seg.Toolbutton',
                                command=self.mode_changed, cursor='hand2')
            b.grid(row=i // 3, column=i % 3, sticky='nsew', padx=(0, px(6)), pady=(0, px(6)))
            self.mode_buttons.append(b)
        self.mode_select = self.mode_buttons[0]
        self.mode_hint_label = tk.Label(right, textvariable=self.mode_hint, bg=CARD, fg=MUTED, font=ui.FONTS['small'],
                                        justify='left', anchor='w')
        self.mode_hint_label.pack(fill='x')
        right.bind('<Configure>', lambda e: self.mode_hint_label.configure(wraplength=max(px(200), e.width)))

        ui.hline(card).pack(fill='x', pady=(px(20), px(16)))
        pathrow = tk.Frame(card, bg=CARD)
        pathrow.pack(fill='x')
        tk.Label(pathrow, text='저장 위치', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(10)))
        self.output_entry = ttk.Entry(pathrow, textvariable=self.output)
        self.output_entry.pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(pathrow, text='폴더 변경', command=self.choose, cursor='hand2')
        self.choose_button.pack(side='left', padx=(px(8), 0))

        buttons = tk.Frame(page, bg=BG)
        buttons.pack(fill='x', pady=(px(16), px(10)))
        self.start_button = ttk.Button(buttons, text='통합 검색 시작', style='Primary.TButton', command=self.start, cursor='hand2')
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='중단 후 저장', style='Large.TButton', command=self.stop, cursor='hand2')
        self.stop_button.pack(side='left', padx=(px(10), 0))
        ttk.Button(buttons, textvariable=self.source_summary, style='Link.TButton', cursor='hand2',
                   command=lambda: self.show_page('sources')).pack(side='left', padx=(px(10), 0))
        ttk.Button(buttons, text='문헌 결과 보기  →', style='Large.TButton', command=self.view, cursor='hand2').pack(side='right')
        tk.Label(page, text='빠른 검색은 후보 목록부터 모읍니다. 필요한 문헌만 골라 본문을 받을 수 있습니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w')

    # ── 자료원 연결 ────────────────────────────────────────

    def _build_sources(self, page):
        tools = tk.Frame(page, bg=BG)
        tools.pack(fill='x', pady=(0, px(12)))
        for kind, label, note in (('full', '목록·본문 통합', '앱 안에서 본문까지'),
                                  ('catalog', '서지·요약 통합', '첫 화면 미리보기'),
                                  ('official', '공식 사이트 연결', '브라우저에서 열기')):
            ui.badge(tools, label, kind).pack(side='left')
            tk.Label(tools, text=note, bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(6), px(18)))
        ttk.Button(tools, text='선택 해제', command=lambda: self.select_sources(False), cursor='hand2').pack(side='right')
        ttk.Button(tools, text='모두 선택', command=lambda: self.select_sources(True), cursor='hand2').pack(side='right', padx=(0, px(8)))

        scroller = ui.ScrollFrame(page)
        scroller.pack(fill='both', expand=True)
        content = scroller.body
        content.columnconfigure(0, weight=1, uniform='source')
        content.columnconfigure(1, weight=1, uniform='source')
        badges = {'full': '목록·본문 통합', 'catalog': '서지·요약 통합', 'official': '공식 사이트 연결'}
        for i, source in enumerate(SOURCES):
            box = ui.card(content, 18, 14)
            box.grid(row=i // 2, column=i % 2, sticky='nsew', padx=(0, px(10)) if i % 2 == 0 else 0, pady=(0, px(10)))
            box.columnconfigure(0, weight=1)
            if source.mode != 'official':
                check = ttk.Checkbutton(box, text=source.name, variable=self.source_vars[source.id],
                                        command=self.sources_changed, style='Source.TCheckbutton', cursor='hand2')
                check.grid(row=0, column=0, sticky='w')
                self.source_checks.append(check)
                indent = px(26)
            else:
                tk.Label(box, text=source.name, bg=CARD, fg=INK, font=ui.FONTS['h3']).grid(row=0, column=0, sticky='w', padx=(px(26), 0))
                indent = px(26)
            ui.badge(box, badges[source.mode], source.mode).grid(row=0, column=1, sticky='e', padx=(px(10), px(6)))
            ttk.Button(box, text='공식 검색 ↗' if source.search_path else '사이트 열기 ↗', style='Quiet.TButton',
                       command=lambda key=source.id: self.open_source(key), cursor='hand2').grid(row=0, column=2, rowspan=2, sticky='e')
            tk.Label(box, text=source.description, bg=CARD, fg=MUTED, font=ui.FONTS['small'], anchor='w',
                     justify='left').grid(row=1, column=0, columnspan=2, sticky='w', padx=(indent, 0), pady=(px(2), 0))
        tk.Label(page, text='검색어는 첫 번째 별칭을 사용합니다. 규장각·국가기록포털은 검색어를 복사한 뒤 홈페이지를 엽니다. '
                            '서지 통합은 검색 첫 화면의 미리보기입니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small'], justify='left', wraplength=px(960)).pack(anchor='w', pady=(px(6), 0))

    # ── 내 문헌 ────────────────────────────────────────────

    def _build_library(self, page):
        info = ui.card(page, 24, 18)
        info.pack(fill='x', pady=(0, px(16)))
        ttk.Button(info, text='문헌·분석 열기', style='Primary.TButton', command=self.view, cursor='hand2').pack(side='right')
        ttk.Button(info, text='기존 결과 폴더 선택', style='Large.TButton', command=self.choose, cursor='hand2').pack(side='right', padx=(px(16), px(10)))
        text = tk.Frame(info, bg=CARD, width=px(200))
        text.pack(side='left', fill='x', expand=True)
        tk.Label(text, text='현재 보관함', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w')
        self.library_path = tk.Label(text, textvariable=self.output, bg=CARD, fg=INK, font=ui.FONTS['label'],
                                     anchor='w', justify='left', wraplength=px(560))
        self.library_path.pack(anchor='w', fill='x', pady=(px(2), 0))
        text.bind('<Configure>', lambda e: self.library_path.configure(wraplength=max(px(200), e.width - px(4))))

        table = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        table.pack(fill='both', expand=True)
        self.recent = ttk.Treeview(table, columns=('title', 'source', 'state'), show='headings', height=12)
        for key, label, width in (('title', '최근 저장된 문헌', 520), ('source', '자료원', 170), ('state', '보관 상태', 110)):
            self.recent.heading(key, text=label, anchor='w')
            self.recent.column(key, width=px(width), stretch=key == 'title', anchor='w')
        self.recent.tag_configure('odd', background='#FBFAF7')
        self.recent.tag_configure('even', background=CARD)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.recent.yview, style='Card.Vertical.TScrollbar')
        self.recent.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.recent.pack(side='left', fill='both', expand=True)
        self.recent.bind('<Double-1>', lambda e: self.view())
        self.library_empty = tk.Label(table, bg=CARD, fg=MUTED, font=ui.FONTS['body'], justify='center',
                                      text='아직 이 보관함에 저장된 문헌이 없습니다.\n통합 검색을 실행하거나 기존 결과 폴더를 선택하세요.')
        self.library_empty.place(relx=0.5, rely=0.45, anchor='center')

    # ── 진행 기록 ──────────────────────────────────────────

    def _build_activity(self, page):
        box = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        box.pack(fill='both', expand=True)
        self.log = tk.Text(box, height=20, state='disabled', font=ui.FONTS['mono'], wrap='word', bg=CARD, fg=INK,
                           relief='flat', padx=px(20), pady=px(16), spacing3=px(3), highlightthickness=0,
                           selectbackground=ACCENT, selectforeground='white')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.log.yview, style='Card.Vertical.TScrollbar')
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)

    # ── 동작 ───────────────────────────────────────────────

    def show_page(self, key, remember=True):
        if not hasattr(self, 'pages'):
            return
        if remember and self.current_page and self.current_page != key:
            self.history.append(self.current_page)
            del self.history[:-30]
        self.current_page = key
        self._update_back()
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill='both', expand=True)
        for name, title, lead in PAGES:
            if name == key:
                self.page_title.set(title)
                self.page_lead.set(lead)
        for name, b in self.nav_buttons.items():
            b.set_active(name == key)
        if key == 'library':
            self.refresh_counts()

    def _update_back(self):
        # 돌아갈 화면이 있을 때만 '← 뒤로'를 제목 앞에 보입니다.
        if self.history:
            if not self.back_button.winfo_ismapped():
                self.back_button.pack(side='left', before=self.back_button.master.winfo_children()[-1],
                                      padx=(0, px(10)))
            prev = next(t for n, t, _ in PAGES if n == self.history[-1])
            self.back_button.configure(text=f'←  {prev}')
        else:
            self.back_button.pack_forget()

    def go_back(self):
        if self.history:
            self.show_page(self.history.pop(), remember=False)

    def selected_sources(self):
        return [key for key in AUTO_IDS if self.source_vars[key].get()]

    def select_sources(self, value):
        if self.running:
            return
        for key in AUTO_IDS:
            self.source_vars[key].set(value)
        self.sources_changed()

    def sources_changed(self):
        count = len(self.selected_sources())
        self.stats['selected'].set(str(count))
        self.source_summary.set(f'자료원 {count}곳 선택됨 · 변경' if count else '자료원을 선택하세요 →')

    def open_source(self, key):
        term = self.terms.get('1.0', 'end').strip().splitlines()
        term = term[0] if term else self.target.get().strip()
        source = BY_ID[key]
        self.clipboard_clear()
        self.clipboard_append(term)
        try:
            open_web_url(source.search_url(term))
            self.state.set(source.name + ' 열기 · 검색어를 복사했습니다.' + ('' if source.search_path else ' 사이트 검색창에 붙여넣으세요.'))
        except Exception as exc:
            messagebox.showerror('공식 사이트 열기', str(exc), parent=self)

    def refresh_counts(self):
        path = Path(self.output.get()) / 'collection.sqlite3'
        if not path.exists():
            self.stats['records'].set('0')
            self.stats['bodies'].set('0')
            self._fill_recent([])
            return
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
                total = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
                bodies = conn.execute("SELECT COUNT(*) FROM documents WHERE status='complete'").fetchone()[0]
                rows = conn.execute('SELECT data,status FROM documents ORDER BY rowid DESC LIMIT 15').fetchall()
            self.stats['records'].set(str(total))
            self.stats['bodies'].set(str(bodies))
            self._fill_recent(rows)
        except (sqlite3.Error, OSError, ValueError):
            return

    def _fill_recent(self, rows):
        if not hasattr(self, 'recent'):
            return
        for child in self.recent.get_children():
            self.recent.delete(child)
        labels = {'catalog': '서지·요약', 'complete': '본문 저장', 'pending': '본문 미수집', 'failed': '오류'}
        for i, (data, status) in enumerate(rows):
            row = json.loads(data)
            self.recent.insert('', 'end', tags=('odd' if i % 2 else 'even',),
                               values=(row['title'], row.get('source', ''), labels.get(status, status)))
        if rows:
            self.library_empty.place_forget()
        else:
            self.library_empty.place(relx=0.5, rely=0.45, anchor='center')

    def apply_target(self):
        if self.running:
            return
        name = self.target.get().strip()
        if not name:
            return
        profile = profile_for_target(name)
        self.terms.delete('1.0', 'end')
        self.terms.insert('1.0', '\n'.join(profile['quick_terms']))
        self.state.set('대상 적용: ' + name + ' · 다른 인물은 입력 이름만 직접 명칭으로 사용합니다. 별칭은 검색어에 추가하세요.')

    def mode_changed(self, event=None):
        self.mode_hint.set(MODE_HINTS.get(self.mode.get(), ''))
        profile = profile_for_target(self.target.get())
        current = self.terms.get('1.0', 'end').strip().splitlines()
        broad = json.loads((ROOT / 'queries.json').read_text(encoding='utf-8'))['terms']
        if self.mode.get() == '전체 수집 / 이어받기':
            if profile['target'] == '숙빈 최씨' and current == profile['quick_terms']:
                self.terms.delete('1.0', 'end')
                self.terms.insert('1.0', '\n'.join(broad))
            self.state.set('전체 수집: 요청 제한 없이 모든 후보 본문을 받습니다. 오래 걸릴 수 있습니다.')
        elif self.mode.get() == '빠른 검색 (목록 우선)':
            if current == broad:
                self.apply_target()
            self.state.set('빠른 검색: 최대 20회 새 요청. 연결 자료원의 서지·요약과 첫 목록을 모읍니다.')
        elif self.mode.get() == '우선 본문 (최대 30건)':
            self.state.set('저장된 목록에서 60점 이상 후보를 점수순으로 최대 30건 받습니다. 누락 후보는 결과 보기에서 직접 선택할 수 있습니다.')

    def choose(self):
        if self.running:
            return
        folder = filedialog.askdirectory(initialdir=self.output.get())
        if folder:
            self.output.set(folder)
            self.stats['records'].set('0')
            self.stats['bodies'].set('0')
            self.refresh_counts()

    def _set_inputs(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for widget in (self.start_button, self.output_entry, self.choose_button, self.target_entry, self.target_button):
            widget.configure(state=state)
        for b in self.mode_buttons:
            b.configure(state=state)
        self.terms.configure(state=state, bg=FIELD if enabled else '#F3F1EC')
        for check in self.source_checks:
            check.configure(state=state)

    def start(self, selected=None, profile_override=None):
        if self.running:
            return
        terms = [x.strip() for x in self.terms.get('1.0', 'end').splitlines() if x.strip()]
        if not terms:
            messagebox.showerror('검색어', '검색어를 한 개 이상 입력하세요.')
            return
        sources = self.selected_sources()
        if not sources and self.mode.get() not in ('본문 이어받기', '우선 본문 (최대 30건)'):
            messagebox.showinfo('자료원 선택', '자료원 연결에서 통합 검색할 사이트를 한 곳 이상 선택하세요.')
            self.show_page('sources')
            return
        out = Path(self.output.get()).resolve()
        profile = profile_override or profile_for_target(self.target.get())
        saved_profile = out / 'active_profile.json'
        if not profile_override and saved_profile.exists():
            try:
                saved = json.loads(saved_profile.read_text(encoding='utf-8'))
                if saved.get('target') == profile['target']:
                    profile = saved
            except (ValueError, OSError):
                pass
        if not profile['target'].strip():
            messagebox.showerror('분석 대상', '분석 대상 이름을 입력하세요.')
            return
        try:
            out.mkdir(parents=True, exist_ok=True)
            self.stop_file = out / 'stop.request'
            self.stop_file.unlink(missing_ok=True)
            query = out / 'active_queries.json'
            query.write_text(json.dumps({'terms': terms}, ensure_ascii=False), encoding='utf-8')
            profile_path = out / 'active_profile.json'
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
            cmd = ['--queries', str(query), '--output', str(out), '--stop-file', str(self.stop_file),
                   '--profile', str(profile_path)]
            if sources:
                cmd += ['--sources', ','.join(sources)]
            if self.mode.get() == '빠른 검색 (목록 우선)':
                cmd += ['--quick']
            if self.mode.get() == '검색 목록만':
                cmd += ['--search-only']
            if self.mode.get() == '본문 이어받기':
                cmd += ['--download-only']
            if self.mode.get() == '우선 본문 (최대 30건)':
                cmd += ['--download-only', '--min-score', '60', '--max-documents', '30']
            if selected is not None:
                selected_file = out / 'selected_keys.json'
                selected_file.write_text(json.dumps(selected, ensure_ascii=False), encoding='utf-8')
                cmd += ['--selected-keys', str(selected_file)]
        except Exception as exc:
            messagebox.showerror('실행 실패', str(exc))
            return
        self.active_out = out
        self.running = True
        self._set_inputs(False)
        self._set_busy(True)
        self.state.set('실행 중입니다. 첫 목록이 저장되면 결과 보기에서 바로 읽을 수 있습니다.')
        self.worker = threading.Thread(target=self.run_collection, args=(cmd,), daemon=True)
        self.worker.start()

    def run_collection(self, args):
        try:
            result = collector.main(args, log_stream=QueueStream(self.events))
        except BaseException:
            self.events.put(('log', traceback.format_exc()))
            result = 1
        self.events.put(('done', result))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.log.configure(state='normal')
                    self.log.insert('end', value)
                    self.log.see('end')
                    self.log.configure(state='disabled')
                    if '목록 저장됨' in value:
                        self.refresh_counts()
                else:
                    self.running = False
                    self._set_inputs(True)
                    self._set_busy(False)
                    self.refresh_counts()
                    report = self.active_out / 'report.json'
                    if report.exists():
                        d = json.loads(report.read_text(encoding='utf-8'))
                        prefix = '빠른 예비 검색' if d.get('quick') else '실행 종료'
                        self.state.set(f"{prefix}: 문헌 {d['unique_records']}건 · 서지 {d.get('catalog_count', 0)}건 · "
                                       f"본문 {d['downloaded']}건 / 요청 {d.get('http_requests', '?')}회. 문헌 결과 보기에서 확인하세요.")
                    else:
                        self.state.set('실행이 종료되었습니다. 로그를 확인하세요.')
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def stop(self):
        if self.running:
            self.stop_file.write_text('stop', encoding='utf-8')
            self.state.set('현재 요청이 끝나면 저장 후 중단합니다. 창을 잠시 열어 두세요.')

    def view(self):
        folder = Path(self.output.get())
        if not any((folder / name).exists() for name in ('collection.sqlite3', 'documents.jsonl')):
            chosen = filedialog.askdirectory(parent=self, title='기존 수집결과 폴더 선택 (collection.sqlite3가 있는 폴더)',
                                             initialdir=folder if folder.exists() else APP_HOME)
            if not chosen:
                return
            folder = Path(chosen)
            if not self.running:
                self.output.set(str(folder))
        self.result_window = ResultsWindow(self, folder, on_collect=self.collect_selected)

    def collect_selected(self, folder, keys, profile):
        if self.running:
            messagebox.showinfo('수집 중', '현재 실행이 끝나거나 중단 후 저장한 다음 선택 문헌을 받을 수 있습니다.')
            return
        self.output.set(str(folder))
        self.target.set(profile['target'])
        self.mode.set('본문 이어받기')
        self.mode_hint.set(MODE_HINTS['본문 이어받기'])
        self.start(selected=keys, profile_override=profile)

    def close(self):
        if self.running:
            self.stop()
            return
        self.destroy()


if __name__ == '__main__':
    app = App()
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke-test':
        test_output = Path(sys.argv[2]).resolve()
        app.withdraw()
        app.output.set(str(test_output))
        app.mode.set('본문 이어받기')

        def start_test():
            app.start()
            app.after(250, finish_test)

        def finish_test():
            if app.running:
                app.after(250, finish_test)
                return
            test_output.mkdir(parents=True, exist_ok=True)
            app.view()
            app.result_window.withdraw()
            app.result_window.update_idletasks()
            graph = RelationsWindow(app.result_window, app.result_window.rows)
            graph.withdraw()
            graph.update_idletasks()
            (test_output / 'gui-smoke.json').write_text(json.dumps({
                'frozen': bool(getattr(sys, 'frozen', False)),
                'queries': len(app.terms.get('1.0', 'end').strip().splitlines()),
                'connected_sources': len(SOURCES),
                'automatic_sources': len(app.selected_sources()),
                'pages': list(app.pages),
                'viewer_records': len(app.result_window.rows),
                'viewer_text': app.result_window.detail.get('1.0', 'end'),
                'relation_edges': len(graph.edges),
                'state': app.state.get(),
                'log': app.log.get('1.0', 'end'),
                'default_output': str(APP_HOME / '수집결과'),
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            app.destroy()

        app.after(150, start_test)
    app.mainloop()
