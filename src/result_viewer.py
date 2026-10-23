"""브라우저 없이 SQLite/JSONL을 읽는 로컬 문헌 뷰어."""
import json
import math
import os
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import Counter

import functools
import re

import analysis_engine as _engine
from analysis_engine import analyze, default_profile
from source_connectors import BY_ID
from browser_links import open_web_url
import ranker
import textlab as tl
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


SORTS = ('학습 추천순', '규칙 점수순', '판정 필요순')


def read_results(folder, profile=None):
    folder = Path(folder).resolve()
    settings = folder / 'active_profile.json'
    profile = profile or (json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else default_profile())
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
    text.tag_configure('link', font=ui.FONTS['small'], foreground=ACCENT)
    return text


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

    def __init__(self, parent, folder, on_collect=None):
        super().__init__(parent)
        apply_theme(self)
        self.title('문헌 결과 — 앱 안에서 보기')
        ui.fit_geometry(self, 1280, 860, 940, 620)
        self.folder = Path(folder).resolve()
        self.on_collect = on_collect
        self.rows = []
        self.filtered = []
        self.shown = {}
        self._timer = None
        self.query = tk.StringVar()
        self.facet = tk.StringVar(value='모든 관점')
        self.period = tk.StringVar(value='모든 연대')
        self.threshold = tk.StringVar(value='전체 후보')
        self.source_filter = tk.StringVar(value='모든 자료원')
        self.sort_mode = tk.StringVar(value=SORTS[0])
        self.info = tk.StringVar()
        self.learn_info = tk.StringVar()
        self.judg = None
        self.model = ranker.Ranker()
        self.pred = {}
        self.index = None          # TF-IDF (2강)
        self.wordspace = None      # PMI·SVD (3강)
        self.topics = None         # 주제 주석 (5·6강)
        self.classifier = None
        self._by_key = {}
        self.lab = None

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
        ttk.Button(head, text='다른 결과 폴더', command=self.choose_folder, cursor='hand2').pack(side='right', anchor='n')
        ttk.Button(head, text='파일 폴더 열기', command=self.open_folder, cursor='hand2').pack(side='right', anchor='n', padx=(0, px(8)))

        controls = tk.Frame(top, bg=CARD)
        controls.pack(fill='x', padx=px(28), pady=(px(14), px(16)))
        tk.Label(controls, text='찾기', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(8)))
        self.query_entry = ttk.Entry(controls, textvariable=self.query, width=24)
        self.query_entry.pack(side='left', padx=(0, px(10)))
        self.source_box = ttk.Combobox(controls, textvariable=self.source_filter, state='readonly', width=13,
                                       values=['모든 자료원'] + [s.short for s in BY_ID.values()])
        self.source_box.pack(side='left', padx=(0, px(6)))
        self.facet_box = ttk.Combobox(controls, textvariable=self.facet, state='readonly', width=10,
                                      values=['모든 관점', '정치사', '사회사', '제도사', '미분류'])
        self.facet_box.pack(side='left', padx=(0, px(6)))
        self.period_box = ttk.Combobox(controls, textvariable=self.period, state='readonly', width=10)
        self.period_box.pack(side='left', padx=(0, px(6)))
        ttk.Combobox(controls, textvariable=self.threshold, state='readonly', width=10,
                     values=['전체 후보', '60점 이상', '80점 이상']).pack(side='left', padx=(0, px(6)))
        tk.Label(controls, text='정렬', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(10), px(8)))
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
        self.tree = ttk.Treeview(upper, columns=('rec', 'score', 'label', 'title', 'source', 'facets', 'year', 'status'),
                                 show='headings', selectmode='extended', height=12)
        centered = ('rec', 'score', 'label', 'year')
        for name, label, width in (('rec', '추천', 50), ('score', '규칙', 50), ('label', '판정', 44), ('title', '제목', 230),
                                   ('source', '자료집', 100), ('facets', '관점', 80), ('year', '연대', 56), ('status', '상태', 84)):
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

        lower = tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        split.add(lower, weight=4)
        bar = tk.Frame(lower, bg=HEAD)
        bar.pack(side='top', fill='x')
        ui.hline(lower, LINE).pack(side='top', fill='x')
        tk.Label(bar, text='이 문헌은', bg=HEAD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(14), px(8)), pady=px(8))
        ttk.Button(bar, text='✓ 관련 있음  1', style='Outline.TButton', command=lambda: self.judge(1), cursor='hand2').pack(side='left', pady=px(6))
        ttk.Button(bar, text='✕ 관련 없음  0', command=lambda: self.judge(0), cursor='hand2').pack(side='left', padx=(px(6), 0), pady=px(6))
        ttk.Button(bar, text='지우기', style='Quiet.TButton', command=lambda: self.judge(None), cursor='hand2').pack(side='left', padx=(px(2), 0))
        self.topic_bar = tk.Frame(lower, bg=HEAD)
        self.topic_bar.pack(side='top', fill='x')
        self.topic_vars = {}

        self.detail = _reading_text(lower, height=15)
        dscroll = ttk.Scrollbar(lower, orient='vertical', command=self.detail.yview, style='Card.Vertical.TScrollbar')
        self.detail.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side='right', fill='y')
        self.detail.pack(side='left', fill='both', expand=True)
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
        self.judg = ranker.Judgments(self.folder, self.profile.get('target', ''))
        self._by_key = {r.get('key'): r for r in self.rows}
        self.index, self.wordspace, self.classifier = None, None, None
        self.topics = tl.TopicLabels(self.folder, self.profile.get('target', ''), list(self.profile.get('facets', {})))
        self.facet_box.configure(values=['모든 관점'] + list(self.topics.labels) + ['미분류'])
        self.refresh_topic_bar()
        self._train(full=True)
        self.filter_rows()

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
            self.filtered.append(row)
        self._sort(self.filtered)
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.shown = {str(i): row for i, row in enumerate(self.filtered[:3000])}
        for i, row in self.shown.items():
            a = row['analysis']
            self.tree.insert('', 'end', iid=i, tags=('even' if int(i) % 2 == 0 else 'odd',),
                             values=(self._rec_text(row), a['score'], self._label_text(row), row.get('title', '(제목 없음)'), row.get('source', ''),
                                     self._facet_text(row), a['year'] or '미상',
                                     STATUS.get(row.get('status'), row.get('status', ''))))
        counts = Counter(name for row in self.filtered for name in row['analysis']['facets'])
        self.info.set(f'{self.folder}\n전체 {len(self.rows)}건 / 필터 {len(self.filtered)}건 / 화면 {len(self.shown)}건 · 관련성 점수순'
                      '  ·  분류 후보(복수 태그): ' + ' · '.join(f'{name} {counts[name]}건' for name in ('정치사', '사회사', '제도사')))
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
        row = row or (self.selected() or [None])[0]
        key = row.get('key') if row else None
        ann = self.topics.get(key) if key else None
        rule = set(row['analysis'].get('facets', {})) if row else set()
        probs = self.classifier.probs.get(key, {}) if (self.classifier and key) else {}
        top = tk.Frame(bar, bg=HEAD)
        top.pack(fill='x', padx=(px(14), px(10)), pady=(px(6), 0))
        tk.Label(top, text='주제 ✎ 주석됨' if ann is not None else '주제 (미주석 · 괄호는 규칙/모델 확률)', bg=HEAD,
                 fg=ACCENT if ann is not None else MUTED, font=ui.FONTS['label_sm']).pack(side='left')
        if key:
            for text, command in (('지우기', lambda: self.save_topics(clear=True)), ('해당 없음', self.no_topics),
                                  ('제안대로 주석', self.accept_topics)):
                link = tk.Label(top, text=text, bg=HEAD, fg=ACCENT, font=ui.FONTS['label_sm'], cursor='hand2')
                link.pack(side='right', padx=(px(12), 0))
                link.bind('<Button-1>', lambda e, c=command: c())
        checks = tk.Frame(bar, bg=HEAD)
        checks.pack(fill='x', padx=(px(10), px(10)), pady=(px(2), px(6)))
        self.topic_vars = {}
        for name in self.topics.labels:
            var = tk.BooleanVar(value=(name in ann) if ann is not None else False)
            self.topic_vars[name] = var
            hint = []
            if name in rule:
                hint.append('규칙')
            if name in probs:
                hint.append(f'{probs[name]:.2f}')
            cb = tk.Checkbutton(checks, text=name + (f" ({' · '.join(hint)})" if hint else ''), variable=var, bg=HEAD,
                                activebackground=HEAD, fg=INK, selectcolor=CARD, font=ui.FONTS['small'], bd=0,
                                highlightthickness=0, cursor='hand2', command=self.save_topics)
            cb.pack(side='left', padx=(0, px(10)))

    def save_topics(self, clear=False):
        rows = self.selected()
        if not rows or self.topics is None:
            return
        chosen = None if clear else {n for n, v in self.topic_vars.items() if v.get()}
        for row in rows:
            self.topics.set(row.get('key'), chosen, row.get('title', ''))
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
        suggestion = set(row['analysis'].get('facets', {}))
        if self.classifier is not None:
            suggestion = set(self.classifier.predict(row.get('key'))) | suggestion
        for n, v in self.topic_vars.items():
            v.set(n in suggestion)
        self.save_topics()

    # ── 판정 · 학습 ──

    def _train(self, full=False):
        if self.judg is None:
            return
        data = ranker.training_data(self.rows, self.judg)
        if full or not self.model.trained:
            self.model = ranker.Ranker().fit(data)
        else:
            self.model.fit(data, epochs=8, warm=True)
        self.pred = {r.get('key'): self.model.prob(ranker.features(r)) for r in self.rows} if self.model.trained else {}
        pos, neg = self.judg.counts()
        if self.model.trained:
            self.learn_info.set(f'판정 {pos + neg}건 (관련 {pos} · 무관 {neg}) · 학습 추천 사용 중')
        else:
            need = []
            if pos < ranker.MIN_PER_CLASS:
                need.append(f'관련 {ranker.MIN_PER_CLASS - pos}건')
            if neg < ranker.MIN_PER_CLASS:
                need.append(f'무관 {ranker.MIN_PER_CLASS - neg}건')
            self.learn_info.set(f'판정 {pos + neg}건 · 학습까지 ' + ', '.join(need) + ' 더')

    def _rec_text(self, row):
        p = self.pred.get(row.get('key'))
        return '–' if p is None else str(int(round(p * 100)))

    def _label_text(self, row):
        label = self.judg.get(row.get('key')) if self.judg else None
        return '✓' if label == 1 else '✕' if label == 0 else ''

    def _sort(self, rows):
        mode = self.sort_mode.get()
        rule = {id(r): i for i, r in enumerate(self.rows)}  # 원래 규칙 점수 순서
        if mode == '학습 추천순' and self.pred:
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
            if self.pred:
                return (labeled, round(abs(self.pred.get(r.get('key'), 0.5) - 0.5), 2), turn[id(r)], rule[id(r)])
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

    def _write_detail(self, parts):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        for text, tag in parts:
            self.detail.insert('end', text, tag)
        self.detail.configure(state='disabled')

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
            (f" 규칙 {a['score']}점 ", 'chip2' if p is not None else 'chip'), ('  ', None),
            (f" {', '.join(a['facets']) or '미분류'} ", 'chip2'), ('  ', None),
            (f" {a['year'] or '연대 미상'} ", 'chip2'), ('  ', None),
            (f" {status} ", 'chip2'), ('\n', 'meta'),
            (row.get('title', '(제목 없음)') + '\n', 'title'),
            (row.get('source', '') + '\n', 'meta'),
            (row.get('url', '') + '\n', 'link'),
        ]
        if p is not None:
            why = self.model.contributions(ranker.features(row))
            parts.append(('추천 근거 — 내 판정에서 배운 가중치\n', 'head'))
            parts.append(('\n'.join(f"{'+' if c > 0 else '−'}{abs(c):.2f}  {name}" for name, c in why) + '\n', 'note'))
        parts += [
            ('점수 근거 — 확률 아님\n', 'head'),
            ('\n'.join(a['score_reasons']) + '\n', 'note'),
            (f"분석 근거: {a['basis']} / 기록 연대: {a['year'] or '미상'}\n연대 근거: {a['year_source'] or '명시 연대 없음'}\n", 'note'),
        ]
        for name, evidence in a['facets'].items():
            parts.append((f'{name} · 자동 분류 후보\n', 'head'))
            parts.append(('\n'.join(e['term'] + ' → ' + e['evidence'] for e in evidence[:3]) + '\n', 'note'))
        if row.get('body'):
            parts += [('본문\n', 'head'), (row['body'] + '\n', 'body')]
        else:
            parts += [('본문 미수집 — 검색 요약\n', 'head'), (row.get('snippet', '요약 없음') + '\n', 'body')]
        if row.get('error'):
            parts += [('수집 오류\n', 'head'), (row['error'] + '\n', 'error')]
        self._write_detail(parts)
        self.detail.yview_moveto(0)
        self.refresh_topic_bar(row)

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

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self, title='기존 수집결과 폴더 선택', initialdir=self.folder)
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
        tk.Label(inner, text='판정한 문헌만 놓고, 규칙 점수 순위와 학습 추천 순위가 내 판정을 얼마나 잘 맞히는지 비교합니다.\n'
                             '학습 추천은 교차검증 값입니다(각 문헌을 그 문헌의 판정을 보지 않은 모델로 채점).',
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
        for c, text in enumerate(('지표', '규칙 점수', '학습 추천')):
            tk.Label(table, text=text, bg=HEAD, fg=MUTED, font=ui.FONTS['label_sm'], anchor='w' if c == 0 else 'center',
                     padx=px(14), pady=px(8)).grid(row=0, column=c, sticky='nsew')

        def fmt(v):
            return '–' if v is None else f'{v * 100:.0f}%'

        for r, (name, rule, learned) in enumerate(ev['rows'], start=1):
            better = learned is not None and rule is not None and learned > rule + 1e-9
            tk.Label(table, text=name, bg=CARD, fg=INK, font=ui.FONTS['body'], anchor='w', padx=px(14), pady=px(8)).grid(row=r, column=0, sticky='nsew')
            tk.Label(table, text=fmt(rule), bg=CARD, fg=INK, font=ui.FONTS['body'], width=10).grid(row=r, column=1, sticky='nsew')
            tk.Label(table, text=fmt(learned), bg=CARD, fg=ACCENT if better else INK,
                     font=ui.FONTS['label'] if better else ui.FONTS['body'], width=10).grid(row=r, column=2, sticky='nsew')
        tk.Label(page, text=f'참고: 판정한 문헌 중 관련 비율은 {fmt(ev["base"])}입니다. 아무렇게나 순서를 매기면 Precision이 이 값, AUC가 50%가 됩니다.\n'
                            '판정한 문헌 안에서만 잰 값이라, 판정을 고르게(위·아래 순위 모두) 할수록 믿을 만합니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small'], justify='left').pack(anchor='w', pady=(px(8), 0))

        if parent.model.trained:
            up, down = parent.model.top_weights(8)
            cols = tk.Frame(page, bg=BG)
            cols.pack(fill='x', pady=(px(16), 0))
            cols.columnconfigure(0, weight=1, uniform='w')
            cols.columnconfigure(1, weight=1, uniform='w')
            for c, (title, items, color) in enumerate((('관련 쪽으로 올리는 근거', up, OK_INK), ('무관 쪽으로 내리는 근거', down, '#9A2F1F'))):
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
        tk.Label(page, text='판정은 결과 폴더의 judgments.json · judgments.csv에 저장되어 연구 기록으로 남습니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(anchor='w', pady=(px(14), 0))
