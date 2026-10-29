"""브라우저 없이 SQLite/JSONL을 읽는 로컬 문헌 뷰어."""
import json
import math
import os
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from collections import Counter

import functools
import re
import shutil
from datetime import datetime

import analysis_engine as _engine
from analysis_engine import analyze, default_profile
from source_connectors import BY_ID
from browser_links import open_web_url
import ranker
import textlab as tl
import mtl
import bookmarks as bm
import reader
import history_fields as hf
import settings
import hanja_input
import threading
from textlab_ui import TextLabWindow
import ui_theme as ui
from ui_theme import (apply_theme, px, BG, CARD, INK, MUTED, ACCENT, LINE, ACCENT_PALE, NEUTRAL_PALE,
                      OK_PALE, OK_INK, HEAD)


# ── 분석 속도 개선 ──────────────────────────────────────────
# 원본 analysis_engine.compact()/contains()는 문헌마다 같은 별칭·본문을 수백 번
# 정규식으로 다시 정리합니다(5,000건에 약 390만 번). 결과가 입력에만 달려 있는
# 순수 함수라서, 같은 입력의 결과를 기억해 두고 재사용합니다. 분석 결과는 같습니다.
_WS = re.compile(r'\s+')
_PAREN = re.compile(r'\([^()]*\)')
_original_compact = _engine.compact
_original_contains = _engine.contains


@functools.lru_cache(maxsize=4096)
def _compact_cached(value):
    return _WS.sub('', value)


@functools.lru_cache(maxsize=65536)
def _contains_cached(value, alias):
    key = _compact_cached(alias)
    return key in _compact_cached(value) or key in _compact_cached(_PAREN.sub('', value))


def _fast_compact(value):
    if type(value) is str:
        return _compact_cached(value)
    return _original_compact(value)


def _fast_contains(value, alias):
    if type(alias) is str and type(value) is str:
        return _contains_cached(value, alias)
    return _original_contains(value, alias)


_engine.compact = _fast_compact
_engine.contains = _fast_contains


STATUS = {
    'pending': '본문 미수집',
    'complete': '본문 저장',
    'failed': '수집 실패',
    'catalog': '서지·요약',
}


def _terms_regex(terms):
    """검색어들을 띄어쓰기와 무관하게 찾는 정규식(긴 말 먼저)."""
    pats = []
    for t in sorted({re.sub(r'\s+', '', t) for t in terms if t}, key=len, reverse=True):
        if len(t) >= 2:
            pats.append(r'\s*'.join(re.escape(ch) for ch in t))
    return re.compile('|'.join(pats)) if pats else None


# 사료가 아닌 연구·해설 문헌(논문 목록·학술지·개설서·연표·해설). 이름이 제목·목차에 몰려 있어
# 점수가 사료보다 높게 나오기 쉬우므로 낮춥니다(목록에서 지우지는 않음).
SECONDARY_SOURCES = {'한국사연구휘보', '국사관논총', '한국사', '신편 한국사', '한국사론', '주제별연표', '우리역사넷',
                     '수집사료해제집'}
SECONDARY_WORDS = ('휘보', '논총', '논저', '해제', '연표', '학술지', 'KCI', '학위논문')   # 자료집·제공처 이름에 들어가면 연구 문헌
SECONDARY_HOSTS = ('kci.go.kr', 'riss.kr', 'dbpia.co.kr', 'kiss.kstudy.com')
SECONDARY_PENALTY = 60


def is_secondary(row):
    source = str(row.get('source') or '')
    if source in SECONDARY_SOURCES or str(row.get('key') or '').startswith(('db:hb_', 'db:nh_')):
        return True
    # 한국학자료통합플랫폼처럼 여러 기관을 모은 곳은 제공처(path)·주소로 논문을 가려냅니다
    where = source + ' ' + str(row.get('path') or '')
    return any(w in where for w in SECONDARY_WORDS) or any(h in str(row.get('url') or '') for h in SECONDARY_HOSTS)


def _mark_secondary(row):
    a = row['analysis']
    if is_secondary(row) and not a.get('secondary'):
        a['secondary'] = True
        a['score'] = a['score'] - SECONDARY_PENALTY
        a.setdefault('score_reasons', []).append(f'연구·해설 문헌(사료 아님) -{SECONDARY_PENALTY}')


SORTS = ('추천순', '판정 필요순')
OLD_SORTS = {'학습 추천순': '추천순', '기본 점수순': '추천순'}   # 예전 설정 이름


def read_results(folder, profile=None):
    folder = Path(folder).resolve()
    settings = folder / 'active_profile.json'
    profile = profile or (json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else default_profile())
    hf.apply(profile)  # 예전 폴더도 새 여섯 분류로
    db = folder / 'collection.sqlite3'
    if db.exists():
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=3) as conn:
            rows = []
            matches = {}
            for key, term in conn.execute('SELECT key,term FROM matches'):
                matches.setdefault(key, []).append(term)
            for key, data, status, error in conn.execute('SELECT key,data,status,error FROM documents'):
                row = json.loads(data)
                row.update(key=key, status=status, error=error, terms=matches.get(key, []))
                rows.append(row)
    elif (folder / 'documents.jsonl').exists():
        rows = [json.loads(line) for line in (folder / 'documents.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    else:
        raise FileNotFoundError('이 폴더에 collection.sqlite3 또는 documents.jsonl이 없습니다. 기존 수집결과 폴더를 선택하세요.')
    for row in rows:
        row['analysis'] = analyze(row, profile)
        _mark_secondary(row)
        row['_search'] = '\n'.join([str(row.get('title') or ''), str(row.get('body') or ''),
                                    str(row.get('snippet') or ''), str(row.get('source') or '')]).casefold()
    rows.sort(key=lambda row: (-row['analysis']['score'], row['key']))
    return rows, profile


def _reading_text(parent, **kw):
    text = tk.Text(parent, wrap='word', font=ui.FONTS['reading'], relief='flat', bg=CARD, fg=INK,
                   padx=px(26), pady=px(22), spacing1=px(1), spacing3=px(6), highlightthickness=0,
                   selectbackground=ACCENT, selectforeground='white', cursor='arrow', **kw)
    text.tag_configure('title', font=ui.FONTS['title'], foreground=INK, spacing3=px(6))
    text.tag_configure('meta', font=ui.FONTS['small'], foreground=MUTED, spacing3=px(2))
    text.tag_configure('chip', font=ui.FONTS['label_sm'], foreground=ACCENT, background=ACCENT_PALE)
    text.tag_configure('chip2', font=ui.FONTS['label_sm'], foreground=MUTED, background=NEUTRAL_PALE)
    text.tag_configure('chip_ok', font=ui.FONTS['label_sm'], foreground=OK_INK, background=OK_PALE)
    text.tag_configure('head', font=ui.FONTS['label'], foreground=ACCENT, spacing1=px(14), spacing3=px(4))
    text.tag_configure('note', font=ui.FONTS['small'], foreground=MUTED, lmargin1=px(2), lmargin2=px(2))
    text.tag_configure('body', font=ui.FONTS['reading'], foreground=INK, spacing2=px(5))
    text.tag_configure('error', font=ui.FONTS['small'], foreground='#9A2F1F')
    text.tag_configure('link', font=ui.FONTS['small'], foreground=ACCENT, underline=True)
    # 사이트 주소를 누르면 브라우저로 엽니다
    text.tag_bind('link', '<Enter>', lambda e: text.configure(cursor='hand2'))
    text.tag_bind('link', '<Leave>', lambda e: text.configure(cursor='arrow'))
    text.tag_bind('link', '<Button-1>', lambda e: _open_link(text, e))
    # 검색어 강조: 놓인 자리 글꼴 크기에 맞춰 굵게 + 옅은 노랑
    text.tag_configure('hit', font=ui.FONTS['reading_b'], background='#FFEFA8')
    text.tag_configure('hit_small', font=ui.FONTS['small_b'], background='#FFEFA8')
    text.tag_configure('hit_bg', background='#FFEFA8')
    return text


def _open_link(text, event):
    index = text.index(f'@{event.x},{event.y}')
    rng = text.tag_prevrange('link', f'{index}+1c')
    url = text.get(*rng).strip() if rng else ''
    if url.startswith(('http://', 'https://')):
        try:
            open_web_url(url)
        except Exception as exc:
            messagebox.showerror('주소 열기', str(exc), parent=text.winfo_toplevel())
    return 'break'


class RelationsWindow(tk.Toplevel):

    def __init__(self, parent, rows):
        super().__init__(parent)
        apply_theme(self)
        self.title('관계 후보 지도 — 공동 등장, 관계 미확정')
        ui.fit_geometry(self, 1040, 800, 760, 600)
        relations = [rel for row in rows for rel in row['analysis']['relations']]
        counts = Counter(n for rel in relations for n in (rel['source'], rel['target']))
        names = [name for name, _ in counts.most_common(12)]
        grouped = {}
        for rel in relations:
            pair = tuple(sorted((rel['source'], rel['target'])))
            if all(x in names for x in pair):
                grouped.setdefault(pair, []).append(rel)
        self.edges = sorted(grouped.items(), key=lambda item: -len(item[1]))[:30]

        head = tk.Frame(self, bg=CARD)
        head.pack(fill='x')
        inner = tk.Frame(head, bg=CARD)
        inner.pack(fill='x', padx=px(24), pady=(px(18), px(14)))
        trow = tk.Frame(inner, bg=CARD)
        trow.pack(anchor='w')
        ttk.Button(trow, text='←  결과로', style='Quiet.TButton', command=self.go_back, cursor='hand2').pack(side='left', padx=(0, px(10)))
        tk.Label(trow, text='관계 후보 지도', bg=CARD, fg=INK, font=ui.FONTS['h1']).pack(side='left')
        self.bind('<Escape>', lambda e: self.go_back())
        self.bind('<Alt-Left>', lambda e: self.go_back())
        tk.Label(inner, text='현재 필터의 문헌에서 같은 문장에 나온 표현을 연결합니다. 혈연·지시·소유 관계를 뜻하지 않습니다.\n'
                             '상위 12개 표현 / 최대 30개 연결. 한문·국문 및 주석 중복이 있을 수 있습니다.',
                 bg=CARD, fg=MUTED, font=ui.FONTS['small'], justify='left').pack(anchor='w', pady=(px(4), 0))
        ui.hline(head, LINE).pack(fill='x')

        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True, padx=px(20), pady=px(16))
        graph = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        graph.pack(fill='x')
        self.canvas = tk.Canvas(graph, height=px(360), bg=CARD, highlightthickness=0)
        self.canvas.pack(fill='x')
        self._names = names
        self._edge_ids = []
        self._current = 0
        self.canvas.bind('<Configure>', lambda e: self._draw(e.width))

        pick = tk.Frame(body, bg=BG)
        pick.pack(fill='x', pady=(px(14), px(8)))
        tk.Label(pick, text='연결', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(10)))
        self.edges_list = ttk.Combobox(pick, state='readonly',
                                       values=[f'{a} ↔ {b} · 근거 기록 {len(e)}건' for (a, b), e in self.edges])
        self.edges_list.pack(side='left', fill='x', expand=True)
        self.edges_list.bind('<<ComboboxSelected>>', lambda e: self.show_edge(self.edges_list.current()))
        box = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        box.pack(fill='both', expand=True)
        self.evidence = _reading_text(box)
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.evidence.yview, style='Card.Vertical.TScrollbar')
        self.evidence.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.evidence.pack(side='left', fill='both', expand=True)
        if self.edges:
            self.show_edge(0)
        else:
            self.evidence.insert('1.0', '현재 문헌에서 근거를 갖춘 공동 등장 후보를 찾지 못했습니다.', 'note')

    def _draw(self, width):
        self.canvas.delete('all')
        names = self._names
        cx, cy = width / 2, px(180)
        rx, ry = max(px(160), width / 2 - px(110)), px(130)
        positions = {name: (cx + rx * math.cos(2 * math.pi * i / max(1, len(names))),
                            cy + ry * math.sin(2 * math.pi * i / max(1, len(names))))
                     for i, name in enumerate(names)}
        self._edge_ids = []
        for index, (pair, evidence) in enumerate(self.edges):
            x1, y1 = positions[pair[0]]
            x2, y2 = positions[pair[1]]
            tag = f'edge{index}'
            width_ = max(1, min(px(1) + len(evidence) // 2, px(6)))
            line = self.canvas.create_line(x1, y1, x2, y2, fill=ACCENT if index == self._current else '#C9C1B0',
                                           width=width_, tags=(tag,))
            self._edge_ids.append(line)
            self.canvas.tag_bind(tag, '<Button-1>', lambda event, i=index: self.show_edge(i))
            self.canvas.tag_bind(tag, '<Enter>', lambda e: self.canvas.configure(cursor='hand2'))
            self.canvas.tag_bind(tag, '<Leave>', lambda e: self.canvas.configure(cursor=''))
        for name, (x, y) in positions.items():
            w = max(px(40), ui.FONTS['label_sm'].measure(name) // 2 + px(14))
            self.canvas.create_rectangle(x - w, y - px(16), x + w, y + px(16), fill=CARD, outline=ACCENT, width=2)
            self.canvas.create_text(x, y, text=name, fill=ACCENT, font=ui.FONTS['label_sm'])

    def go_back(self):
        parent = self.master
        self.destroy()
        try:
            parent.lift()
            parent.focus_force()
        except tk.TclError:
            pass

    def show_edge(self, index):
        self._current = index
        self.edges_list.current(index)
        for i, line in enumerate(self._edge_ids):
            self.canvas.itemconfigure(line, fill=ACCENT if i == index else '#C9C1B0')
        self.evidence.delete('1.0', 'end')
        for rel in self.edges[index][1][:30]:
            self.evidence.insert('end', f"[{rel['basis']}] {rel['record_key']}\n", 'meta')
            self.evidence.insert('end', f"{rel['evidence']}\n", 'body')
            self.evidence.insert('end', f"{rel['url']}\n\n", 'link')


class ResultsWindow(tk.Toplevel):

    def __init__(self, parent, folder, on_collect=None, on_fetch=None):
        super().__init__(parent)
        apply_theme(self)
        self.title('문헌 결과 — 앱 안에서 보기')
        ui.fit_geometry(self, 1280, 860, 940, 620)
        self.folder = Path(folder).resolve()
        self.on_collect = on_collect
        self.on_fetch = on_fetch
        self._fetching = set()   # 본문을 받는 중인 문헌 키
        self.rows = []
        self.filtered = []
        self.shown = {}
        self._timer = None
        self.query = tk.StringVar()
        self.facet = tk.StringVar(value='모든 관점')
        self.period = tk.StringVar(value='모든 연대')
        last = settings.load().get('results', {})   # 마지막으로 고른 결과 창 설정
        self.threshold = tk.StringVar(value=last.get('threshold') if last.get('threshold') in ('전체 후보', '60점 이상', '80점 이상') else '전체 후보')
        self.source_filter = tk.StringVar(value='모든 자료원')
        last_sort = OLD_SORTS.get(last.get('sort'), last.get('sort'))
        self.sort_mode = tk.StringVar(value=last_sort if last_sort in SORTS else SORTS[0])
        self.only_hits = tk.BooleanVar(value=bool(last.get('only_hits', False)))
        self.hl_terms, self.hl_re = [], None
        self.info = tk.StringVar()
        self.learn_info = tk.StringVar()
        self.judg = None
        self.model = ranker.Ranker()
        self.pred = {}       # 추천(0~1): 기본 점수와 학습 결과를 판정 수에 따라 섞은 값
        self.pred_raw = {}   # 학습 모델만의 값(판정 필요순에 씀)
        self.index = None          # TF-IDF (2강)
        self.wordspace = None      # PMI·SVD (3강)
        self.topics = None         # 주제 주석 (5·6강)
        self.classifier = None
        self._by_key = {}
        self.lab = None
        self.annot_task = tk.StringVar(value='주제')
        self.bm = getattr(parent, 'bookmarks', None) or bm.BookmarkStore()
        self.save_cat = tk.StringVar(value=last.get('save_cat') if last.get('save_cat') in self.bm.names() else bm.UNSORTED)
        for var in (self.threshold, self.sort_mode, self.only_hits, self.save_cat):
            var.trace_add('write', lambda *a: self._remember())
        self.bm_msg = tk.StringVar()

        top = tk.Frame(self, bg=CARD)
        top.pack(fill='x')
        head = tk.Frame(top, bg=CARD)
        head.pack(fill='x', padx=px(28), pady=(px(20), 0))
        titles = tk.Frame(head, bg=CARD)
        titles.pack(side='left', fill='x', expand=True)
        trow = tk.Frame(titles, bg=CARD)
        trow.pack(anchor='w')
        ttk.Button(trow, text='←  연구실로', style='Quiet.TButton', command=self.go_back, cursor='hand2').pack(side='left', padx=(0, px(10)))
        tk.Label(trow, text='문헌 결과', bg=CARD, fg=INK, font=ui.FONTS['h1']).pack(side='left')
        self.bind('<Escape>', self._on_escape)
        self.bind('<Alt-Left>', lambda e: self.go_back())
        tk.Label(titles, textvariable=self.info, bg=CARD, fg=MUTED, font=ui.FONTS['small'], anchor='w',
                 justify='left').pack(anchor='w', pady=(px(3), 0))
        tk.Label(titles, textvariable=self.learn_info, bg=CARD, fg=ACCENT, font=ui.FONTS['label_sm'], anchor='w',
                 justify='left').pack(anchor='w', pady=(px(2), 0))
        ttk.Button(head, text='저장한 검색 열기', command=self.choose_folder, cursor='hand2').pack(side='right', anchor='n')
        ttk.Button(head, text='이 검색 저장', style='Primary.TButton', command=self.save_search,
                   cursor='hand2').pack(side='right', anchor='n', padx=(0, px(8)))
        ttk.Button(head, text='파일 폴더 열기', command=self.open_folder, cursor='hand2').pack(side='right', anchor='n', padx=(0, px(8)))

        controls = tk.Frame(top, bg=CARD)
        controls.pack(fill='x', padx=px(28), pady=(px(14), px(16)))
        tk.Label(controls, text='찾기', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(8)))
        self.query_entry = ttk.Entry(controls, textvariable=self.query, width=16)
        self.query_entry.pack(side='left', padx=(0, px(10)))
        self.source_box = ttk.Combobox(controls, textvariable=self.source_filter, state='readonly', width=13,
                                       values=['모든 자료원'] + [s.short for s in BY_ID.values()])
        self.source_box.pack(side='left', padx=(0, px(6)))
        self.facet_box = ttk.Combobox(controls, textvariable=self.facet, state='readonly', width=10,
                                      values=['모든 관점'] + hf.NAMES + ['미분류'])
        self.facet_box.pack(side='left', padx=(0, px(6)))
        self.period_box = ttk.Combobox(controls, textvariable=self.period, state='readonly', width=10)
        self.period_box.pack(side='left', padx=(0, px(6)))
        ttk.Combobox(controls, textvariable=self.threshold, state='readonly', width=10,
                     values=['전체 후보', '60점 이상', '80점 이상']).pack(side='left', padx=(0, px(6)))
        ttk.Checkbutton(controls, text='검색어가 나온 문헌만', variable=self.only_hits, style='TCheckbutton',
                        command=self.filter_rows, cursor='hand2').pack(side='left', padx=(px(10), 0))
        # '우선 본문 30건'으로 고른 문헌만 보기(그 방식으로 돌린 폴더에서만 나타남)
        self.only_priority = tk.BooleanVar(value=True)
        self.priority_keys = None
        self.priority_check = ttk.Checkbutton(controls, text='우선 30건만', variable=self.only_priority, style='TCheckbutton',
                                              command=self.filter_rows, cursor='hand2')
        self.priority_anchor = tk.Label(controls, text='정렬', bg=CARD, fg=MUTED, font=ui.FONTS['small'])
        self.priority_anchor.pack(side='left', padx=(px(10), px(8)))
        ttk.Combobox(controls, textvariable=self.sort_mode, state='readonly', width=12, values=SORTS).pack(side='left')
        ttk.Button(controls, text='새로고침', style='Quiet.TButton', command=self.reload, cursor='hand2').pack(side='right')
        for variable in (self.query, self.facet, self.period, self.threshold, self.source_filter, self.sort_mode):
            variable.trace_add('write', self.schedule_filter)
        ui.hline(top, LINE).pack(fill='x')

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill='x', padx=px(28), pady=(px(14), px(12)))
        ttk.Button(buttons, text='선택 문헌 본문 받기', style='Primary.TButton', command=self.collect_selected, cursor='hand2').pack(side='left')
        ttk.Button(buttons, text='관계 후보 지도', style='Large.TButton', cursor='hand2',
                   command=lambda: RelationsWindow(self, self.filtered)).pack(side='left', padx=(px(8), 0))
        self.copy_button = ttk.Button(buttons, text='원문 주소 복사', style='Large.TButton', command=self.copy_url, cursor='hand2')
        self.copy_button.pack(side='left', padx=(px(8), 0))
        ttk.Button(buttons, text='공식 원문 ↗', style='Outline.TButton', command=self.open_original, cursor='hand2').pack(side='left', fill='y', padx=(px(8), 0))
        ttk.Button(buttons, text='추천 성능', style='Large.TButton', command=lambda: PerformanceWindow(self), cursor='hand2').pack(side='left', padx=(px(8), 0))
        ttk.Button(buttons, text='비슷한 문헌', style='Large.TButton', cursor='hand2',
                   command=lambda: self.open_lab('search', similar_to=(self.selected() or [{}])[0].get('key'))).pack(side='left', padx=(px(8), 0))
        ttk.Button(buttons, text='텍스트 분석', style='Outline.TButton', command=lambda: self.open_lab('search'), cursor='hand2').pack(side='left', fill='y', padx=(px(8), 0))


        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True, padx=px(28), pady=(0, px(20)))
        upper = tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        split.add(upper, weight=5)
        self.tree = ttk.Treeview(upper, columns=('fav', 'hit', 'rec', 'score', 'label', 'title', 'source', 'facets', 'year', 'status'),
                                 show='headings', selectmode='extended', height=12)
        centered = ('fav', 'hit', 'rec', 'score', 'label', 'year')
        for name, label, width in (('fav', '★', 30), ('hit', '검색어', 52), ('rec', '추천', 48), ('score', '점수', 48), ('label', '판정', 42), ('title', '제목', 220),
                                   ('source', '자료집', 100), ('facets', '관점', 120), ('year', '연대', 56), ('status', '상태', 84)):
            self.tree.heading(name, text=label, anchor='center' if name in centered else 'w')
            self.tree.column(name, width=px(width), minwidth=px(40), stretch=name == 'title',
                             anchor='center' if name in centered else 'w')
        scroll = ttk.Scrollbar(upper, orient='vertical', command=self.tree.yview, style='Card.Vertical.TScrollbar')
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        self.tree.tag_configure('even', background=CARD)
        self.tree.tag_configure('odd', background='#FBFAF7')
        for key, label in (('1', 1), ('0', 0), ('<Delete>', None), ('<BackSpace>', None)):
            self.tree.bind(key, lambda e, v=label: self.judge(v))
        self.tree.bind('f', lambda e: self.toggle_favorite())
        self.tree.bind('<Button-3>', self._tree_menu)
        self.bind('<Control-s>', lambda e: self.save_selection_or_doc())

        lower = tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        split.add(lower, weight=4)
        bar = tk.Frame(lower, bg=HEAD)
        bar.pack(side='top', fill='x')
        ui.hline(lower, LINE).pack(side='top', fill='x')
        tk.Label(bar, text='이 문헌은', bg=HEAD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(14), px(8)), pady=px(8))
        ttk.Button(bar, text='✓ 관련 있음  1', style='Outline.TButton', command=lambda: self.judge(1), cursor='hand2').pack(side='left', pady=px(6))
        ttk.Button(bar, text='✕ 관련 없음  0', command=lambda: self.judge(0), cursor='hand2').pack(side='left', padx=(px(6), 0), pady=px(6))
        ttk.Button(bar, text='지우기', style='Quiet.TButton', command=lambda: self.judge(None), cursor='hand2').pack(side='left', padx=(px(2), 0))
        fav = tk.Frame(lower, bg=HEAD)
        fav.pack(side='top', fill='x')
        self.fav_button = ttk.Button(fav, text='☆ 즐겨찾기', style='Quiet.TButton', command=self.toggle_favorite, cursor='hand2')
        self.fav_button.pack(side='left', padx=(px(8), 0), pady=(0, px(4)))
        ttk.Button(fav, text='❝ 선택 부분 저장', style='Quiet.TButton', command=self.save_selection, cursor='hand2').pack(side='left', pady=(0, px(4)))
        tk.Label(fav, text='분류', bg=HEAD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(8), px(4)))
        self.cat_box = ttk.Combobox(fav, textvariable=self.save_cat, state='readonly', width=12,
                                    postcommand=lambda: self.cat_box.configure(values=self.bm.names() + ['＋ 새 분류…']))
        self.cat_box.pack(side='left')
        self.cat_box.bind('<<ComboboxSelected>>', self._category_chosen)
        tk.Label(fav, textvariable=self.bm_msg, bg=HEAD, fg=OK_INK, font=ui.FONTS['label_sm']).pack(side='left', padx=px(8))
        ui.hline(lower, LINE).pack(side='top', fill='x')
        self.topic_bar = tk.Frame(lower, bg=HEAD)
        self.topic_bar.pack(side='top', fill='x')
        self.topic_vars = {}

        self.detail = _reading_text(lower, height=15)
        dscroll = ttk.Scrollbar(lower, orient='vertical', command=self.detail.yview, style='Card.Vertical.TScrollbar')
        self.detail.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side='right', fill='y')
        self.detail.pack(side='left', fill='both', expand=True)
        self.detail.tag_configure('saved', background='#F5ECDA')
        self.detail.bind('<Button-3>', self._detail_menu)
        # 본문 펼치기 단추(본문 칸 안의 글자 단추)
        self.detail.tag_configure('action', font=ui.FONTS['label'], foreground='white', background=ACCENT,
                                  spacing1=px(10), spacing3=px(4))
        self.detail.tag_configure('action_off', font=ui.FONTS['label'], foreground=MUTED, background=NEUTRAL_PALE,
                                  spacing1=px(10), spacing3=px(4))
        self.detail.tag_bind('action', '<Button-1>', lambda e: self.expand_body())
        self.detail.tag_bind('action', '<Enter>', lambda e: self.detail.configure(cursor='hand2'))
        self.detail.tag_bind('action', '<Leave>', lambda e: self.detail.configure(cursor='arrow'))
        self.detail.tag_bind('open_link', '<Button-1>', lambda e: self.open_original())
        self.detail.tag_bind('open_link', '<Enter>', lambda e: self.detail.configure(cursor='hand2'))
        self.detail.tag_bind('open_link', '<Leave>', lambda e: self.detail.configure(cursor='arrow'))
        self.detail.tag_configure('open_link', font=ui.FONTS['label'], foreground=ACCENT, underline=True)
        # 정리된 본문: 문단 간격, 원문(한문)·주석 접기, 주석 표시([註 001]) 누르면 해당 풀이로
        self._show_hanja = bool(last.get('show_hanja', False))
        self._show_notes = bool(last.get('show_notes', False))
        self._struct = {}
        self.detail.tag_configure('para', spacing1=px(2), spacing3=px(14))
        self.detail.tag_configure('hanja_t', spacing2=px(7))
        self.detail.tag_configure('noteitem', font=ui.FONTS['small'], foreground=INK, lmargin1=px(4), lmargin2=px(22),
                                  spacing3=px(7))
        self.detail.tag_configure('fn', font=ui.FONTS['small'], foreground=ACCENT, offset=px(4))
        self.detail.tag_configure('flash', background='#FFEFA8')
        for tag, func in (('tg_hanja', lambda e: self._toggle('hanja')), ('tg_notes', lambda e: self._toggle('notes')),
                          ('fn', self._jump_note)):
            self.detail.tag_bind(tag, '<Button-1>', func)
            self.detail.tag_bind(tag, '<Enter>', lambda e: self.detail.configure(cursor='hand2'))
            self.detail.tag_bind(tag, '<Leave>', lambda e: self.detail.configure(cursor='arrow'))
        self.bind('<Return>', lambda e: self.expand_body() if e.widget in (self.tree, self.detail) else None)
        self._split = split
        split.bind('<Configure>', self._place_sash)
        self.reload()

    def _place_sash(self, event):
        # 처음 한 번만 목록 58% : 본문 42%로 나눕니다.
        if event.width > px(400) and not getattr(self, '_sash_done', False):
            self._sash_done = True
            self.after_idle(lambda: self._split.sashpos(0, int(event.width * 0.58)))

    def _on_escape(self, event=None):
        if self.focus_get() is self.query_entry and self.query.get():
            self.query.set('')
            return
        self.go_back()

    def go_back(self):
        # 결과 창을 닫고 메인 연구실 창으로 돌아갑니다.
        parent = self.master
        self.destroy()
        try:
            parent.deiconify()
            parent.lift()
            parent.focus_force()
        except tk.TclError:
            pass

    def reload(self):
        try:
            self.rows, self.profile = read_results(self.folder)
            self._fetching.clear()
            rows_for_hanja = list(self.rows)   # 사료에 쓰인 '한글(漢字)' 표기를 한자 변환 후보로
            threading.Thread(target=lambda: hanja_input.learn_pairs(rows_for_hanja), daemon=True).start()
            self._struct = {}
            self._load_priority()
        except Exception as exc:
            self.rows, self.filtered, self.shown = [], [], {}
            children = self.tree.get_children()
            if children:
                self.tree.delete(*children)
            self.period_box.configure(values=['모든 연대'])
            self._write_detail([(str(exc), 'note')])
            self.info.set(str(exc))
            return
        self.period_box.configure(values=['모든 연대'] + sorted({r['analysis']['period'] for r in self.rows}))
        self._load_search_terms()
        self.judg = ranker.Judgments(self.folder, self.profile.get('target', ''))
        self._by_key = {r.get('key'): r for r in self.rows}
        self.index, self.wordspace, self.classifier = None, None, None
        self.topics = tl.TopicLabels(self.folder, self.profile.get('target', ''), list(self.profile.get('facets', {})))
        self.facet_box.configure(values=['모든 관점'] + list(self.topics.labels) + ['미분류'])
        self.refresh_topic_bar()
        self._train(full=True)
        self.filter_rows()

    def _load_priority(self):
        try:
            data = json.loads((self.folder / 'priority.json').read_text(encoding='utf-8'))
            keys = data.get('keys') or []
        except (OSError, ValueError, AttributeError):
            keys = []
        self.priority_keys = set(keys) or None
        if self.priority_keys:
            self.priority_check.configure(text=f'우선 {len(self.priority_keys)}건만')
            self.priority_check.pack(side='left', padx=(px(10), 0), before=self.priority_anchor)
        else:
            self.priority_check.pack_forget()

    def schedule_filter(self, *args):
        if self._timer:
            self.after_cancel(self._timer)
        self._timer = self.after(200, self.filter_rows)

    def filter_rows(self):
        self._timer = None
        keep = {r.get('key') for r in self.selected()}  # 새로고침·필터 후에도 보던 문헌 선택 유지
        query = self.query.get().casefold().strip()
        minimum = {'전체 후보': -10000, '60점 이상': 60, '80점 이상': 80}[self.threshold.get()]
        facet, period, source = self.facet.get(), self.period.get(), self.source_filter.get()
        wanted = None
        if source != '모든 자료원':
            wanted = next((s.id for s in BY_ID.values() if s.short == source), None)
        self.filtered = []
        for row in self.rows:
            a = row['analysis']
            if wanted is not None:
                origins = row.get('source_ids') or ['history' if row.get('kind') == 'db' else row.get('kind')]
                if wanted not in ('history' if key == 'db' else key for key in origins):
                    continue
            if a['score'] < minimum:
                continue
            if facet != '모든 관점':
                topics = self._topics_of(row)
                if facet == '미분류' and topics:
                    continue
                if facet != '미분류' and facet not in topics:
                    continue
            if period != '모든 연대' and a['period'] != period:
                continue
            if query and query not in row.get('_search', ''):
                continue
            if self.priority_keys and self.only_priority.get() and row.get('key') not in self.priority_keys:
                continue
            if self.only_hits.get() and not row.get('_hit'):
                continue
            self.filtered.append(row)
        self._sort(self.filtered)
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.shown = {str(i): row for i, row in enumerate(self.filtered[:3000])}
        for i, row in self.shown.items():
            a = row['analysis']
            self.tree.insert('', 'end', iid=i, tags=('even' if int(i) % 2 == 0 else 'odd',),
                             values=(self._fav_text(row), '✓' if row.get('_hit') else '', self._rec_text(row), a['score'], self._label_text(row), row.get('title', '(제목 없음)'), row.get('source', ''),
                                     self._facet_text(row), a['year'] or '미상',
                                     STATUS.get(row.get('status'), row.get('status', ''))))
        counts = Counter(name for row in self.filtered for name in row['analysis']['facets'])
        prio = (f'  ·  우선 본문: 관련성 높은 {len(self.priority_keys)}건만 보는 중' if self.priority_keys and self.only_priority.get() else '')
        self.info.set(f'{self.folder}\n전체 {len(self.rows)}건 중 {len(self.filtered)}건 표시' + prio
                      + (('  ·  관점: ' + ' · '.join(f'{name} {counts[name]}건' for name in hf.NAMES if counts[name]))
                         if any(counts[name] for name in hf.NAMES) else ''))
        if self.shown:
            again = [i for i, row in self.shown.items() if keep and row.get('key') in keep]
            self.tree.selection_set(again or ['0'])
            self.tree.see((again or ['0'])[0])
            self.show_selected()
        else:
            self._write_detail([('현재 조건에 해당하는 자료가 없습니다.', 'note')])

    # ── 텍스트 분석 · 주제 ──

    def row_by_key(self, key):
        return self._by_key.get(key)

    def ensure_index(self):
        if self.index is None:
            self.configure(cursor='watch')
            self.update_idletasks()
            try:
                self.index = tl.TfidfIndex(self.rows)
            finally:
                self.configure(cursor='')
        return self.index

    def open_lab(self, tab, **kw):
        if self.lab is not None and self.lab.winfo_exists():
            self.lab.deiconify()
            self.lab.lift()
            self.lab.show(tab, **kw)
            return
        self.lab = TextLabWindow(self, tab)
        if kw:
            self.lab.show(tab, **kw)

    def focus_key(self, key):
        for iid, row in self.shown.items():
            if row.get('key') == key:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.show_selected()
                self.lift()
                return
        messagebox.showinfo('문헌 열기', '현재 필터에 보이지 않는 문헌입니다. 필터를 풀고 다시 시도하세요.', parent=self)

    def _topics_of(self, row):
        topics = set(row['analysis'].get('facets', {}))
        if self.classifier is not None:
            topics.update(self.classifier.predict(row.get('key')))
        ann = self.topics.get(row.get('key')) if self.topics else None
        return ann if ann is not None else topics

    def _facet_text(self, row):
        key = row.get('key')
        ann = self.topics.get(key) if self.topics else None
        if ann is not None:
            return ('✎ ' + ', '.join(sorted(ann))) if ann else '✎ 해당 없음'
        rule = list(row['analysis'].get('facets', {}))
        extra = [n for n in (self.classifier.predict(key) if self.classifier else []) if n not in rule]
        return ', '.join(rule + ['+' + n for n in extra]) or '미분류'

    def refresh_topic_bar(self, row=None):
        bar = self.topic_bar
        for w in bar.winfo_children():
            w.destroy()
        if self.topics is None:
            return
        tasks = self.topics.task_names()
        if self.annot_task.get() not in tasks:
            self.annot_task.set('주제')
        task = self.annot_task.get()
        row = row or (self.selected() or [None])[0]
        key = row.get('key') if row else None
        ann = self.topics.task_get(task, key) if key else None
        rule, probs = self._suggest(task, row)
        top = tk.Frame(bar, bg=HEAD)
        top.pack(fill='x', padx=(px(14), px(10)), pady=(px(6), 0))
        box = ttk.Combobox(top, textvariable=self.annot_task, values=tasks, state='readonly', width=10)
        box.pack(side='left')
        box.bind('<<ComboboxSelected>>', lambda e: self.refresh_topic_bar())
        tk.Label(top, text=' ✎ 표시함' if ann is not None else ' 아직 표시 안 함 · 괄호 안은 제안', bg=HEAD,
                 fg=ACCENT if ann is not None else MUTED, font=ui.FONTS['label_sm']).pack(side='left')
        if key:
            for text, command in (('지우기', lambda: self.save_topics(clear=True)), ('해당 없음', self.no_topics),
                                  ('제안대로', self.accept_topics)):
                link = tk.Label(top, text=text, bg=HEAD, fg=ACCENT, font=ui.FONTS['label_sm'], cursor='hand2')
                link.pack(side='right', padx=(px(12), 0))
                link.bind('<Button-1>', lambda e, c=command: c())
        checks = tk.Frame(bar, bg=HEAD)
        checks.pack(fill='x', padx=(px(10), px(10)), pady=(px(2), px(6)))
        self.topic_vars = {}
        for i, name in enumerate(self.topics.task_labels(task)):
            var = tk.BooleanVar(value=(name in ann) if ann is not None else False)
            self.topic_vars[name] = var
            hint = []
            if name in rule:
                hint.append('제안')
            if name in probs:
                hint.append(f'{probs[name]:.2f}')
            cb = tk.Checkbutton(checks, text=name + (f" ({' · '.join(hint)})" if hint else ''), variable=var, bg=HEAD,
                                activebackground=HEAD, fg=INK, selectcolor=CARD, font=ui.FONTS['small'], bd=0,
                                highlightthickness=0, cursor='hand2', command=self.save_topics)
            cb.grid(row=i // 3, column=i % 3, sticky='w', padx=(0, px(10)))   # 세 개씩 줄 바꿈

    def _suggest(self, task, row):
        """과제별 제안: 주제는 규칙 관점 + 분류 모델, 자료 유형은 서식 규칙, 직접 만든 과제는 없음."""
        if not row:
            return set(), {}
        key = row.get('key')
        if task == '주제':
            rule = set(row['analysis'].get('facets', {}))
            probs = self.classifier.probs.get(key, {}) if self.classifier else {}
            return rule, probs
        if task == '자료 유형':
            return mtl.rule_types(row), {}
        return set(), {}

    def save_topics(self, clear=False):
        rows = self.selected()
        if not rows or self.topics is None:
            return
        task = self.annot_task.get()
        chosen = None if clear else {n for n, v in self.topic_vars.items() if v.get()}
        for row in rows:
            self.topics.task_set(task, row.get('key'), chosen, row.get('title', ''))
        if task == '주제':
            for iid, row in self.shown.items():
                if row in rows:
                    self.tree.set(iid, 'facets', self._facet_text(row))
        self.refresh_topic_bar(rows[0])

    def no_topics(self):
        for v in self.topic_vars.values():
            v.set(False)
        self.save_topics()

    def accept_topics(self):
        row = (self.selected() or [None])[0]
        if row is None:
            return
        task = self.annot_task.get()
        rule, probs = self._suggest(task, row)
        suggestion = set(rule)
        if task == '주제' and self.classifier is not None:
            suggestion |= set(self.classifier.predict(row.get('key')))
        for n, v in self.topic_vars.items():
            v.set(n in suggestion)
        self.save_topics()

    # ── 즐겨찾기 ──

    def _fav_text(self, row):
        key = row.get('key')
        if self.bm.document_bookmark(self.folder, key):
            return '★'
        if self.bm.excerpts_of(self.folder, key):
            return '❝'
        return ''

    def _refresh_fav_cells(self, rows):
        for iid, row in self.shown.items():
            if row in rows:
                self.tree.set(iid, 'fav', self._fav_text(row))
        parent = self.master
        if hasattr(parent, 'refresh_bookmarks'):
            parent.refresh_bookmarks()

    def _flash(self, text):
        self.bm_msg.set(text)
        self.after(2200, lambda: self.winfo_exists() and self.bm_msg.get() == text and self.bm_msg.set(''))

    def _category_chosen(self, _=None):
        if self.save_cat.get() == '＋ 새 분류…':
            self.save_cat.set(self.new_category() or bm.UNSORTED)

    def new_category(self):
        name = simpledialog.askstring('새 분류', '분류 이름 (예: 추숭 논쟁, 궁방 경제)', parent=self)
        if not name:
            return None
        made = self.bm.create(name)
        if not made:
            messagebox.showinfo('새 분류', '이름이 비었거나 이미 있는 분류입니다.', parent=self)
            return None
        return made

    def _mark_saved(self, row):
        self.detail.tag_remove('saved', '1.0', 'end')
        key = row.get('key') if row else None
        doc = self.bm.document_bookmark(self.folder, key) if key else None
        self.fav_button.configure(text=f'★ 즐겨찾기됨 · {doc["category"]}' if doc else '☆ 즐겨찾기')
        if not key:
            return
        text = None
        for it in self.bm.excerpts_of(self.folder, key):
            probe = (it.get('text') or '').strip()
            if not probe:
                continue
            if text is None:
                text = self.detail.get('1.0', 'end-1c')
            rx = _terms_regex([probe[:2000]])
            m = rx.search(text) if rx else None
            if m:
                self.detail.tag_add('saved', f'1.0+{m.start()}c', f'1.0+{m.end()}c')

    def toggle_favorite(self, category=None):
        rows = self.selected()
        if not rows:
            return 'break'
        category = category or self.save_cat.get()
        if category not in self.bm.names():
            category = bm.UNSORTED
        all_saved = all(self.bm.document_bookmark(self.folder, r.get('key')) for r in rows)
        for row in rows:
            it = self.bm.document_bookmark(self.folder, row.get('key'))
            if all_saved and it:
                self.bm.remove(it['id'])
            elif not it:
                self.bm.add(row, self.folder, category=category)
        self._flash('해제됨' if all_saved else '저장 ✓')
        self._refresh_fav_cells(rows)
        self._mark_saved(rows[0])
        return 'break'

    def _selected_text(self):
        try:
            return self.detail.get('sel.first', 'sel.last').strip()
        except tk.TclError:
            return ''

    def save_selection(self, category=None):
        rows = self.selected()
        text = self._selected_text()
        if not rows:
            return
        if not text:
            messagebox.showinfo('선택 부분 저장', '오른쪽 본문에서 저장할 부분을 마우스로 끌어 선택한 뒤 누르세요.', parent=self)
            return
        category = category or self.save_cat.get()
        if category not in self.bm.names():
            category = bm.UNSORTED
        self.bm.add(rows[0], self.folder, text=text, category=category)
        self._flash(f'발췌 {len(text)}자 저장 ✓')
        self._refresh_fav_cells(rows[:1])
        self._mark_saved(rows[0])

    def save_selection_or_doc(self):
        if self._selected_text():
            self.save_selection()
        else:
            row = (self.selected() or [None])[0]
            if row is not None and not self.bm.document_bookmark(self.folder, row.get('key')):
                self.toggle_favorite()
        return 'break'

    def _category_menu(self, parent, command):
        sub = tk.Menu(parent, tearoff=0)
        for name in self.bm.names():
            sub.add_command(label=name, command=lambda n=name: command(n))
        sub.add_separator()

        def make_and_run():
            made = self.new_category()
            if made:
                self.save_cat.set(made)
                command(made)
        sub.add_command(label='새 분류 만들기…', command=make_and_run)
        return sub

    def _detail_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        has_sel = bool(self._selected_text())
        menu.add_cascade(label='선택 부분 저장 →', menu=self._category_menu(menu, lambda n: self.save_selection(n)),
                         state='normal' if has_sel else 'disabled')
        menu.add_cascade(label='이 문헌 즐겨찾기 →', menu=self._category_menu(menu, lambda n: self._fav_to(n)))
        if has_sel:
            menu.add_separator()
            menu.add_command(label='복사', command=lambda: (self.clipboard_clear(), self.clipboard_append(self._selected_text())))
        menu.tk_popup(event.x_root, event.y_root)

    def _tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self.show_selected()
        if not self.selected():
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_cascade(label='즐겨찾기 →', menu=self._category_menu(menu, lambda n: self._fav_to(n)))
        if any(self.bm.document_bookmark(self.folder, r.get('key')) for r in self.selected()):
            menu.add_command(label='즐겨찾기 해제', command=self._unfav)
        menu.tk_popup(event.x_root, event.y_root)

    def _fav_to(self, category):
        rows = self.selected()
        for row in rows:
            it = self.bm.document_bookmark(self.folder, row.get('key'))
            if it:
                self.bm.move(it['id'], category)
            else:
                self.bm.add(row, self.folder, category=category)
        self._flash('저장 ✓')
        self._refresh_fav_cells(rows)
        if rows:
            self._mark_saved(rows[0])

    def _unfav(self):
        rows = self.selected()
        for row in rows:
            it = self.bm.document_bookmark(self.folder, row.get('key'))
            if it:
                self.bm.remove(it['id'])
        self._flash('해제됨')
        self._refresh_fav_cells(rows)
        if rows:
            self._mark_saved(rows[0])

    # ── 판정 · 학습 ──

    def _train(self, full=False):
        if self.judg is None:
            return
        data = ranker.training_data(self.rows, self.judg)
        if full or not self.model.trained:
            self.model = ranker.Ranker().fit(data)
        else:
            self.model.fit(data, epochs=8, warm=True)
        self.pred_raw = {r.get('key'): self.model.prob(ranker.features(r)) for r in self.rows} if self.model.trained else {}
        pos, neg = self.judg.counts()
        self.pred = ranker.blend(self.rows, self.pred_raw, pos + neg)
        if self.model.trained:
            w = ranker.blend_weight(pos + neg)
            self.learn_info.set(f'판정 {pos + neg}건 (관련 {pos} · 무관 {neg}) · 추천 = 판정 학습 {w * 100:.0f}% + 기본 점수 {100 - w * 100:.0f}%')
        else:
            need = []
            if pos < ranker.MIN_PER_CLASS:
                need.append(f'관련 {ranker.MIN_PER_CLASS - pos}건')
            if neg < ranker.MIN_PER_CLASS:
                need.append(f'무관 {ranker.MIN_PER_CLASS - neg}건')
            self.learn_info.set(f'판정 {pos + neg}건 · 지금 추천은 기본 점수만 씀 · 판정 학습까지 ' + ', '.join(need) + ' 더')

    def _rec_text(self, row):
        p = self.pred.get(row.get('key'))
        return '–' if p is None else str(int(round(p * 100)))

    def _label_text(self, row):
        label = self.judg.get(row.get('key')) if self.judg else None
        return '✓' if label == 1 else '✕' if label == 0 else ''

    def _sort(self, rows):
        mode = self.sort_mode.get()
        rule = {id(r): i for i, r in enumerate(self.rows)}  # 원래 규칙 점수 순서
        if mode == '추천순' and self.pred:
            rows.sort(key=lambda r: (-self.pred.get(r.get('key'), 0.0), rule[id(r)]))
        elif mode == '판정 필요순':
            rows.sort(key=self._need_key(rows, rule))
        else:
            rows.sort(key=lambda r: rule[id(r)])

    def _need_key(self, rows, rule):
        # 능동 학습: 모델이 가장 헷갈리는(추천 50 근처) 미판정 문헌부터.
        # 학습 전에는 규칙 점수 경계 근처를, 자료집이 번갈아 나오게 섞어서 보여줍니다.
        turn, seen = {}, {}
        for r in sorted(rows, key=lambda r: rule[id(r)]):
            src = r.get('source', '')
            turn[id(r)] = seen.get(src, 0)
            seen[src] = turn[id(r)] + 1

        def key(r):
            labeled = self.judg is not None and self.judg.get(r.get('key')) is not None
            if self.pred_raw:
                return (labeled, round(abs(self.pred_raw.get(r.get('key'), 0.5) - 0.5), 2), turn[id(r)], rule[id(r)])
            return (labeled, turn[id(r)], round(abs(r['analysis']['score'] - 60) / 100.0, 1), rule[id(r)])
        return key

    def _next_to_judge(self, current):
        if self.sort_mode.get() == '판정 필요순':
            rule = {id(r): i for i, r in enumerate(self.rows)}
            open_rows = [(i, r) for i, r in self.shown.items() if i != current and self.judg.get(r.get('key')) is None]
            if open_rows:
                key = self._need_key([r for _, r in open_rows], rule)
                return min(open_rows, key=lambda x: key(x[1]))[0]
        return self.tree.next(current)

    def judge(self, label):
        rows = self.selected()
        if not rows or self.judg is None:
            return 'break'
        for row in rows:
            self.judg.set(row.get('key'), label, row.get('title', ''))
        self._train()
        for iid, row in self.shown.items():
            self.tree.set(iid, 'rec', self._rec_text(row))
            self.tree.set(iid, 'label', self._label_text(row))
        current = self.tree.selection()
        if len(current) == 1 and label is not None:
            nxt = self._next_to_judge(current[0])
            if nxt:
                self.tree.selection_set(nxt)
                self.tree.see(nxt)
                self.tree.focus(nxt)
        self.show_selected()
        return 'break'

    def selected(self):
        return [self.shown[key] for key in self.tree.selection() if key in self.shown]

    # ── 검색어 표시 ──

    def _load_search_terms(self):
        """이번 검색에 쓴 검색어 + 연구 대상의 직접 명칭. 띄어쓰기는 무시하고 찾습니다(숙빈 최씨 = 숙빈최씨)."""
        terms = []
        try:
            terms += json.loads((self.folder / 'active_queries.json').read_text(encoding='utf-8')).get('terms', [])
        except (OSError, ValueError, AttributeError):
            pass
        prof = self.profile or {}
        terms += list(prof.get('quick_terms', [])) + list(prof.get('direct_aliases', []))
        if not terms and prof.get('target'):
            terms.append(prof['target'])
        for r in self.rows:
            terms += [t for t in r.get('terms', []) or [] if isinstance(t, str)]
        seen, clean = set(), []
        for t in terms:
            key = re.sub(r'\s+', '', str(t))
            if len(key) >= 2 and key not in seen:
                seen.add(key)
                clean.append(str(t).strip())
        self.hl_terms = clean
        self.hl_re = _terms_regex(clean)
        for r in self.rows:
            text = ' '.join(str(r.get(k) or '') for k in ('title', 'snippet', 'body'))
            r['_hit'] = bool(self.hl_re and self.hl_re.search(text))

    def _highlight_terms(self, row):
        for tag in ('hit', 'hit_small', 'hit_bg'):
            self.detail.tag_remove(tag, '1.0', 'end')
        extra = self.query.get().strip()
        rx = _terms_regex(self.hl_terms + ([extra] if len(extra) >= 2 else []))
        if rx is None:
            return
        text = self.detail.get('1.0', 'end-1c')
        for m in rx.finditer(text):
            start, end = f'1.0+{m.start()}c', f'1.0+{m.end()}c'
            names = set(self.detail.tag_names(start))
            if names & {'chip', 'chip2', 'chip_ok'}:
                continue
            tag = 'hit_bg' if 'title' in names else 'hit' if 'body' in names else 'hit_small'
            self.detail.tag_add(tag, start, end)
        for tag in ('hit_bg', 'hit_small', 'hit'):
            self.detail.tag_raise(tag)

    def _write_detail(self, parts):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        for text, tag in parts:
            self.detail.insert('end', text, tag)
        self.detail.configure(state='disabled')

    def _structured(self, row):
        key = row.get('key')
        doc = self._struct.get(key)
        if doc is None:
            try:
                doc = reader.structure(row, self.folder)
            except Exception:
                doc = {'sections': [{'label': '본문', 'paras': [row['body']], 'hanja': False}], 'notes': []}
            self._struct[key] = doc
        return doc

    @staticmethod
    def _para_parts(text, tags):
        out, pos = [], 0
        for m in reader.MARK.finditer(text):
            if m.start() > pos:
                out.append((text[pos:m.start()], tags))
            out.append((m.group(0), tags + ('fn',)))
            pos = m.end()
        out.append((text[pos:] + '\n', tags))
        return out

    def _hit_count(self, texts):
        rx = getattr(self, 'hl_re', None)
        return sum(len(rx.findall(t)) for t in texts) if rx else 0

    def _body_parts(self, row):
        doc = self._structured(row)
        secs = doc['sections']
        has_ko = any(not s['hanja'] for s in secs)
        parts = []
        for sec in secs:
            foldable = sec['hanja'] and has_ko
            if foldable:
                hits = self._hit_count(sec['paras'])
                opened = self._show_hanja
                parts.append((f"{'▾' if opened else '▸'} {sec['label']} · 한문 {len(''.join(sec['paras'])):,}자"
                               + (f' · 검색어 {hits}곳' if hits and not opened else '')
                               + ('  (접기)' if opened else '  (펼치기)') + '\n', ('head', 'tg_hanja')))
                if not opened:
                    continue
            else:
                parts.append((sec['label'] + '\n', 'head'))
            for p in sec['paras']:
                if p.lstrip().startswith(('【', 'ⓒ')):   # 실록 출처 정보(사고본·영인본·분류)는 작고 흐리게
                    parts += self._para_parts(p, ('meta', 'para'))
                    continue
                parts += self._para_parts(p, ('body', 'para') + (('hanja_t',) if sec['hanja'] else ()))
        notes = doc['notes']
        if notes:
            opened = self._show_notes
            parts.append((f"{'▾' if opened else '▸'} 주석 · 낱말 풀이 {len(notes)}개" + ('  (접기)' if opened else '  (펼치기)') + '\n',
                          ('head', 'tg_notes')))
            if opened:
                for n in notes:
                    parts += self._para_parts(n, ('noteitem',))
        return parts

    def _remember(self):
        try:
            settings.save(results={'threshold': self.threshold.get(), 'sort': self.sort_mode.get(),
                                   'only_hits': bool(self.only_hits.get()), 'save_cat': self.save_cat.get(),
                                   'show_hanja': self._show_hanja, 'show_notes': self._show_notes})
        except (tk.TclError, AttributeError):
            pass

    def _toggle(self, what):
        if what == 'hanja':
            self._show_hanja = not self._show_hanja
        else:
            self._show_notes = not self._show_notes
        self._remember()
        top = self.detail.yview()[0]
        self.show_selected()
        self.detail.yview_moveto(top)
        return 'break'

    def _jump_note(self, event):
        index = self.detail.index(f'@{event.x},{event.y}')
        rng = self.detail.tag_prevrange('fn', f'{index}+1c')
        mark = self.detail.get(*rng) if rng else ''
        if not self._show_notes:
            self._show_notes = True
            top = self.detail.yview()[0]
            self.show_selected()
            self.detail.yview_moveto(top)
        head = self.detail.tag_ranges('tg_notes')
        found = self.detail.search(mark, head[0], 'end') if mark and head else ''
        if found:
            self.detail.tag_remove('flash', '1.0', 'end')
            self.detail.tag_add('flash', found, f'{found} lineend')
            self.detail.see(found)
            self.after(1800, lambda: self.detail.winfo_exists() and self.detail.tag_remove('flash', '1.0', 'end'))
        return 'break'

    def show_selected(self, event=None):
        rows = self.selected()
        if not rows:
            return
        row = rows[0]
        a = row['analysis']
        status = STATUS.get(row.get('status'), row.get('status', ''))
        label = self.judg.get(row.get('key')) if self.judg else None
        p = self.pred.get(row.get('key'))
        parts = []
        if p is not None:
            parts += [(f' 추천 {int(round(p * 100))} ', 'chip'), ('  ', None)]
        if label is not None:
            parts += [(' ✓ 관련 ' if label == 1 else ' ✕ 무관 ', 'chip_ok' if label == 1 else 'chip2'), ('  ', None)]
        parts += [
            (f" {', '.join(sorted(self._topics_of(row))) or '미분류'} ", 'chip2'), ('  ', None),
            (f" {a['year'] or '연대 미상'} ", 'chip2'), ('  ', None),
            *([(' 연구·해설 문헌 ', 'chip2'), ('  ', None)] if a.get('secondary') else []),
            (f" {status} ", 'chip2'), ('\n', 'meta'),
            (row.get('title', '(제목 없음)') + '\n', 'title'),
            (row.get('source', '') + '\n', 'meta'),
            (row.get('url', '') + '\n', 'link'),
        ]
        if row.get('body'):
            parts += self._body_parts(row)
        else:
            parts.append(('검색 화면에 보인 일부\n', 'head'))
            paras = reader.snippet_paragraphs(row.get('snippet'))
            for p in paras or ['(검색 화면에 보인 글이 없습니다)']:
                parts += self._para_parts(p, ('body', 'para'))
            if not row.get('download_supported', True):
                parts += [('\n이 자료는 사이트가 서지·요약만 공개합니다. 본문은 원문 사이트에서 보세요.  ', 'note'),
                          ('↗ 원문 사이트 열기', 'open_link'), ('\n', None)]
            elif row.get('key') in self._fetching:
                parts += [('\n', None), ('  본문을 받아 오는 중입니다…  ', 'action_off'),
                          ('\n받는 대로 이 자리에 펼쳐집니다.\n', 'note')]
            else:
                parts += [('\n', None), ('  ▼ 본문 펼치기  ', 'action'),
                          ('\n원문 사이트에서 이 문헌의 본문 전체를 받아 이 자리에 펼칩니다. (Enter 키)\n', 'note'),
                          ('↗ 원문 사이트 열기', 'open_link'), ('\n', None)]
        if row.get('error'):
            parts += [('수집 오류\n', 'head'), (row['error'] + '\n', 'error')]
        self._write_detail(parts)
        self._highlight_terms(row)
        self.detail.yview_moveto(0)
        self.refresh_topic_bar(row)
        self._mark_saved(row)

    def expand_body(self):
        """선택한 문헌(여러 건 가능) 중 본문이 없는 것의 본문을 받아 펼칩니다."""
        rows = [r for r in self.selected() if not r.get('body') and r.get('download_supported', True)
                and r.get('key') not in self._fetching]
        if not rows:
            return
        if self.on_fetch is None:
            self.collect_selected()
            return
        sources = set()
        for r in rows:
            for key in r.get('source_ids') or ['history' if r.get('kind') == 'db' else r.get('kind')]:
                key = 'history' if key == 'db' else key
                if key in BY_ID:
                    sources.add(key)
        keys = [r['key'] for r in rows]
        if self.on_fetch(self.folder, keys, sources, self.profile):
            self._fetching.update(keys)
            self.show_selected()

    def copy_url(self):
        rows = self.selected()
        if rows:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(r.get('url', '') for r in rows))
            self.copy_button.configure(text=f'복사됨 ✓ ({len(rows)}건)')
            self.after(1600, lambda: self.copy_button.winfo_exists() and self.copy_button.configure(text='원문 주소 복사'))

    def collect_selected(self):
        rows = self.selected()
        if not rows:
            return
        supported = [r for r in rows if r.get('download_supported', True)]
        if not supported:
            messagebox.showinfo('서지·요약 자료', '선택한 자료는 공식 검색의 서지·요약을 통합한 기록입니다. '
                                '공식 원문 버튼에서 해당 기관의 본문·이미지를 확인하세요.', parent=self)
            return
        if self.on_collect:
            self.on_collect(self.folder, [r['key'] for r in supported], self.profile)

    def open_original(self):
        rows = self.selected()
        if not rows:
            return
        try:
            open_web_url(rows[0]['url'])
        except Exception as exc:
            messagebox.showerror('원문 열기', str(exc), parent=self)

    def _saved_home(self):
        home = getattr(self.master, 'saved_home', None)
        return Path(home) if home else self.folder.parent / '저장한 검색'

    def save_search(self):
        """지금 보는 검색 결과를 이름을 붙여 '저장한 검색'에 보관합니다."""
        target = (self.profile or {}).get('target') or '검색'
        name = simpledialog.askstring('이 검색 저장', '저장할 이름을 적으세요.\n(문헌·본문·판정·주제 표시가 함께 저장됩니다)',
                                      initialvalue=f'{target} {datetime.now():%Y-%m-%d}', parent=self)
        name = bm.clean_name(name or '')
        if not name:
            return
        home = self._saved_home()
        dest = home / name
        if dest.resolve() == self.folder:
            return
        if dest.exists():
            if not messagebox.askyesno('이 검색 저장', f'「{name}」이(가) 이미 있습니다. 덮어쓸까요?', parent=self):
                return
            shutil.rmtree(dest, ignore_errors=True)
        try:
            home.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.folder, dest, ignore=shutil.ignore_patterns('stop.request', '*.tmp', '.saved'))
            (self.folder / '.saved').write_text(str(dest), encoding='utf-8')
        except OSError as exc:
            messagebox.showerror('이 검색 저장', f'저장하지 못했습니다.\n{exc}', parent=self)
            return
        old = self.folder
        self.bm.repoint(old, dest.resolve())
        self.folder = dest.resolve()
        app = self.master
        if hasattr(app, 'output') and not getattr(app, 'running', False):
            app.output.set(str(self.folder))
            settings.save(output=str(self.folder))
            if hasattr(app, 'refresh_counts'):
                app.refresh_counts()
        self.reload()
        messagebox.showinfo('이 검색 저장', f'「{name}」으로 저장했습니다.\n{self.folder}\n\n'
                            '이제 이 창에서 하는 판정·표시는 저장한 검색에 기록됩니다.', parent=self)

    def choose_folder(self):
        home = self._saved_home()
        folder = filedialog.askdirectory(parent=self, title='저장한 검색 열기', initialdir=home if home.is_dir() else self.folder)
        if folder:
            self.folder = Path(folder).resolve()
            self.reload()

    def open_folder(self):
        try:
            if os.name == 'nt':
                os.startfile(str(self.folder))
            else:
                messagebox.showinfo('결과 폴더', str(self.folder), parent=self)
        except OSError as exc:
            messagebox.showerror('폴더 열기', str(exc), parent=self)



class PerformanceWindow(tk.Toplevel):
    """추천 성능: 내 판정 기준으로 규칙 점수와 학습 추천을 비교합니다."""

    def __init__(self, parent):
        super().__init__(parent)
        apply_theme(self)
        self.title('추천 성능 — 내 판정 기준')
        ui.fit_geometry(self, 920, 720, 700, 520)
        self.bind('<Escape>', lambda e: self.destroy())
        head = tk.Frame(self, bg=CARD)
        head.pack(fill='x')
        inner = tk.Frame(head, bg=CARD)
        inner.pack(fill='x', padx=px(24), pady=(px(18), px(14)))
        trow = tk.Frame(inner, bg=CARD)
        trow.pack(anchor='w')
        ttk.Button(trow, text='←  결과로', style='Quiet.TButton', command=self.destroy, cursor='hand2').pack(side='left', padx=(0, px(10)))
        tk.Label(trow, text='추천 성능', bg=CARD, fg=INK, font=ui.FONTS['h1']).pack(side='left')
        tk.Label(inner, text='내가 관련/무관을 표시한 문헌을 기준으로 기본 점수만, 판정 학습만, 둘을 섞은 추천(목록 정렬에 쓰는 방식) 중 어느 쪽이 더 잘 맞히는지 비교합니다.',
                 bg=CARD, fg=MUTED, font=ui.FONTS['small'], justify='left').pack(anchor='w', pady=(px(4), 0))
        ui.hline(head, LINE).pack(fill='x')
        body = ui.ScrollFrame(self)
        body.pack(fill='both', expand=True)
        page = tk.Frame(body.body, bg=BG)
        page.pack(fill='both', expand=True, padx=px(24), pady=px(18))

        data = ranker.training_data(parent.rows, parent.judg) if parent.judg else []
        pos = sum(y for _, y in data)
        unlabeled = len(parent.rows) - len(data)
        stats = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        stats.pack(fill='x')
        for i, (label, value) in enumerate((('판정한 문헌', len(data)), ('관련', pos), ('무관', len(data) - pos), ('미판정', unlabeled))):
            stats.columnconfigure(i, weight=1, uniform='s')
            cell = tk.Frame(stats, bg=CARD, padx=px(18), pady=px(12))
            cell.grid(row=0, column=i, sticky='nsew')
            tk.Label(cell, text=label, bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w')
            tk.Label(cell, text=str(value), bg=CARD, fg=INK, font=ui.FONTS['metric']).pack(anchor='w')

        if pos < 2 or len(data) - pos < 2:
            tk.Label(page, text='관련·무관 판정이 각각 2건 이상 있어야 비교할 수 있습니다.\n'
                                '결과 창에서 문헌을 고르고 1(관련) / 0(무관)을 누르세요. "판정 필요순" 정렬이 도움이 됩니다.',
                     bg=BG, fg=MUTED, font=ui.FONTS['body'], justify='left').pack(anchor='w', pady=(px(18), 0))
            return
        ev = ranker.evaluate(data)
        table = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        table.pack(fill='x', pady=(px(16), 0))
        table.columnconfigure(0, weight=1)
        for c, text in enumerate(('기준', '기본 점수만', '판정 학습만', '추천 (둘을 섞음 · 지금 쓰는 방식)')):
            tk.Label(table, text=text, bg=HEAD, fg=MUTED, font=ui.FONTS['label_sm'], anchor='w' if c == 0 else 'center',
                     padx=px(14), pady=px(8)).grid(row=0, column=c, sticky='nsew')

        def fmt(v):
            return '–' if v is None else f'{v * 100:.0f}%'

        for r, (name, *vals) in enumerate(ev['rows'], start=1):
            best = max((v for v in vals if v is not None), default=None)
            tk.Label(table, text=name, bg=CARD, fg=INK, font=ui.FONTS['body'], anchor='w', padx=px(14), pady=px(8)).grid(row=r, column=0, sticky='nsew')
            for c, v in enumerate(vals, start=1):
                top = v is not None and best is not None and v >= best - 1e-9 and len([x for x in vals if x is not None]) > 1
                tk.Label(table, text=fmt(v), bg=CARD, fg=ACCENT if top else INK,
                         font=ui.FONTS['label'] if top else ui.FONTS['body'], width=10).grid(row=r, column=c, sticky='nsew')
        tk.Label(page, text=f'참고: 표시한 문헌 중 관련 비율은 {fmt(ev["base"])}입니다. 아무렇게나 늘어놓아도 \'상위 몇 건 중 관련 비율\'은 이 값, 마지막 줄은 50%가 나옵니다.\n'
                            '목록 위·아래를 고르게 표시할수록 믿을 만합니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small'], justify='left').pack(anchor='w', pady=(px(8), 0))

        if parent.model.trained:
            up, down = parent.model.top_weights(8)
            cols = tk.Frame(page, bg=BG)
            cols.pack(fill='x', pady=(px(16), 0))
            cols.columnconfigure(0, weight=1, uniform='w')
            cols.columnconfigure(1, weight=1, uniform='w')
            for c, (title, items, color) in enumerate((('추천을 올리는 특징', up, OK_INK), ('추천을 내리는 특징', down, '#9A2F1F'))):
                box = ui.card(cols, 18, 14)
                box.grid(row=0, column=c, sticky='nsew', padx=(0, px(8)) if c == 0 else 0)
                tk.Label(box, text=title, bg=CARD, fg=INK, font=ui.FONTS['h3']).pack(anchor='w', pady=(0, px(6)))
                for name, w in items:
                    line = tk.Frame(box, bg=CARD)
                    line.pack(fill='x')
                    tk.Label(line, text=name, bg=CARD, fg=INK, font=ui.FONTS['small'], anchor='w').pack(side='left')
                    tk.Label(line, text=f'{w:+.2f}', bg=CARD, fg=color, font=ui.FONTS['label_sm']).pack(side='right')
                if not items:
                    tk.Label(box, text='아직 없음', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w')
        tk.Label(page, text='표시한 내용은 결과 폴더의 judgments.csv(엑셀로 열림)에 남습니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w', pady=(px(14), 0))
