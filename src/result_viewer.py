"""브라우저 없이 SQLite/JSONL을 읽는 로컬 문헌 뷰어."""
import json
import math
import os
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import Counter

from analysis_engine import analyze, default_profile
from source_connectors import BY_ID
from browser_links import open_web_url
import ui_theme as ui
from ui_theme import (apply_theme, px, BG, CARD, INK, MUTED, GREEN, ACCENT, LINE, LINE_SOFT, FIELD, HEAD,
                      ACCENT_PALE, NEUTRAL_PALE, OK_INK, WARN_INK)

STATUS = {
    'pending': '본문 미수집',
    'complete': '본문 저장',
    'failed': '수집 실패',
    'catalog': '서지·요약',
}


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
        self.info = tk.StringVar()
        self.folder_text = tk.StringVar()

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
        self.bind('<Escape>', lambda e: self.go_back())
        self.bind('<Alt-Left>', lambda e: self.go_back())
        tk.Label(titles, textvariable=self.info, bg=CARD, fg=MUTED, font=ui.FONTS['small'], anchor='w',
                 justify='left').pack(anchor='w', pady=(px(3), 0))
        ttk.Button(head, text='다른 결과 폴더', command=self.choose_folder, cursor='hand2').pack(side='right', anchor='n')
        ttk.Button(head, text='파일 폴더 열기', command=self.open_folder, cursor='hand2').pack(side='right', anchor='n', padx=(0, px(8)))

        controls = tk.Frame(top, bg=CARD)
        controls.pack(fill='x', padx=px(28), pady=(px(14), px(16)))
        tk.Label(controls, text='찾기', bg=CARD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(8)))
        ttk.Entry(controls, textvariable=self.query, width=24).pack(side='left', padx=(0, px(10)))
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
        ttk.Button(controls, text='새로고침', style='Quiet.TButton', command=self.reload, cursor='hand2').pack(side='right')
        for variable in (self.query, self.facet, self.period, self.threshold, self.source_filter):
            variable.trace_add('write', self.schedule_filter)
        ui.hline(top, LINE).pack(fill='x')

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill='x', padx=px(28), pady=(px(14), px(12)))
        ttk.Button(buttons, text='선택 문헌 본문 받기', style='Primary.TButton', command=self.collect_selected, cursor='hand2').pack(side='left')
        ttk.Button(buttons, text='관계 후보 지도', style='Large.TButton', cursor='hand2',
                   command=lambda: RelationsWindow(self, self.filtered)).pack(side='left', padx=(px(8), 0))
        ttk.Button(buttons, text='원문 주소 복사', style='Large.TButton', command=self.copy_url, cursor='hand2').pack(side='left', padx=(px(8), 0))
        ttk.Button(buttons, text='공식 원문 ↗', style='Outline.TButton', command=self.open_original, cursor='hand2').pack(side='left', fill='y', padx=(px(8), 0))
        tk.Label(buttons, text='점수·분류는 검토용 규칙 기반 후보입니다. 빠른 검색은 일부 결과이며 낮은 점수도 관련 문헌일 수 있습니다.',
                 bg=BG, fg=MUTED, font=ui.FONTS['small'], justify='right', wraplength=px(360)).pack(side='right')

        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True, padx=px(28), pady=(0, px(20)))
        upper = tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        split.add(upper, weight=5)
        self.tree = ttk.Treeview(upper, columns=('score', 'title', 'source', 'facets', 'year', 'status'),
                                 show='headings', selectmode='extended', height=12)
        for name, label, width in (('score', '점수', 56), ('title', '제목', 260), ('source', '자료집', 110),
                                   ('facets', '관점', 96), ('year', '연대', 64), ('status', '상태', 92)):
            self.tree.heading(name, text=label, anchor='center' if name in ('score', 'year') else 'w')
            self.tree.column(name, width=px(width), minwidth=px(45), stretch=name == 'title',
                             anchor='center' if name in ('score', 'year') else 'w')
        scroll = ttk.Scrollbar(upper, orient='vertical', command=self.tree.yview, style='Card.Vertical.TScrollbar')
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)

        lower = tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        split.add(lower, weight=4)
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
            self._write_detail([(str(exc), 'note')])
            self.info.set(str(exc))
            return
        self.period_box.configure(values=['모든 연대'] + sorted({r['analysis']['period'] for r in self.rows}))
        self.filter_rows()

    def schedule_filter(self, *args):
        if self._timer:
            self.after_cancel(self._timer)
        self._timer = self.after(200, self.filter_rows)

    def filter_rows(self):
        self._timer = None
        query = self.query.get().casefold().strip()
        minimum = {'전체 후보': -10000, '60점 이상': 60, '80점 이상': 80}[self.threshold.get()]
        self.filtered = []
        for row in self.rows:
            a = row['analysis']
            if self.source_filter.get() != '모든 자료원':
                selected = next((s.id for s in BY_ID.values() if s.short == self.source_filter.get()), None)
                origins = row.get('source_ids', ['history' if row.get('kind') == 'db' else row.get('kind')])
                origins = ['history' if key == 'db' else key for key in origins]
                if selected not in origins:
                    continue
            if a['score'] < minimum:
                continue
            if self.facet.get() == '미분류' and a['facets']:
                continue
            if self.facet.get() not in ('모든 관점', '미분류') and self.facet.get() not in a['facets']:
                continue
            if self.period.get() != '모든 연대' and a['period'] != self.period.get():
                continue
            if query and query not in '\n'.join([row.get('title', ''), row.get('body', ''), row.get('snippet', ''),
                                                  row.get('source', '')]).casefold():
                continue
            self.filtered.append(row)
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.shown = {str(i): row for i, row in enumerate(self.filtered[:3000])}
        for i, row in self.shown.items():
            a = row['analysis']
            self.tree.insert('', 'end', iid=i, tags=('even' if int(i) % 2 == 0 else 'odd',),
                             values=(a['score'], row['title'], row.get('source', ''), ', '.join(a['facets']) or '미분류',
                                     a['year'] or '미상', STATUS.get(row.get('status'), row.get('status', ''))))
        self.tree.tag_configure('even', background=CARD)
        self.tree.tag_configure('odd', background='#FBFAF7')
        counts = Counter(name for row in self.filtered for name in row['analysis']['facets'])
        self.info.set(f'{self.folder}\n전체 {len(self.rows)}건 / 필터 {len(self.filtered)}건 / 화면 {len(self.shown)}건 · 관련성 점수순'
                      '  ·  분류 후보(복수 태그): ' + ' · '.join(f'{name} {counts[name]}건' for name in ('정치사', '사회사', '제도사')))
        if self.shown:
            self.tree.selection_set('0')
            self.show_selected()
        else:
            self._write_detail([('현재 조건에 해당하는 자료가 없습니다.', 'note')])

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
        parts = [
            (f" 관련성 {a['score']}점 ", 'chip'), ('  ', None),
            (f" {', '.join(a['facets']) or '미분류'} ", 'chip2'), ('  ', None),
            (f" {a['year'] or '연대 미상'} ", 'chip2'), ('  ', None),
            (f" {status} ", 'chip2'), ('\n', 'meta'),
            (row['title'] + '\n', 'title'),
            (row.get('source', '') + '\n', 'meta'),
            (row.get('url', '') + '\n', 'link'),
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

    def copy_url(self):
        rows = self.selected()
        if rows:
            self.clipboard_clear()
            self.clipboard_append('\n'.join(r.get('url', '') for r in rows))

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
            self.folder = Path(folder)
            self.reload()

    def open_folder(self):
        try:
            if os.name == 'nt':
                os.startfile(str(self.folder))
            else:
                messagebox.showinfo('결과 폴더', str(self.folder), parent=self)
        except OSError as exc:
            messagebox.showerror('폴더 열기', str(exc), parent=self)
