"""마지막으로 고른 설정을 기억합니다(exe 옆 'HISTRO 설정.json').

검색 방식·범위·자료원 선택·저장 위치, 결과 창의 정렬·거르기·펼침 상태 등을
사용자가 바꿀 때마다 저장하고 다음 실행 때 그대로 불러옵니다.
연구 대상과 검색어는 기억하지 않습니다(빈 칸으로 시작).
"""
import json
from pathlib import Path
import sys


def _path():
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    return base / 'HISTRO 설정.json'


_cache = None


def load():
    global _cache
    if _cache is None:
        try:
            data = json.loads(_path().read_text(encoding='utf-8'))
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def get(key, default=None):
    return load().get(key, default)


def save(**changes):
    data = load()
    if all(data.get(k) == v for k, v in changes.items()):
        return
    data.update(changes)
    try:
        path = _path()
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(path)
    except OSError:
        pass   # 쓰기 금지 폴더 등: 기억만 못 할 뿐 앱은 그대로
