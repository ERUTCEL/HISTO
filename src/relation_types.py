"""관계도의 '관계 종류' 후보 — 근거 문장 속 단서 낱말로 정합니다.

방법(규칙 기반, 확정 아님):
1. 관계도의 연결은 '같은 문장에 함께 나온 두 표현'입니다(공동 등장).
2. 그 근거 문장에서 두 표현 사이나 가까이에 있는 단서 낱말을 찾습니다.
   단서 낱말은 종류마다 한글·한문 표현을 함께 둡니다(예: 처벌·대립 → 사사·賜死·유배·定配·탄핵·彈劾).
3. 단서가 여럿이면 두 표현에 가장 가까운 단서의 종류를 고릅니다(같은 문장 안의 다른 이야기에 끌려가지 않게).
4. 단서가 없으면 '공동 등장(미확정)'으로 둡니다.
5. 연결(두 표현의 짝)의 종류는 근거 기록들의 종류 가운데 가장 많은 것입니다. 모든 종류별 건수도 함께 보여 줍니다.
한계: 단서 낱말은 관계의 '방향'(누가 누구를)을 알려 주지 않고, 부정문(…하지 않았다)이나 인용문을 가리지 못합니다.
그래서 화면에는 '후보'와 단서 낱말을 함께 보여 주고, 근거 문장을 직접 읽어 확인하게 합니다.
"""
import re

UNKNOWN = '공동 등장'

# (종류, 색, 단서 낱말들). 한 글자 한자는 다른 말의 일부로 잘 섞이므로(世子의 子 등) 두 글자 이상 위주로 둡니다.
TYPES = (
    ('처벌·대립', '#B4442F', '사사되 사사하 사사를 사사한 사사된 賜死 유배 귀양 定配 圍籬 竄 처형 誅 탄핵 彈劾 논죄 論罪 국문 鞫問 推鞫 친국 親鞫 '
                          '공초 供招 역모 逆謀 謀逆 모함 誣告 폐출 廢出 廢黜 폐위 廢位 삭탈 削奪 파직 罷職 처벌 治罪 죄를 罪人'),
    ('혈연·가족', '#7A5AA6', '아들 딸 어머니 아버지 아내 남편 생모 生母 생부 生父 소생 所生 장자 長子 차자 次子 之子 之女 之妻 '
                          '之母 之父 형제 兄弟 자매 姊妹 손자 孫子 외조 外祖 친정 親庭 모친 母親 부친 父親 조카 甥姪 혈육'),
    ('책봉·혼인', '#C07A16', '책봉 冊封 封爲 진봉 進封 간택 揀擇 嘉禮 혼인 婚姻 국혼 國婚 納采 왕비로 중전으로 빈으로 嬪으로 '
                          '후궁 後宮 승격 昇格 복위 復位'),
    ('명령·보고', '#27507A', '전교하 전교를 전교에 傳敎 하교하 하교를 下敎 敎曰 명하 명령 하유 下諭 諭曰 아뢰 啓曰 계사 啓辭 '
                          '상소하 상소를 상소에 上疏 차자 箚子 장계를 장계에 장계의 狀啓 계청 啓請 비답 批答 윤허 允許'),
    ('천거·협력', '#2E7D5B', '천거 薦擧 추천 推薦 보필 輔弼 협력 구원 救援 비호 庇護 옹호 擁護 지지하 동지로 同志 도와'),
    ('의례·장소', '#6B7B3A', '사당 祠宇 祠堂 묘소 墓所 원소 園所 제사 祭祀 致祭 장례 葬禮 예장 禮葬 천장 遷葬 '
                          '능소 陵所 봉안 奉安 신주 神主 묘호 廟號 시호 諡號 추숭 追崇'),
)
# 단서가 무엇을 말하는가에 따라 다르게 봅니다.
#  · 사건형(처벌·대립, 의례·장소): 문장 속 모두가 함께 겪는 일 → 두 표현 가까이에 단서가 있으면 됨
#  · 두 사람형(혈연·가족, 책봉·혼인, 명령·보고, 천거·협력): 특정한 두 사람 사이의 관계 → 단서가 두 이름 '사이'에 있고
#    두 이름이 가까울 때만(다른 사람의 혼인·명령에 끌려가지 않게: '김춘추와 혼인하여 법민·인문·노차를…'의 노차↔지경)
EVENT_TYPES = {'처벌·대립', '의례·장소'}
PAIR_GAP = 25          # 두 사람형: 두 이름 사이 최대 글자 수
EVENT_REACH = 60       # 사건형: 단서가 두 이름에서 떨어질 수 있는 최대 글자 수
_NEGATION = re.compile(r'^.{0,6}(?:않|아니|못하|불허|不許|勿)')
# 표시하는 종류: 규칙을 정할 때 쓰지 않은 대상의 근거 문장을 직접 읽어 70% 이상 맞은 종류만.
#  의례·장소 9~10/10 · 처벌·대립 8/10 · 명령·보고 8/10 → 표시
#  혈연·가족 6/10 · 천거·협력 2/4 · 책봉·혼인 0/6 → 표시하지 않음('공동 등장'으로 둠)
SHOWN = ('처벌·대립', '명령·보고', '의례·장소')
COLORS = {name: color for name, color, _ in TYPES}
COLORS[UNKNOWN] = '#B9B1A2'
NAMES = list(SHOWN) + [UNKNOWN]
_CUES = [(name, cue) for name, _, words in TYPES if name in SHOWN for cue in words.split()]


def _positions(text, word):
    word = re.sub(r'\s+', '', word or '')
    if not word:
        return []
    pat = r'\s*'.join(re.escape(ch) for ch in word)
    return [m.start() for m in re.finditer(pat, text)]


_HANGUL = re.compile(r'[가-힣]')


def _cue_positions(text, cue, avoid):
    """단서 낱말 위치. 한글 단서는 낱말 첫머리일 때만(앞 글자가 한글이면 다른 말의 일부: 잡아들이게·홍명하),
    두 표현(이름) 자리와 겹치면 빼기(박영호의 직함 '사사(司事)'가 처벌 '사사'로 잡히지 않게)."""
    out = []
    for pos in _positions(text, cue):
        if _HANGUL.match(cue[0]) and pos > 0 and _HANGUL.match(text[pos - 1]):
            continue
        if any(lo <= pos < hi for lo, hi in avoid):
            continue
        out.append(pos)
    return out


def classify(evidence, a, b):
    """근거 문장과 두 표현 → (종류, 단서 낱말 또는 '')."""
    text = str(evidence or '')
    pa, pb = _positions(text, a), _positions(text, b)
    if pa and pb:
        # 두 표현이 가장 가까이 함께 나온 곳을 기준점으로
        i, j = min(((x, y) for x in pa for y in pb), key=lambda p: abs(p[0] - p[1]))
        anchor_lo, anchor_hi = min(i, j), max(i, j)
    else:
        found = pa or pb
        anchor_lo = anchor_hi = found[0] if found else len(text) // 2
    avoid = [(p, p + len(re.sub(r'\s+', '', w))) for w, ps in ((a, pa), (b, pb)) for p in ps]
    both = bool(pa and pb)
    best = None
    for name, cue in _CUES:
        for pos in _cue_positions(text, cue, avoid):
            if _NEGATION.match(text[pos + len(cue):]):     # '추천하지 않음'처럼 부정이면 단서가 아님
                continue
            inside = anchor_lo <= pos <= anchor_hi
            d = 0 if inside else min(abs(pos - anchor_lo), abs(pos - anchor_hi))
            if name in EVENT_TYPES:
                if d > EVENT_REACH:
                    continue
            elif not (both and inside and anchor_hi - anchor_lo <= PAIR_GAP + len(a or '')):
                continue
            if best is None or d < best[0] or (d == best[0] and len(cue) > len(best[2])):
                best = (d, name, cue)
    if best is None:
        return UNKNOWN, ''
    return best[1], best[2]


def summarize(rels, a, b):
    """한 연결의 근거 기록들 → (대표 종류, {종류: 건수}). 단서가 있는 종류가 있으면 그중 가장 많은 것."""
    counts = {}
    for rel in rels:
        kind, cue = classify(rel.get('evidence'), a, b)
        rel['_kind'], rel['_cue'] = kind, cue
        counts[kind] = counts.get(kind, 0) + 1
    typed = {k: v for k, v in counts.items() if k != UNKNOWN}
    main = max(typed, key=lambda k: (typed[k], -NAMES.index(k))) if typed else UNKNOWN
    return main, counts
