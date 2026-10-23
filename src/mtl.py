"""여러 분석 작업 동시 학습(7강)과 시대·출처 적응(8강) — 파이썬 표준 기능만 쓰는 소형 신경망.

7강  멀티태스크
  · 과제: 주제(다중 라벨) · 자료 유형(규칙으로 약한 라벨) · 연구자가 만든 과제(예: 정치적 입장)
  · 구조: 입력(TF-IDF → 해시 무작위 사영 32차원) → 공유 전문가 E개(tanh) → 과제별 게이트(softmax) → 과제별 타워
          + 과제별 '넓은' 희소 선형항(TF-IDF 그대로). 공유는 전문가에서만 일어납니다.
  · 비교: 과제별 단독 모델 / 공유 하단(shared-bottom) / MMoE 를 같은 k-겹으로 재고,
          단독보다 나빠진 과제를 부정적 전이로 표시합니다.
  · GradNorm: 과제마다 공유 표현으로 흘러드는 기울기 크기를 맞추도록 과제 가중치를 조정(에폭 단위 근사).

8강  도메인 적응 (도메인 = 시대 또는 자료집)
  · 먼저 측정: 도메인 구별 가능성(proxy A-거리), 도메인 내 성능 vs 다른 도메인에서 학습했을 때 성능.
  · 데이터 재가중: 도메인 분류기로 p(목표|x)/p(원천|x)를 추정해 원천 학습 예에 가중치(상한 있음).
  · GRL(DANN): 공유 전문가 위에 도메인 판별기를 두고, 판별기 기울기를 뒤집어(−λ) 전문가로 보냅니다.
    넓은 희소항(낱말 그대로)은 함께 둡니다. 시험에서 GRL 단독보다 이 조합이 목표 도메인에서 나았습니다.
  · 위험 점검: 적응 후 라벨별 성능이 떨어지면(클래스가 섞임) 경고합니다.
"""
from collections import defaultdict
import math
from operator import mul
import random
import re
import zlib

import textlab as tl


# ── 자료 유형: 규칙(약한 라벨) ──────────────────────────────

DOC_TYPES = {
    '상소': r'상소|소를 올|차자|伏以|上疏|箚子|疏曰|엎드려 아뢰',
    '전교': r'전교|하교|교서|傳曰|敎曰|下敎',
    '계사': r'계사|啓曰|아뢰기를|啓辭',
    '논의': r'의논|논의|議于|收議|議大臣',
}
_TYPE_RE = {k: re.compile(v) for k, v in DOC_TYPES.items()}


def rule_types(row):
    text = ' '.join(str(row.get(k) or '') for k in ('title', 'snippet', 'body'))[:6000]
    return {name for name, rx in _TYPE_RE.items() if rx.search(text)}


def domain_of(row, axis):
    if axis == '자료집':
        return row.get('source') or '미상'
    return row.get('analysis', {}).get('period') or '연대 미상'


# ── 입력 표현 ───────────────────────────────────────────────

class DenseEncoder:
    """TF-IDF 희소 벡터를 낱말별 고정 무작위 벡터로 사영(존슨–린덴슈트라우스). 같은 낱말은 늘 같은 벡터."""

    def __init__(self, index, dim=32):
        self.dim, self.index = dim, index
        self._cache = {}
        self.vecs = [self._project(v) for v in index.vecs]

    def _rand(self, term):
        r = self._cache.get(term)
        if r is None:
            g = random.Random(zlib.crc32(term.encode('utf-8')))
            r = [g.gauss(0.0, 1.0) for _ in range(self.dim)]
            self._cache[term] = r
        return r

    def _project(self, vec):
        out = [0.0] * self.dim
        for t, w in vec.items():
            r = self._rand(t)
            for j in range(self.dim):
                out[j] += w * r[j]
        n = math.sqrt(sum(x * x for x in out)) or 1.0
        s = 1.5 / n
        return [x * s for x in out]

    def dense(self, key):
        return self.vecs[self.index.pos[key]]

    def sparse(self, key):
        return self.index.vecs[self.index.pos[key]]


def _sig(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _dot(a, b):
    return sum(map(mul, a, b))


# ── 신경망 ─────────────────────────────────────────────────

class MTLNet:
    """mode: 'mmoe'(전문가 E개 + 과제별 게이트) | 'shared'(전문가 1개, 게이트 없음 = shared-bottom)."""

    def __init__(self, dim, tasks, mode='mmoe', experts=3, hidden=8, wide=True, seed=1, domain_head=False):
        self.dim, self.tasks, self.mode, self.wide = dim, dict(tasks), mode, wide
        self.E = experts if mode == 'mmoe' else 1
        self.H = hidden
        rng = random.Random(seed)
        sd = 1.0 / math.sqrt(dim)
        self.We = [[[rng.gauss(0, sd) for _ in range(dim)] for _ in range(self.H)] for _ in range(self.E)]
        self.be = [[0.0] * self.H for _ in range(self.E)]
        self.Wg = {t: [[rng.gauss(0, 0.1) for _ in range(dim)] for _ in range(self.E)] for t in self.tasks}
        self.bg = {t: [0.0] * self.E for t in self.tasks}
        ht = 1.0 / math.sqrt(self.H)
        self.Wt = {t: [[rng.gauss(0, ht) for _ in range(self.H)] for _ in range(n)] for t, n in self.tasks.items()}
        self.bt = {t: [0.0] * n for t, n in self.tasks.items()}
        self.wd = {t: [dict() for _ in range(n)] for t, n in self.tasks.items()}  # 넓은 희소 항
        self.lam = {t: 1.0 for t in self.tasks}                                   # GradNorm 과제 가중치
        self.domain_head = domain_head
        if domain_head:
            self.Wd = [[rng.gauss(0, ht) for _ in range(self.H)] for _ in range(self.E)]
            self.bd = 0.0

    # 순전파
    def _forward(self, x):
        pre = [[_dot(w, x) + b for w, b in zip(We_k, be_k)] for We_k, be_k in zip(self.We, self.be)]
        ex = [[math.tanh(v) for v in p] for p in pre]
        gates = {}
        mix = {}
        for t in self.tasks:
            if self.E == 1:
                g = [1.0]
            else:
                a = [_dot(w, x) + b for w, b in zip(self.Wg[t], self.bg[t])]
                m = max(a)
                e = [math.exp(v - m) for v in a]
                s = sum(e)
                g = [v / s for v in e]
            gates[t] = g
            f = [0.0] * self.H
            for gk, ek in zip(g, ex):
                for j in range(self.H):
                    f[j] += gk * ek[j]
            mix[t] = f
        return ex, gates, mix

    def _logit(self, t, l, f, s):
        z = _dot(self.Wt[t][l], f) + self.bt[t][l]
        if self.wide and s:
            w = self.wd[t][l]
            z += sum(w.get(k, 0.0) * v for k, v in s.items())
        return z

    def predict(self, x, s):
        _, _, mix = self._forward(x)
        return {t: [_sig(self._logit(t, l, mix[t], s)) for l in range(n)] for t, n in self.tasks.items()}

    def gate_usage(self, samples):
        """과제별 평균 게이트(어느 전문가를 쓰는지)."""
        acc = {t: [0.0] * self.E for t in self.tasks}
        for x, _ in samples:
            _, gates, _ = self._forward(x)
            for t in self.tasks:
                for k in range(self.E):
                    acc[t][k] += gates[t][k]
        n = max(1, len(samples))
        return {t: [v / n for v in g] for t, g in acc.items()}

    def fit(self, data, epochs=14, lr=0.08, l2=1e-4, gradnorm=True, alpha=1.5, grl=0.0, domain=None, seed=3):
        """data: [(x, s, {과제: (y 목록, 가중치)}, 표본가중치)]. domain: [0/1/None] (GRL용, data와 같은 길이)."""
        rng = random.Random(seed)
        order = list(range(len(data)))
        # 라벨별 클래스 균형
        cw = {}
        for t, n in self.tasks.items():
            for l in range(n):
                pos = sum(sw * w for _, _, ys, sw in data if t in ys for y, w in [(ys[t][0][l], ys[t][1])] if y == 1)
                neg = sum(sw * w for _, _, ys, sw in data if t in ys for y, w in [(ys[t][0][l], ys[t][1])] if y == 0)
                tot = pos + neg
                cw[t, l] = (tot / (2 * pos) if pos else 1.0, tot / (2 * neg) if neg else 1.0)
        first_loss = None
        H, E = self.H, self.E
        for epoch in range(epochs):
            rng.shuffle(order)
            step = lr / (1.0 + 0.08 * epoch)
            p_prog = epoch / max(1, epochs - 1)
            lam_grl = grl * (2.0 / (1.0 + math.exp(-10 * p_prog)) - 1.0)
            loss_sum = defaultdict(float)
            gnorm = defaultdict(float)
            gcount = defaultdict(int)
            for i in order:
                x, s, ys, sw = data[i]
                ex, gates, mix = self._forward(x)
                d_ex = [[0.0] * H for _ in range(E)]
                for t, (y, w) in ys.items():
                    f = mix[t]
                    df = [0.0] * H
                    lam = self.lam[t] * w * sw
                    Wt, bt = self.Wt[t], self.bt[t]
                    for l, yl in enumerate(y):
                        if yl is None:
                            continue
                        p = _sig(self._logit(t, l, f, s))
                        c = cw[t, l][0] if yl == 1 else cw[t, l][1]
                        loss_sum[t] += -c * (math.log(p + 1e-9) if yl == 1 else math.log(1 - p + 1e-9))
                        dz = (p - yl) * c * lam
                        row = Wt[l]
                        for j in range(H):
                            df[j] += dz * row[j]
                        Wt[l] = [wj - step * (dz * fj + l2 * wj) for wj, fj in zip(row, f)]
                        bt[l] -= step * dz
                        if self.wide and s:
                            wd = self.wd[t][l]
                            for k, v in s.items():
                                wd[k] = wd.get(k, 0.0) - step * (dz * v + l2 * wd.get(k, 0.0))
                    gnorm[t] += math.sqrt(sum(v * v for v in df)) / (lam or 1.0)
                    gcount[t] += 1
                    g = gates[t]
                    for k in range(E):
                        gk = g[k]
                        dk = d_ex[k]
                        for j in range(H):
                            dk[j] += gk * df[j]
                    if E > 1:
                        dg = [_dot(df, ex[k]) for k in range(E)]
                        avg = _dot(g, dg)
                        da = [g[k] * (dg[k] - avg) for k in range(E)]
                        Wg, bg = self.Wg[t], self.bg[t]
                        for k in range(E):
                            if da[k]:
                                Wg[k] = [w - step * da[k] * xv for w, xv in zip(Wg[k], x)]
                                bg[k] -= step * da[k]
                # GRL: 도메인 판별기는 정상 학습, 전문가로는 −λ 기울기
                if self.domain_head and domain is not None and domain[i] is not None and lam_grl > 0:
                    z = sum(_dot(self.Wd[k], ex[k]) for k in range(E)) + self.bd
                    p = _sig(z)
                    dz = p - domain[i]
                    for k in range(E):
                        row = self.Wd[k]
                        for j in range(H):
                            d_ex[k][j] += -lam_grl * dz * row[j]
                        self.Wd[k] = [w - step * dz * v for w, v in zip(row, ex[k])]
                    self.bd -= step * dz
                for k in range(E):
                    dk = d_ex[k]
                    e = ex[k]
                    We_k, be_k = self.We[k], self.be[k]
                    for j in range(H):
                        dp = dk[j] * (1.0 - e[j] * e[j])
                        if dp:
                            We_k[j] = [w - step * (dp * xv + l2 * w) for w, xv in zip(We_k[j], x)]
                            be_k[j] -= step * dp
            # GradNorm(에폭 근사): 기울기 크기를 '학습이 덜 된 과제일수록 크게' 맞춥니다.
            if gradnorm and len(self.tasks) > 1 and gcount:
                losses = {t: loss_sum[t] / max(1, gcount[t]) for t in self.tasks if gcount[t]}
                if first_loss is None:
                    first_loss = dict(losses)
                ratio = {t: losses[t] / (first_loss.get(t) or 1.0) for t in losses}
                mean_r = sum(ratio.values()) / len(ratio)
                G = {t: self.lam[t] * gnorm[t] / gcount[t] for t in losses}
                mean_G = sum(G.values()) / len(G)
                for t in losses:
                    target = mean_G * (ratio[t] / mean_r) ** alpha
                    self.lam[t] *= max(0.5, min(2.0, (target / (G[t] or 1e-9)) ** 0.5))
                norm = len(self.lam) / sum(self.lam.values())
                for t in self.lam:
                    self.lam[t] *= norm
        return self


# ── 과제 데이터 ─────────────────────────────────────────────

class TaskSpec:
    def __init__(self, name, labels, strong, weak=None):
        self.name, self.labels = name, list(labels)
        self.strong = strong or {}   # 키 → 라벨 집합 (연구자 주석)
        self.weak = weak             # 키 → 라벨 집합 (규칙, 빈 집합 = 규칙상 해당 없음) / None = 규칙 없음


def build_samples(enc, keys, specs, use_weak=True, weak_w=0.35, weights=None):
    out = []
    for k in keys:
        ys = {}
        for sp in specs:
            if k in sp.strong:
                ys[sp.name] = ([int(l in sp.strong[k]) for l in sp.labels], 1.0)
            elif use_weak and sp.weak is not None and k in sp.weak:
                lab = sp.weak[k]
                ys[sp.name] = ([int(l in lab) for l in sp.labels], weak_w)
        if ys:
            out.append((enc.dense(k), enc.sparse(k), ys, (weights or {}).get(k, 1.0)))
    return out


def _f1(tp, fp, fn):
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def compare_mtl(enc, specs, all_keys, folds=3, seed=21, epochs=12):
    """같은 k-겹에서 단독 / 공유 하단 / MMoE의 과제별 마이크로 F1(연구자 주석 기준)."""
    eval_keys = sorted({k for sp in specs for k in sp.strong})
    if len(eval_keys) < 6:
        return None
    rng = random.Random(seed)
    rng.shuffle(eval_keys)
    k = min(folds, len(eval_keys))
    counts = {m: {sp.name: [0, 0, 0] for sp in specs} for m in ('single', 'shared', 'mmoe')}
    gates, lam = None, {}
    for f in range(k):
        test = set(eval_keys[f::k])
        train_specs = [TaskSpec(sp.name, sp.labels, {kk: v for kk, v in sp.strong.items() if kk not in test}, sp.weak) for sp in specs]
        train_keys = [kk for kk in all_keys if kk not in test]
        train = _subsample(build_samples(enc, train_keys, train_specs), 700, seed + f)
        models = {}
        for sp in specs:
            data = [(x, s, {sp.name: ys[sp.name]}, w) for x, s, ys, w in train if sp.name in ys]
            models['single', sp.name] = MTLNet(enc.dim, {sp.name: len(sp.labels)}, mode='shared').fit(data, epochs=epochs, gradnorm=False)
        tasks = {sp.name: len(sp.labels) for sp in specs}
        models['shared'] = MTLNet(enc.dim, tasks, mode='shared').fit(train, epochs=epochs)
        models['mmoe'] = MTLNet(enc.dim, tasks, mode='mmoe').fit(train, epochs=epochs)
        if f == 0:
            gates = models['mmoe'].gate_usage([(enc.dense(kk), None) for kk in train_keys[:300]])
            lam = dict(models['mmoe'].lam)
        for key in test:
            x, s = enc.dense(key), enc.sparse(key)
            pm = {'shared': models['shared'].predict(x, s), 'mmoe': models['mmoe'].predict(x, s)}
            for sp in specs:
                if key not in sp.strong:
                    continue
                pm['single'] = models['single', sp.name].predict(x, s)
                truth = sp.strong[key]
                for m in ('single', 'shared', 'mmoe'):
                    probs = pm[m][sp.name]
                    for l, name in enumerate(sp.labels):
                        g, y = probs[l] >= 0.5, name in truth
                        c = counts[m][sp.name]
                        c[0] += g and y
                        c[1] += g and not y
                        c[2] += y and not g
    result = {'tasks': [], 'gates': gates, 'lam': lam, 'n': len(eval_keys)}
    for sp in specs:
        row = {'task': sp.name, 'n': sum(1 for kk in eval_keys if kk in sp.strong)}
        for m in ('single', 'shared', 'mmoe'):
            row[m] = _f1(*counts[m][sp.name])
        row['negative'] = row['mmoe'] < row['single'] - 0.02
        result['tasks'].append(row)
    return result


def _subsample(samples, limit, seed):
    """주석(가중치 1) 예는 모두, 규칙 예는 limit까지 무작위로."""
    strong = [s for s in samples if any(w >= 1.0 for _, w in s[2].values())]
    weak = [s for s in samples if s not in strong]
    random.Random(seed).shuffle(weak)
    return strong + weak[:max(0, limit - len(strong))]


# ── 8강: 도메인 차이 측정과 적응 ────────────────────────────

def domain_shift_check(enc, keys, dom, seed=5):
    """proxy A-거리: 두 도메인을 TF-IDF로 얼마나 잘 가르는지(교차검증 오류 ε) → 2(1−2ε). 0이면 같고 2면 완전히 다름."""
    a = [k for k in keys if dom[k] == 1]
    b = [k for k in keys if dom[k] == 0]
    if len(a) < 10 or len(b) < 10:
        return None
    rng = random.Random(seed)
    n = min(len(a), len(b), 400)
    sample = rng.sample(a, n) + rng.sample(b, n)
    rng.shuffle(sample)
    errors = 0
    for f in range(3):
        test = sample[f::3]
        train = [k for i, k in enumerate(sample) if i % 3 != f]
        clf = tl.Binary().fit([(enc.sparse(k), dom[k], 1.0) for k in train], epochs=8)
        errors += sum(1 for k in test if (clf.prob(enc.sparse(k)) >= 0.5) != dom[k])
    eps = errors / len(sample)
    return max(0.0, 2 * (1 - 2 * eps))


def importance_weights(enc, src_keys, tgt_keys, clip=(0.2, 5.0)):
    """도메인 분류기로 w(x) = p(목표|x)/p(원천|x) · n원천/n목표 (데이터 재가중)."""
    data = [(enc.sparse(k), 0, 1.0) for k in src_keys] + [(enc.sparse(k), 1, 1.0) for k in tgt_keys]
    clf = tl.Binary().fit(data, epochs=8)
    ratio = len(src_keys) / max(1, len(tgt_keys))
    out = {}
    for k in src_keys:
        p = min(max(clf.prob(enc.sparse(k)), 1e-3), 1 - 1e-3)
        out[k] = min(clip[1], max(clip[0], p / (1 - p) * ratio))
    m = sum(out.values()) / max(1, len(out))
    return {k: min(clip[1], max(clip[0], v / m)) for k, v in out.items()}  # 평균 1로 맞춘 뒤 다시 상한


def adaptation_report(enc, rows, spec, axis, epochs=12, grl=0.6):
    """목표 도메인마다: (a) 도메인 안 교차검증(참고 상한) (b) 다른 도메인으로만 학습 (c) +재가중 (d) +GRL.
    목표 도메인의 주석은 평가에만 쓰고, 학습에는 목표 도메인의 '라벨 없는 본문'만 씁니다."""
    keys = [r.get('key') for r in rows if r.get('key') in enc.index.pos]
    doms = {k: domain_of(r, axis) for r in rows for k in [r.get('key')] if k in enc.index.pos}
    out = {'axis': axis, 'domains': []}
    names = sorted(set(doms.values()), key=lambda d: -sum(1 for v in doms.values() if v == d))
    for target in names:
        tgt = [k for k in keys if doms[k] == target]
        src = [k for k in keys if doms[k] != target]
        tgt_eval = [k for k in tgt if k in spec.strong]
        if len(tgt_eval) < 6 or len(src) < 20:
            continue
        dom01 = {k: int(doms[k] == target) for k in keys}
        a_dist = domain_shift_check(enc, keys, dom01)
        src_spec = TaskSpec(spec.name, spec.labels, {k: v for k, v in spec.strong.items() if doms.get(k) != target},
                            {k: v for k, v in spec.weak.items() if doms.get(k) != target} if spec.weak is not None else None)
        tasks = {spec.name: len(spec.labels)}

        def score(model_of, keys_eval):
            per = {l: [0, 0, 0] for l in spec.labels}
            for k in keys_eval:
                probs = model_of(k)
                for i, l in enumerate(spec.labels):
                    g, y = probs[i] >= 0.5, l in spec.strong[k]
                    per[l][0] += g and y
                    per[l][1] += g and not y
                    per[l][2] += y and not g
            tot = [sum(v[i] for v in per.values()) for i in range(3)]
            return _f1(*tot), {l: _f1(*v) for l, v in per.items()}

        train = _subsample(build_samples(enc, src, [src_spec]), 700, 7)
        base = MTLNet(enc.dim, tasks, mode='shared').fit(train, epochs=epochs, gradnorm=False)
        b_f1, b_per = score(lambda k: base.predict(enc.dense(k), enc.sparse(k))[spec.name], tgt_eval)

        w = importance_weights(enc, src, tgt)
        train_w = _subsample(build_samples(enc, src, [src_spec], weights=w), 700, 7)
        rew = MTLNet(enc.dim, tasks, mode='shared').fit(train_w, epochs=epochs, gradnorm=False)
        r_f1, r_per = score(lambda k: rew.predict(enc.dense(k), enc.sparse(k))[spec.name], tgt_eval)

        # GRL(DANN): 원천 라벨 예 + 목표 무라벨 예(도메인 라벨만). 판별기 기울기를 뒤집어 전문가로 보냅니다.
        unl = random.Random(9).sample(tgt, min(len(tgt), 300))
        grl_data = train + [(enc.dense(k), enc.sparse(k), {}, 1.0) for k in unl]
        dom_list = [0] * len(train) + [1] * len(unl)
        net = MTLNet(enc.dim, tasks, mode='shared', domain_head=True)
        net.fit(grl_data, epochs=epochs, gradnorm=False, grl=grl, domain=dom_list)
        g_f1, g_per = score(lambda k: net.predict(enc.dense(k), enc.sparse(k))[spec.name], tgt_eval)

        # 도메인 안 교차검증(참고)
        rng = random.Random(3)
        ev = list(tgt_eval)
        rng.shuffle(ev)
        tp = fp = fn = 0
        for f in range(3):
            test = set(ev[f::3])
            in_spec = TaskSpec(spec.name, spec.labels, {k: v for k, v in spec.strong.items() if doms.get(k) == target and k not in test},
                               {k: v for k, v in spec.weak.items() if doms.get(k) == target} if spec.weak is not None else None)
            data = _subsample(build_samples(enc, [k for k in tgt if k not in test], [in_spec]), 700, 7)
            if not data:
                continue
            m = MTLNet(enc.dim, tasks, mode='shared').fit(data, epochs=epochs, gradnorm=False)
            for k in test:
                probs = m.predict(enc.dense(k), enc.sparse(k))[spec.name]
                for i, l in enumerate(spec.labels):
                    g, y = probs[i] >= 0.5, l in spec.strong[k]
                    tp += g and y
                    fp += g and not y
                    fn += y and not g
        in_f1 = _f1(tp, fp, fn)
        hurt = [l for l in spec.labels if max(r_per[l], g_per[l]) < b_per[l] - 0.05]
        out['domains'].append({'domain': target, 'n_target': len(tgt), 'n_eval': len(tgt_eval), 'a_distance': a_dist,
                               'in_domain': in_f1, 'source_only': b_f1, 'reweight': r_f1,
                               'grl': g_f1, 'hurt_labels': hurt,
                               'weight_range': (min(w.values()), max(w.values())) if w else None})
    return out
