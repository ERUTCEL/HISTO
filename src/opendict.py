"""우리말샘(국립국어원) 뜻 찾기 — 본문·입력칸에서 고른 낱말의 뜻풀이를 앱 안에서 보여 줍니다.

- 국립국어원 우리말샘 공개 API(https://opendict.korean.go.kr/api/search)를 씁니다.
  인증키는 사용자가 우리말샘에서 직접 신청해 한 번 넣으면 설정('HISTRO 설정.json')에 저장됩니다.
- 한글 낱말은 표제어로, 한자 낱말(爲白乎矣 등)은 원어(한자 표기)로 찾습니다.
- 옛말·역사 분야 풀이를 먼저 보여 줍니다.
- 내용 출처: 국립국어원 우리말샘(CC BY-SA 2.0 KR). 창 아래에 출처를 표시합니다.
"""
import json
import re
import threading
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.parse
import urllib.request

from browser_links import open_web_url
import settings
import ui_theme as ui
from ui_theme import px, CARD, INK, MUTED, ACCENT, LINE

API = 'https://opendict.korean.go.kr/api/search'
KEY_PAGE = 'https://opendict.korean.go.kr/service/openApiInfo'   # 개발 지원(Open API) 안내·신청
SOURCE_NOTE = '출처: 국립국어원 우리말샘 (CC BY-SA 2.0 KR)'
_HANJA = re.compile(r'[㐀-鿿豈-﫿]')
_cache = {}

KEY_ERRORS = {'020': '등록되지 않은 인증키입니다.', '021': '일시적으로 사용이 중지된 인증키입니다.'}


class OpendictError(Exception):
    def __init__(self, message, key_problem=False):
        super().__init__(message)
        self.key_problem = key_problem


def _params(word, key):
    p = {'key': key, 'q': word, 'req_type': 'json', 'num': 50}
    if _HANJA.search(word) and not re.search(r'[가-힣]', word):
        # 한자만 고른 경우: 원어(한자 표기)가 같은 표제어를 찾습니다
        p.update(advanced='y', target=2, method='exact')
    return p


def _list(x):
    """응답에서 하나뿐이면 목록이 아닌 값으로 오는 칸을 목록으로 맞춥니다."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _find_error(data):
    if isinstance(data, dict):
        if 'error_code' in data:
            return str(data.get('error_code') or '').strip(), str(data.get('message') or '').strip()
        for v in data.values():
            found = _find_error(v)
            if found:
                return found
    return None


def parse(text):
    """API 응답(JSON) → [{'word','definition','pos','type','cat','origin','link'}]. 오류면 OpendictError."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OpendictError('우리말샘 응답을 읽지 못했습니다.') from exc
    err = _find_error(data)
    if err:
        code, message = err
        raise OpendictError(KEY_ERRORS.get(code, f'우리말샘 오류 {code}: {message}'), key_problem=code in KEY_ERRORS)
    channel = data.get('channel') if isinstance(data, dict) else None
    out = []
    for item in _list((channel or {}).get('item')):
        if not isinstance(item, dict):
            continue
        word = str(item.get('word') or '').strip()
        for sense in _list(item.get('sense')):
            if not isinstance(sense, dict):
                continue
            def f(key):
                return str(sense.get(key) or '').strip()
            out.append({'word': word, 'definition': f('definition'), 'pos': f('pos'), 'type': f('type'),
                        'cat': f('cat'), 'origin': f('origin'), 'link': f('link')})
    # 옛말·역사 분야를 먼저(사료 읽기에 맞는 풀이부터), 그 안에서는 사전 순서 그대로
    out.sort(key=lambda s: (0 if '옛말' in s['type'] else 1, 0 if '역사' in s['cat'] else 1))
    return out


def lookup(word, key, timeout=12):
    word = word.strip()
    if (word, key) in _cache:
        return _cache[(word, key)]
    url = API + '?' + urllib.parse.urlencode(_params(word, key))
    req = urllib.request.Request(url, headers={'User-Agent': 'HISTRO'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raise OpendictError(f'우리말샘 서버 오류(HTTP {exc.code})') from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OpendictError(f'우리말샘에 연결하지 못했습니다: {getattr(exc, "reason", exc)}') from exc
    result = parse(text)
    _cache[(word, key)] = result
    return result


# ── 화면 ────────────────────────────────────────────────

def ask_key(parent, message=''):
    """인증키 넣기 창. 저장하면 키, 취소하면 None."""
    win = tk.Toplevel(parent)
    win.title('우리말샘 인증키')
    win.configure(bg=CARD)
    win.transient(parent)
    box = tk.Frame(win, bg=CARD, padx=px(18), pady=px(14))
    box.pack(fill='both', expand=True)
    if message:
        tk.Label(box, text=message, bg=CARD, fg='#A33', font=ui.FONTS['body'], anchor='w', justify='left').pack(fill='x')
    tk.Label(box, text='뜻 찾기는 국립국어원 우리말샘의 공개 API를 씁니다. 인증키는 무료이며 한 번만 넣으면 됩니다.\n'
                       '① 우리말샘에 로그인 → 개발 지원(Open API) → 인증키 신청\n② 받은 인증키를 아래 칸에 붙여 넣고 저장',
             bg=CARD, fg=INK, font=ui.FONTS['body'], anchor='w', justify='left').pack(fill='x', pady=(0, px(8)))
    ttk.Button(box, text='우리말샘 Open API 안내 열기 ↗', style='Outline.TButton',
               command=lambda: open_web_url(KEY_PAGE)).pack(anchor='w', pady=(0, px(10)))
    var = tk.StringVar(value=settings.get('opendict_key', '') or '')
    entry = ttk.Entry(box, textvariable=var, width=48)
    entry.pack(fill='x')
    result = {'key': None}

    def save():
        key = var.get().strip()
        if key:
            settings.save(opendict_key=key)
            result['key'] = key
        win.destroy()
    row = tk.Frame(box, bg=CARD)
    row.pack(fill='x', pady=(px(12), 0))
    ttk.Button(row, text='저장', style='Primary.TButton', command=save).pack(side='right')
    ttk.Button(row, text='취소', command=win.destroy).pack(side='right', padx=(0, px(8)))
    entry.bind('<Return>', lambda e: save())
    entry.focus_set()
    win.grab_set()
    parent.wait_window(win)
    return result['key']


class LookupWindow(tk.Toplevel):
    def __init__(self, parent, word):
        super().__init__(parent)
        self.word = word.strip()
        self.title(f'우리말샘 뜻 찾기 — {self.word}')
        self.configure(bg=CARD)
        ui.fit_geometry(self, 560, 520, 420, 300)
        top = tk.Frame(self, bg=CARD, padx=px(14), pady=px(10))
        top.pack(fill='x')
        tk.Label(top, text=self.word, bg=CARD, fg=INK, font=ui.FONTS['title']).pack(side='left')
        ttk.Button(top, text='인증키 바꾸기', command=self._change_key).pack(side='right')
        tk.Label(self, text=SOURCE_NOTE, bg=CARD, fg=MUTED, font=ui.FONTS['small'], anchor='w',
                 padx=px(14), pady=px(6)).pack(side='bottom', fill='x')   # 출처는 항상 보이게 먼저 아래에 붙임
        tk.Frame(self, bg=LINE, height=1).pack(side='bottom', fill='x')
        self.text = tk.Text(self, wrap='word', font=ui.FONTS['reading'], relief='flat', bg=CARD, fg=INK,
                            padx=px(14), pady=px(6), highlightthickness=0, cursor='arrow')
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('head', font=ui.FONTS['reading_b'])
        self.text.tag_configure('meta', foreground=MUTED, font=ui.FONTS['small'])
        self.text.tag_configure('link', foreground=ACCENT, underline=True, font=ui.FONTS['small'])
        self.text.tag_bind('link', '<Enter>', lambda e: self.text.configure(cursor='hand2'))
        self.text.tag_bind('link', '<Leave>', lambda e: self.text.configure(cursor='arrow'))
        self.bind('<Escape>', lambda e: self.destroy())
        self.after(10, self.start)

    def _write(self, parts):
        t = self.text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        for text, tags in parts:
            t.insert('end', text, tags)
        t.configure(state='disabled')

    def _change_key(self):
        if ask_key(self):
            _cache.clear()
            self.start()

    def start(self):
        key = settings.get('opendict_key')
        if not key:
            key = ask_key(self)
            if not key:
                self._write([('인증키가 없어 찾지 못했습니다. 위 \'인증키 바꾸기\'에서 넣을 수 있습니다.', ('meta',))])
                return
        self._write([('찾는 중…', ('meta',))])
        threading.Thread(target=self._fetch, args=(key,), daemon=True).start()

    def _fetch(self, key):
        try:
            res, err = lookup(self.word, key), None
        except OpendictError as exc:
            res, err = None, exc
        try:
            self.after(0, lambda: self._show(res, err))
        except (tk.TclError, RuntimeError):
            pass   # 창이 이미 닫힘

    def _show(self, senses, err):
        if not self.winfo_exists():
            return
        if err is not None:
            self._write([(str(err), ())] + ([('\n위 \'인증키 바꾸기\'에서 다시 넣어 주세요.', ('meta',))] if err.key_problem else []))
            return
        if not senses:
            self._write([(f"우리말샘에 '{self.word}' 항목이 없습니다.", ())])
            return
        t = self.text
        t.configure(state='normal')
        t.delete('1.0', 'end')
        for i, s in enumerate(senses):
            if i:
                t.insert('end', '\n')
            head = s['word'] + (f" ({s['origin']})" if s['origin'] and s['origin'] != s['word'] else '')
            t.insert('end', head + '\n', ('head',))
            meta = ' · '.join(x for x in (s['pos'], s['type'], s['cat']) if x)
            if meta:
                t.insert('end', meta + '\n', ('meta',))
            t.insert('end', s['definition'] + '\n')
            if s['link']:
                tag = f'link{i}'
                t.insert('end', '우리말샘에서 보기 ↗\n', ('link', tag))
                t.tag_bind(tag, '<Button-1>', lambda e, u=s['link']: open_web_url(u))
        t.configure(state='disabled')


def show(parent, word):
    word = ' '.join(str(word or '').split())
    if not word or len(word) > 30:
        return None
    return LookupWindow(parent.winfo_toplevel(), word)
