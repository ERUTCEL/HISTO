"""연구자 판정으로 배우는 추천 순위 (학습 기반 순위 + 능동 학습).

- 판정: 결과 폴더의 judgments.json(+ judgments.csv)에 대상 인물별로 저장합니다.
- 특징: 원본 규칙 점수와 그 근거(어떤 규칙·별칭에 걸렸는지), 관점, 자료원, 본문 여부,
  공동 등장 인물, 검색어, 본문 낱말을 씁니다.
- 모델: L2 정규화 로지스틱 회귀. 외부 라이브러리 없이 파이썬만 씁니다.
- 평가: 판정한 문헌 안에서 층화 k-겹 교차검증으로 규칙 점수와 학습 추천을 비교합니다.
"""
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import random
import re

MIN_PER_CLASS = 3          # 관련·무관 각각 이만큼 판정하면 학습을 시작합니다.
WORD_WEIGHT = 0.35         # 본문 낱말 특징은 규칙 근거보다 약하게 시작합니다.
_REASON = re.compile(r'^(.*?)\s*([+-]\d+)?\s*(?::\s*(.*))?$')
_WORD = re.compile(r'[가-힣]{2,8}|[㐀-鿿]{2,6}')


# ── 판정 저장 ──────────────────────────────────────────────

class Judgments:
    def __init__(self, folder, target):
        self.path = Path(folder) / 'judgments.json'
        self.target = target or ''
        self.data = {'version': 1, 'targets': {}}
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(loaded.get('targets'), dict):
                self.data = loaded
        except (OSError, ValueError, AttributeError):
            pass
        self.labels = self.data['targets'].setdefault(self.target, {})

    def get(self, key):
        item = self.labels.get(key)
        return item.get('label') if item else None

    def set(self, key, label, title=''):
        if label is None:
            self.labels.pop(key, None)
        else:
            self.labels[key] = {'label': int(label), 'at': datetime.now().isoformat(timespec='seconds'),
                                'title': title}
        self.save()

    def counts(self):
        pos = sum(1 for v in self.labels.values() if v.get('label') == 1)
        return pos, len(self.labels) - pos

    def save(self):
        try:
            tmp = self.path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(self.path)
            with open(self.path.with_suffix('.csv'), 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['대상', '문헌 키', '판정', '판정 시각', '제목'])
                for target, labels in self.data['targets'].items():
                    for key, v in labels.items():
                        w.writerow([target, key, '관련' if v.get('label') == 1 else '무관', v.get('at', ''), v.get('title', '')])
        except OSError:
            pass


# ── 특징 ───────────────────────────────────────────────────

def features(row):
    """문헌 한 건 → {특징 이름: 값}. 결과는 row['_feat']에 기억합니다."""
    cached = row.get('_feat')
    if cached is not None:
        return cached
    a = row.get('analysis', {})
    f = {'rule': (a.get('score') or 0) / 100.0}
    for reason in a.get('score_reasons', []):
        m = _REASON.match(reason)
        label = (m.group(1) if m else reason).strip()
        if label.startswith('검색 제목·요약에서'):
            label = '근거 부족'
        f['why:' + label] = 1.0
        if m and m.group(3):
            for alias in m.group(3).split(', '):
                if alias:
                    f['alias:' + alias.strip()] = 1.0
    f['basis:' + str(a.get('basis', ''))] = 1.0
    f['status:' + str(row.get('status', ''))] = 1.0
    if row.get('source'):
        f['src:' + str(row['source'])] = 1.0
    for name in a.get('facets', {}):
        f['facet:' + name] = 1.0
    if a.get('period'):
        f['period:' + str(a['period'])] = 1.0
    f['year_known'] = 1.0 if a.get('year') else 0.0
    rels = a.get('relations', [])
    f['rel_n'] = min(len(rels), 10) / 10.0
    for rel in rels:
        for name in (rel.get('source'), rel.get('target')):
            if name and name != a.get('target'):
                f['with:' + name] = 1.0
    for term in row.get('terms', []) or []:
        f['term:' + str(term)] = 1.0
    text = ' '.join(str(row.get(k) or '') for k in ('title', 'snippet')) + ' ' + str(row.get('body') or '')[:3000]
    for word in list(dict.fromkeys(_WORD.findall(text)))[:120]:
        f['w:' + word] = WORD_WEIGHT
    row['_feat'] = f
    return f


STATUS_NAMES = {'pending': '본문 미수집', 'complete': '본문 저장', 'failed': '수집 실패', 'catalog': '서지·요약'}


def describe(name):
    kind, _, value = name.partition(':')
    if kind == 'status':
        value = STATUS_NAMES.get(value, value)
    return {
        'rule': '규칙 점수', 'year_known': '연대 확인됨', 'rel_n': '공동 등장 수',
        'why': f'규칙 「{value}」', 'alias': f'명칭 「{value}」', 'basis': f'근거: {value}',
        'status': f'상태: {value}', 'src': f'자료집: {value}', 'facet': f'관점: {value}',
        'period': f'연대: {value}', 'with': f'함께 나온 말: {value}', 'term': f'검색어: {value}',
        'w': f'낱말: {value}',
    }.get(kind, name)


# ── 모델 ───────────────────────────────────────────────────

def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class Ranker:
    def __init__(self, l2=0.05):
        self.l2 = l2
        self.w = {}
        self.bias = 0.0
        self.trained = False

    def margin(self, f):
        w = self.w
        return self.bias + sum(w.get(k, 0.0) * v for k, v in f.items())

    def prob(self, f):
        return _sigmoid(self.margin(f))

    def fit(self, data, epochs=60, warm=False, seed=7):
        """data: [(특징, 0/1)]. 관련·무관 수가 달라도 균형 잡히게 가중합니다."""
        pos = sum(y for _, y in data)
        neg = len(data) - pos
        if pos < MIN_PER_CLASS or neg < MIN_PER_CLASS:
            self.trained = False
            return self
        if not warm:
            self.w, self.bias = {}, 0.0
        cw = {1: len(data) / (2.0 * pos), 0: len(data) / (2.0 * neg)}
        rng = random.Random(seed)
        order = list(range(len(data)))
        w = self.w
        for epoch in range(epochs):
            rng.shuffle(order)
            lr = 0.5 / (1.0 + 0.1 * epoch)
            for i in order:
                f, y = data[i]
                g = (self.prob(f) - y) * cw[y]
                self.bias -= lr * g
                for k, v in f.items():
                    wk = w.get(k, 0.0)
                    w[k] = wk - lr * (g * v + self.l2 * wk / len(data))
        self.trained = True
        return self

    def contributions(self, f, n=6):
        items = [(self.w.get(k, 0.0) * v, k) for k, v in f.items()]
        items = [x for x in items if abs(x[0]) > 1e-3]
        items.sort(key=lambda x: -abs(x[0]))
        return [(describe(k), c) for c, k in items[:n]]

    def top_weights(self, n=8):
        up = sorted(((v, k) for k, v in self.w.items() if v > 1e-3), reverse=True)[:n]
        down = sorted((v, k) for k, v in self.w.items() if v < -1e-3)[:n]
        return [(describe(k), v) for v, k in up], [(describe(k), v) for v, k in down]


def training_data(rows, judgments):
    by_key = {r.get('key'): r for r in rows}
    return [(features(by_key[k]), v['label']) for k, v in judgments.labels.items()
            if k in by_key and v.get('label') in (0, 1)]


# ── 평가 ───────────────────────────────────────────────────

def precision_at(ranked_labels, k):
    top = ranked_labels[:k]
    return sum(top) / len(top) if top else None


def auc(scores, labels):
    """관련 문헌이 무관 문헌보다 위에 올 확률(동점은 절반)."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    win = 0.0
    for p in pos:
        for q in neg:
            win += 1.0 if p > q else 0.5 if p == q else 0.0
    return win / (len(pos) * len(neg))


def cross_validated(data, folds=5, seed=11):
    """층화 k-겹 교차검증: 각 판정 문헌을 '그 문헌을 보지 않은 모델'로 점수 매깁니다."""
    pos = [i for i, (_, y) in enumerate(data) if y == 1]
    neg = [i for i, (_, y) in enumerate(data) if y == 0]
    k = min(folds, len(pos), len(neg))
    if k < 2:
        return None
    rng = random.Random(seed)
    rng.shuffle(pos)
    rng.shuffle(neg)
    fold_of = {}
    for group in (pos, neg):
        for j, i in enumerate(group):
            fold_of[i] = j % k
    scores = [0.0] * len(data)
    for fold in range(k):
        train = [data[i] for i in range(len(data)) if fold_of[i] != fold]
        model = Ranker().fit(train)
        if not model.trained:
            return None
        for i in range(len(data)):
            if fold_of[i] == fold:
                scores[i] = model.prob(data[i][0])
    return scores


def evaluate(data):
    labels = [y for _, y in data]
    rule = [f.get('rule', 0.0) for f, _ in data]
    learned = cross_validated(data)

    def ranked(scores):
        order = sorted(range(len(labels)), key=lambda i: -scores[i])
        return [labels[i] for i in order]

    out = {'n': len(labels), 'pos': sum(labels), 'rows': []}
    rr = ranked(rule)
    lr = ranked(learned) if learned else None
    for k in (5, 10, 20):
        out['rows'].append((f'상위 {k}건 중 관련 비율 (Precision@{k})',
                            precision_at(rr, k), precision_at(lr, k) if lr else None))
    out['rows'].append(('관련이 무관보다 위에 올 확률 (AUC)', auc(rule, labels), auc(learned, labels) if learned else None))
    out['base'] = sum(labels) / len(labels) if labels else None
    return out
