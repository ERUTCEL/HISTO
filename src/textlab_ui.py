"""텍스트 분석 창: 유사 검색(2강) · 어휘 관계(3강) · 문맥 용례(4강) · 주제 분류(5·6강)."""
import csv
import threading
import tkinter as tk
from tkinter import messagebox, ttk

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
        self.title('텍스트 분석 — 표현과 분류')
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
        for key, label in (('search', '유사 검색 · TF-IDF'), ('words', '어휘 관계 · PMI·SVD'),
                           ('context', '문맥 용례'), ('topics', '주제 분류 · 준지도 학습')):
            frame = tk.Frame(self.book, bg=BG, padx=px(16), pady=px(14))
            self.book.add(frame, text=label)
            self.tabs[key] = frame
        self._build_search(self.tabs['search'])
        self._build_words(self.tabs['words'])
        self._build_context(self.tabs['context'])
        self._build_topics(self.tabs['topics'])
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
        _note(page, '문서를 낱말 주머니(BoW)로 바꾸고 TF-IDF로 가중한 뒤, 코사인 유사도로 순위를 매깁니다. '
                    '한글은 조사를 떼고, 띄어쓰기 없는 한문은 두 글자씩 잘라 고유명사·원문 구절도 찾습니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.search_var, font=ui.FONTS['input'])
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda e: self.search())
        ttk.Button(row, text='유사도 검색', style='Primary.TButton', command=self.search, cursor='hand2').pack(side='left', padx=(px(8), 0))
        ttk.Button(row, text='선택 문헌과 비슷한 문헌', command=lambda: self.similar(None), cursor='hand2').pack(side='left', padx=(px(8), 0))
        self.search_title = tk.StringVar(value='검색어(예: 淑嬪崔氏 追崇, 육상궁 제향)를 넣거나, 결과 창에서 문헌을 고른 뒤 비슷한 문헌을 찾으세요.')
        tk.Label(page, textvariable=self.search_title, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w').pack(fill='x', pady=(0, px(6)))
        frame, self.search_tree = _table(page, [('sim', '유사도', 70), ('title', '제목', 420), ('source', '자료집', 120), ('terms', '겹치는 핵심어', 260)])
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
        self.search_title.set(f'「{text}」 유사도 순 {len(hits)}건  ·  질의 낱말: ' + ', '.join(list(q)[:12]))
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
        self.search_title.set(f'「{(row or {}).get("title", key)[:40]}」와 비슷한 문헌  ·  이 문헌의 핵심어: '
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
            rows.append((f'{score:.3f}', row.get('title', key), row.get('source', ''), ', '.join(shared)))
            self._search_keys.append(key)
        _fill(self.search_tree, rows)

    def _open_in_results(self, tree):
        sel = tree.selection()
        if sel and int(sel[0]) < len(self._search_keys):
            self.results.focus_key(self._search_keys[int(sel[0])])

    # ── 3강: 어휘 관계 ──

    def _build_words(self, page):
        _note(page, '낱말이 가까이(4어절 안) 함께 나온 횟수로 공기 행렬을 만들고, 양의 점별 상호정보량(PPMI)으로 바꾼 뒤 '
                    '절단 SVD로 32차원 단어 벡터를 얻습니다. PPMI의 SVD는 Skip-gram(Word2Vec)이 암묵적으로 분해하는 행렬과 같은 계열입니다.').pack(fill='x')
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
        ttk.Button(row, text='관계 보기', style='Primary.TButton', command=self.words, cursor='hand2').pack(side='left', padx=(px(8), 0))
        cols = tk.Frame(page, bg=BG)
        cols.pack(fill='both', expand=True)
        cols.columnconfigure(0, weight=1, uniform='w')
        cols.columnconfigure(1, weight=1, uniform='w')
        left = tk.Frame(cols, bg=BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, px(8)))
        right = tk.Frame(cols, bg=BG)
        right.grid(row=0, column=1, sticky='nsew')
        cols.rowconfigure(0, weight=1)
        tk.Label(left, text='쓰임이 비슷한 말 (SVD 벡터 코사인, 전체)', bg=BG, fg=INK, font=ui.FONTS['label']).pack(anchor='w', pady=(0, px(6)))
        f1, self.near_tree = _table(left, [('word', '낱말', 200), ('sim', '코사인', 80)])
        f1.pack(fill='both', expand=True)
        self.assoc_title = tk.StringVar(value='자주 함께 나온 말 (PPMI)')
        tk.Label(right, textvariable=self.assoc_title, bg=BG, fg=INK, font=ui.FONTS['label']).pack(anchor='w', pady=(0, px(6)))
        f2, self.assoc_tree = _table(right, [('word', '낱말', 200), ('pmi', 'PPMI', 80)])
        f2.pack(fill='both', expand=True)
        self.words_note = _note(page, '')
        self.words_note.pack(anchor='w', pady=(px(6), 0))

    def _build_wordspace(self):
        self.status.set('어휘 관계 계산 중… (공기 행렬 → PPMI → SVD)')
        rows = list(self.results.rows)

        def done(space):
            self.results.wordspace = space
            self.period_box.configure(values=['전체'] + sorted(space.periods))
            self.status.set(f'어휘 {len(space.vocab)}개 · 시대 {len(space.periods)}개 구간으로 계산했습니다.')
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
        _fill(self.near_tree, [(w, f'{s:.3f}') for w, s in space.neighbors(word, 20)])
        period = self.period_var.get()
        self.assoc_title.set('자주 함께 나온 말 (PPMI, ' + ('전체' if period == '전체' else period) + ')')
        _fill(self.assoc_tree, [(w, f'{v:.2f}') for w, v in space.associations(word, None if period == '전체' else period, 20)])

    # ── 4강: 문맥 용례 ──

    def _build_context(self, page):
        _note(page, '용어가 나온 자리마다 앞뒤 문맥을 벡터로 만들고, k-평균으로 묶어 쓰임새를 나눕니다(군집 수는 실루엣 점수로 선택). '
                    '같은 말이 문맥에 따라 다르게 쓰이는지(예: 「숙빈」이 최씨인지 다른 인물인지) 볼 수 있습니다. '
                    'BERT 같은 사전학습 문맥 모델은 쓰지 않은 근사 방법입니다.').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        self.ctx_var = tk.StringVar(value='숙빈')
        entry = ttk.Entry(row, textvariable=self.ctx_var, font=ui.FONTS['input'], width=24)
        entry.pack(side='left')
        entry.bind('<Return>', lambda e: self.context())
        ttk.Button(row, text='용례 묶기', style='Primary.TButton', command=self.context, cursor='hand2').pack(side='left', padx=(px(8), 0))
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
        _note(page, '① 규칙 관점(키워드 적중)을 약한 라벨로, ② 결과 창에서 단 주제 주석을 강한 라벨로 써서 라벨마다 로지스틱 회귀(다중 라벨)를 학습합니다.\n'
                    '③ 확신이 높은 미주석 문헌에 의사라벨을 붙여 다시 학습하고(자기학습), ④ 약한 음성에 섞인 실제 양성만큼 확률을 보정합니다(PU 보정).').pack(fill='x')
        row = tk.Frame(page, bg=BG)
        row.pack(fill='x', pady=(px(10), px(10)))
        ttk.Button(row, text='학습 · 전체 분류', style='Primary.TButton', command=self.train, cursor='hand2').pack(side='left')
        ttk.Button(row, text='예측 결과 CSV로 저장', command=self.export, cursor='hand2').pack(side='left', padx=(px(8), 0))
        tk.Label(row, text='새 주제', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(18), px(6)))
        self.new_label = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.new_label, width=14)
        e.pack(side='left')
        e.bind('<Return>', lambda ev: self.add_label())
        ttk.Button(row, text='추가', command=self.add_label, cursor='hand2').pack(side='left', padx=(px(6), 0))
        self.topic_summary = tk.StringVar()
        tk.Label(page, textvariable=self.topic_summary, bg=BG, fg=INK, font=ui.FONTS['label'], anchor='w', justify='left').pack(fill='x')
        frame, self.topic_tree = _table(page, [('label', '주제', 140), ('ann', '주석(관련/전체)', 120), ('rule', '규칙 문헌 수', 100),
                                               ('pred', '분류 문헌 수', 100), ('pseudo', '의사라벨(양성)', 110),
                                               ('rule_f1', '규칙 F1', 80), ('model_f1', '모델 F1', 80)], height=8)
        frame.pack(fill='x', pady=(px(8), 0))
        self.topic_note = _note(page, '')
        self.topic_note.pack(anchor='w', fill='x', pady=(px(8), 0))
        _note(page, '주석 방법: 결과 창 오른쪽 \'주제\' 줄에서 맞는 주제를 체크하면 바로 저장됩니다. 어느 주제도 아니면 \'해당 없음\', '
                    '규칙·모델 제안이 맞으면 \'제안대로 주석\'. 주석은 결과 폴더 topic_labels.csv에 남습니다. '
                    'F1은 주석 문헌으로 k-겹 교차검증한 값이며, 주석을 여러 자료집·주제에 고르게 할수록 믿을 만합니다.').pack(anchor='w', fill='x', pady=(px(6), 0))
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
            pseudo = clf.pseudo_counts.get(name, '–') if clf else '–'

            def fmt(v):
                return '–' if v is None else f'{v:.2f}'
            rf = f1.get(name, {})
            rows.append((name, f'{pos}/{len(ann)}', rule_n, pred_n, pseudo,
                         fmt((rf.get('rule') or {}).get('f1')), fmt((rf.get('model') or {}).get('f1'))))
        _fill(self.topic_tree, rows)
        ann_n = len([k for k in topics.docs if res.row_by_key(k)])
        if ev and ev.get('micro'):
            m = ev['micro']
            self.topic_summary.set(f"교차검증(주석 {ev['n']}건) 마이크로 F1 — 규칙 {m['rule']['f1'] or 0:.2f} → 모델 {m['model']['f1'] or 0:.2f}")
        elif clf:
            self.topic_summary.set(f'학습 완료 · 주석 {ann_n}건. 주석이 4건 이상이면 규칙과 모델을 교차검증으로 비교합니다.')
        else:
            self.topic_summary.set(f'주석 {ann_n}건 · 아직 학습하지 않았습니다.')

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
        self.status.set('주제 분류 학습 중… (약한 라벨 → 학습 → 의사라벨 → 재학습 → PU 보정)')
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
            self.status.set('주제 분류를 마쳤습니다. 결과 창 \'관점\' 열의 +표시가 모델이 새로 찾은 주제입니다.')
        run_background(self, work, done)

    def export(self):
        res = self.results
        if res.classifier is None:
            messagebox.showinfo('예측 결과 저장', '먼저 \'학습 · 전체 분류\'를 실행하세요.', parent=self)
            return
        path = res.folder / 'topic_predictions.csv'
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                labels = res.topics.labels
                w.writerow(['문헌 키', '제목', '자료집', '규칙 관점', '모델 예측 주제', '연구자 주석'] + [f'{n} 확률' for n in labels])
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
