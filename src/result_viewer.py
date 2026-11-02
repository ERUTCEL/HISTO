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
import aliases
import opendict
import relation_types as rtypes
import relations as relmap
import reader
import period
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
                     '수집사료해제집', '학술회의총서', '재외동포사총서', '한국고문서입문'}
SECONDARY_WORDS = ('휘보', '논총', '논저', '해제', '연표', '학술지', 'KCI', '학위논문')   # 자료집·제공처 이름에 들어가면 연구 문헌
SECONDARY_HOSTS = ('kci.go.kr', 'riss.kr', 'dbpia.co.kr', 'kiss.kstudy.com')
# 연구 문헌은 원래 점수가 높아도(최고 155점 정도) 항상 0점 아래로 내려가게 합니다 → 우선 본문 30건에 들지 않고 목록 맨 뒤로
SECONDARY_PENALTY = 200
LATER_PENALTY = 60   # 문예·후대 서술


def is_secondary(row):
    source = str(row.get('source') or '')
    if source in SECONDARY_SOURCES or str(row.get('key') or '').startswith(('db:hb_', 'db:nh_', 'db:hhcs_', 'db:oksr_')):
        return True
    # 한국학자료통합플랫폼처럼 여러 기관을 모은 곳은 제공처(path)·주소로 논문을 가려냅니다
    where = source + ' ' + str(row.get('path') or '')
    return any(w in where for w in SECONDARY_WORDS) or any(h in str(row.get('url') or '') for h in SECONDARY_HOSTS)


# 문예·후대 서술: 시간이 아니라 '기록의 종류'로 봅니다. 사이트가 붙인 기사 형식(소설·문예·기고·논설·기행…)과
# 제목의 史話·秘話·哀史 같은 말. 근현대 신문·잡지라도 '일반기사(보도)'는 그 시대의 사료로 그대로 둡니다.
PRESS_WORDS = ('신문', '일보', '신보', '잡지', '매일')
LITERARY_GENRES = ('소설', '문예', '문학', '기고', '해설·주장', '논설', '기행', '회고', '수필', '야담', '평론')
LITERARY_TITLES = ('史話', '사화)', '秘話', '비화', '哀史', '애사', '소설', '연재', '野談', '야담')


def is_later_narrative(row):
    """문예·야사·평론처럼 '후대에 이야기로 쓴' 기록인가(시간과 무관, 기록 종류로 판단)."""
    title = str(row.get('title') or '')
    if any(w in title for w in LITERARY_TITLES):
        return True
    source = str(row.get('source') or '')
    press = source == '근현대잡지자료' or any(w in source for w in PRESS_WORDS)
    genre = str(row.get('path') or '')   # 사이트가 붙인 기사 형식·분류(예: '기사형식 | 문예, 학술', '기사형태 | 기행문')
    return press and any(g in genre for g in LITERARY_GENRES)


def secondary_kind(row):
    if is_secondary(row):
        return '연구·해설 문헌'
    if is_later_narrative(row):
        return '문예·후대 서술'
    return None


# 점수 엔진은 제목·요약만 봅니다. 그래서 이름이 본문에만 있는 기록은 0점('근거 부족')이 됩니다.
# - 본문을 받았고 본문에 대상 이름이 나오면: 요약에 나온 것과 같게 +80
# - 본문은 아직 없지만 사이트가 검색어로 찾아 준 기록이면: 이름이 본문 뒤쪽에 있는 것이므로 +30
#   ('관련 없음'이 아니라 '요약으로는 확인 안 됨'. 요약·제목에 이름이 나온 기록 80~100점보다 아래)
BODY_NAME_SCORE = 80
BODY_HIT_SCORE = 30
_NO_EVIDENCE = '검색 제목·요약에서 인물 관련 근거 부족'


def _body_hit(row, names):
    a = row['analysis']
    reasons = a.get('score_reasons') or []
    if a['score'] != 0 or not all(_NO_EVIDENCE in x for x in reasons):
        return
    body = re.sub(r'\s+', '', str(row.get('body') or ''))
    name = next((n for n in names if n and n in body), None) if body else None
    if name:
        a['score'] = BODY_NAME_SCORE
        a['score_reasons'] = [f'본문 직접명칭 +{BODY_NAME_SCORE}: {name}']
    elif row.get('terms'):
        a['score'] = BODY_HIT_SCORE
        a['score_reasons'] = [f'사이트 검색에서 본문에 검색어가 나옴(요약에는 없음) +{BODY_HIT_SCORE}']


def _mark_secondary(row):
    a = row['analysis']
    kind = secondary_kind(row)
    if kind and not a.get('secondary'):
        a['secondary'] = kind
        cut = SECONDARY_PENALTY if kind == '연구·해설 문헌' else LATER_PENALTY
        a['score'] = a['score'] - cut
        a.setdefault('score_reasons', []).append(f'{kind}(사료 아님) -{cut}')


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
    # 사료는 이름을 한자로 씁니다(宋時烈). 한글 이름만으로는 사료가 0점이 되므로, 문헌에서 음이 맞는 한자 표기를 찾아 더합니다.
    profile = dict(profile)
    hanja_found = aliases.add_hanja_aliases(profile, rows)
    direct_names = [re.sub(r'\s+', '', x) for x in profile.get('direct_aliases') or []]
    for row in rows:
        row['analysis'] = analyze(row, profile)
        _body_hit(row, direct_names)
        _mark_secondary(row)
        period.fill_year(row)   # 연대가 비어 있으면 사이트 번호에서 읽어 채움(표시용, 점수에는 쓰지 않음)
        row['_search'] = '\n'.join([str(row.get('title') or ''), str(row.get('body') or ''),
                                    str(row.get('snippet') or ''), str(row.get('source') or '')]).casefold()
        row['_search_c'] = re.sub(r'\s+', '', row['_search'])   # 찾기용: 띄어쓰기를 지운 글(宋 時 烈 = 宋時烈)
    # 시대 가중치: 대상의 큰 시대(고대·고려·조선·근대·일제강점기·현대)와 다른 시대의 기록은 낮춤.
    # 대상 시대는 직접 고른 값(설정 '대상 시대')이 있으면 그것, 없으면 자동(period.target_era: 확실할 때만, 이웃한 두 시대일 수 있음)
    choice = profile.get('era_choice')
    if choice in period.ERAS:
        i = period.ERAS.index(choice)
        era, candidates = (i, i), []
    else:
        era, candidates = period.target_era(rows, secondary_kind)   # 확실할 때만 (시작, 끝) 시대가 정해짐
    period.apply_era(rows, era)
    profile = dict(profile, _era=era, _era_auto=choice not in period.ERAS, _era_candidates=candidates, _hanja_found=hanja_found)
    rows.sort(key=lambda row: (-row['analysis']['score'], row['key']))
    return rows, profile


def _reading_text(parent, **kw):
    """열람 칸: 종이색 바탕 위에 출전줄 · 제목 · 서지 · 구분선 · 본문 · 주석(다른 바탕의 단)을 역할별로 구별합니다."""
    text = tk.Text(parent, wrap='word', font=ui.FONTS['reading'], relief='flat', bg=ui.PAPER, fg=INK,
                   padx=px(28), pady=px(16), spacing1=px(1), spacing3=px(6), highlightthickness=0,
                   selectbackground=ACCENT, selectforeground='white', cursor='arrow', **kw)
    text.tag_configure('src', font=ui.FONTS['label_sm'], foreground=INK)
    text.tag_configure('meta', font=ui.FONTS['small'], foreground=MUTED, spacing3=px(2))
    text.tag_configure('faint', font=ui.FONTS['small'], foreground=ui.FAINT)
    text.tag_configure('warnline', font=ui.FONTS['label_sm'], foreground=ui.SEAL, spacing1=px(2))
    text.tag_configure('title', font=ui.FONTS['title'], foreground=INK, spacing1=px(6), spacing3=px(4))
    text.tag_configure('rule', font=(ui.FAMILY, 1), background=LINE, spacing1=0, spacing2=0, spacing3=0)
    text.tag_configure('chip', font=ui.FONTS['label_sm'], foreground=ACCENT, background=ACCENT_PALE)
    text.tag_configure('chip2', font=ui.FONTS['label_sm'], foreground=MUTED, background=NEUTRAL_PALE)
    text.tag_configure('chip_ok', font=ui.FONTS['label_sm'], foreground=OK_INK, background=OK_PALE)
    text.tag_configure('head', font=ui.FONTS['label_sm'], foreground=MUTED, spacing1=px(16), spacing3=px(6))
    text.tag_configure('note', font=ui.FONTS['small'], foreground=MUTED, background=ui.ANNOT, lmargin1=px(10),
                       lmargin2=px(10), rmargin=px(10), spacing1=px(6), spacing3=px(6))
    text.tag_configure('body', font=ui.FONTS['reading'], foreground=INK, spacing2=px(6))
    text.tag_configure('error', font=ui.FONTS['small'], foreground=ui.SEAL)
    text.tag_configure('link', font=ui.FONTS['small'], foreground=ACCENT, underline=True, spacing3=px(12))
    # 사이트 주소를 누르면 브라우저로 엽니다
    text.tag_bind('link', '<Enter>', lambda e: text.configure(cursor='hand2'))
    text.tag_bind('link', '<Leave>', lambda e: text.configure(cursor='arrow'))
    text.tag_bind('link', '<Button-1>', lambda e: _open_link(text, e))
    # 검색어 강조: 놓인 자리 글꼴 크기에 맞춰 굵게 + 옅은 노랑
    text.tag_configure('hit', font=ui.FONTS['reading_b'], background=ui.HIT)
    text.tag_configure('hit_small', font=ui.FONTS['small_b'], background=ui.HIT)
    text.tag_configure('hit_bg', background=ui.HIT)
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


def _pill(c, x1, y1, x2, y2, r=None, **kw):
    """캔버스에 둥근 사각형(반경 r, 기본은 높이의 절반 = 알약 모양)."""
    r = min(r if r is not None else (y2 - y1) / 2, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return c.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class RelationsWindow(tk.Toplevel):
    """관계 후보 지도: 같은 문장에 함께 나온 표현을 잇습니다(관계의 종류는 뜻하지 않음).
    같은 데이터를 여러 모양으로 그립니다 — 원형 · 중심형 · 연결망 · 표 · 시간 흐름."""

    STYLES = (('center', '중심형', '연구 대상을 가운데 두고, 함께 나온 기록이 많을수록 가까이 놓습니다. 선 위 숫자 = 함께 나온 기록 수. 다른 표현을 누르면 가운데가 바뀝니다.'),
              ('circle', '원형', '표현을 원 위에 놓고 함께 나온 짝을 잇습니다. 선이 굵을수록 근거 기록이 많습니다.'),
              ('network', '연결망', '자주 함께 나오는 표현끼리 모이도록 배치합니다. 무리를 보기 좋습니다.'),
              ('matrix', '표', '표현 × 표현 표. 칸이 진할수록 함께 나온 기록이 많습니다. 칸을 누르면 근거를 봅니다.'),
              ('timeline', '시간 흐름', '연결마다 근거 기록이 어느 시기에 나오는지 연대 축 위에 점으로 보여 줍니다(연대가 있는 기록만).'))

    def __init__(self, parent, rows):
        super().__init__(parent)
        apply_theme(self)
        self.title('관계도 — 공동 등장, 관계 미확정')
        ui.fit_geometry(self, 1100, 860, 780, 620)
        self._rows = rows
        self._year_of = {r.get('key'): r['analysis'].get('year') for r in rows}
        self._prof = getattr(parent, 'profile', None) or {}
        self.kind_vars = {k: tk.BooleanVar(value=True) for k in rtypes.NAMES}
        self._edge_ids = {}
        self._current = 0
        self._center = None
        self._compute()
        self.style_var = tk.StringVar(value=settings.get('relations_style', 'center'))
        if self.style_var.get() not in {k for k, _, _ in self.STYLES}:
            self.style_var.set('center')

        head = tk.Frame(self, bg=CARD)
        head.pack(fill='x')
        inner = tk.Frame(head, bg=CARD)
        inner.pack(fill='x', padx=px(24), pady=(px(18), px(14)))
        trow = tk.Frame(inner, bg=CARD)
        trow.pack(fill='x')
        ttk.Button(trow, text='←  결과로', style='Quiet.TButton', command=self.go_back, cursor='hand2').pack(side='left', padx=(0, px(10)))
        tk.Label(trow, text='관계도', bg=CARD, fg=INK, font=ui.FONTS['h1']).pack(side='left')
        ui.info_icon(trow, '현재 목록의 문헌에서 같은 문장에 나온 표현을 잇습니다. 혈연·지시·소유 같은 관계의 종류를 뜻하지 않습니다.\n'
                           '많이 나온 표현 12개 · 연결 최대 30개. 한문·국문·주석 중복이 있을 수 있습니다.', CARD).pack(side='left', padx=(px(12), 0))
        seg = tk.Frame(trow, bg=CARD)
        seg.pack(side='right')
        for key, label, desc in self.STYLES:
            b = ttk.Radiobutton(seg, text=label, value=key, variable=self.style_var, style='Seg.Toolbutton',
                                command=self._style_changed, cursor='hand2')
            b.pack(side='left', padx=(px(4), 0))
            ui.tip(b, desc)
        self.bind('<Escape>', lambda e: self.go_back())
        self.bind('<Alt-Left>', lambda e: self.go_back())
        self.legend = tk.Frame(inner, bg=CARD)
        self.legend.pack(fill='x', pady=(px(10), 0))
        self._build_legend()
        ui.hline(head, LINE).pack(fill='x')

        body = tk.Frame(self, bg=BG)
        body.pack(fill='both', expand=True, padx=px(20), pady=px(16))
        graph = ui.round_corners(tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
        graph.pack(fill='x')
        self.canvas = tk.Canvas(graph, height=px(430), bg=CARD, highlightthickness=0)
        self.canvas.pack(fill='x')
        self.canvas.bind('<Configure>', lambda e: self._draw())

        pick = tk.Frame(body, bg=BG)
        pick.pack(fill='x', pady=(px(14), px(8)))
        tk.Label(pick, text='연결', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(10)))
        self.edges_list = ttk.Combobox(pick, state='readonly')
        self._fill_edges_list()
        self.edges_list.pack(side='left', fill='x', expand=True)
        self.edges_list.bind('<<ComboboxSelected>>', lambda e: self.show_edge(self.edges_list.current()))
        box = ui.round_corners(tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
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

    # ── 데이터: 연결 세기 · 숨기기 · 합치기 ─────────────────────
    def _target_key(self):
        return re.sub(r'\s+', '', self._prof.get('target') or '') or '_'

    def _merges(self):
        return dict((settings.get('relations_merges') or {}).get(self._target_key(), {}))

    def _hidden(self):
        return set(settings.get('relations_hidden') or [])

    def _compute(self):
        """연결을 문장 단위로 직접 세고(엔진은 기록마다 몇 개만 뽑아 많이 빠짐), 숨긴 점은 빼고, 합친 점은 하나로."""
        relations = relmap.build(self._rows, self._prof.get('target'), self._prof.get('direct_aliases') or [],
                                 merges=self._merges())
        hidden = self._hidden()
        relations = [r for r in relations if r['source'] not in hidden and r['target'] not in hidden]
        counts = Counter(n for rel in relations for n in (rel['source'], rel['target']))
        names = [name for name, _ in counts.most_common(12)]
        grouped = {}
        for rel in relations:
            pair = tuple(sorted((rel['source'], rel['target'])))
            if pair[0] != pair[1] and all(x in names for x in pair):
                grouped.setdefault(pair, []).append(rel)
        self.edges = sorted(grouped.items(), key=lambda item: -len(item[1]))[:30]
        # 연결마다 관계 종류 후보(근거 문장의 단서 낱말 · relation_types 참고)
        self.kinds = [rtypes.summarize(ev, a, b) for (a, b), ev in self.edges]
        self._names, self._counts = names, counts
        self._current = 0
        target = self._prof.get('target')
        if self._center not in names:
            self._center = target if target in names else (names[0] if names else None)

    def _build_legend(self):
        for w in self.legend.winfo_children():
            w.destroy()
        legend = self.legend
        tk.Label(legend, text='관계 종류', bg=CARD, fg=MUTED, font=ui.FONTS['small']).grid(row=0, column=0, sticky='nw', padx=(0, px(8)))
        n_by = Counter()
        for _, cnt in self.kinds:   # 종류별 근거 기록 수(연결 하나를 한 종류로 세지 않음)
            n_by.update(cnt)
        for k in rtypes.NAMES:
            if not n_by.get(k) and k != rtypes.UNKNOWN:
                continue   # 이 자료에 없는 종류는 범례에서도 뺌(깔끔하게)
            box = tk.Frame(legend, bg=CARD)
            slot = sum(1 for w in legend.winfo_children() if isinstance(w, tk.Frame)) - 1
            box.grid(row=slot // 5, column=1 + slot % 5, sticky='w', padx=(0, px(12)))   # 한 줄에 5개씩
            tk.Label(box, text='●', bg=CARD, fg=rtypes.COLORS[k], font=ui.FONTS['body']).pack(side='left')
            cb = ttk.Checkbutton(box, text=f'{k} {n_by.get(k, 0)}건', variable=self.kind_vars[k], command=self._draw, cursor='hand2')
            cb.pack(side='left')
            ui.tip(cb, '근거 문장에 이 종류의 단서 낱말이 있는 연결입니다(후보). 끄면 지도에서 숨깁니다.' if k != rtypes.UNKNOWN
                   else '단서 낱말이 없어 종류를 정하지 않은 연결(같은 문장에 함께 나옴).')
        ui.info_icon(legend, '점을 오른쪽 클릭하면 숨기거나(도감·대신 같은 일반 낱말) 다른 점과 합칠 수 있습니다(원자·세자 → 경종).\n'
                             '관계 종류는 근거 문장 속 단서 낱말로 정한 후보입니다(자세한 방법: 관계도_방법.md).\n'
                             '· 사건형 — 처벌·대립, 군사·전쟁, 외교·사신, 의례·장소: 두 표현 가까이에 단서가 있으면\n'
                             '· 두 사람형 — 명령·보고: 단서가 두 이름 사이에 있고 두 이름이 가까울 때\n'
                             '· 어순 틀 — 혈연·가족(A의 아들 B / B는 A의 아들), 관직·임명(A를 B로 삼다)\n'
                             '새 대상의 근거 문장을 읽어 70% 이상 맞은 종류만 표시합니다.', CARD).grid(row=0, column=6, sticky='w')
        if self._hidden() or self._merges():
            undo = tk.Label(legend, text='숨긴·합친 점 되돌리기', bg=CARD, fg=ACCENT, font=ui.FONTS['small'], cursor='hand2')
            undo.grid(row=1, column=6, sticky='w')
            undo.bind('<Button-1>', lambda e: self._reset_edits())
            ui.tip(undo, '숨김: ' + (', '.join(sorted(self._hidden())) or '없음') + '\n합침: '
                   + (', '.join(f'{a}→{b}' for a, b in self._merges().items()) or '없음'))

    def _fill_edges_list(self):
        self.edges_list.configure(values=[f'{a} ↔ {b} · {self.kinds[i][0]} · 근거 기록 {len(e)}건'
                                          for i, ((a, b), e) in enumerate(self.edges)])

    def _refresh(self):
        self._compute()
        self._build_legend()
        self._fill_edges_list()
        if self.edges:
            self.show_edge(0)
        else:
            self.evidence.delete('1.0', 'end')
            self._draw()

    def _node_menu(self, event, name):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"'{name}' 숨기기 (일반 낱말·잡음)", command=lambda: self._hide(name))
        merge = tk.Menu(menu, tearoff=0)
        for other in self._names:
            if other != name:
                merge.add_command(label=other, command=lambda o=other: self._merge(name, o))
        menu.add_cascade(label=f"'{name}'을(를) 같은 대상으로 합치기 →", menu=merge)
        if self.style_var.get() == 'center':
            menu.add_command(label=f"'{name}'을(를) 가운데로", command=lambda: self._node_clicked(name))
        menu.tk_popup(event.x_root, event.y_root)

    def _hide(self, name):
        settings.save(relations_hidden=sorted(self._hidden() | {name}))
        self._refresh()

    def _merge(self, src, dst):
        allm = dict(settings.get('relations_merges') or {})
        mine = dict(allm.get(self._target_key(), {}))
        mine[src] = dst
        for k, v in list(mine.items()):   # 합친 대상이 다시 합쳐지면 끝까지 따라감
            if v == src:
                mine[k] = dst
        allm[self._target_key()] = mine
        settings.save(relations_merges=allm)
        if self._center == src:
            self._center = dst
        self._refresh()

    def _reset_edits(self):
        allm = dict(settings.get('relations_merges') or {})
        allm.pop(self._target_key(), None)
        settings.save(relations_hidden=[], relations_merges=allm)
        self._refresh()

    # ── 그리기 ──────────────────────────────────────────
    def _style_changed(self):
        settings.save(relations_style=self.style_var.get())
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete('all')
        self._edge_ids = {}
        width, height = max(c.winfo_width(), px(600)), max(c.winfo_height(), px(300))
        if not self._names:
            c.create_text(width / 2, height / 2, text='그릴 연결이 없습니다.', fill=MUTED, font=ui.FONTS['body'])
            return
        style = self.style_var.get()
        if style == 'matrix':
            self._draw_matrix(width, height)
        elif style == 'timeline':
            self._draw_timeline(width, height)
        else:
            pos = {'center': self._layout_center, 'network': self._layout_network}.get(style, self._layout_circle)(width, height)
            edges = [(i, e) for i, e in enumerate(self.edges)
                     if self._visible(i) and (style != 'center' or self._center in e[0])]
            if style == 'center':   # 가운데와 이어지지 않은 표현은 숨김(헷갈리지 않게)
                linked = {n for _, ((a, b), _) in edges for n in (a, b)} | {self._center}
                pos = {n: p for n, p in pos.items() if n in linked}
            self._draw_graph(pos, edges, numbers=style == 'center')

    def _visible(self, index):
        return self.kind_vars[self.kinds[index][0]].get()

    def _layout_circle(self, width, height):
        names = self._names
        cx, cy = width / 2, height / 2
        rx, ry = max(px(160), width / 2 - px(110)), height / 2 - px(40)
        return {n: (cx + rx * math.cos(2 * math.pi * i / len(names) - math.pi / 2),
                    cy + ry * math.sin(2 * math.pi * i / len(names) - math.pi / 2)) for i, n in enumerate(names)}

    def _layout_center(self, width, height):
        center = self._center if self._center in self._names else self._names[0]
        self._center = center
        strength = Counter()
        for (a, b), ev in self.edges:
            if center in (a, b):
                strength[b if a == center else a] += len(ev)
        others = sorted((n for n in self._names if n != center), key=lambda n: -strength[n])
        top = max(strength.values()) if strength else 1
        cx, cy = width / 2, height / 2
        rmax = min(width / 2 - px(90), height / 2 - px(30))
        pos = {center: (cx, cy)}
        for i, n in enumerate(others):
            # 함께 나온 기록이 많을수록 가까이(없으면 바깥 테두리)
            r = rmax * (0.55 + 0.45 * (1 - strength[n] / top)) if strength[n] else rmax
            ang = 2 * math.pi * i / max(1, len(others)) - math.pi / 2
            pos[n] = (cx + r * math.cos(ang) * (width / 2 - px(90)) / rmax, cy + r * math.sin(ang))
        return pos

    def _layout_network(self, width, height):
        """간단한 힘 배치: 연결된 표현은 끌어당기고, 모든 표현은 서로 밀어냅니다(같은 입력이면 같은 그림)."""
        names = self._names
        n = len(names)
        pos = {name: [math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n)] for i, name in enumerate(names)}
        weight = {pair: len(ev) for pair, ev in self.edges}
        k = 1.6 / math.sqrt(n)
        for step in range(300):
            disp = {name: [0.0, 0.0] for name in names}
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                    d = math.hypot(dx, dy) or 0.01
                    f = k * k / d
                    disp[a][0] += dx / d * f; disp[a][1] += dy / d * f
                    disp[b][0] -= dx / d * f; disp[b][1] -= dy / d * f
            for (a, b), w in weight.items():
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 0.01
                f = d * d / k * (1 + math.log1p(w))
                disp[a][0] -= dx / d * f; disp[a][1] -= dy / d * f
                disp[b][0] += dx / d * f; disp[b][1] += dy / d * f
            t = 0.1 * (1 - step / 300) + 0.005
            for name in names:
                dx, dy = disp[name]
                d = math.hypot(dx, dy) or 0.01
                pos[name][0] += dx / d * min(d, t)
                pos[name][1] += dy / d * min(d, t)
        xs = [p[0] for p in pos.values()]; ys = [p[1] for p in pos.values()]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        sx = (width - px(200)) / ((x1 - x0) or 1); sy = (height - px(70)) / ((y1 - y0) or 1)
        return {name: (px(100) + (p[0] - x0) * sx, px(35) + (p[1] - y0) * sy) for name, p in pos.items()}

    def _draw_graph(self, pos, edges, numbers=False):
        c = self.canvas
        for index, (pair, evidence) in edges:
            if pair[0] not in pos or pair[1] not in pos:
                continue
            (x1, y1), (x2, y2) = pos[pair[0]], pos[pair[1]]
            tag = f'edge{index}'
            n = len(evidence)
            base = max(px(2), min(px(1) + n // 4, px(8)))
            width_ = base + (px(3) if index == self._current else 0)
            # 선 = 회색 바탕 + 단서가 있는 기록의 비율만큼만 그 종류 색(가운데 쪽부터). 연결 전체를 한 종류로 칠하지 않음
            line = c.create_line(x1, y1, x2, y2, fill=ui.MUTED if index == self._current else ui.LINE,
                                 width=width_, tags=(tag,))
            self._edge_ids[index] = line
            mx, my = (x1 * 0.42 + x2 * 0.58), (y1 * 0.42 + y2 * 0.58)
            typed = sorted(((k, v) for k, v in self.kinds[index][1].items() if k != rtypes.UNKNOWN), key=lambda kv: -kv[1])
            if numbers:   # 선 위에 함께 나온 기록 수
                t = c.create_text(mx, my, text=str(n), fill=INK, font=ui.FONTS['label_sm'], tags=(tag,))
                bx = c.bbox(t)
                c.tag_lower(_pill(c, bx[0] - px(6), bx[1] - px(2), bx[2] + px(6), bx[3] + px(2), fill=CARD,
                                  outline=ui.LINE, width=1, tags=(tag,)), t)
                bar_y = bx[3] + px(5)
            else:
                bar_y = my
            if typed:   # 작은 비율 막대: 회색 = 단서 없음, 색 = 그 종류 단서가 있는 기록의 비율
                bw, bh = px(40), px(6)
                bx0 = mx - bw / 2
                c.create_rectangle(bx0, bar_y, bx0 + bw, bar_y + bh, fill=ui.LINE_SOFT, outline='', tags=(tag,))
                x_ = bx0
                for kind, cnt in typed:
                    w_ = max(px(2), bw * cnt / n)
                    c.create_rectangle(x_, bar_y, min(bx0 + bw, x_ + w_), bar_y + bh, fill=rtypes.COLORS[kind], outline='', tags=(tag,))
                    x_ += w_
            c.tag_bind(tag, '<Motion>', lambda e, i=index: self._edge_hover(e, i))
            c.tag_bind(tag, '<Leave>', lambda e: c.delete('tl_tip'), add='+')
            c.tag_bind(tag, '<Button-1>', lambda event, i=index: self.show_edge(i))
            c.tag_bind(tag, '<Enter>', lambda e: c.configure(cursor='hand2'))
            c.tag_bind(tag, '<Leave>', lambda e: c.configure(cursor=''))
        top = max(self._counts[n] for n in self._names) or 1
        for name, (x, y) in pos.items():
            tag = 'node_' + str(self._names.index(name))
            w = max(px(40), ui.FONTS['label_sm'].measure(name) // 2 + px(14))
            hub = self.style_var.get() == 'center' and name == self._center
            _pill(c, x - w, y - px(16), x + w, y + px(16), fill=ACCENT if hub else ACCENT_PALE, outline=ACCENT,
                  width=1 + int(2 * self._counts[name] / top), tags=(tag,))
            c.create_text(x, y, text=name, fill='white' if hub else ACCENT, font=ui.FONTS['label_sm'], tags=(tag,))
            c.tag_bind(tag, '<Button-1>', lambda e, n=name: self._node_clicked(n))
            c.tag_bind(tag, '<Button-3>', lambda e, n=name: self._node_menu(e, n))
            c.tag_bind(tag, '<Enter>', lambda e: c.configure(cursor='hand2'))
            c.tag_bind(tag, '<Leave>', lambda e: c.configure(cursor=''))

    def _edge_hover(self, event, index):
        """선에 마우스를 올리면: 두 표현 · 전체 건수 · 종류별 건수와 비율."""
        c = self.canvas
        (a, b), ev = self.edges[index]
        n = len(ev)
        parts = [f'{k} {v}건({v * 100 // n}%)' for k, v in sorted(self.kinds[index][1].items(), key=lambda kv: -kv[1])]
        c.delete('tl_tip')
        t = c.create_text(event.x + px(12), event.y + px(12), anchor='nw', fill=INK, font=ui.FONTS['small'],
                          text=f'{a} ↔ {b} · 함께 나온 기록 {n}건\n' + ' · '.join(parts), tags=('tl_tip',))
        x1, y1, x2, y2 = c.bbox(t)
        if x2 > c.winfo_width() - 4:
            c.move(t, -(x2 - x1) - px(24), 0)
            x1, y1, x2, y2 = c.bbox(t)
        c.tag_raise(t, c.create_rectangle(x1 - px(6), y1 - px(4), x2 + px(6), y2 + px(4), fill='#FFFFFF', outline=INK, tags=('tl_tip',)))

    def _node_clicked(self, name):
        """표현을 누르면: 중심형이면 가운데로, 아니면 그 표현의 가장 굵은 연결을 보여 줌."""
        if self.style_var.get() == 'center':
            self._center = name
        first = next((i for i, (pair, _) in enumerate(self.edges) if name in pair), None)
        if first is not None:
            self._current = first
            self.edges_list.current(first)
            self._fill_evidence(first)
        self._draw()

    def _draw_matrix(self, width, height):
        c, names = self.canvas, self._names
        n = len(names)
        label_w = max(ui.FONTS['label_sm'].measure(x) for x in names) + px(16)
        cell = min((width - label_w - px(30)) / n, (height - px(90)) / n)
        x0, y0 = label_w + px(10), px(80)
        weight = {pair: (i, len(ev)) for i, (pair, ev) in enumerate(self.edges)}
        top = max((w for _, w in weight.values()), default=1)
        for i, name in enumerate(names):
            c.create_text(x0 - px(8), y0 + (i + 0.5) * cell, text=name, anchor='e', fill=INK, font=ui.FONTS['label_sm'])
            c.create_text(x0 + (i + 0.5) * cell, y0 - px(6), text=name, anchor='w', angle=60, fill=INK, font=ui.FONTS['label_sm'])
        for i, a in enumerate(names):
            for j, b in enumerate(names):
                pair = tuple(sorted((a, b)))
                x, y = x0 + j * cell, y0 + i * cell
                if a == b:
                    c.create_rectangle(x, y, x + cell, y + cell, fill=ui.LINE_SOFT, outline=CARD)
                    continue
                idx, w = weight.get(pair, (None, 0))
                if idx is not None and not self._visible(idx):
                    idx, w = None, 0
                shade = 0 if not w else 0.2 + 0.8 * w / top
                fill = _blend('#FFFFFF', ACCENT, shade) if w else ui.ROW_ALT   # 진하기 = 건수(종류는 칸 모서리 점)
                tag = f'cell{i}_{j}'
                c.create_rectangle(x, y, x + cell, y + cell, fill=fill, outline=CARD, tags=(tag,))
                if w:
                    c.create_text(x + cell / 2, y + cell / 2, text=str(w), fill='white' if shade > 0.55 else INK,
                                  font=ui.FONTS['label_sm'], tags=(tag,))
                    main = self.kinds[idx][0]
                    if main != rtypes.UNKNOWN:
                        r_ = max(px(3), cell * 0.09)
                        c.create_oval(x + px(3), y + px(3), x + px(3) + 2 * r_, y + px(3) + 2 * r_, fill=rtypes.COLORS[main],
                                      outline=CARD, tags=(tag,))
                    c.tag_bind(tag, '<Button-1>', lambda e, k=idx: self.show_edge(k))
                    c.tag_bind(tag, '<Enter>', lambda e: c.configure(cursor='hand2'))
                    c.tag_bind(tag, '<Leave>', lambda e: c.configure(cursor=''))

    def _draw_timeline(self, width, height):
        """시간 흐름: 행 = 연결(처음 나온 순), 열 = 시기. 칸 안 숫자 = 그 시기 근거 기록 수, 색 = 관계 종류, 진할수록 많음.
        맨 위 '전체' 줄은 시기별 합계. 몇 건뿐인 아주 이른·늦은 기록은 '이전'·'이후' 칸에 모읍니다."""
        c = self.canvas
        dated = []   # (연결 번호, 연도, 종류)
        for i, ((a, b), ev) in enumerate(self.edges):
            if not self._visible(i):
                continue
            for rel in ev:
                yr = self._year_of.get(rel.get('record_key'))
                if yr:
                    dated.append((i, int(yr), rel.get('_kind', rtypes.UNKNOWN)))
        if not dated:
            c.create_text(width / 2, height / 2, text='연대가 있는 근거 기록이 없습니다.', fill=MUTED, font=ui.FONTS['body'])
            return
        years = sorted(y for _, y, _ in dated)
        lo, hi = years[int(len(years) * 0.05)], years[min(len(years) - 1, int(len(years) * 0.95))]
        span = max(1, hi - lo)
        size = next(s_ for s_ in (5, 10, 20, 50, 100) if span / s_ <= 14)
        start, stop = lo // size * size, hi // size * size
        cols = [('before', f'{start} 이전')] if years[0] < start else []
        cols += [(y, f'{y}') for y in range(start, stop + 1, size)]
        if years[-1] >= stop + size:
            cols.append(('after', f'{stop + size} 이후'))

        def col_of(y):
            if y < start:
                return 'before'
            if y >= stop + size:
                return 'after'
            return y // size * size
        # 연결별·시기별 건수와 종류
        cells = {}
        first = {}
        for i, y, kind in dated:
            key = (i, col_of(y))
            cells.setdefault(key, Counter())[kind] += 1
            first[i] = min(first.get(i, y), y)
        rows = sorted(first, key=lambda i: (first[i], -len(self.edges[i][1])))[:12]
        total = Counter()
        for (i, col), cnt in cells.items():
            if i in rows:
                total[col] += sum(cnt.values())
        label_w = max(ui.FONTS['label_sm'].measure(f'{a} ↔ {b}') for (a, b), _ in (self.edges[i] for i in rows)) + px(38)
        x0, top_y = label_w, px(40)
        cw = (width - x0 - px(20)) / len(cols)
        rh = min(px(30), (height - top_y - px(46)) / (len(rows) + 1.4))
        top_count = max([sum(v.values()) for v in cells.values()] + [1])
        self._tl_cells = {}
        # 머리: 시기
        unit = f'{size}년 단위'
        c.create_text(px(10), top_y - px(18), text=f'시기 ({unit})', anchor='w', fill=MUTED, font=ui.FONTS['small'])
        for j, (col, text) in enumerate(cols):
            c.create_text(x0 + (j + 0.5) * cw, top_y - px(4), text=text, fill=MUTED, font=ui.FONTS['small'], anchor='s')

        def cell(x, y, n, kind, tag, info):
            shade = 0.15 + 0.85 * n / top_count   # 한 가지 색: 진할수록 많음(종류는 줄 앞 점으로)
            fill = _blend('#FFFFFF', ACCENT, shade)
            c.create_rectangle(x + 1, y + 1, x + cw - 1, y + rh - 1, fill=fill, outline='', tags=(tag,))
            c.create_text(x + cw / 2, y + rh / 2, text=str(n), fill='white' if shade > 0.55 else INK,
                          font=ui.FONTS['label_sm'], tags=(tag,))
            self._tl_cells[tag] = info
            c.tag_bind(tag, '<Enter>', lambda e, t=tag: self._tl_hover(e, t))
            c.tag_bind(tag, '<Leave>', lambda e: (c.delete('tl_tip'), c.configure(cursor='')))

        # 전체 줄: 막대(높을수록 그 시기 기록이 많음)
        y = top_y
        c.create_text(px(10), y + rh / 2, text='전체', anchor='w', fill=INK, font=ui.FONTS['label_sm'])
        tmax = max(total.values()) if total else 1
        for j, (col, text) in enumerate(cols):
            if total[col]:
                h = (rh - px(4)) * total[col] / tmax
                c.create_rectangle(x0 + j * cw + cw * 0.2, y + rh - h, x0 + (j + 0.8) * cw, y + rh, fill=ui.LINE_STRONG, outline='')
                c.create_text(x0 + (j + 0.5) * cw, y + rh / 2, text=str(total[col]), fill=INK, font=ui.FONTS['small'])
        c.create_line(px(8), y + rh + px(3), x0 + len(cols) * cw, y + rh + px(3), fill=LINE)
        # 연결 줄
        for r, i in enumerate(rows):
            (a, b), ev = self.edges[i]
            y = top_y + (r + 1.3) * rh
            current = i == self._current
            c.create_text(px(10), y + rh / 2, text='●', anchor='w', fill=rtypes.COLORS[self.kinds[i][0]], font=ui.FONTS['small'])
            c.create_text(px(24), y + rh / 2, text=f'{a} ↔ {b}', anchor='w', fill=INK if current else MUTED,
                          font=ui.FONTS['label_sm'])
            if current:
                c.create_rectangle(px(4), y, x0 + len(cols) * cw, y + rh, outline=ACCENT, width=2)
            for j, (col, text) in enumerate(cols):
                cnt = cells.get((i, col))
                if not cnt:
                    continue
                typed = {k: v for k, v in cnt.items() if k != rtypes.UNKNOWN}
                kind = max(typed, key=typed.get) if typed else rtypes.UNKNOWN
                tag = f'tl{i}_{j}'
                period = (None, start - 1) if col == 'before' else (stop + size, None) if col == 'after' else (col, col + size - 1)
                cell(x0 + j * cw, y, sum(cnt.values()), kind, tag, (i, period, text, cnt))
                c.tag_bind(tag, '<Button-1>', lambda e, i=i, p=period: self.show_edge(i, period=p))
        c.create_text(px(10), height - px(12), anchor='w', fill=MUTED, font=ui.FONTS['small'],
                      text='칸 숫자 = 그 시기에 함께 나온 기록 수(진할수록 많음) · 줄 앞 ● = 관계 종류 · 칸을 누르면 그 시기 기록만 아래에')

    def _tl_hover(self, event, tag):
        c = self.canvas
        c.configure(cursor='hand2')
        i, period, label, cnt = self._tl_cells[tag]
        (a, b), _ = self.edges[i]
        when = f'{period[0]}~{period[1]}년' if period[0] and period[1] else label
        kinds = ' · '.join(f'{k} {v}' for k, v in cnt.most_common())
        c.delete('tl_tip')
        x, y = event.x + px(12), event.y + px(12)
        t = c.create_text(x, y, text=f'{a} ↔ {b}\n{when} · {sum(cnt.values())}건\n{kinds}', anchor='nw',
                          fill=INK, font=ui.FONTS['small'], tags=('tl_tip',))
        x1, y1, x2, y2 = c.bbox(t)
        if x2 > c.winfo_width() - 4:
            c.move(t, -(x2 - x1) - px(24), 0)
            x1, y1, x2, y2 = c.bbox(t)
        r = c.create_rectangle(x1 - px(6), y1 - px(4), x2 + px(6), y2 + px(4), fill='#FFFFFF', outline=INK, tags=('tl_tip',))
        c.tag_raise(t, r)

    def go_back(self):
        parent = self.master
        self.destroy()
        try:
            parent.lift()
            parent.focus_force()
        except tk.TclError:
            pass

    def _fill_evidence(self, index, period=None):
        self.evidence.delete('1.0', 'end')
        rels = sorted(self.edges[index][1], key=lambda r: r.get('_kind', rtypes.UNKNOWN) == rtypes.UNKNOWN)   # 종류 근거가 있는 기록 먼저
        if period:
            lo, hi = period

            def inside(rel):
                yr = self._year_of.get(rel.get('record_key'))
                return bool(yr) and (lo is None or int(yr) >= lo) and (hi is None or int(yr) <= hi)
            rels = [r for r in rels if inside(r)]
            when = f'{lo}~{hi}년' if lo and hi else (f'{hi}년 이전' if hi else f'{lo}년 이후')
            self.evidence.insert('end', f'{when} 기록 {len(rels)}건  (다른 시기까지 보려면 위 연결 목록에서 다시 고르세요)\n\n', 'note')
        for rel in rels[:30]:
            yr = self._year_of.get(rel.get('record_key'))
            kind, cue = rel.get('_kind', rtypes.UNKNOWN), rel.get('_cue', '')
            self.evidence.insert('end', f"[{kind}{f' · 단서 {cue}' if cue else ''}] [{rel['basis']}] {rel['record_key']}{f' · {yr}' if yr else ''}\n", 'meta')
            self.evidence.insert('end', f"{rel['evidence']}\n", 'body')
            self.evidence.insert('end', f"{rel['url']}\n\n", 'link')

    def show_edge(self, index, period=None):
        if index is None or index < 0:
            return
        self._current = index
        self.edges_list.current(index)
        self._fill_evidence(index, period)
        self._draw()


def _blend(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


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

        # ── 머리줄: 돌아가기 · 화면 이름 · 현황 | 검색 저장 ──────────────
        top = tk.Frame(self, bg=BG)
        top.pack(fill='x')
        head = tk.Frame(top, bg=BG)
        head.pack(fill='x', padx=px(18), pady=(px(14), px(8)))
        ttk.Button(head, text='←  연구실로', style='Tool.TButton', command=self.go_back, cursor='hand2').pack(side='left')
        tk.Label(head, text='문헌 결과', bg=BG, fg=INK, font=ui.FONTS['h1']).pack(side='left', padx=(px(6), px(14)))
        self.bind('<Escape>', self._on_escape)
        for key in ('<Control-f>', '<Control-F>'):
            self.bind(key, self._focus_query)
        self.bind('<F5>', lambda e: self.reload())
        self.bind('<Alt-Left>', lambda e: self.go_back())
        self.info_detail = ''
        info_label = tk.Label(head, textvariable=self.info, bg=BG, fg=MUTED, font=ui.FONTS['small'], anchor='w',
                              justify='left', cursor='question_arrow')
        info_label.pack(side='left', pady=(px(3), 0))
        ui.tip(info_label, lambda: '\n'.join(x for x in (self.info_detail, self.learn_info.get()) if x))
        ttk.Button(head, text='저장한 검색 열기', style='Tool.TButton', command=self.choose_folder, cursor='hand2').pack(side='right')
        ttk.Button(head, text='이 검색 저장', command=self.save_search, cursor='hand2').pack(side='right', padx=(0, px(4)))
        # 대상 시대: 결과 전체에 걸리는 설정이라 머리줄에. 자동(직접 언급 사료가 가장 많은 시대) 또는 직접 고르기
        self.era_choice = tk.StringVar(value='자동')
        era_box = ttk.Combobox(head, textvariable=self.era_choice, state='readonly', width=10, values=['자동'] + list(period.ERAS))
        era_box.pack(side='right', padx=(0, px(18)))
        tk.Label(head, text='대상 시대', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='right', padx=(0, px(8)))
        era_box.bind('<<ComboboxSelected>>', lambda e: self._set_era())

        # ── 거르기 줄: 찾기 | 자료원 · 관점 · 연대 · 점수 | 검색어 나온 것만 | 정렬 ─
        controls = tk.Frame(top, bg=BG)
        controls.pack(fill='x', padx=px(24), pady=(0, px(12)))
        tk.Label(controls, text='찾기', bg=BG, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(0, px(6)))
        self.query_entry = ttk.Entry(controls, textvariable=self.query, width=22)
        self.query_entry.pack(side='left', padx=(0, px(6)))
        # 검색엔진처럼: Enter → 첫 결과를 열어 찾은 곳 표시 · 한 번 더 Enter → 본문 펼치기 · ↓ → 목록으로
        for key in ('<Return>', '<KP_Enter>'):
            self.query_entry.bind(key, self._query_enter)
        self.query_entry.bind('<Down>', self._query_to_list)
        self.find_msg = tk.StringVar()
        tk.Label(controls, textvariable=self.find_msg, bg=BG, fg=ACCENT, font=ui.FONTS['label_sm']).pack(side='left', padx=(0, px(10)))
        tk.Frame(controls, bg=LINE, width=1, height=px(20)).pack(side='left', padx=(0, px(10)))
        self.source_box = ttk.Combobox(controls, textvariable=self.source_filter, state='readonly', width=12,
                                       values=['모든 자료원'] + [s.short for s in BY_ID.values()])
        self.source_box.pack(side='left', padx=(0, px(4)))
        self.facet_box = ttk.Combobox(controls, textvariable=self.facet, state='readonly', width=9,
                                      values=['모든 관점'] + hf.NAMES + ['미분류'])
        self.facet_box.pack(side='left', padx=(0, px(4)))
        self.period_box = ttk.Combobox(controls, textvariable=self.period, state='readonly', width=9)
        self.period_box.pack(side='left', padx=(0, px(4)))
        ttk.Combobox(controls, textvariable=self.threshold, state='readonly', width=9,
                     values=['전체 후보', '60점 이상', '80점 이상']).pack(side='left', padx=(0, px(4)))
        ttk.Checkbutton(controls, text='검색어가 나온 문헌만', variable=self.only_hits, style='TCheckbutton',
                        command=self.filter_rows, cursor='hand2').pack(side='left', padx=(px(8), 0))
        # '우선 본문 30건'으로 고른 문헌만 보기(그 방식으로 돌린 폴더에서만 나타남)
        self.only_priority = tk.BooleanVar(value=True)
        self.priority_keys = None
        self.priority_check = ttk.Checkbutton(controls, text='우선 30건만', variable=self.only_priority, style='TCheckbutton',
                                              command=self.filter_rows, cursor='hand2')
        sort_box = ttk.Combobox(controls, textvariable=self.sort_mode, state='readonly', width=11, values=SORTS)
        sort_box.pack(side='right')
        self.priority_anchor = tk.Label(controls, text='정렬', bg=BG, fg=MUTED, font=ui.FONTS['small'])
        self.priority_anchor.pack(side='right', padx=(px(10), px(6)))
        for variable in (self.query, self.facet, self.period, self.threshold, self.source_filter, self.sort_mode):
            variable.trace_add('write', self.schedule_filter)

        # 목록 58% : 열람 42% — 얇은 선으로만 나눈 두 작업면
        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True, padx=px(24), pady=(0, px(18)))
        upper = ui.round_corners(tk.Frame(split, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
        split.add(upper, weight=5)

        # 목록 도구줄: 고른 문헌에 하는 일. 주요 동작은 '본문 받기' 하나, 나머지는 도구 버튼
        buttons = tk.Frame(upper, bg=HEAD)
        buttons.pack(side='top', fill='x')
        inner = tk.Frame(buttons, bg=HEAD)
        inner.pack(fill='x', padx=px(10), pady=px(6))
        ttk.Button(inner, text='선택 문헌 본문 받기', style='PrimarySm.TButton', command=self.collect_selected, cursor='hand2').pack(side='left', padx=(0, px(8)))
        ttk.Button(inner, text='공식 원문 ↗', style='Tool.TButton', command=self.open_original, cursor='hand2').pack(side='left')
        ttk.Button(inner, text='비슷한 문헌', style='Tool.TButton', cursor='hand2',
                   command=lambda: self.open_lab('search', similar_to=(self.selected() or [{}])[0].get('key'))).pack(side='left')
        ttk.Button(inner, text='텍스트 분석', style='Tool.TButton', command=lambda: self.open_lab('search'), cursor='hand2').pack(side='left')
        ttk.Button(inner, text='관계도', style='Tool.TButton', cursor='hand2',
                   command=lambda: RelationsWindow(self, self.filtered)).pack(side='left')
        # 덜 쓰는 기능은 한곳에 모아 버튼 줄을 짧게
        more = tk.Menu(self, tearoff=0)
        more.add_command(label='새로고침  (F5)', command=self.reload)
        more.add_command(label='원문 주소 복사', command=self.copy_url)
        more.add_command(label='파일 폴더 열기', command=self.open_folder)
        more.add_command(label='추천 성능 보기', command=lambda: PerformanceWindow(self))
        more_btn = ttk.Button(inner, text='더 보기 ▾', style='Tool.TButton', cursor='hand2')
        more_btn.configure(command=lambda: more.tk_popup(more_btn.winfo_rootx(), more_btn.winfo_rooty() + more_btn.winfo_height()))
        more_btn.pack(side='left')
        ui.hline(upper, LINE).pack(side='top', fill='x')

        # 목록 열: 판정 · ★ · 제목 · 출전 · 연대 · 추천(맨 끝, 작게)
        self.tree = ttk.Treeview(upper, columns=('fav', 'hit', 'rec', 'score', 'label', 'title', 'source', 'facets', 'year', 'status'),
                                 show='headings', selectmode='extended', height=12)
        centered = ('fav', 'hit', 'rec', 'score', 'label', 'year')
        for name, label, width in (('fav', '★', 26), ('hit', '검색어', 52), ('rec', '추천', 44), ('score', '점수', 48), ('label', '판정', 40), ('title', '제목', 220),
                                   ('source', '출전', 104), ('facets', '관점', 120), ('year', '연대', 52), ('status', '상태', 84)):
            self.tree.heading(name, text=label, anchor='center' if name in centered else 'w')
            self.tree.column(name, width=px(width), minwidth=px(26), stretch=name == 'title',
                             anchor='center' if name in centered else 'w')
        scroll = ttk.Scrollbar(upper, orient='vertical', command=self.tree.yview, style='Card.Vertical.TScrollbar')
        self.tree.configure(yscrollcommand=scroll.set,
                            displaycolumns=('label', 'fav', 'title', 'source', 'year', 'rec'))   # 검색어·점수·관점·상태 열은 숨김(본문 위에 표시)
        self.tree.column('title', width=px(380))
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        self.tree.tag_configure('even', background=CARD)
        self.tree.tag_configure('odd', background=ui.ROW_ALT)
        self.tree.tag_configure('irr', foreground=ui.FAINT)    # 무관으로 판정한 문헌은 흐리게
        self.tree.tag_configure('rel', foreground=ui.OK_INK)   # 관련으로 판정한 문헌
        for key, label in (('1', 1), ('0', 0), ('<Delete>', None), ('<BackSpace>', None)):
            self.tree.bind(key, lambda e, v=label: self.judge(v))
        self.tree.bind('f', lambda e: self.toggle_favorite())
        self.tree.bind('<Button-3>', self._tree_menu)
        self.bind('<Control-s>', lambda e: self.save_selection_or_doc())

        lower = ui.round_corners(tk.Frame(split, bg=ui.PAPER, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
        split.add(lower, weight=4)
        # 판정 줄: 지금 판정이 눌린 상태로 보입니다. 1·0·Del 키와 같고, 판정하면 다음 문헌으로 넘어갑니다.
        bar = tk.Frame(lower, bg=HEAD)
        bar.pack(side='top', fill='x')
        self._more_line = ui.hline(lower, LINE)
        self._more_line.pack(side='top', fill='x')
        self.more_open = tk.BooleanVar(value=bool(settings.get('viewer_more_open', False)))
        self.more_toggle = ttk.Button(bar, style='Tool.TButton', cursor='hand2', command=self._toggle_more)
        self.more_toggle.pack(side='right', padx=(0, px(6)))
        ui.tip(self.more_toggle, '주제·분류 표시와 즐겨찾기 분류 고르기를 펼칩니다.')
        tk.Label(bar, text='판정', bg=HEAD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(14), px(8)), pady=px(7))
        self.judge_var = tk.StringVar(value='')
        judge_yes = ttk.Radiobutton(bar, text='✓ 관련  1', value='1', variable=self.judge_var, style='Judge.Toolbutton',
                                    command=lambda: self.judge(1), cursor='hand2')
        judge_yes.pack(side='left', pady=px(7))
        judge_no = ttk.Radiobutton(bar, text='✕ 무관  0', value='0', variable=self.judge_var, style='JudgeNo.Toolbutton',
                                   command=lambda: self.judge(0), cursor='hand2')
        judge_no.pack(side='left', padx=(px(4), 0), pady=px(7))
        judge_clear = ttk.Button(bar, text='지움', style='Tool.TButton', command=lambda: self.judge(None), cursor='hand2')
        judge_clear.pack(side='left', padx=(px(2), 0))
        ui.tip(judge_yes, '이 문헌은 연구 대상과 관련 있음 (키 1). 판정하면 다음 문헌으로 넘어갑니다.\n판정이 쌓이면 추천 순서가 내 판단을 배웁니다.')
        ui.tip(judge_no, '이 문헌은 관련 없음 (키 0). 목록에서 흐리게 보입니다.')
        ui.tip(judge_clear, '판정 지우기 (Delete 키)')
        tk.Frame(bar, bg=LINE, width=1, height=px(18)).pack(side='left', padx=px(8))
        self.fav_button = ttk.Button(bar, text='☆ 즐겨찾기', style='Tool.TButton', command=self.toggle_favorite, cursor='hand2')
        self.fav_button.pack(side='left')
        ui.tip(self.fav_button, lambda: f"이 문헌을 즐겨찾기 '{self.save_cat.get()}' 분류에 저장 (F). 분류는 '분류 ▸'에서 바꿉니다.")
        excerpt = ttk.Button(bar, text='❝ 발췌', style='Tool.TButton', command=self.save_selection, cursor='hand2')
        excerpt.pack(side='left')
        ui.tip(excerpt, '본문에서 끌어 고른 부분만 즐겨찾기에 저장 (Ctrl+S)')
        tk.Label(bar, textvariable=self.bm_msg, bg=HEAD, fg=OK_INK, font=ui.FONTS['label_sm']).pack(side='left', padx=px(8))
        # 펼치면 보이는 칸: 즐겨찾기 분류 · 주제/분류 표시
        self.more_panel = tk.Frame(lower, bg=HEAD)
        fav = tk.Frame(self.more_panel, bg=HEAD)
        fav.pack(side='top', fill='x', pady=(px(2), 0))
        tk.Label(fav, text='즐겨찾기 분류', bg=HEAD, fg=MUTED, font=ui.FONTS['small']).pack(side='left', padx=(px(14), px(6)))
        self.cat_box = ttk.Combobox(fav, textvariable=self.save_cat, state='readonly', width=14,
                                    postcommand=lambda: self.cat_box.configure(values=self.bm.names() + ['＋ 새 분류…']))
        self.cat_box.pack(side='left')
        self.cat_box.bind('<<ComboboxSelected>>', self._category_chosen)
        self.topic_bar = tk.Frame(self.more_panel, bg=HEAD)
        self.topic_bar.pack(side='top', fill='x')
        self.topic_vars = {}
        self._apply_more()

        self.detail = _reading_text(lower, height=15)
        dscroll = ttk.Scrollbar(lower, orient='vertical', command=self.detail.yview, style='Card.Vertical.TScrollbar')
        self.detail.configure(yscrollcommand=dscroll.set)
        dscroll.pack(side='right', fill='y')
        self.detail.pack(side='left', fill='both', expand=True)
        self.detail.tag_configure('saved', background=ui.WARN_PALE)
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
        self.detail.tag_configure('noteitem', font=ui.FONTS['small'], foreground=INK, background=ui.ANNOT,
                                  lmargin1=px(10), lmargin2=px(28), rmargin=px(10), spacing1=px(3), spacing3=px(4))
        self.detail.tag_configure('fn', font=ui.FONTS['small'], foreground=ui.SEAL, offset=px(4))
        self.detail.tag_configure('flash', background=ui.HIT)
        for tag, func in (('tg_hanja', lambda e: self._toggle('hanja')), ('tg_notes', lambda e: self._toggle('notes')),
                          ('fn', self._jump_note)):
            self.detail.tag_bind(tag, '<Button-1>', func)
            self.detail.tag_bind(tag, '<Enter>', lambda e: self.detail.configure(cursor='hand2'))
            self.detail.tag_bind(tag, '<Leave>', lambda e: self.detail.configure(cursor='arrow'))
        self.bind('<Return>', lambda e: self._enter_in_list() if e.widget in (self.tree, self.detail) else None)
        self.bind('<KP_Enter>', lambda e: self._enter_in_list() if e.widget in (self.tree, self.detail) else None)
        self._split = split
        split.bind('<Configure>', self._place_sash)
        self._add_tips()
        self.reload()

    def _place_sash(self, event):
        # 처음 한 번만 목록 58% : 본문 42%로 나눕니다.
        if event.width > px(400) and not getattr(self, '_sash_done', False):
            self._sash_done = True
            self.after_idle(lambda: self._split.sashpos(0, int(event.width * 0.58)))

    BUTTON_TIPS = {
        '선택 문헌 본문 받기': '목록에서 고른 문헌(여러 개 가능)의 본문을 원문 사이트에서 받아 옵니다.',
        '공식 원문 ↗': '고른 문헌의 원문 사이트를 브라우저로 엽니다.',
        '비슷한 문헌': '고른 문헌과 내용이 비슷한 문헌을 찾습니다.',
        '텍스트 분석': '비슷한 문헌 찾기 · 낱말 살펴보기 · 자동 분류',
        '더 보기 ▾': '새로고침(F5) · 원문 주소 복사 · 파일 폴더 열기 · 추천 성능',
        '관계도': '지금 목록에 보이는 문헌에서 인물·장소·관청 등이 함께 나오는 관계를 지도로 봅니다(관계 후보 지도).',
        '이 검색 저장': '지금 결과를 이름을 붙여 원하는 위치에 저장합니다. 임시 검색결과는 새 검색을 하면 바뀝니다.',
        '저장한 검색 열기': '전에 저장해 둔 검색을 엽니다.',
        '←  연구실로': '결과 창을 닫고 검색 화면으로 돌아갑니다(Esc).',
        '검색어가 나온 문헌만': '제목·요약·본문에 검색어가 실제로 나온 문헌만 봅니다.',
    }

    def _add_tips(self):
        """버튼·거르기 칸에 마우스를 올리면 설명이 나오게(화면에 깔린 회색 설명 대신)."""
        def walk(w):
            for c in w.winfo_children():
                yield c
                yield from walk(c)
        for w in walk(self):
            try:
                text = w.cget('text')
            except tk.TclError:
                continue
            if text in self.BUTTON_TIPS:
                ui.tip(w, self.BUTTON_TIPS[text])
            if isinstance(w, ttk.Combobox):
                var = str(w.cget('textvariable'))
                tips = {str(self.threshold): '점수로 거르기: 60점·80점 이상만 보기',
                        str(self.era_choice): '대상의 시대. 자동이면 이름이 나온 사료로 정하고, 다른 시대 기록은 점수를 낮춥니다.',
                        str(self.sort_mode): '추천순: 점수와 내 판정 학습을 함께 반영한 순서',
                        str(self.source_filter): '자료원으로 거르기', str(self.facet): '역사 분류(관점)로 거르기',
                        str(self.period): '연대로 거르기'}
                if var in tips:
                    ui.tip(w, tips[var])
        ui.tip(self.query_entry, '제목·본문에서 찾기. Enter: 첫 결과 열기 → 한 번 더 Enter: 본문 펼치기·다음 찾은 곳. ↓: 목록으로. Ctrl+F: 이 칸으로.')

    def _toggle_more(self):
        self.more_open.set(not self.more_open.get())
        settings.save(viewer_more_open=self.more_open.get())
        self._apply_more()

    def _apply_more(self):
        if self.more_open.get():
            self.more_panel.pack(side='top', fill='x', after=self._more_line)
            self.more_toggle.configure(text='분류 ▾')
        else:
            self.more_panel.pack_forget()
            self.more_toggle.configure(text='분류 ▸')

    def _query_groups(self):
        """찾기 칸 → 낱말 묶음들. 띄어 쓴 낱말은 모두 들어 있어야 하고(띄어쓰기 무시),
        연구 대상의 이름(다른 이름·한자 표기 포함)을 치면 그 이름들 가운데 하나만 있어도 됩니다.
        예: '숙종 희빈' → 숙종 AND 희빈 / '장희빈' → 장희빈·희빈장씨·張禧嬪·禧嬪張氏 중 하나"""
        words = [re.sub(r'\s+', '', w) for w in self.query.get().casefold().split()]
        names = [re.sub(r'\s+', '', x).casefold() for x in (self.profile or {}).get('direct_aliases') or []]
        names = [x for x in names if x]
        whole = ''.join(words)
        if whole in names and len(names) > 1:   # '희빈 장씨'처럼 띄어 쓴 이름 전체
            return [set(names)], True
        groups, expanded = [], False
        for w in words:
            if not w:
                continue
            if w in names and len(names) > 1:
                groups.append(set(names))
                expanded = True
            else:
                groups.append({w})
        return groups, expanded

    def _query_terms(self):
        """본문 노란 표시에 쓸 낱말(찾기 칸의 낱말과 넓혀 찾은 다른 이름)."""
        groups, _ = self._query_groups()
        return [t for g in groups for t in g]

    def _find_text(self):
        if not self.query.get().strip():
            return ''
        text = f'{len(self.filtered)}건'
        if getattr(self, '_query_expanded', False):
            text += ' · 다른 이름 포함'
        if self.priority_keys and self.only_priority.get():
            text += f' · 우선 {len(self.priority_keys)}건 안에서'
        return text

    def _focus_query(self, event=None):
        self.query_entry.focus_set()
        self.query_entry.select_range(0, 'end')
        self.query_entry.icursor('end')
        return 'break'

    def _query_enter(self, event=None):
        """찾기 칸 Enter: 바로 거르고 첫 결과를 열어 찾은 낱말이 있는 곳을 보여 준 뒤, 목록으로 이동합니다.
        목록에서 Enter를 한 번 더 누르면 본문을 펼치고, ↑↓로 다음 결과를 봅니다."""
        if self._timer:
            self.after_cancel(self._timer)
        self.filter_rows()
        if not self.shown:
            self.find_msg.set('찾은 문헌 없음')
            return 'break'
        self.tree.selection_set('0')
        self.tree.focus('0')
        self.tree.see('0')
        self.show_selected()
        self._see_first_hit()
        self.tree.focus_set()
        q = self.query.get().strip()
        self.find_msg.set(self._find_text())
        return 'break'

    def _query_to_list(self, event=None):
        if self.shown:
            first = self.tree.selection()[0] if self.tree.selection() else '0'
            self.tree.focus(first)
            self.tree.focus_set()
            return 'break'

    def _enter_in_list(self):
        """목록에서 Enter: 본문이 아직 없으면 받아서 펼치고, 이미 있으면 본문에서 찾은 다음 곳으로 이동합니다."""
        rows = self.selected()
        if rows and any(not r.get('body') for r in rows):
            self.expand_body()
        else:
            self._see_next_hit()

    def _see_next_hit(self):
        hits = []
        for tag in ('hit', 'hit_small', 'hit_bg'):
            r = self.detail.tag_ranges(tag)
            hits += [str(r[i]) for i in range(0, len(r), 2)]
        if not hits:
            return
        hits.sort(key=lambda ix: tuple(int(x) for x in ix.split('.')))
        cur = getattr(self, '_hit_pos', None)
        later = [h for h in hits if cur is None or self.detail.compare(h, '>', cur)]
        nxt = later[0] if later else hits[0]   # 끝까지 가면 처음으로
        self._hit_pos = nxt
        self.detail.see(nxt)
        self.detail.tag_remove('hit_now', '1.0', 'end')
        self.detail.tag_add('hit_now', nxt, f'{nxt} wordend')
        self.detail.tag_configure('hit_now', underline=True)
        self.find_msg.set(f'찾은 곳 {hits.index(nxt) + 1}/{len(hits)}')

    def _see_first_hit(self):
        """본문에서 처음 찾은 낱말(노란 표시)이 보이게 내립니다."""
        self._hit_pos = None
        for tag in ('hit', 'hit_small', 'hit_bg'):
            r = self.detail.tag_nextrange(tag, '1.0')
            if r:
                self.detail.see(r[0])
                return

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
            self.era_choice.set(self.profile.get('era_choice') if self.profile.get('era_choice') in period.ERAS else '자동')
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

    def _set_era(self):
        """고른 대상 시대를 이 결과 폴더 설정에 저장하고 다시 계산합니다."""
        choice = self.era_choice.get()
        path = self.folder / 'active_profile.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(self.profile or {})
            data = {k: v for k, v in data.items() if not k.startswith('_')}
            if choice in period.ERAS:
                data['era_choice'] = choice
            else:
                data.pop('era_choice', None)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except (OSError, ValueError) as exc:
            messagebox.showerror('대상 시대', str(exc), parent=self)
            return
        self.reload()

    def schedule_filter(self, *args):
        if self._timer:
            self.after_cancel(self._timer)
        self._timer = self.after(200, self.filter_rows)

    def filter_rows(self):
        self._timer = None
        keep = {r.get('key') for r in self.selected()}  # 새로고침·필터 후에도 보던 문헌 선택 유지
        query = self.query.get().casefold().strip()
        groups, self._query_expanded = self._query_groups()
        minimum = {'전체 후보': -10000, '60점 이상': 60, '80점 이상': 80}[self.threshold.get()]
        facet, decade, source = self.facet.get(), self.period.get(), self.source_filter.get()
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
            if decade != '모든 연대' and a['period'] != decade:
                continue
            if groups and not all(any(g in row.get('_search_c', '') for g in group) for group in groups):
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
            self.tree.insert('', 'end', iid=i, tags=('even' if int(i) % 2 == 0 else 'odd',) + self._judge_tag(row),
                             values=(self._fav_text(row), '✓' if row.get('_hit') else '', self._rec_text(row), a['score'], self._label_text(row), row.get('title', '(제목 없음)'), row.get('source', ''),
                                     self._facet_text(row), a['year'] or '미상',
                                     STATUS.get(row.get('status'), row.get('status', ''))))
        counts = Counter(name for row in self.filtered for name in row['analysis']['facets'])
        prio = (f'  ·  우선 본문: 관련성 높은 {len(self.priority_keys)}건만 보는 중' if self.priority_keys and self.only_priority.get() else '')
        era = (self.profile or {}).get('_era')
        if era is not None:
            prio += f"  ·  대상 시대: {period.era_label(era)}{'(자동)' if (self.profile or {}).get('_era_auto') else ''}"
        else:
            cands = (self.profile or {}).get('_era_candidates') or []
            prio += ('  ·  대상 시대가 확실하지 않아 시대 가중치를 쓰지 않습니다'
                     + (f"(후보: {'·'.join(period.ERAS[c] for c in cands)})" if cands else '(이름이 직접 나오는 사료가 적음)')
                     + " — 오른쪽 '대상 시대'에서 고를 수 있습니다")
        found = (self.profile or {}).get('_hanja_found') or []
        if found:
            prio += '  ·  사료의 한자 이름도 찾음: ' + ' · '.join(found)
        if hasattr(self, 'find_msg'):
            self.find_msg.set(self._find_text())
        where = ('임시 검색결과(저장 안 함)' if self.folder.name == '임시 검색결과' else self.folder.name)
        self.info.set(f'{where}  ·  {len(self.filtered)} / {len(self.rows)}건  ⓘ')
        self.info_detail = '\n'.join(x.strip() for x in (prio + (('  ·  관점: ' + ' · '.join(f'{name} {counts[name]}건' for name in hf.NAMES if counts[name]))
                                                                if any(counts[name] for name in hf.NAMES) else '')).split('  ·  ') if x.strip())
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
        ui.tip(box, '표시할 분류를 고릅니다. 체크하면 바로 저장되고, 이름 옆 (제안)은 앱이 낱말로 짐작한 것입니다.')
        if ann is not None:
            tk.Label(top, text=' ✎ 표시함', bg=HEAD, fg=ACCENT, font=ui.FONTS['label_sm']).pack(side='left')
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
            menu.add_command(label='우리말샘에서 뜻 찾기', command=lambda: opendict.show(self, self._selected_text()))
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

    def _judge_tag(self, row):
        label = self.judg.get(row.get('key')) if self.judg else None
        return ('rel',) if label == 1 else ('irr',) if label == 0 else ()

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
            self.tree.item(iid, tags=('even' if int(iid) % 2 == 0 else 'odd',) + self._judge_tag(row))
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
        rx = _terms_regex(self.hl_terms + [t for t in self._query_terms() if len(t) >= 2])
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
        self._hit_pos = None   # 다른 문헌을 열면 '찾은 곳' 이동을 처음부터
        rows = self.selected()
        if not rows:
            return
        row = rows[0]
        a = row['analysis']
        status = STATUS.get(row.get('status'), row.get('status', ''))
        label = self.judg.get(row.get('key')) if self.judg else None
        p = self.pred.get(row.get('key'))
        self.judge_var.set('' if label is None else str(label))
        # 문서 머리: 출전줄(자료집 · 연대 · 관점 · 상태 · 추천) → 제목 → 서지(주소) → 구분선
        topics = ', '.join(sorted(self._topics_of(row))) or '미분류'
        parts = [(row.get('source', '') or '출전 미상', 'src'),
                 (f"   {a['year'] or '연대 미상'}   ·   {topics}   ·   {status}", 'meta')]
        if p is not None:
            parts.append((f'   ·   추천 {int(round(p * 100))}', 'faint'))
        parts.append(('\n', 'meta'))
        if a.get('secondary'):   # 사료가 아닌 연구·해설 문헌: 사료 비판에 중요하므로 붉은 먹으로 따로
            parts.append(((a['secondary'] if isinstance(a.get('secondary'), str) else '연구·해설 문헌') + ' — 1차 사료가 아님\n', 'warnline'))
        parts += [
            (row.get('title', '(제목 없음)') + '\n', 'title'),
            (row.get('url', '') + '\n', 'link'),
            ('\n', 'rule'),
        ]
        if row.get('body'):
            parts += self._body_parts(row)
        else:
            parts.append(('검색 요약 — 사이트 검색 화면에 보인 일부\n', 'head'))
            paras = reader.snippet_paragraphs(row.get('snippet'))
            for p in paras or ['(검색 화면에 보인 글이 없습니다)']:
                parts += self._para_parts(p, ('body', 'para'))
            if not row.get('download_supported', True):
                parts += [('\n', None), ('이 자료는 사이트가 서지·요약만 공개합니다. 본문은 원문 사이트에서 보세요.\n', 'note'),
                          ('↗ 원문 사이트 열기', 'open_link'), ('\n', None)]
            elif row.get('key') in self._fetching:
                parts += [('\n', None), ('  본문을 받아 오는 중입니다…  ', 'action_off'),
                          ('\n받는 대로 이 자리에 펼쳐집니다.\n', 'note')]
            else:
                parts += [('\n', None), ('  ▼ 본문 펼치기 (Enter)  ', 'action'), ('\n\n', None),
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
            self._flash(f'원문 주소 복사됨 ✓ ({len(rows)}건)')

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
        """저장할 때 처음 열어 보여 줄 위치: 마지막으로 저장한 곳, 없으면 앱 옆 '저장한 검색'."""
        last = settings.get('save_home')
        if last and Path(last).is_dir():
            return Path(last)
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
        start = self._saved_home()
        try:
            start.mkdir(parents=True, exist_ok=True)   # 처음이면 기본 위치를 만들어 그곳부터 보여 줌
        except OSError:
            start = self.folder.parent
        chosen = filedialog.askdirectory(parent=self, title=f'「{name}」을(를) 저장할 위치 고르기', initialdir=str(start))
        if not chosen:
            return
        home = Path(chosen)
        settings.save(save_home=str(home.resolve()))
        dest = home / name
        if dest.resolve() == self.folder:
            return
        if self.folder in dest.resolve().parents:
            messagebox.showerror('이 검색 저장', '지금 보는 검색 결과 폴더 안에는 저장할 수 없습니다. 다른 위치를 고르세요.', parent=self)
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
        stats = ui.round_corners(tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
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
        table = ui.round_corners(tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE))
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
            for c, (title, items, color) in enumerate((('추천을 올리는 특징', up, OK_INK), ('추천을 내리는 특징', down, ui.SEAL))):
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
