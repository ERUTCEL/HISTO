"""텍스트 분석 창: 유사 검색(2강) · 어휘 관계(3강) · 문맥 용례(4강) · 주제 분류(5·6강)."""
import csv
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import mtl
import textlab as tl
import ui_theme as ui
from ui_theme import apply_theme, px, BG, CARD, INK, MUTED, ACCENT, LINE, ACCENT_PALE, OK_INK


def run_background(widget, work, done, on_error=None):
    """무거운 계산은 별도 스레드에서, 결과 반영은 Tk 스레드에서."""
    box = {}

    def target():
        try:
            box['value'] = work()
        except Exception as exc:  # 화면 쪽에서 알려 줍니다
            box['error'] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def check():
        if not widget.winfo_exists():
            return
        if thread.is_alive():
            widget.after(120, check)
        elif 'error' in box:
            (on_error or (lambda e: messagebox.showerror('분석 오류', str(e), parent=widget)))(box['error'])
        else:
            done(box['value'])
    widget.after(120, check)


def _table(parent, columns, height=12):
    frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
    tree = ttk.Treeview(frame, columns=[c for c, _, _ in columns], show='headings', height=height)
    for name, label, width in columns:
        tree.heading(name, text=label, anchor='w')
        tree.column(name, width=px(width), stretch=width >= 200, anchor='w')
    tree.tag_configure('odd', background='#FBFAF7')
    scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview, style='Card.Vertical.TScrollbar')
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    tree.pack(side='left', fill='both', expand=True)
    return frame, tree


def _fill(tree, rows):
    tree.delete(*tree.get_children())
    for i, values in enumerate(rows):
        tree.insert('', 'end', iid=str(i), values=values, tags=('odd',) if i % 2 else ())


def _note(parent, text, bg=BG):
    label = tk.Label(parent, text=text, bg=bg, fg=MUTED, font=ui.FONTS['small'], justify='left', anchor='w')
    label.bind('<Configure>', lambda e: label.configure(wraplength=max(px(200), e.width - px(4))))
    return label


class TextLabWindow(tk.Toplevel):

    def __init__(self, results, tab='search'):
        super().__init__(results)
        apply_theme(self)
        self.results = results
        self.title('텍스트 분석')
        ui.fit_geometry(self, 1180, 820, 860, 600)
        self.bind('<Escape>', lambda e: self.destroy())
        head = tk.Frame(self, bg=CARD)
        head.pack(fill='x')
        inner = tk.Frame(head, bg=CARD)
        inner.pack(fill='x', padx=px(24), pady=(px(18), px(12)))
        trow = tk.Frame(inner, bg=CARD)
        trow.pack(anchor='w')
        ttk.Button(trow, text='←  결과로', style='Quiet.TButton', command=self.destroy, cursor='hand2').pack(side='left', padx=(0, px(10)))
        tk.Label(trow, text='텍스트 분석', bg=CARD, fg=INK, font=ui.FONTS['h1']).pack(side='left')
        self.status = tk.StringVar(value=f'문헌 {len(results.rows)}건')
        tk.Label(inner, textvariable=self.status, bg=CARD, fg=ACCENT, font=ui.FONTS['label_sm']).pack(anchor='w', pady=(px(4), 0))
        ui.hline(head, LINE).pack(fill='x')

        self.book = ttk.Notebook(self)
        self.book.pack(fill='both', expand=True, padx=px(20), pady=px(14))
        self.tabs = {}
        for key, label in (('search', '비슷한 문헌 찾기'), ('relate', '단어 관계'), ('words', '함께 쓰인 말'),
                           ('context', '쓰임새 보기'), ('topics', '자동 분류'),
                           ('mtl', '여러 분류 함께 학습'), ('domain', '시대·자료집 차이 점검')):
            frame = tk.Frame(self.book, bg=BG, padx=px(16), pady=px(14))
            self.book.add(frame, text=label)
            self.tabs[key] = frame
        self._build_search(self.tabs['search'])
        self._build_words(self.tabs['words'])
        self._build_relate(self.tabs['relate'])
        self._build_context(self.tabs['context'])
        self._build_topics(self.tabs['topics'])
        self._build_mtl(self.tabs['mtl'])
        self._build_domain(self.tabs['domain'])
        self.book.bind('<<NotebookTabChanged>>', self._tab_changed)
        self.show(tab)

    def show(self, tab, **kw):
        self.book.select(self.tabs[tab])
        if tab == 'search' and kw.get('similar_to'):
            self.similar(kw['similar_to'])

    def _tab_changed(self, _=None):
        current = self.book.select()
        if current == str(self.tabs['words']) and self.results.wordspace is None:
            self._build_wordspace()

    # ── 2강: 유사 검색 ──

    def _build_search(self, page):
        _note(page, '낱말이나 원문 구절을 넣으면 내용이 가장 비슷한 문헌부터 보여줍니다. 한글·한문 모두 찾고, '
                    '여러 문헌에 흔한 말보다 드문 고유명사가 겹칠수록 위로 올라옵니다. '
                    '결과 창에서 문헌을 고른 뒤 \'이 문헌과 비슷한 문헌\'을 누르면 내용이 닮은 다른 기록을 찾습니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.search_var, font=ui.FONTS['input'])
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda e: self.search())
        ttk.Button(row, text='찾기', style='Primary.TButton', command=self.search, cursor='hand2').pack(side='left', padx=(px(8), 0))
        ttk.Button(row, text='이 문헌과 비슷한 문헌', command=lambda: self.similar(None), cursor='hand2').pack(side='left', padx=(px(8), 0))
        self.search_title = tk.StringVar(value='검색어(예: 淑嬪崔氏 追崇, 육상궁 제향)를 넣거나, 결과 창에서 문헌을 고른 뒤 비슷한 문헌을 찾으세요.')
        tk.Label(page, textvariable=self.search_title, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w').pack(fill='x', pady=(0, px(6)))
        frame, self.search_tree = _table(page, [('sim', '비슷한 정도', 90), ('title', '제목', 420), ('source', '자료집', 120), ('terms', '겹치는 낱말', 260)])
        frame.pack(fill='both', expand=True)
        self.search_tree.bind('<Double-1>', lambda e: self._open_in_results(self.search_tree))
        _note(page, '두 번 클릭하면 결과 창에서 그 문헌을 엽니다.').pack(anchor='w', pady=(px(6), 0))
        self._search_keys = []

    def search(self):
        text = self.search_var.get().strip()
        if not text:
            return
        index = self.results.ensure_index()
        q = index.vector(text)
        hits = index.search(text, 200)
        self.search_title.set(f'「{text}」와 비슷한 문헌 {len(hits)}건')
        self._show_hits(hits, q)

    def similar(self, key):
        if key is None:
            sel = self.results.selected()
            if not sel:
                messagebox.showinfo('비슷한 문헌', '결과 창에서 문헌을 먼저 고르세요.', parent=self)
                return
            key = sel[0].get('key')
        index = self.results.ensure_index()
        row = self.results.row_by_key(key)
        hits = index.similar(key, 100)
        self.search_title.set(f'「{(row or {}).get("title", key)[:40]}」와 비슷한 문헌  ·  이 문헌의 특징적인 말: '
                              + ', '.join(t for t, _ in index.top_terms(key, 8)))
        self._show_hits(hits, index.vecs[index.pos[key]] if key in index.pos else {})

    def _show_hits(self, hits, q):
        index = self.results.ensure_index()
        rows = []
        self._search_keys = []
        for key, score in hits:
            row = self.results.row_by_key(key) or {}
            vec = index.vecs[index.pos[key]]
            shared = sorted((t for t in q if t in vec), key=lambda t: -(q[t] * vec[t]))[:5]
            rows.append((f'{score * 100:.0f}', row.get('title', key), row.get('source', ''), ', '.join(shared)))
            self._search_keys.append(key)
        _fill(self.search_tree, rows)

    def _open_in_results(self, tree):
        sel = tree.selection()
        if sel and int(sel[0]) < len(self._search_keys):
            self.results.focus_key(self._search_keys[int(sel[0])])

    # ── 3강: 어휘 관계 ──

    def _build_words(self, page):
        _note(page, '낱말 하나를 넣으면, 모은 사료에서 그 말 가까이 자주 함께 나온 말과, 비슷한 맥락에서 쓰이는 말을 보여줍니다. '
                    '\'시대\'를 바꾸면 세기마다 어떤 말과 엮였는지 비교할 수 있습니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.word_var = tk.StringVar(value=(self.results.profile or {}).get('target', '').split()[0] if self.results.profile else '')
        entry = ttk.Entry(row, textvariable=self.word_var, font=ui.FONTS['input'], width=24)
        entry.pack(side='left')
        entry.bind('<Return>', lambda e: self.words())
        tk.Label(row, text='시대', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(12), px(6)))
        self.period_var = tk.StringVar(value='전체')
        self.period_box = ttk.Combobox(row, textvariable=self.period_var, state='readonly', width=12, values=['전체'])
        self.period_box.pack(side='left')
        self.period_box.bind('<<ComboboxSelected>>', lambda e: self.words())
        ttk.Button(row, text='보기', style='Primary.TButton', command=self.words, cursor='hand2').pack(side='left', padx=(px(8), 0))
        cols = tk.Frame(page, bg=BG)
        cols.pack(fill='both', expand=True)
        cols.columnconfigure(0, weight=1, uniform='w')
        cols.columnconfigure(1, weight=1, uniform='w')
        left = tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, px(8)))
        right = tk.Frame(cols, bg=BG)
        right.grid(row=0, column=1, sticky='nsew')
        cols.rowconfigure(0, weight=1)
        tk.Label(left, text='비슷한 맥락에서 쓰이는 말 (전체 시대)', bg=BG, fg=INK, font=ui.FONTS['label']).pack(anchor='w', pady=(0, px(6)))
        f1, self.near_tree = _table(left, [('word', '낱말', 200), ('sim', '비슷한 정도', 90)])
        f1.pack(fill='both', expand=True)
        self.assoc_title = tk.StringVar(value='자주 함께 나온 말')
        tk.Label(right, textvariable=self.assoc_title, bg=BG, fg=INK, font=ui.FONTS['label']).pack(anchor='w', pady=(0, px(6)))
        f2, self.assoc_tree = _table(right, [('word', '낱말', 200), ('pmi', '함께 나온 정도', 100)])
        f2.pack(fill='both', expand=True)
        self.words_note = _note(page, '')
        self.words_note.pack(anchor='w', pady=(px(6), 0))

    def _build_wordspace(self):
        self.status.set('낱말 관계를 계산하는 중입니다… (몇 초 걸립니다)')
        rows = list(self.results.rows)

        def done(space):
            self.results.wordspace = space
            self.period_box.configure(values=['전체'] + sorted(space.periods))
            self.status.set(f'자주 나온 낱말 {len(space.vocab)}개로 계산했습니다.')
            self.words()
        run_background(self, lambda: tl.WordSpace(rows, vocab_size=1500, k=32), done)

    def words(self):
        space = self.results.wordspace
        if space is None:
            self._build_wordspace()
            return
        word = self.word_var.get().strip()
        if not word:
            return
        if space.lookup(word) is None:
            self.words_note.configure(text=f'「{word}」는 자주 나온 낱말(상위 {len(space.vocab)}개)에 없습니다. 다른 표기나 두 글자 낱말로 찾아보세요.')
            _fill(self.near_tree, [])
            _fill(self.assoc_tree, [])
            return
        self.words_note.configure(text=f'찾은 낱말: {space.lookup(word)}')
        _fill(self.near_tree, [(w, f'{max(0.0, s) * 100:.0f}') for w, s in space.neighbors(word, 20)])
        period = self.period_var.get()
        self.assoc_title.set('자주 함께 나온 말 (' + ('전체 시대' if period == '전체' else period) + ')')
        _fill(self.assoc_tree, [(w, f'{v:.2f}') for w, v in space.associations(word, None if period == '전체' else period, 20)])

    # ── 단어 관계 ──

    def _build_relate(self, page):
        _note(page, '낱말을 두세 개 넣으면, 모은 문헌에서 그 말들이 얼마나 함께 나오는지, 우연보다 자주 붙어 나오는지, '
                    '같은 문장에 함께 쓰였는지, 둘 다와 자주 같이 나오는 말과 시대별 변화, 함께 나온 문장을 보여줍니다. '
                    '아래 문장을 두 번 누르면 결과 창에서 그 문헌을 엽니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.rel_vars = []
        for i, hint in enumerate(('예: 숙빈 최씨', '예: 육상궁', '셋째 낱말 (선택)')):
            var = tk.StringVar()
            entry = ttk.Entry(row, textvariable=var, font=ui.FONTS['input'], width=18)
            entry.pack(side='left', padx=(0 if i == 0 else px(6), 0))
            entry.bind('<Return>', lambda e: self.relate())
            ui.placeholder(entry, hint, var)
            self.rel_vars.append(var)
        ttk.Button(row, text='관계 보기', style='Primary.TButton', command=self.relate, cursor='hand2').pack(side='left', padx=(px(10), 0))

        panes = ttk.Panedwindow(page, orient='vertical')
        panes.pack(fill='both', expand=True)
        box = tk.Frame(panes, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        self.rel_text = tk.Text(box, wrap='word', font=ui.FONTS['body'], relief='flat', bg=CARD, fg=INK, height=14,
                                padx=px(18), pady=px(14), spacing3=px(5), highlightthickness=0, cursor='arrow')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.rel_text.yview, style='Card.Vertical.TScrollbar')
        self.rel_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.rel_text.pack(side='left', fill='both', expand=True)
        t = self.rel_text
        t.tag_configure('h', font=ui.FONTS['label'], foreground=ACCENT, spacing1=px(10), spacing3=px(4))
        t.tag_configure('big', font=ui.FONTS['label'], foreground=INK)
        t.tag_configure('ok', font=ui.FONTS['label'], foreground=OK_INK)
        t.tag_configure('muted', foreground=MUTED, font=ui.FONTS['small'])
        t.tag_configure('chip', background=ACCENT_PALE, foreground=ACCENT, font=ui.FONTS['label_sm'])
        t.insert('end', '낱말을 두 개 이상 넣고 \'관계 보기\'를 누르세요.', 'muted')
        t.configure(state='disabled')
        panes.add(box, weight=3)
        lower = tk.Frame(panes, bg=BG)
        tk.Label(lower, text='함께 나온 문장', bg=BG, fg=INK, font=ui.FONTS['label']).pack(anchor='w', pady=(px(8), px(4)))
        f, self.rel_tree = _table(lower, [('title', '문헌', 260), ('year', '연대', 70), ('sent', '함께 나온 문장', 520)], height=6)
        f.pack(fill='both', expand=True)
        self.rel_tree.bind('<Double-1>', lambda e: self._open_rel())
        panes.add(lower, weight=2)
        self._rel_keys = []

    def relate(self):
        words = []
        for v in self.rel_vars:
            w = v.get().strip()
            if w and w not in words:
                words.append(w)
        if len(words) < 2:
            messagebox.showinfo('단어 관계', '낱말을 두 개 이상 넣으세요.', parent=self)
            return
        rows = list(self.results.rows)
        space = self.results.wordspace
        idf = self.results.ensure_index().idf
        self.status.set('「' + '」·「'.join(words) + '」 관계를 찾는 중입니다…')

        def work():
            ws = space or tl.WordSpace(rows, vocab_size=1500, k=32)
            return ws, tl.relate(rows, words, ws, idf=idf)

        def done(value):
            ws, res = value
            if self.results.wordspace is None:
                self.results.wordspace = ws
                self.period_box.configure(values=['전체'] + sorted(ws.periods))
            self._show_relation(res)
        run_background(self, work, done)

    def _show_relation(self, res):
        t = self.rel_text
        words = res['words']
        q = lambda w: f'「{w}」'
        t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('end', '각 낱말이 나온 문헌\n', 'h')
        for w in words:
            t.insert('end', f"  {q(w)}  ", 'big')
            t.insert('end', f"{res['count'][w]:,}건  ", None)
            t.insert('end', f"(전체 {res['n_docs']:,}건 중 {res['count'][w] / max(1, res['n_docs']) * 100:.0f}%)\n", 'muted')
        if len(words) == 3:
            t.insert('end', f"  셋이 모두 나온 문헌  ", 'big')
            t.insert('end', f"{res['all']:,}건\n")
        for p in res['pairs']:
            t.insert('end', f"{q(p['a'])} 와 {q(p['b'])}\n", 'h')
            t.insert('end', '  ' + p['verdict'] + '\n', 'ok' if p['both'] >= 3 and p['lift'] >= 1.5 else 'big')
            t.insert('end', f"  · 함께 나온 문헌 {p['both']:,}건")
            if p['both'] and p['lift']:
                t.insert('end', f" — 따로 나오는 빈도로 보면 예상보다 {p['lift']:.1f}배")
            t.insert('end', '\n')
            if p['sentences']:
                t.insert('end', f"  · 같은 문장 안에서 함께 쓰인 곳 {p['sentences']:,}곳\n")
            if p['median_gap'] is not None:
                t.insert('end', f"  · 한 문헌 안에서 두 말 사이 거리: 보통 {p['median_gap']:,}자 안팎\n")
            if p['similar'] is not None:
                t.insert('end', f"  · 쓰이는 맥락이 비슷한 정도 {p['similar'] * 100:.0f}%", None)
                t.insert('end', '  (주변에 오는 말이 얼마나 닮았는지 — 높으면 비슷한 이야기에서 쓰임)\n', 'muted')
        if res['common']:
            t.insert('end', '모두와 자주 같이 나오는 말\n', 'h')
            t.insert('end', '  ')
            for word, n in res['common']:
                t.insert('end', f' {word} {n} ', 'chip')
                t.insert('end', ' ')
            t.insert('end', '\n')
            t.insert('end', '  숫자는 각 낱말과 같은 문장에 함께 나온 횟수 중 가장 적은 값입니다.\n', 'muted')
        if res['periods']:
            label = '모두' if len(words) > 2 else '둘 다'
            t.insert('end', f'시대별로 {label} 나온 문헌\n', 'h')
            for period, both, anyone in res['periods']:
                t.insert('end', f"  {period}  ", 'big')
                t.insert('end', f"{both:,}건", None)
                t.insert('end', f"  (어느 하나라도 나온 문헌 {anyone:,}건 중 {both / max(1, anyone) * 100:.0f}%)\n", 'muted')
        t.insert('end', '\n함께 나왔다고 해서 바로 관계가 있다는 뜻은 아닙니다. 아래 문장으로 실제 맥락을 확인하세요.\n', 'muted')
        t.configure(state='disabled')
        rows = []
        self._rel_keys = []
        for key, sent in res['examples']:
            row = self.results.row_by_key(key) or {}
            year = (row.get('analysis') or {}).get('year') or '미상'
            rows.append((row.get('title', key), year, ' '.join(sent.split())[:300]))
            self._rel_keys.append(key)
        _fill(self.rel_tree, rows)
        self.status.set('「' + '」·「'.join(words) + f"」 — 함께 나온 문장 {len(rows)}개를 찾았습니다.")

    def _open_rel(self):
        sel = self.rel_tree.selection()
        if sel and int(sel[0]) < len(self._rel_keys):
            self.results.focus_key(self._rel_keys[int(sel[0])])

    # ── 4강: 문맥 용례 ──

    def _build_context(self, page):
        _note(page, '낱말이 나온 문장들을 앞뒤 내용이 비슷한 것끼리 묶어 보여줍니다. '
                    '같은 말이 다른 뜻·다른 대상으로 쓰였는지(예: 「숙빈」이 최씨인지 다른 인물인지) 확인할 때 씁니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.ctx_var = tk.StringVar(value='숙빈')
        entry = ttk.Entry(row, textvariable=self.ctx_var, font=ui.FONTS['input'], width=24)
        entry.pack(side='left')
        entry.bind('<Return>', lambda e: self.context())
        ttk.Button(row, text='묶어 보기', style='Primary.TButton', command=self.context, cursor='hand2').pack(side='left', padx=(px(8), 0))
        box = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE)
        box.pack(fill='both', expand=True)
        self.ctx_text = tk.Text(box, wrap='word', font=ui.FONTS['body'], relief='flat', bg=CARD, fg=INK,
                                padx=px(18), pady=px(14), spacing3=px(4), highlightthickness=0, cursor='arrow')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.ctx_text.yview, style='Card.Vertical.TScrollbar')
        self.ctx_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.ctx_text.pack(side='left', fill='both', expand=True)
        self.ctx_text.tag_configure('head', font=ui.FONTS['label'], foreground=ACCENT, spacing1=px(10), spacing3=px(4))
        self.ctx_text.tag_configure('term', font=ui.FONTS['label'], foreground=INK, background=ACCENT_PALE)
        self.ctx_text.tag_configure('side', foreground=MUTED)

    def context(self):
        term = self.ctx_var.get().strip()
        if not term:
            return
        index = self.results.ensure_index()
        hits = tl.concordance(self.results.rows, term, limit=400)
        groups, labels = tl.usage_clusters(hits, index.idf) if hits else ([], [])
        t = self.ctx_text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        if not hits:
            t.insert('end', f'「{term}」가 나온 자리를 찾지 못했습니다.', 'side')
        for g, (members, words) in enumerate(zip(groups, labels), start=1):
            t.insert('end', f'쓰임 {g}  ·  {len(members)}회  ·  주변어: {", ".join(words) or "—"}\n', 'head')
            for i in members[:12]:
                _, left, mid, right = hits[i]
                t.insert('end', '…' + left, 'side')
                t.insert('end', mid, 'term')
                t.insert('end', right + '…\n', 'side')
            if len(members) > 12:
                t.insert('end', f'외 {len(members) - 12}회\n', 'side')
        t.configure(state='disabled')
        self.status.set(f'「{term}」 용례 {len(hits)}회 · 쓰임 {len(groups)}가지로 묶었습니다.')

    # ── 5·6강: 주제 분류 ──

    def _build_topics(self, page):
        _note(page, '결과 창에서 몇십 건에 주제를 표시해 두면, 그 표시와 키워드 규칙을 함께 배워 나머지 문헌 전체의 주제를 자동으로 붙입니다. '
                    '한 문헌에 주제가 여러 개 붙을 수 있습니다. 키워드가 없어도 내용이 비슷하면 찾아냅니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        ttk.Button(row, text='전체 자동 분류', style='Primary.TButton', command=self.train, cursor='hand2').pack(side='left')
        ttk.Button(row, text='분류 결과 엑셀(CSV)로 저장', command=self.export, cursor='hand2').pack(side='left', padx=(px(8), 0))
        tk.Label(row, text='새 주제', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(18), px(6)))
        self.new_label = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.new_label, width=14)
        e.pack(side='left')
        e.bind('<Return>', lambda ev: self.add_label())
        ttk.Button(row, text='추가', command=self.add_label, cursor='hand2').pack(side='left', padx=(px(6), 0))
        self.topic_summary = tk.StringVar()
        tk.Label(page, textvariable=self.topic_summary, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w', justify='left').pack(fill='x')
        frame, self.topic_tree = _table(page, [('label', '주제', 140), ('ann', '내가 표시(해당/전체)', 140), ('rule', '키워드로 찾은 문헌', 130),
                                               ('pred', '자동 분류된 문헌', 120),
                                               ('rule_f1', '키워드만 정확도', 120), ('model_f1', '자동 분류 정확도', 130)], height=8)
        frame.pack(fill='x', pady=(px(8), 0))
        self.topic_note = _note(page, '')
        self.topic_note.pack(anchor='w', fill='x', pady=(px(8), 0))
        _note(page, '표시하는 법: 결과 창 오른쪽 \'주제\' 줄에서 맞는 주제를 체크하면 바로 저장됩니다. 어느 주제도 아니면 \'해당 없음\', '
                    '제안이 맞으면 \'제안대로\'. 정확도는 내가 표시한 문헌을 기준으로 잰 값이며, 여러 자료집·주제에 고르게 표시할수록 믿을 만합니다.').pack(anchor='w', fill='x', pady=(px(6), 0))
        self._refresh_topic_table()

    def _refresh_topic_table(self, ev=None):
        res = self.results
        topics = res.topics
        if topics is None:
            return
        clf = res.classifier
        rows = []
        f1 = {r['name']: r for r in (ev or {}).get('labels', [])} if ev else {}
        for name in topics.labels:
            ann = [k for k in topics.docs if res.row_by_key(k)]
            pos = sum(1 for k in ann if name in topics.get(k))
            rule_n = sum(1 for r in res.rows if name in r['analysis'].get('facets', {}))
            pred_n = sum(1 for r in res.rows if name in clf.predict(r.get('key'))) if clf else '–'
            def fmt(v):
                return '–' if v is None else f'{v * 100:.0f}%'
            rf = f1.get(name, {})
            rows.append((name, f'{pos}/{len(ann)}', rule_n, pred_n,
                         fmt((rf.get('rule') or {}).get('f1')), fmt((rf.get('model') or {}).get('f1'))))
        _fill(self.topic_tree, rows)
        ann_n = len([k for k in topics.docs if res.row_by_key(k)])
        if ev and ev.get('micro'):
            m = ev['micro']
            self.topic_summary.set(f"정확도(내가 표시한 {ev['n']}건 기준) — 키워드만 {(m['rule']['f1'] or 0) * 100:.0f}% → 자동 분류 {(m['model']['f1'] or 0) * 100:.0f}%")
        elif clf:
            self.topic_summary.set(f'자동 분류를 마쳤습니다 · 내가 표시한 문헌 {ann_n}건. 4건 이상 표시하면 정확도도 보여줍니다.')
        else:
            self.topic_summary.set(f'내가 표시한 문헌 {ann_n}건 · 아직 자동 분류하지 않았습니다.')

    def add_label(self):
        name = self.new_label.get().strip()
        if name and self.results.topics.add_label(name):
            self.new_label.set('')
            self.results.refresh_topic_bar()
            self._refresh_topic_table()

    def train(self):
        res = self.results
        index = res.ensure_index()
        rows, labels = list(res.rows), list(res.topics.labels)
        strong = {k: res.topics.get(k) for k in res.topics.docs if res.row_by_key(k)}
        self.status.set('자동 분류하는 중입니다… (몇 초 걸립니다)')
        self.topic_note.configure(text='')

        def work():
            clf = tl.TopicClassifier(rows, index, labels).fit(strong)
            ev = tl.evaluate_topics(rows, index, labels, strong) if len(strong) >= 4 else None
            return clf, ev

        def done(result):
            clf, ev = result
            res.classifier = clf
            res.filter_rows()
            self._refresh_topic_table(ev)
            self.status.set('자동 분류를 마쳤습니다. 결과 창 \'관점\' 열에 +표시가 붙은 주제가 새로 찾은 주제입니다.')
        run_background(self, work, done)

    def export(self):
        res = self.results
        if res.classifier is None:
            messagebox.showinfo('분류 결과 저장', '먼저 \'전체 자동 분류\'를 실행하세요.', parent=self)
            return
        path = res.folder / 'topic_predictions.csv'
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                labels = res.topics.labels
                w.writerow(['문헌 키', '제목', '자료집', '키워드로 찾은 주제', '자동 분류 주제', '내가 표시한 주제'] + [f'{n} 가능성' for n in labels])
                for r in res.rows:
                    k = r.get('key')
                    probs = res.classifier.probs.get(k, {})
                    ann = res.topics.get(k)
                    w.writerow([k, r.get('title', ''), r.get('source', ''), ' · '.join(r['analysis'].get('facets', {})),
                                ' · '.join(res.classifier.predict(k)), '' if ann is None else (' · '.join(sorted(ann)) or '(해당 없음)')]
                               + [f'{probs.get(n, 0):.3f}' for n in labels])
        except OSError as exc:
            messagebox.showerror('예측 결과 저장', str(exc), parent=self)
            return
        self.topic_note.configure(text=f'저장했습니다: {path}', fg=OK_INK)

    # ── 7강: 여러 작업 동시 학습 ──

    def _specs(self):
        res = self.results
        store = res.topics
        specs = []
        for task in store.task_names():
            strong = {k: store.task_get(task, k) for k in store.task_docs(task) if res.row_by_key(k)}
            if task == '주제':
                weak = {r.get('key'): set(r['analysis'].get('facets', {})) for r in res.rows}
            elif task == '자료 유형':
                weak = {r.get('key'): mtl.rule_types(r) for r in res.rows}
            else:
                weak = None
            specs.append(mtl.TaskSpec(task, store.task_labels(task), strong, weak))
        return specs

    def _encoder(self):
        res = self.results
        if getattr(res, 'encoder', None) is None or res.encoder.index is not res.ensure_index():
            res.encoder = mtl.DenseEncoder(res.ensure_index(), 32)
        return res.encoder

    def _build_mtl(self, page):
        _note(page, '주제 · 자료 유형(상소·전교 등) · 직접 만든 분류(예: 정치적 입장)를 따로따로 배울 때와 함께 배울 때 중 어느 쪽이 더 정확한지 비교합니다. '
                    '함께 배우면 서로 도움이 되기도 하지만, 오히려 헷갈려 나빠지는 분류도 있어 그런 경우 ⚠로 알려 줍니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(6)))
        ttk.Button(row, text='비교하기', style='Primary.TButton', command=self.run_mtl, cursor='hand2').pack(side='left')
        tk.Label(row, text='새 분류 이름', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(18), px(6)))
        self.task_name = tk.StringVar(value='정치적 입장')
        ttk.Entry(row, textvariable=self.task_name, width=12).pack(side='left')
        tk.Label(row, text='항목(쉼표로)', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(8), px(6)))
        self.task_labels = tk.StringVar(value='찬성, 반대, 중립')
        ttk.Entry(row, textvariable=self.task_labels, width=20).pack(side='left')
        ttk.Button(row, text='분류 만들기', command=self.add_task, cursor='hand2').pack(side='left', padx=(px(6), 0))
        self.mtl_summary = tk.StringVar(value='')
        tk.Label(page, textvariable=self.mtl_summary, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w', justify='left').pack(fill='x', pady=(px(6), 0))
        frame, self.mtl_tree = _table(page, [('task', '분류', 140), ('n', '내가 표시한 수', 110), ('single', '따로 배울 때', 110),
                                             ('mmoe', '함께 배울 때', 110), ('verdict', '결과', 260)], height=6)
        frame.pack(fill='x', pady=(px(8), 0))
        _note(page, '분류마다 표시하는 곳: 결과 창 오른쪽 표시 줄 맨 앞의 선택칸(주제 ▾)에서 분류를 바꿔 체크합니다. '
                    '표시한 문헌이 적으면 차이가 우연일 수 있으니 2%p 안팎의 차이는 같다고 보세요.').pack(anchor='w', fill='x', pady=(px(8), 0))
        self._mtl_rows()

    def _mtl_rows(self, result=None):
        specs = self._specs()
        rows = []
        by = {r['task']: r for r in (result or {}).get('tasks', [])}
        for sp in specs:
            r = by.get(sp.name)
            if r:
                best = max(r['single'], r['mmoe'])
                if r['negative']:
                    verdict = '⚠ 함께 배우면 나빠짐 — 따로 쓰세요'
                elif r['mmoe'] >= r['single'] + 0.02:
                    verdict = '함께 배우면 더 정확함'
                else:
                    verdict = '거의 같음'
                rows.append((sp.name, len(sp.strong), f"{r['single'] * 100:.0f}%", f"{r['mmoe'] * 100:.0f}%",
                             verdict if best > 0 else '표시가 부족해 비교 불가'))
            else:
                rows.append((sp.name, len(sp.strong), '–', '–', ''))
        _fill(self.mtl_tree, rows)

    def add_task(self):
        labels = [x for x in self.task_labels.get().replace('·', ',').split(',')]
        if self.results.topics.add_task(self.task_name.get(), labels):
            self.results.refresh_topic_bar()
            self._mtl_rows()
            self.domain_task.configure(values=self.results.topics.task_names())
            self.mtl_summary.set(f'분류 「{self.task_name.get().strip()}」를 만들었습니다. 결과 창 표시 줄에서 이 분류를 골라 표시하세요.')
        else:
            messagebox.showinfo('분류 만들기', '이름이 비었거나 이미 있습니다. 항목은 2개 이상 쉼표로 적어 주세요.', parent=self)

    def run_mtl(self):
        specs = [sp for sp in self._specs() if sp.strong or sp.weak]
        annotated = {k for sp in specs for k in sp.strong}
        if len(specs) < 2 or len(annotated) < 6:
            messagebox.showinfo('여러 분류 함께 학습', '분류 2개 이상에, 표시한 문헌이 6건 이상 있어야 비교할 수 있습니다.', parent=self)
            return
        enc = self._encoder()
        keys = [r.get('key') for r in self.results.rows]
        self.status.set('비교하는 중입니다… (20초쯤 걸립니다)')
        self.mtl_summary.set('계산 중…')

        def done(result):
            if not result:
                self.mtl_summary.set('표시한 문헌이 부족해 비교하지 못했습니다.')
                return
            neg = [r['task'] for r in result['tasks'] if r['negative']]
            self.mtl_summary.set(f"내가 표시한 {result['n']}건으로 비교했습니다. "
                                 + (f"⚠ {', '.join(neg)}은(는) 따로 배우는 편이 낫습니다." if neg else '함께 배워서 나빠지는 분류는 없습니다.'))
            self._mtl_rows(result)
            self.status.set('비교를 마쳤습니다.')
        run_background(self, lambda: mtl.compare_mtl(enc, specs, keys), done)

    # ── 8강: 시대·출처 적응 ──

    def _build_domain(self, page):
        _note(page, '시대나 자료집에 따라 용어·문체가 달라 자동 분류가 덜 맞는지 점검합니다. '
                    '예를 들어 18세기 자료로 배운 분류를 17세기 자료에 썼을 때 정확도가 얼마나 떨어지는지 보고, '
                    '두 가지 보완 방법(비슷한 자료 우선 · 공통 표현 중심)을 썼을 때와 비교합니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(6)))
        tk.Label(row, text='나눌 기준', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(6)))
        self.domain_axis = tk.StringVar(value='시대')
        ttk.Combobox(row, textvariable=self.domain_axis, values=['시대', '자료집'], state='readonly', width=8).pack(side='left')
        tk.Label(row, text='분류', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(12), px(6)))
        self.domain_task_var = tk.StringVar(value='주제')
        self.domain_task = ttk.Combobox(row, textvariable=self.domain_task_var, values=self.results.topics.task_names(),
                                        state='readonly', width=10)
        self.domain_task.pack(side='left')
        ttk.Button(row, text='점검하기', style='Primary.TButton', command=self.run_domain, cursor='hand2').pack(side='left', padx=(px(10), 0))
        self.domain_summary = tk.StringVar(value='')
        tk.Label(page, textvariable=self.domain_summary, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w', justify='left').pack(fill='x', pady=(px(6), 0))
        frame, self.domain_tree = _table(page, [('domain', '시대·자료집', 110), ('n', '문헌', 60), ('eval', '내가 표시', 70),
                                                ('in', '같은 자료로 배울 때', 130), ('src', '다른 자료로 배울 때', 130),
                                                ('rew', '보완 ①', 70), ('grl', '보완 ②', 70), ('note', '결과', 260)], height=7)
        frame.pack(fill='x', pady=(px(8), 0))
        warn = tk.Label(page, bg=BG, fg='#9A2F1F', font=ui.FONTS['small'], justify='left', anchor='w',
                        text='보완 ①: 점검할 시대·자료집과 비슷한 자료를 더 중요하게 배움 · 보완 ②: 시대·자료집에 상관없이 통하는 표현 중심으로 배움.\n'
                             '주의: 시대에 따른 용어·문체 차이 자체가 연구 대상이라면 보완 결과를 쓰지 마세요. '
                             '보완 뒤 특정 주제가 오히려 덜 맞으면 \'결과\' 칸에 표시합니다. 표시한 문헌이 10건 미만이면 숫자가 흔들릴 수 있습니다.')
        warn.pack(anchor='w', fill='x', pady=(px(8), 0))
        warn.bind('<Configure>', lambda e: warn.configure(wraplength=max(px(200), e.width - px(4))))

    def run_domain(self):
        specs = {sp.name: sp for sp in self._specs()}
        spec = specs.get(self.domain_task_var.get())
        if spec is None or len(spec.strong) < 6:
            messagebox.showinfo('시대·자료집 차이 점검', '이 분류에 표시한 문헌이 6건 이상 필요합니다(시대·자료집마다 6건 이상 있어야 점검합니다).', parent=self)
            return
        enc = self._encoder()
        rows = list(self.results.rows)
        axis = self.domain_axis.get()
        self.status.set(f'{axis}별로 점검하는 중입니다… (10초 남짓 걸립니다)')
        self.domain_summary.set('계산 중…')

        def done(rep):
            items = rep['domains']
            rows_out = []
            for d in items:
                drop = d['in_domain'] - d['source_only']
                best = max(('보완 ①', d['reweight']), ('보완 ②', d['grl']), key=lambda x: x[1])
                notes = []
                if drop >= 0.05:
                    notes.append(f'다른 자료로 배우면 {drop * 100:.0f}%p 떨어짐')
                if best[1] >= d['source_only'] + 0.02:
                    notes.append(f'{best[0]}로 {(best[1] - d["source_only"]) * 100:.0f}%p 회복')
                elif max(d['reweight'], d['grl']) < d['source_only'] - 0.02:
                    notes.append('보완이 오히려 나쁨')
                if d['hurt_labels']:
                    notes.append('⚠ 덜 맞게 된 주제: ' + ', '.join(d['hurt_labels']))
                if d['n_eval'] < 10:
                    notes.append('표시 적음')
                pct = lambda v: f'{v * 100:.0f}%'
                rows_out.append((d['domain'], d['n_target'], d['n_eval'], pct(d['in_domain']), pct(d['source_only']),
                                 pct(d['reweight']), pct(d['grl']), ' · '.join(notes) or '차이 없음'))
            _fill(self.domain_tree, rows_out)
            if not items:
                self.domain_summary.set('표시한 문헌이 6건 이상인 시대·자료집이 없어 점검하지 못했습니다. 표시를 더 해 주세요.')
            else:
                shifted = [d['domain'] for d in items if d['in_domain'] - d['source_only'] >= 0.05]
                self.domain_summary.set((f"{', '.join(shifted)}: 다른 자료로 배우면 덜 맞습니다. 보완 결과를 참고하세요." if shifted
                                         else '시대·자료집이 달라도 정확도가 크게 떨어지지 않습니다. 보완은 필요 없어 보입니다.'))
            self.status.set('점검을 마쳤습니다.')
        run_background(self, lambda: mtl.adaptation_report(enc, rows, spec, axis), done)
