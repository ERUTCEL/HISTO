"""관계도의 연결(공동 등장)을 문장 단위로 직접 셉니다.

왜 따로 세나: 기존 분석 엔진은 기록마다 연결을 몇 개만 뽑아(장희빈 자료 632건 중 453건은 0개) 많이 함께 나온 짝도
적게 잡혔습니다(장희빈↔경종: 같은 문장 79번 → 엔진 9건). 그래서 엔진이 찾은 표현들은 '찾을 낱말 목록'으로만 쓰고,
연결은 모든 기록의 제목·요약·본문을 문장으로 나눠 같은 문장에 함께 나온 짝을 빠짐없이 셉니다.

- 연구 대상의 다른 이름(희빈 장씨·張禧嬪·禧嬪張氏…)은 한 점(연구 대상 이름)으로 합칩니다. 자기 자신과의 연결은 뺍니다.
- 띄어쓰기는 무시하고 찾습니다(宋 時 烈 = 宋時烈). 겹치는 표현은 긴 것을 먼저 잡습니다(장희빈 안의 '희빈'을 따로 세지 않음).
- 한 문장에서 같은 짝은 한 번만 셉니다.
"""
import re

_SPLIT = re.compile(r'(?<=[.。!?！？])\s+|\n+|…+|(?<=다\.)')


def _sentences(text):
    return [s.strip() for s in _SPLIT.split(str(text or '')) if s and len(s.strip()) >= 4]


def build(rows, target=None, aliases=(), max_terms=400, merges=None):
    """rows(read_results 결과) → 엔진 relations와 같은 꼴의 목록
    [{'source','target','evidence','basis','url','record_key','type'}]"""
    canon = {}
    target = (target or '').strip()
    for a in list(aliases) + ([target] if target else []):
        k = re.sub(r'\s+', '', a or '')
        if k:
            canon[k] = target or a
    # 찾을 표현: 엔진이 뽑은 연결의 양 끝 + 연구 대상의 이름들
    counts = {}
    for r in rows:
        for rel in r['analysis'].get('relations') or []:
            for n in (rel.get('source'), rel.get('target')):
                k = re.sub(r'\s+', '', n or '')
                if len(k) >= 2:
                    counts[k] = counts.get(k, 0) + 1
                    canon.setdefault(k, n)
    for src, dst in (merges or {}).items():   # 사용자가 합친 표현: 원자·세자 → 경종
        k = re.sub(r'\s+', '', src or '')
        if k:
            canon[k] = dst
            counts.setdefault(k, 1)
    terms = sorted(counts, key=lambda k: -counts[k])[:max_terms]
    terms = sorted(set(terms) | {re.sub(r'\s+', '', k) for k in (merges or {})}, key=len, reverse=True)
    terms = sorted(set(terms) | {k for k in canon if canon[k] == target}, key=len, reverse=True)
    if not terms:
        return []
    pattern = re.compile('|'.join(re.escape(t) for t in terms))
    out = []
    for r in rows:
        for field, basis in (('title', '제목·검색요약'), ('snippet', '제목·검색요약'), ('body', '본문')):
            for sent in _sentences(r.get(field)):
                compact = re.sub(r'\s+', '', sent)
                found = []
                for m in pattern.finditer(compact):
                    name = canon.get(m.group(0), m.group(0))
                    if name not in found:
                        found.append(name)
                if len(found) < 2:
                    continue
                for i, a in enumerate(found):
                    for b in found[i + 1:]:
                        if a == b:
                            continue
                        out.append({'source': a, 'target': b, 'evidence': sent[:400], 'basis': basis,
                                    'url': r.get('url', ''), 'record_key': r.get('key'), 'type': '공동 등장·관계 미확정'})
    return out
