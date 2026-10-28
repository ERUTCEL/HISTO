"""한글 낱말을 한자로 바꾸기 (입력칸에서 F9 또는 오른쪽 클릭 → 한자로 바꾸기).

이 앱의 입력칸(Tk)은 Windows 한자 키의 '이미 친 낱말 다시 바꾸기'를 받지 못해, 글자가 넘어가면
한자 키가 듣지 않습니다. 그래서 앱이 직접 후보를 보여 줍니다.
- 후보: 모은 문헌에 '한글(漢字)'로 함께 적힌 표기(가장 먼저) → 한자 사전(libhangul, BSD) →
  사전 낱말로 나눠 바꾼 조합 → '한 글자씩 고르기'
"""
from collections import Counter, defaultdict
import os
import re
import threading
import tkinter as tk
import zlib

import ui_theme as ui

_HANGUL = re.compile(r'[가-힣]')
_PAIR = re.compile(r'([가-힣]{1,10})\s*\(([㐀-鿿豈-﫿]{1,10})\)')
# 일반 사전에 없거나 뒤에 묻히는 조선 시대 호칭·인물·기관(사전보다 먼저 보여 줍니다)
HISTORY = {}
for _pair in '''빈:嬪 숙빈:淑嬪 희빈:禧嬪 경빈:敬嬪 영빈:暎嬪 의빈:宜嬪 수빈:綬嬪 화빈:和嬪 귀인:貴人 소의:昭儀 숙의:淑儀
소용:昭容 숙용:淑容 소원:昭媛 숙원:淑媛 상궁:尙宮 나인:內人 무수리:水賜伊 왕후:王后 대비:大妃 왕대비:王大妃
대왕대비:大王大妃 중전:中殿 세자빈:世子嬪 빈궁:嬪宮 후궁:後宮 최씨:崔氏 김씨:金氏 이씨:李氏 박씨:朴氏 장씨:張氏
민씨:閔氏 홍씨:洪氏 정씨:鄭氏 윤씨:尹氏 서씨:徐氏 숙빈최씨:淑嬪崔氏 최숙빈:崔淑嬪 장희빈:張禧嬪 희빈장씨:禧嬪張氏
태조:太祖 정종:定宗 태종:太宗 세종:世宗 문종:文宗 단종:端宗 세조:世祖 예종:睿宗 성종:成宗 연산군:燕山君 중종:中宗
인종:仁宗 명종:明宗 선조:宣祖 광해군:光海君 인조:仁祖 효종:孝宗 현종:顯宗 숙종:肅宗 경종:景宗 영조:英祖 정조:正祖
순조:純祖 헌종:憲宗 철종:哲宗 고종:高宗 순종:純宗 연잉군:延礽君 사도세자:思悼世子 장헌세자:莊獻世子 인현왕후:仁顯王后
인경왕후:仁敬王后 인원왕후:仁元王后 정성왕후:貞聖王后 정순왕후:貞純王后 혜경궁:惠慶宮 육상궁:毓祥宮 육상묘:毓祥廟
소령원:昭寧園 소령묘:昭寧墓 소녕원:昭寧園 칠궁:七宮 종묘:宗廟 사직:社稷 비변사:備邊司 승정원:承政院 의정부:議政府
홍문관:弘文館 사간원:司諫院 사헌부:司憲府 규장각:奎章閣 이조:吏曹 호조:戶曹 예조:禮曹 병조:兵曹 형조:刑曹 공조:工曹
영의정:領議政 좌의정:左議政 우의정:右議政 판서:判書 참판:參判 승지:承旨 수봉관:守奉官 추숭:追崇 책봉:冊封
환국:換局 탕평:蕩平 노론:老論 소론:少論 남인:南人 서인:西人 실록:實錄 등록:謄錄 일성록:日省錄
승정원일기:承政院日記 비변사등록:備邊司謄錄 조선왕조실록:朝鮮王朝實錄 의궤:儀軌'''.split():
    _k, _h = _pair.split(':')
    HISTORY.setdefault(_k, []).append(_h)

_words = None
_singles = None
_lock = threading.Lock()
corpus_pairs = defaultdict(Counter)   # 모은 문헌에서 찾은 한글 → 한자 표기와 횟수


def _load():
    global _words, _singles
    with _lock:
        if _words is not None:
            return
        words, singles = {}, {}
        try:
            raw = zlib.decompress((ui.resource_dir() / 'data' / 'hanja.bin').read_bytes()).decode('utf-8')
        except (OSError, zlib.error):
            raw = ''
        for line in raw.split('\n'):
            parts = line.split('\t')
            if len(parts) == 3:
                singles[parts[0]] = [tuple(x.split('=', 1)) if '=' in x else (x, '') for x in parts[1].split('|')]
            elif len(parts) == 2:
                words[parts[0]] = parts[1].split(',')
        _singles, _words = singles, words


def preload():
    """처음 F9를 누를 때 기다리지 않게 미리 읽어 둡니다."""
    threading.Thread(target=_load, daemon=True).start()


def learn_pairs(rows):
    """문헌의 '숙빈(淑嬪)' 같은 표기를 모아 둡니다(사료에 실제로 쓰인 표기를 먼저 보여 주려고)."""
    found = defaultdict(Counter)
    for r in rows:
        text = ' '.join(str(r.get(k) or '') for k in ('title', 'snippet', 'body'))
        for m in _PAIR.finditer(text):
            k, h = m.group(1), m.group(2)
            if len(k) == len(h):
                found[k][h] += 1
            elif len(k) > len(h):
                # '숙빈최씨(崔氏)'처럼 앞말이 붙은 경우: 끝부분만
                found[k[-len(h):]][h] += 1
    corpus_pairs.clear()
    corpus_pairs.update(found)


def word_candidates(word):
    """→ [(한자, 설명)] 겹침 없이, 좋은 순서로."""
    _load()
    out, seen = [], set()

    def add(h, note):
        if h and h not in seen:
            seen.add(h)
            out.append((h, note))
    for h, n in corpus_pairs.get(word, Counter()).most_common(5):
        add(h, f'모은 문헌 {n}회')
    for h in HISTORY.get(word, []):
        add(h, '사료 용어')
    for h in _words.get(word, [])[:12]:
        add(h, '사전')
    if len(word) == 1:
        for h, mean in _singles.get(word, [])[:40]:
            add(h, mean)
    elif not out:
        for combo in _segment(word)[:6]:
            add(combo, '나눠 바꿈')
    return out


def syllable_candidates(syl):
    _load()
    out = [(h, f'모은 문헌 {n}회') for h, n in corpus_pairs.get(syl, Counter()).most_common(3)]
    seen = {h for h, _ in out}
    out += [(h, m) for h, m in _singles.get(syl, []) if h not in seen]
    return out[:60]


def _segment(word):
    """사전 낱말(또는 모은 문헌 표기)로 가장 적게 나누는 방법 → 한자 조합 몇 가지."""
    n = len(word)
    best = [None] * (n + 1)   # (조각 수, 조각 목록)
    best[0] = (0, [])
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 1, min(n, i + 8) + 1):
            piece = word[i:j]
            known = piece in _words or piece in corpus_pairs or piece in HISTORY or (j - i == 1)
            if not known:
                continue
            cand = (best[i][0] + 1, best[i][1] + [piece])
            if best[j] is None or cand[0] < best[j][0]:
                best[j] = cand
    if best[n] is None:
        return []
    pieces = best[n][1]
    if all(len(p) == 1 for p in pieces):
        return []   # 한 글자씩이면 '한 글자씩 고르기'가 낫습니다

    def options(p):
        opts = [h for h, _ in corpus_pairs.get(p, Counter()).most_common(2)]
        opts += [h for h in HISTORY.get(p, []) if h not in opts]
        opts += [h for h in _words.get(p, []) if h not in opts]
        if len(p) == 1 and not opts:
            return [p]   # 사전에 없는 한 글자는 한글로 둡니다
        return opts or [p]
    first = [options(p) for p in pieces]
    combos = [''.join(o[0] for o in first)]
    for k, opts in enumerate(first):   # 한 조각씩 다른 후보로 바꾼 조합
        for alt in opts[1:3]:
            combos.append(''.join(alt if i == k else o[0] for i, o in enumerate(first)))
    return [c for c in combos if not _HANGUL.search(c)]   # 한글이 남는 반쪽 변환은 빼고 '한 글자씩 고르기'로


# ── 입력칸 연결 ────────────────────────────────────────────

def _word_range(widget):
    """선택한 글자, 없으면 커서 바로 앞의 한글 낱말 → (시작, 끝, 낱말)."""
    if isinstance(widget, tk.Text):
        if widget.tag_ranges('sel'):
            a, b = widget.index('sel.first'), widget.index('sel.last')
            return a, b, widget.get(a, b)
        before = widget.get('insert linestart', 'insert')
        m = re.search(r'[가-힣]+$', before)
        if not m:
            after = widget.get('insert', 'insert lineend')
            m2 = re.match(r'[가-힣]+', after)
            if not m2:
                return None
            return widget.index('insert'), widget.index(f'insert+{m2.end()}c'), m2.group()
        return widget.index(f'insert-{len(m.group())}c'), widget.index('insert'), m.group()
    try:
        if widget.selection_present():
            a, b = widget.index('sel.first'), widget.index('sel.last')
            return a, b, widget.get()[a:b]
    except tk.TclError:
        pass
    text, pos = widget.get(), widget.index('insert')
    m = re.search(r'[가-힣]+$', text[:pos])
    if m:
        return pos - len(m.group()), pos, m.group()
    m = re.match(r'[가-힣]+', text[pos:])
    return (pos, pos + m.end(), m.group()) if m else None


def _replace(widget, a, b, new):
    if isinstance(widget, tk.Text):
        widget.delete(a, b)
        widget.insert(a, new)
        widget.mark_set('insert', f'{a}+{len(new)}c')
    else:
        widget.delete(a, b)
        widget.insert(a, new)
        widget.icursor(a + len(new))
    try:
        widget.event_generate('<<HanjaChanged>>')
    except tk.TclError:
        pass


def convert(widget):
    """입력칸에서 한자 후보 창을 엽니다."""
    try:
        if str(widget.cget('state')) == 'disabled':
            return 'break'
    except tk.TclError:
        pass
    rng = _word_range(widget)
    if not rng or not _HANGUL.search(rng[2]):
        return 'break'
    HanjaPopup(widget, *rng)
    return 'break'


class HanjaPopup(tk.Toplevel):
    def __init__(self, widget, start, end, word):
        super().__init__(widget)
        self.widget, self.start, self.end, self.word = widget, start, end, word
        self.overrideredirect(True)
        self.configure(bg=ui.LINE)
        frame = tk.Frame(self, bg=ui.CARD)
        frame.pack(fill='both', expand=True, padx=1, pady=1)
        self.title_var = tk.StringVar()
        tk.Label(frame, textvariable=self.title_var, bg=ui.CARD, fg=ui.ACCENT, font=ui.FONTS['label_sm'],
                 anchor='w', padx=ui.px(10), pady=ui.px(6)).pack(fill='x')
        self.box = tk.Listbox(frame, font=ui.FONTS['input'], height=10, width=24, activestyle='none',
                              relief='flat', highlightthickness=0, bg=ui.CARD, fg=ui.INK,
                              selectbackground=ui.ACCENT, selectforeground='white')
        self.box.pack(fill='both', expand=True, padx=ui.px(6))
        tk.Label(frame, text='↑↓ 고르기 · Enter·숫자 바꾸기 · Esc 닫기', bg=ui.CARD, fg=ui.MUTED,
                 font=ui.FONTS['small'], anchor='w', padx=ui.px(10), pady=ui.px(4)).pack(fill='x')
        self.box.bind('<Double-1>', lambda e: self.choose())
        self.box.bind('<Return>', lambda e: self.choose())
        self.box.bind('<Escape>', lambda e: self.close())
        self.bind('<FocusOut>', lambda e: self.after(80, self._maybe_close))
        for n in range(1, 10):
            self.box.bind(str(n), lambda e, n=n: self.choose(n - 1))
        self.done = ''      # 한 글자씩 고를 때 지금까지 고른 것
        self.pos = None     # None이면 낱말 후보, 숫자면 그 글자 차례
        self.show_word()
        self._place()
        self.box.focus_force()

    def _place(self):
        w = self.widget
        try:
            bbox = w.bbox('insert')
        except tk.TclError:
            bbox = None
        x = w.winfo_rootx() + (bbox[0] if bbox else 0)
        y = w.winfo_rooty() + ((bbox[1] + bbox[3]) if bbox else w.winfo_height()) + ui.px(4)
        self.update_idletasks()
        x = min(x, w.winfo_screenwidth() - self.winfo_reqwidth() - 8)
        y = min(y, w.winfo_screenheight() - self.winfo_reqheight() - 40)
        self.geometry(f'+{max(0, x)}+{max(0, y)}')

    def _fill(self, items):
        self.items = items
        self.box.configure(height=max(3, min(10, len(items))))
        self.box.delete(0, 'end')
        for i, (label, note) in enumerate(items):
            num = f'{i + 1}. ' if i < 9 else '   '
            self.box.insert('end', f'{num}{label}   {note}'.rstrip())
        if items:
            self.box.selection_set(0)
            self.box.activate(0)

    def show_word(self):
        cands = word_candidates(self.word)
        self.title_var.set(f'「{self.word}」 한자로 바꾸기')
        items = [(h, n) for h, n in cands]
        if len(self.word) > 1:
            items.append(('한 글자씩 고르기…', ''))
        if not cands and len(self.word) == 1:
            items = [('(후보 없음)', '')]
        self._fill(items)

    def show_syllable(self):
        syl = self.word[self.pos]
        self.title_var.set(f'「{self.word}」 {self.pos + 1}/{len(self.word)}번째 글자 「{syl}」 → {self.done}…')
        items = syllable_candidates(syl) + [(syl, '한글 그대로')]
        self._fill(items)

    def choose(self, index=None):
        if index is None:
            sel = self.box.curselection()
            index = sel[0] if sel else 0
        if index >= len(self.items):
            return 'break'
        label, note = self.items[index]
        if self.pos is None:
            if label == '한 글자씩 고르기…':
                self.pos = 0
                self.show_syllable()
                return 'break'
            if label == '(후보 없음)':
                self.close()
                return 'break'
            self._apply(label)
            return 'break'
        self.done += label
        self.pos += 1
        if self.pos >= len(self.word):
            self._apply(self.done)
        else:
            self.show_syllable()
        return 'break'

    def _apply(self, text):
        widget = self.widget
        self.destroy()
        _replace(widget, self.start, self.end, text)
        widget.focus_set()

    def _maybe_close(self):
        if self.winfo_exists() and self.focus_get() is None:
            self.close()

    def close(self):
        widget = self.widget
        if self.winfo_exists():
            self.destroy()
        try:
            widget.focus_set()
        except tk.TclError:
            pass


def _context_menu(event):
    w = event.widget
    try:
        if str(w.cget('state')) == 'disabled':
            return
    except tk.TclError:
        return
    menu = tk.Menu(w, tearoff=0)
    menu.add_command(label='잘라내기', command=lambda: w.event_generate('<<Cut>>'))
    menu.add_command(label='복사', command=lambda: w.event_generate('<<Copy>>'))
    menu.add_command(label='붙여넣기', command=lambda: w.event_generate('<<Paste>>'))
    menu.add_separator()
    menu.add_command(label='한자로 바꾸기  (F9)', command=lambda: convert(w))
    try:
        if not isinstance(w, tk.Text):
            w.icursor(f'@{event.x}')
        else:
            w.mark_set('insert', f'@{event.x},{event.y}')
    except tk.TclError:
        pass
    menu.tk_popup(event.x_root, event.y_root)


def install(root):
    """모든 입력칸(Entry·Text)에 F9 / 한자 키 / 오른쪽 클릭 메뉴를 붙입니다."""
    for cls in ('TEntry', 'Entry', 'Text'):
        root.bind_class(cls, '<F9>', lambda e: convert(e.widget), add='+')
        root.bind_class(cls, '<KeyPress>', _hanja_key, add='+')
    for cls in ('TEntry', 'Entry'):
        root.bind_class(cls, '<Button-3>', _context_menu, add='+')
    preload()


def _hanja_key(event):
    # Windows 한자 키(VK_HANJA=0x19)가 앱까지 오면 같은 창을 엽니다.
    if event.keysym == 'Hangul_Hanja' or (os.name == 'nt' and event.keycode == 0x19):
        return convert(event.widget)
