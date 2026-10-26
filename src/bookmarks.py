"""즐겨찾기와 분류.

- 즐겨찾기 항목: 문헌 통째(문헌) 또는 본문에서 고른 부분(발췌). 출처·주소·연대·메모를 함께 저장합니다.
- 분류 하나 = 파일 하나: 기본 폴더(exe 옆 '내 즐겨찾기')에 '<분류>.json'(앱이 읽는 파일)과
  '<분류>.md'(메모장·워드로 읽는 파일)를 같이 씁니다. '미분류'는 항상 있습니다.
- 분류를 지우면 그 안의 항목은 '미분류'로 옮깁니다(항목이 사라지지 않게).
"""
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import sys

UNSORTED = '미분류'
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


def default_dir():
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / '내 즐겨찾기'


def clean_name(name):
    name = _BAD.sub(' ', name or '').strip().strip('.')
    return re.sub(r'\s+', ' ', name)[:60]


def citation(item):
    parts = [item.get('source') or '', item.get('title') or '']
    if item.get('year'):
        parts.append(f"{item['year']}")
    text = ', '.join(p for p in parts if p)
    if item.get('url'):
        text += f" ({item['url']})"
    return text


class BookmarkStore:
    def __init__(self, folder=None):
        self.dir = Path(folder) if folder else default_dir()
        self.folders = {}   # 분류 이름 → {'created', 'items': [...]}
        self.reload()

    # ── 파일 ──
    def reload(self):
        self.folders = {}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.dir.glob('*.json')):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    name = clean_name(data.get('name') or path.stem) or path.stem
                    self.folders[name] = {'created': data.get('created', ''), 'items': list(data.get('items', []))}
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        if UNSORTED not in self.folders:
            self.folders[UNSORTED] = {'created': _now(), 'items': []}
        self._index()

    def _index(self):
        self.by_id = {}
        for name, f in self.folders.items():
            for it in f['items']:
                it['category'] = name
                self.by_id[it['id']] = it

    def _write(self, name):
        f = self.folders[name]
        data = {'name': name, 'created': f['created'], 'updated': _now(),
                'items': [{k: v for k, v in it.items() if k != 'category'} for it in f['items']]}
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f'{name}.json'
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(path)
        (self.dir / f'{name}.md').write_text(to_markdown(name, f['items']), encoding='utf-8-sig')

    # ── 분류 ──
    def names(self):
        rest = sorted(n for n in self.folders if n != UNSORTED)
        return [UNSORTED] + rest

    def create(self, name):
        name = clean_name(name)
        if not name or name in self.folders:
            return None
        self.folders[name] = {'created': _now(), 'items': []}
        self._write(name)
        return name

    def rename(self, old, new):
        new = clean_name(new)
        if old == UNSORTED or old not in self.folders or not new or new in self.folders:
            return None
        self.folders[new] = self.folders.pop(old)
        self._write(new)
        for ext in ('.json', '.md'):
            try:
                (self.dir / f'{old}{ext}').unlink()
            except OSError:
                pass
        self._index()
        return new

    def delete(self, name):
        """분류를 지우고 항목은 미분류로."""
        if name == UNSORTED or name not in self.folders:
            return False
        moved = self.folders.pop(name)['items']
        self.folders[UNSORTED]['items'].extend(moved)
        self._write(UNSORTED)
        for ext in ('.json', '.md'):
            try:
                (self.dir / f'{name}{ext}').unlink()
            except OSError:
                pass
        self._index()
        return True

    # ── 항목 ──
    def items(self, name=None):
        if name is None:
            out = [it for f in self.folders.values() for it in f['items']]
            return sorted(out, key=lambda it: it.get('saved_at', ''), reverse=True)
        return list(self.folders.get(name, {}).get('items', []))

    def add(self, row, result_folder, text=None, category=UNSORTED, note=''):
        category = category if category in self.folders else UNSORTED
        a = row.get('analysis', {})
        item = {
            'id': secrets.token_hex(6),
            'kind': '발췌' if text else '문헌',
            'key': row.get('key'),
            'result_folder': str(result_folder),
            'title': row.get('title', ''),
            'source': row.get('source', ''),
            'url': row.get('url', ''),
            'year': a.get('year') or '',
            'text': (text or row.get('body') or row.get('snippet') or '').strip()[:20000],
            'note': note,
            'saved_at': _now(),
        }
        self.folders[category]['items'].append(item)
        item['category'] = category
        self.by_id[item['id']] = item
        self._write(category)
        return item

    def update(self, item_id, **changes):
        it = self.by_id.get(item_id)
        if not it:
            return
        for k in ('note', 'text'):
            if k in changes:
                it[k] = changes[k]
        self._write(it['category'])

    def move(self, item_id, category):
        it = self.by_id.get(item_id)
        if not it or category not in self.folders or it['category'] == category:
            return
        old = it['category']
        self.folders[old]['items'] = [x for x in self.folders[old]['items'] if x['id'] != item_id]
        self.folders[category]['items'].append(it)
        it['category'] = category
        self._write(old)
        self._write(category)

    def remove(self, item_id):
        it = self.by_id.pop(item_id, None)
        if not it:
            return
        cat = it['category']
        self.folders[cat]['items'] = [x for x in self.folders[cat]['items'] if x['id'] != item_id]
        self._write(cat)

    def document_bookmark(self, result_folder, key):
        rf = str(result_folder)
        for it in self.by_id.values():
            if it['kind'] == '문헌' and it.get('key') == key and it.get('result_folder') == rf:
                return it
        return None

    def excerpts_of(self, result_folder, key):
        rf = str(result_folder)
        return [it for it in self.by_id.values()
                if it['kind'] == '발췌' and it.get('key') == key and it.get('result_folder') == rf]

    def keys_in(self, result_folder):
        rf = str(result_folder)
        return {it.get('key') for it in self.by_id.values() if it.get('result_folder') == rf}

    def export_csv(self, name, path):
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['분류', '종류', '제목', '자료집', '연대', '주소', '내용', '메모', '저장 시각'])
            for it in self.items(name):
                w.writerow([it['category'], it['kind'], it['title'], it['source'], it['year'], it['url'],
                            it['text'], it['note'], it['saved_at']])

    def open_dir(self):
        if os.name == 'nt':
            os.startfile(str(self.dir))


def to_markdown(name, items):
    lines = [f'# {name}', '', f'즐겨찾기 {len(items)}건 · HISTRO에서 저장', '']
    for i, it in enumerate(items, start=1):
        lines.append(f"## {i}. {it.get('title') or '(제목 없음)'}")
        lines.append(f"- 종류: {it.get('kind')} · 저장: {it.get('saved_at', '')[:16].replace('T', ' ')}")
        lines.append(f"- 출처: {citation(it)}")
        if it.get('note'):
            lines.append(f"- 메모: {it['note']}")
        lines.append('')
        text = (it.get('text') or '').strip()
        if text:
            if it.get('kind') == '문헌' and len(text) > 1200:
                text = text[:1200] + ' …'
            lines += ['> ' + ln for ln in text.splitlines() if ln.strip()]
            lines.append('')
    return '\n'.join(lines) + '\n'


def _now():
    return datetime.now().isoformat(timespec='seconds')
