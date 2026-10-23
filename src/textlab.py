"""사료 텍스트 분석 — 표현(2~4강)과 분류(5~6강)를 파이썬 표준 기능만으로 구현합니다.

2강 Sparse  : BoW → TF-IDF(서브리니어 tf, 평활 idf) → 코사인 유사도 (유사 검색, 비슷한 문헌)
3강 Static  : 공기 행렬 → PPMI → 절단 SVD(부분공간 반복법) 단어 벡터 (시대별 연관어)
              * PPMI 행렬의 SVD는 Skip-gram(네거티브 샘플링)이 암묵적으로 분해하는 행렬과 같은 계열입니다.
4강 Context : 용어가 나온 자리마다 앞뒤 문맥 벡터를 만들고 k-평균으로 쓰임새를 나눕니다.
              (BERT 문맥 임베딩의 근사. 사전학습 모델은 쓰지 않습니다.)
5강 Classify: TF-IDF 특징 + 라벨별 로지스틱 회귀(one-vs-rest)로 다중 라벨 주제 분류
6강 Semi    : 규칙 관점(약한 라벨) + 연구자 주석(강한 라벨)으로 학습 → 확신 높은 미주석 문헌에
              의사라벨을 붙여 다시 학습(self-training / pseudo-labeling)
"""
import csv
from collections import Counter, defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import random
import re

# ── 토큰화 ──────────────────────────────────────────────────
# 한국어 형태소 분석기 없이: 한글 어절에서 흔한 조사·어미를 떼고, 띄어쓰기 없는 한문은 2글자 단위로 자릅니다.

_TOKEN = re.compile(r'[가-힣]+|[㐀-鿿豈-﫿]+|[A-Za-z]{2,}')
_HANJA = re.compile(r'[㐀-鿿豈-﫿]')
_ENDINGS = sorted('''은 는 이 가 을 를 의 에 에게 에서 으로 로 와 과 도 만 께서 께 이라 라 이며 며 이고 고
하였다 하였으니 하였는데 하였고 하니 하고 하여 하며 하는 하게 하기를 하기 한다 했다 하다 이다 였다 이었다 입니다
들 들은 들이 들을 들의 에는 에도 으로써 로써 부터 까지 처럼 보다 이나 나 이요 요'''.split(), key=len, reverse=True)
STOP = set('''것 수 등 및 그 이 저 그것 이것 때 중 더 또 또한 위 아래 하다 있다 없다 되다 하였다 아니다 이다 여 왈 曰 之 而 也 於 以 其 者 爲 則 不 有 無 乎'''.split())


def _strip(word):
    for end in _ENDINGS:
        if len(word) - len(end) >= 2 and word.endswith(end):
            return word[:-len(end)]
    return word


def tokens(text):
    out = []
    for m in _TOKEN.finditer(text or ''):
        t = m.group()
        if _HANJA.match(t):
            if len(t) <= 2:
                out.append(t)
            else:
                out.extend(t[i:i + 2] for i in range(len(t) - 1))
        elif t[0].isascii():
            out.append(t.lower())
        elif len(t) >= 2:
            t = _strip(t)
            if len(t) >= 2:
                out.append(t)
    return [t for t in out if t not in STOP]


def doc_text(row, body_chars=4000):
    title = str(row.get('title') or '')
    return '\n'.join([title, title, str(row.get('snippet') or ''), str(row.get('body') or '')[:body_chars]])


# ── 2강: TF-IDF · 코사인 ────────────────────────────────────

class TfidfIndex:
    def __init__(self, rows, min_df=1, max_df_ratio=0.6):
        self.keys = [r.get('key') for r in rows]
        self.pos = {k: i for i, k in enumerate(self.keys)}
        counts = [Counter(tokens(doc_text(r))) for r in rows]
        df = Counter()
        for c in counts:
            df.update(c.keys())
        n = max(1, len(rows))
        limit = max(2, int(n * max_df_ratio)) if n >= 20 else n + 1
        self.idf = {t: math.log((n + 1) / (d + 1)) + 1.0 for t, d in df.items() if min_df <= d <= limit}
        self.vecs = [self._weigh(c) for c in counts]
        self.postings = defaultdict(list)
        for i, v in enumerate(self.vecs):
            for t, w in v.items():
                self.postings[t].append((i, w))

    def _weigh(self, counts):
        v = {t: (1.0 + math.log(c)) * self.idf[t] for t, c in counts.items() if t in self.idf}
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        return {t: w / norm for t, w in v.items()}

    def vector(self, text):
        return self._weigh(Counter(tokens(text)))

    def search(self, text, limit=200):
        q = self.vector(text)
        return self._rank(q, limit)

    def similar(self, key, limit=50):
        i = self.pos.get(key)
        if i is None:
            return []
        return [(k, s) for k, s in self._rank(self.vecs[i], limit + 1) if k != key][:limit]

    def _rank(self, q, limit):
        scores = defaultdict(float)
        for t, w in q.items():
            for i, dw in self.postings.get(t, ()):
                scores[i] += w * dw
        best = sorted(scores.items(), key=lambda x: -x[1])[:limit]
        return [(self.keys[i], s) for i, s in best if s > 1e-6]

    def top_terms(self, key, n=8):
        i = self.pos.get(key)
        if i is None:
            return []
        return sorted(self.vecs[i].items(), key=lambda x: -x[1])[:n]


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


# ── 3강: PMI · SVD ─────────────────────────────────────────

def cooccurrence(texts, window=4, vocab_size=1500, min_count=3):
    seqs = [tokens(t) for t in texts]
    freq = Counter(t for s in seqs for t in s)
    vocab = [t for t, c in freq.most_common(vocab_size) if c >= min_count]
    index = {t: i for i, t in enumerate(vocab)}
    co = defaultdict(float)
    for s in seqs:
        ids = [index.get(t) for t in s]
        for i, a in enumerate(ids):
            if a is None:
                continue
            for j in range(i + 1, min(len(ids), i + 1 + window)):
                b = ids[j]
                if b is None or b == a:
                    continue
                w = 1.0 / (j - i)  # 가까울수록 크게
                co[a, b] += w
                co[b, a] += w
    return vocab, co


def ppmi(vocab, co, alpha=0.75):
    """양의 점별 상호정보량. 문맥 분포를 0.75제곱으로 평활(Skip-gram 네거티브 샘플링과 같은 보정)."""
    row = defaultdict(float)
    col = defaultdict(float)
    total = 0.0
    for (a, b), c in co.items():
        row[a] += c
        col[b] += c
        total += c
    col_a = {b: c ** alpha for b, c in col.items()}
    total_a = sum(col_a.values()) or 1.0
    m = defaultdict(dict)
    for (a, b), c in co.items():
        v = math.log((c * total_a) / (row[a] * col_a[b])) if c > 0 else 0.0
        if v > 0:
            m[a][b] = v
    return m


def svd_vectors(m, n, k=48, iters=4, seed=3):
    """희소 대칭 행렬 m(n×n)의 상위 k 특이벡터를 부분공간 반복법으로 구합니다. 반환: 단어별 k차원 벡터(정규화)."""
    k = max(2, min(k, n - 1))
    rng = random.Random(seed)
    q = [[rng.gauss(0, 1) for _ in range(k)] for _ in range(n)]

    def matmul(x):
        out = [[0.0] * k for _ in range(n)]
        for a, cols in m.items():
            acc = out[a]
            for b, v in cols.items():
                xb = x[b]
                for j in range(k):
                    acc[j] += v * xb[j]
        return out

    def orth(x):  # 그람-슈미트 (열 기준)
        cols = [[x[i][j] for i in range(n)] for j in range(k)]
        basis = []
        for c in cols:
            for b in basis:
                d = sum(ci * bi for ci, bi in zip(c, b))
                c = [ci - d * bi for ci, bi in zip(c, b)]
            norm = math.sqrt(sum(ci * ci for ci in c)) or 1.0
            basis.append([ci / norm for ci in c])
        return [[basis[j][i] for j in range(k)] for i in range(n)]

    q = orth(q)
    for _ in range(iters):
        q = orth(matmul(q))
    z = matmul(q)  # 고유값 크기를 곱한 좌표(= U·Σ 근사)
    vecs = []
    for i in range(n):
        v = z[i]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vecs.append([x / norm for x in v])
    return vecs


class WordSpace:
    """시대별 연관어: 전체 SVD 벡터 이웃 + 시대별 PPMI 상위 연관어."""

    def __init__(self, rows, by_period=True, vocab_size=1500, k=48):
        texts = [doc_text(r) for r in rows]
        self.vocab, co = cooccurrence(texts, vocab_size=vocab_size)
        self.index = {t: i for i, t in enumerate(self.vocab)}
        self.pmi = ppmi(self.vocab, co)
        self.vecs = svd_vectors(self.pmi, len(self.vocab), k=k) if len(self.vocab) >= 8 else []
        self.periods = {}
        if by_period:
            groups = defaultdict(list)
            for r, t in zip(rows, texts):
                groups[r.get('analysis', {}).get('period') or '연대 미상'].append(t)
            for period, ts in groups.items():
                if len(ts) >= 3:
                    v, c = cooccurrence(ts, vocab_size=vocab_size, min_count=2)
                    self.periods[period] = (v, ppmi(v, c))

    def lookup(self, word):
        toks = tokens(word)
        if not toks:
            return None
        cand = toks[0] if toks[0] in self.index else next((t for t in toks if t in self.index), None)
        return cand

    def neighbors(self, word, n=15):
        w = self.lookup(word)
        if w is None or not self.vecs:
            return []
        v = self.vecs[self.index[w]]
        sims = [(self.vocab[i], sum(a * b for a, b in zip(v, u))) for i, u in enumerate(self.vecs) if i != self.index[w]]
        return sorted(sims, key=lambda x: -x[1])[:n]

    def associations(self, word, period=None, n=12):
        if period:
            if period not in self.periods:
                return []
            vocab, m = self.periods[period]
        else:
            vocab, m = self.vocab, self.pmi
        toks = tokens(word)
        idx = {t: i for i, t in enumerate(vocab)}
        w = next((t for t in toks if t in idx), None)
        if w is None:
            return []
        return sorted(((vocab[b], v) for b, v in m.get(idx[w], {}).items()), key=lambda x: -x[1])[:n]


# ── 4강: 문맥별 용례 ────────────────────────────────────────

def concordance(rows, term, width=36, limit=400):
    """용어가 나온 자리마다 (문헌 키, 왼쪽 문맥, 용어, 오른쪽 문맥)."""
    hits = []
    if not term:
        return hits
    for r in rows:
        text = doc_text(r, body_chars=20000).replace('\n', ' ')
        start = 0
        while len(hits) < limit:
            i = text.find(term, start)
            if i < 0:
                break
            hits.append((r.get('key'), text[max(0, i - width):i], term, text[i + len(term):i + len(term) + width]))
            start = i + len(term)
    return hits


def kmeans(vectors, k, iters=20, seed=5):
    rng = random.Random(seed)
    centers = [dict(v) for v in rng.sample(vectors, k)]
    assign = [0] * len(vectors)
    for _ in range(iters):
        changed = False
        for i, v in enumerate(vectors):
            best = max(range(k), key=lambda c: cosine(v, centers[c]))
            if best != assign[i]:
                assign[i], changed = best, True
        new = []
        for c in range(k):
            acc = defaultdict(float)
            members = [vectors[i] for i in range(len(vectors)) if assign[i] == c]
            for v in members:
                for t, w in v.items():
                    acc[t] += w
            norm = math.sqrt(sum(w * w for w in acc.values())) or 1.0
            new.append({t: w / norm for t, w in acc.items()} if members else centers[c])
        centers = new
        if not changed:
            break
    return assign, centers


def usage_clusters(hits, idf=None, max_k=4):
    """문맥 벡터를 군집화해 쓰임새를 나눕니다. 군집 수는 평균 실루엣(코사인)이 가장 큰 값을 고릅니다."""
    if len(hits) < 4:
        return [list(range(len(hits)))], [[]]
    term_toks = set()
    vecs = []
    for _, left, term, right in hits:
        term_toks.update(tokens(term))
        c = Counter(t for t in tokens(left + ' ' + right) if t not in term_toks)
        v = {t: (1.0 + math.log(n)) * (idf.get(t, 1.0) if idf else 1.0) for t, n in c.items()}
        norm = math.sqrt(sum(w * w for w in v.values())) or 1.0
        vecs.append({t: w / norm for t, w in v.items()})
    best = None
    for k in range(2, min(max_k, len(hits) - 1) + 1):
        assign, centers = kmeans(vecs, k)
        score = _silhouette(vecs, assign, k)
        if best is None or score > best[0]:
            best = (score, assign, centers)
    _, assign, centers = best
    groups = [[i for i, a in enumerate(assign) if a == c] for c in range(len(centers))]
    labels = [[t for t, _ in sorted(c.items(), key=lambda x: -x[1])[:5]] for c in centers]
    order = sorted(range(len(groups)), key=lambda g: -len(groups[g]))
    return [groups[g] for g in order if groups[g]], [labels[g] for g in order if groups[g]]


def _silhouette(vecs, assign, k, sample=200):
    idx = list(range(len(vecs)))[:sample]
    total = 0.0
    for i in idx:
        dist = defaultdict(list)
        for j in idx:
            if i != j:
                dist[assign[j]].append(1.0 - cosine(vecs[i], vecs[j]))
        own = dist.get(assign[i])
        if not own:
            continue
        a = sum(own) / len(own)
        others = [sum(d) / len(d) for c, d in dist.items() if c != assign[i] and d]
        if not others:
            continue
        b = min(others)
        total += (b - a) / max(a, b, 1e-9)
    return total / max(1, len(idx))


# ── 5~6강: 다중 라벨 분류 · 준지도 학습 ─────────────────────

def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class Binary:
    """희소 특징 로지스틱 회귀(SGD, L2, 표본 가중치·클래스 균형)."""

    def __init__(self, l2=0.02):
        self.l2, self.w, self.b = l2, {}, 0.0

    def prob(self, x):
        w = self.w
        return _sigmoid(self.b + sum(w.get(t, 0.0) * v for t, v in x.items()))

    def fit(self, data, epochs=25, seed=7):
        """data: [(특징, y, 가중치)]"""
        pos = sum(s for _, y, s in data if y == 1) or 1.0
        neg = sum(s for _, y, s in data if y == 0) or 1.0
        tot = pos + neg
        cw = {1: tot / (2 * pos), 0: tot / (2 * neg)}
        rng = random.Random(seed)
        order = list(range(len(data)))
        n = max(1, len(data))
        for epoch in range(epochs):
            rng.shuffle(order)
            lr = 0.6 / (1.0 + 0.15 * epoch)
            for i in order:
                x, y, s = data[i]
                g = (self.prob(x) - y) * cw[y] * s
                self.b -= lr * g
                for t, v in x.items():
                    wt = self.w.get(t, 0.0)
                    self.w[t] = wt - lr * (g * v + self.l2 * wt / n)
        return self


class TopicLabels:
    """연구자 주제 주석(강한 라벨). 결과 폴더 topic_labels.json(+csv)에 대상별로 저장."""

    def __init__(self, folder, target, default_labels):
        self.path = Path(folder) / 'topic_labels.json'
        self.target = target or ''
        self.data = {'version': 1, 'targets': {}}
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(loaded.get('targets'), dict):
                self.data = loaded
        except (OSError, ValueError, AttributeError):
            pass
        t = self.data['targets'].setdefault(self.target, {'labels': list(default_labels), 'docs': {}})
        for name in default_labels:
            if name not in t['labels']:
                t['labels'].append(name)
        self.labels, self.docs = t['labels'], t['docs']
        self.extra = t.setdefault('tasks', {})
        self.extra.setdefault('자료 유형', {'labels': ['상소', '전교', '계사', '논의'], 'docs': {}})

    # ── 여러 과제(7강): '주제'는 위의 labels/docs, 나머지는 tasks 아래 ──
    def task_names(self):
        return ['주제'] + list(self.extra)

    def task_labels(self, task):
        return self.labels if task == '주제' else self.extra[task]['labels']

    def task_docs(self, task):
        return self.docs if task == '주제' else self.extra[task]['docs']

    def task_get(self, task, key):
        item = self.task_docs(task).get(key)
        return set(item['topics']) if item else None

    def task_set(self, task, key, topics, title=''):
        if task == '주제':
            return self.set(key, topics, title)
        docs = self.task_docs(task)
        if topics is None:
            docs.pop(key, None)
        else:
            docs[key] = {'topics': sorted(topics), 'at': datetime.now().isoformat(timespec='seconds'), 'title': title}
        self.save()

    def add_task(self, name, labels):
        name = name.strip()
        labels = [l.strip() for l in labels if l.strip()]
        if not name or name in self.task_names() or len(labels) < 2:
            return False
        self.extra[name] = {'labels': labels, 'docs': {}}
        self.save()
        return True

    def add_task_label(self, task, label):
        labels = self.task_labels(task)
        label = label.strip()
        if label and label not in labels:
            labels.append(label)
            self.save()
            return True
        return False

    def get(self, key):
        item = self.docs.get(key)
        return set(item['topics']) if item else None

    def set(self, key, topics, title=''):
        if topics is None:
            self.docs.pop(key, None)
        else:
            self.docs[key] = {'topics': sorted(topics), 'at': datetime.now().isoformat(timespec='seconds'), 'title': title}
        self.save()

    def add_label(self, name):
        name = name.strip()
        if name and name not in self.labels:
            self.labels.append(name)
            self.save()
            return True
        return False

    def save(self):
        try:
            tmp = self.path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(self.path)
            with open(self.path.with_suffix('.csv'), 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(['대상', '과제', '문헌 키', '라벨(연구자 주석)', '주석 시각', '제목'])
                for target, t in self.data['targets'].items():
                    tasks = [('주제', t['docs'])] + [(n, v['docs']) for n, v in t.get('tasks', {}).items()]
                    for task, docs in tasks:
                        for key, v in docs.items():
                            w.writerow([target, task, key, ' · '.join(v['topics']) or '(해당 없음)', v.get('at', ''), v.get('title', '')])
        except OSError:
            pass


def _features(index, key, row):
    # 규칙 적중 여부는 특징에 넣지 않습니다. 약한 라벨로 이미 쓰므로, 넣으면 모델이 규칙만 베낍니다(정보 누출).
    return index.vecs[index.pos[key]] if key in index.pos else {}


class TopicClassifier:
    """규칙 관점(약한 라벨) + 연구자 주석(강한 라벨) + 의사라벨 자기학습."""

    WEAK, PSEUDO, STRONG = 0.35, 0.5, 1.0

    def __init__(self, rows, index, labels):
        self.rows, self.index, self.labels = rows, index, labels
        self.models, self.probs, self.pseudo_counts, self.thresholds = {}, {}, {}, {}

    def _pu_constant(self, model, feats, strong, name):
        """PU 보정(Elkan–Noto): 약한 음성에 실제 양성이 섞여 확률이 눌린 정도 c = 라벨 붙은 양성의 평균 예측확률.
        주석 양성이 3건 이상이면 그것으로, 아니면 규칙 적중 문헌으로 잽니다."""
        known = [model.prob(feats[k]) for k in strong if name in strong[k] and k in feats]
        if len(known) < 3:
            known = [model.prob(feats[r.get('key')]) for r in self.rows if name in r.get('analysis', {}).get('facets', {})]
        c = sum(known) / len(known) if known else 1.0
        return min(1.0, max(c, 0.05))

    def _seed(self, name, strong, use_weak=True):
        data, labeled = [], set()
        for r in self.rows:
            k = r.get('key')
            x = _features(self.index, k, r)
            if k in strong:
                data.append((x, int(name in strong[k]), self.STRONG))
                labeled.add(k)
            elif use_weak and name in r.get('analysis', {}).get('facets', {}):
                data.append((x, 1, self.WEAK))
                labeled.add(k)
        return data, labeled

    def fit(self, strong, rounds=2, hi=0.8, lo=0.2, use_weak=True):
        """strong: {키: 주제 집합}. 라벨마다 학습 → 의사라벨 추가 → 다시 학습."""
        feats = {r.get('key'): _features(self.index, r.get('key'), r) for r in self.rows}
        self.models, self.probs, self.pseudo_counts, self.thresholds = {}, {k: {} for k in feats}, {}, {}
        for name in self.labels:
            data, labeled = self._seed(name, strong, use_weak)
            pos = sum(1 for _, y, _ in data if y == 1)
            if pos == 0:
                continue
            # 음성 예: 강한 음성이 부족하면, 규칙에 안 걸린 문헌 일부를 약한 음성으로
            if sum(1 for _, y, _ in data if y == 0) < max(3, pos // 2):
                rest = [k for k in feats if k not in labeled]
                random.Random(13).shuffle(rest)
                for k in rest[:max(10, pos * 2)]:
                    data.append((feats[k], 0, self.WEAK))
                    labeled.add(k)
            model = Binary().fit(data)
            c = self._pu_constant(model, feats, strong, name)
            added = 0
            for _ in range(rounds):
                # 의사라벨: 보정 확률(p/c)이 hi 이상이면 양성, 원 확률이 lo 이하이면 음성.
                pseudo = []
                for k, x in feats.items():
                    if k in labeled:
                        continue
                    p = model.prob(x)
                    if p / c >= hi:
                        pseudo.append((x, 1, self.PSEUDO))
                    elif p <= lo * c:
                        pseudo.append((x, 0, self.PSEUDO))
                if not pseudo:
                    break
                added = len(pseudo)
                model = Binary().fit(data + pseudo)
                c = self._pu_constant(model, feats, strong, name)
            self.models[name] = model
            self.pseudo_counts[name] = sum(1 for _, y, _ in pseudo if y == 1) if added else 0
            for k, x in feats.items():
                self.probs[k][name] = min(1.0, model.prob(x) / c)
            self.thresholds[name] = _best_threshold([(self.probs[k][name], name in strong[k]) for k in strong if k in feats])
        return self

    def predict(self, key, threshold=None):
        probs = self.probs.get(key, {})
        return sorted((n for n, p in probs.items() if p >= (threshold if threshold is not None else self.thresholds.get(n, 0.5))),
                      key=lambda n: -probs[n])


def _best_threshold(pairs, default=0.5):
    """연구자 주석(양성·음성 각 2건 이상)이 있으면 F1이 가장 높은 기준선, 없으면 0.5."""
    pos = sum(1 for _, y in pairs if y)
    if pos < 2 or len(pairs) - pos < 2:
        return default
    best = (-1.0, default)
    for t in (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7):
        tp = sum(1 for p, y in pairs if y and p >= t)
        fp = sum(1 for p, y in pairs if not y and p >= t)
        fn = pos - tp
        f = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
        if f > best[0] + 1e-9 or (abs(f - best[0]) <= 1e-9 and abs(t - default) < abs(best[1] - default)):
            best = (f, t)
    return best[1]


def evaluate_topics(rows, index, labels, strong, folds=4, seed=17):
    """연구자 주석 문헌으로 k-겹 교차검증. 규칙 관점(기준선) vs 준지도 분류기, 라벨별·평균 F1."""
    keys = [k for k in strong if k in index.pos]
    if len(keys) < 4:
        return None
    rng = random.Random(seed)
    rng.shuffle(keys)
    k = min(folds, len(keys))
    by_key = {r.get('key'): r for r in rows}
    pred = {}
    for f in range(k):
        test = set(keys[f::k])
        train = {key: strong[key] for key in keys if key not in test}
        clf = TopicClassifier(rows, index, labels).fit(train)
        for key in test:
            pred[key] = set(clf.predict(key))
    out = {'n': len(keys), 'labels': []}
    tot = {'rule': [0, 0, 0], 'model': [0, 0, 0]}
    for name in labels:
        row = {'name': name}
        for kind in ('rule', 'model'):
            tp = fp = fn = 0
            for key in keys:
                truth = name in strong[key]
                guess = (name in by_key[key].get('analysis', {}).get('facets', {})) if kind == 'rule' else (name in pred[key])
                tp += truth and guess
                fp += (not truth) and guess
                fn += truth and not guess
            for i, v in enumerate((tp, fp, fn)):
                tot[kind][i] += v
            row[kind] = _f1(tp, fp, fn)
        row['support'] = sum(1 for key in keys if name in strong[key])
        out['labels'].append(row)
    out['micro'] = {kind: _f1(*tot[kind]) for kind in tot}
    return out


def _f1(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if (p is not None or r is not None) and (tp + fp + fn) else None)
    return {'p': p, 'r': r, 'f1': f}
