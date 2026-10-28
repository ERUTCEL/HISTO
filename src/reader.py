"""본문을 읽기 좋게 나눕니다(화면 표시 전용 — 저장된 본문과 분석에는 손대지 않습니다).

수집기는 본문을 뽑을 때 줄바꿈을 모두 띄어쓰기로 합쳐 저장합니다. 그래서
1) 받아 둔 원문 페이지(cache 폴더의 .html)가 있으면 그것을 다시 읽어 사이트의 문단을 살리고,
2) 없으면 '○' 표시와 문장 끝을 기준으로 문단을 나눕니다.
주석([註 001] 같은 낱말 풀이)은 본문에서 떼어 따로 모읍니다.
"""
from pathlib import Path
import re

LABELS = ('국역', '원문', '본문', '주석')
MARK = re.compile(r'\[註\s*\d+\]')
_NOTE_START = re.compile(r'\[註\s*\d+\][^\[\n]{0,60}?\s?:\s')
_HANJA = re.compile(r'[㐀-鿿豈-﫿]')
_HANGUL = re.compile(r'[가-힣]')
_BLOCKS = ('p', 'div', 'li', 'tr', 'dd', 'dt', 'blockquote', 'table', 'ul', 'ol', 'dl',
           'h1', 'h2', 'h3', 'h4', 'h5', 'h6')


def is_hanja(text):
    h, k = len(_HANJA.findall(text)), len(_HANGUL.findall(text))
    return h >= 6 and h > 3 * k


_OPEN, _CLOSE = '“‘『「(', '”’』」)'


def sentences(text):
    """문장 끝(. ? ! 。)에서 나눕니다. 따옴표·괄호 안의 마침표에서는 나누지 않습니다."""
    out, depth, start, i, n = [], 0, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        elif ch in '.?!。' and depth == 0:
            j = i + 1
            while j < n and text[j] in '.?!。':
                j += 1
            if j >= n or text[j].isspace() or ch == '。':
                out.append(text[start:j].strip())
                start = i = j
                continue
        i += 1
    out.append(text[start:].strip())
    return [s for s in out if s]


def paragraphs_from_flat(text, hanja=False):
    """한 줄로 합쳐진 글 → 문단 목록. '○'에서 새 문단, 그 밖에는 문장 몇 개씩 묶습니다."""
    size = 110 if hanja else 170
    out = []
    for chunk in re.split(r'\s*(?=○)', text):
        chunk = chunk.strip()
        if not chunk:
            continue
        cur = ''
        for s in sentences(chunk):
            cur = (cur + ('' if hanja else ' ') + s).strip() if cur else s
            if len(cur) >= size:
                out.append(cur)
                cur = ''
        if cur:
            out.append(cur)
    return out


def split_notes(text):
    """본문 끝에 붙은 '[註 001] 낱말 : 풀이' 묶음을 떼어 냅니다 → (본문, [주석들])."""
    m = _NOTE_START.search(text)
    if not m:
        return text, []
    body, tail = text[:m.start()].strip(), text[m.start():]
    return body, [n.strip() for n in re.split(r'(?=\[註\s*\d+\])', tail) if n.strip()]


def note_items(text):
    """'주석' 칸의 글 → 낱말 풀이 하나씩."""
    if MARK.search(text):
        parts = re.split(r'(?=\[註\s*\d+\])', text)
    else:
        parts = re.split(r'\s(?=(?:\d{1,3}\)|\[\d{1,3}\]|\(\d{1,3}\))\s)', text)
    return [p.strip() for p in parts if p.strip()]


def _clean_lines(text):
    lines = (re.sub(r'[ \t\r\f\v 　]+', ' ', ln).strip() for ln in text.split('\n'))
    return [ln for ln in lines if ln]


def _html_paragraphs(node):
    """HTML 요소 → (문단들, 따로 떨어진 주석들)."""
    notes = []
    found = [el for el in node.find_all(('ul', 'ol', 'dl', 'div', 'p', 'table'))
             if any(w in ' '.join(el.get('class') or []).lower() for w in ('footnote', 'remark'))]
    for el in found:
        if any(el in other.descendants for other in found if other is not el):
            continue   # 바깥 묶음에서 한꺼번에 처리
        notes += note_items(' '.join(_clean_lines(el.get_text('\n'))))
    for el in found:
        if el.parent is not None:
            el.extract()
    for br in node.find_all('br'):
        br.replace_with('\n')
    for el in node.find_all(_BLOCKS):
        el.insert_before('\n')
        el.insert_after('\n')
    return _clean_lines(node.get_text('')), notes


def _from_html(row, folder):
    path = row.get('html_file')
    if not path:
        return None
    candidates = [Path(path)]
    if folder:
        candidates.append(Path(folder) / 'cache' / Path(path).name)
    file = next((p for p in candidates if p.is_file()), None)
    if file is None:
        return None
    try:
        from bs4 import BeautifulSoup
        doc = BeautifulSoup(file.read_text(encoding='utf-8'), 'html.parser')
    except Exception:
        return None
    sections = []
    kind = row.get('kind')
    if kind in ('sillok', 'sjw'):
        root = doc.select_one('.detail-view')
        if root is None:
            return None
        for block in root.select('.view-item'):
            body = block.select_one('.view-text')
            if not body:
                continue
            head = block.select_one('.view-head, .view-title')
            label = head.get_text(' ', strip=True) if head else ''
            if kind != 'sillok' or not label:
                label = '국역' if 'left' in (block.get('class') or []) else '원문'
            paras, notes = _html_paragraphs(body)
            sections.append({'label': label, 'paras': paras, 'notes': notes})
    else:
        body = doc.select_one('#section-read') or doc.select_one('#contTop .orgArea')
        if body is None:
            return None
        paras, notes = _html_paragraphs(body)
        sections.append({'label': '본문', 'paras': paras, 'notes': notes})
        remark = doc.select_one('#section-remark')
        if remark is not None:
            sections.append({'label': '주석', 'paras': [], 'notes': note_items(' '.join(_clean_lines(remark.get_text('\n'))))})
    return sections if any(s['paras'] or s['notes'] for s in sections) else None


def _from_text(row):
    if isinstance(row.get('sections'), list) and row['sections']:
        raw = [(str(s.get('label') or '본문'), str(s.get('text') or '')) for s in row['sections'] if isinstance(s, dict)]
    else:
        raw = []
        for chunk in re.split(r'\n\s*\n', str(row.get('body') or '')):
            first, _, rest = chunk.strip().partition('\n')
            if first.strip() in LABELS and rest.strip():
                raw.append((first.strip(), rest))
            elif raw and not (first.strip() in LABELS):
                raw[-1] = (raw[-1][0], raw[-1][1] + '\n' + chunk)
            else:
                raw.append(('본문', chunk))
    sections = []
    for label, text in raw:
        if label == '주석':
            sections.append({'label': '주석', 'paras': [], 'notes': note_items(' '.join(text.split()))})
            continue
        lines = _clean_lines(text)
        body, notes = split_notes(' '.join(lines))
        if len(lines) > 1 and not notes:      # 줄바꿈이 남아 있으면 그대로 문단으로
            paras = lines
        else:
            paras = paragraphs_from_flat(body, is_hanja(body))
        sections.append({'label': label, 'paras': paras, 'notes': notes})
    return sections


def structure(row, folder=None):
    """→ {'sections': [{'label', 'paras', 'hanja'}], 'notes': [...], 'from_html': bool}"""
    sections = _from_html(row, folder)
    from_html = sections is not None
    if not from_html:
        sections = _from_text(row)
    out, notes = [], []
    for s in sections:
        notes += s['notes']
        paras = s['paras']
        if from_html and len(paras) == 1 and len(paras[0]) > 600:   # 사이트에도 문단이 없으면 나눠 줍니다
            paras = paragraphs_from_flat(paras[0], is_hanja(paras[0]))
        if from_html and paras:
            body, extra = split_notes(' \n '.join(paras))
            if extra:
                notes += extra
                paras = _clean_lines(body.replace(' \n ', '\n'))
        if paras:
            out.append({'label': s['label'] if s['label'] != '주석' else '본문', 'paras': paras,
                        'hanja': is_hanja(' '.join(paras))})
    seen, uniq = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return {'sections': out, 'notes': uniq, 'from_html': from_html}


def snippet_paragraphs(text):
    """검색 화면 요약 → 문단 목록. '…'로 이어 붙은 조각은 따로, 긴 조각은 문장 단위로 나눕니다."""
    text = ' '.join(str(text or '').split())
    if not text:
        return []
    out = []
    for piece in re.split(r'\s*(?:…|\.{3,})\s*', text):
        piece = piece.strip(' ·')
        if piece:
            out += paragraphs_from_flat(piece, is_hanja(piece))
    return out or [text]
