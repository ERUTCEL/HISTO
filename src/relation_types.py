"""관계도의 '관계 종류' 후보 — 근거 문장 속 단서 낱말로 정합니다.

방법(규칙 기반, 확정 아님):
1. 관계도의 연결은 '같은 문장에 함께 나온 두 표현'입니다(공동 등장).
2. 그 근거 문장에서 두 표현 사이나 가까이에 있는 단서 낱말을 찾습니다.
   단서 낱말은 종류마다 한글·한문 표현을 함께 둡니다(예: 처벌·대립 → 사사·賜死·유배·定配·탄핵·彈劾).
3. 단서가 여럿이면 두 표현에 가장 가까운 단서의 종류를 고릅니다(같은 문장 안의 다른 이야기에 끌려가지 않게).
4. 단서가 없으면 '공동 등장(미확정)'으로 둡니다.
5. 종류마다 단서를 인정하는 자리가 다릅니다(사건형·두 사람형·어순 틀 — 아래 TYPES 설명).
6. 연결(두 표현의 짝)의 종류는 근거 기록들의 종류 가운데 가장 많은 것입니다. 모든 종류별 건수도 함께 보여 줍니다.
한계: 단서 낱말은 관계의 '방향'(누가 누구를)을 알려 주지 않고, 부정문(…하지 않았다)이나 인용문을 가리지 못합니다.
그래서 화면에는 '후보'와 단서 낱말을 함께 보여 주고, 근거 문장을 직접 읽어 확인하게 합니다.
"""
import re

UNKNOWN = '공동 등장'

# (종류, 색, 판단 방식, 단서 낱말들). 한 글자 한자는 다른 말의 일부로 잘 섞이므로(世子의 子 등) 두 글자 이상 위주로 둡니다.
# 판단 방식 — 단서가 무엇을 말하는가에 따라 자리를 다르게 봅니다.
#  event   사건형: 문장 속 모두가 함께 겪는 일. 두 표현 가까이(60자 안)에 단서가 있으면 됨
#  between 두 사람형: 특정한 두 사람 사이의 일. 단서가 두 이름 '사이'에 있고 두 이름이 가까울 때(25자 안)
#  mid     어순 틀 'A의 아들 B': 앞 이름 바로 뒤(3자 안)에 단서, 그 바로 뒤(6자 안)에 다음 이름 — 또는 'B는 A의 아들'
#  after   어순 틀 'A를 B로 삼다/책봉': 두 이름이 가깝고(14자 안) 뒤 이름 바로 뒤(8자 안)에 단서
TYPES = (
    ('처벌·대립', '#B4442F', 'event', '사사되 사사하 사사를 사사한 사사된 賜死 유배 귀양 定配 圍籬 竄 처형 誅 탄핵 彈劾 논죄 論罪 국문 鞫問 推鞫 '
                                    '친국 親鞫 공초 供招 역모 逆謀 謀逆 모함 誣告 폐출 廢出 廢黜 폐위 廢位 삭탈 削奪 파직 罷職 처벌 治罪 죄를 罪人'),
    ('군사·전쟁', '#8C5A2B', 'event', '격파 擊破 침입 침략 侵入 侵略 토벌 討伐 征討 정벌 征伐 반란 叛亂 반역 전투 戰鬪 교전 출병 出兵 '
                                    '출정 出征 포위 包圍 함락 陷落 의병 義兵 왜란 호란 戰死'),
    ('외교·사신', '#3D8A93', 'event', '사신 使臣 사행 使行 조공 朝貢 遣使 입조 入朝 진하사 進賀使 사은사 謝恩使 동지사 冬至使 '
                                    '통신사 通信使 국서 國書 조약 條約 수교 修交'),
    ('의례·장소', '#6B7B3A', 'event', '사당 祠宇 祠堂 묘소 墓所 원소 園所 제사 祭祀 致祭 장례 葬禮 예장 禮葬 천장 遷葬 '
                                    '능소 陵所 봉안 奉安 신주 神主 묘호 廟號 시호 諡號 추숭 追崇'),
    ('명령·보고', '#27507A', 'between', '전교하 전교를 전교에 傳敎 하교하 하교를 下敎 敎曰 명하 명령 하유 下諭 諭曰 아뢰 啓曰 계사 啓辭 '
                                      '상소하 상소를 상소에 上疏 차자 箚子 장계를 장계에 장계의 狀啓 계청 啓請 비답 批答 윤허 允許'),
    ('천거·협력', '#2E7D5B', 'after', '천거 薦擧 추천 推薦 보필 輔弼 협력 구원 救援 비호 庇護 옹호 擁護 지지하 동지로 同志 도와서 도와주'),
    ('학문·사승', '#5D6FB3', 'mid', '문인 門人 제자 弟子 스승 師事 수학 受學 배웠 강학 講學 학통 學統 도통 道統'),
    ('혈연·가족', '#7A5AA6', 'mid', '아들 딸 아내 남편 어머니 아버지 생모 生母 생부 生父 소생 所生 장자 長子 차자 次子 서자 庶子 '
                                  '之子 之女 之妻 之母 之父 손자 孫子 아우 동생 조카 외손 사위 며느리'),
    ('책봉·혼인', '#C07A16', 'after', '책봉 冊封 封爲 진봉 進封 揀擇 嘉禮 혼인 婚姻 국혼 國婚 納采'),
    ('관직·임명', '#D08A1E', 'after', '제수 除授 임명 任命 삼았 삼고 삼아 삼으 승진 승차 陞差 발탁 拔擢 보임 補任 겸임 兼任 차정 差定 임용'),
)
SCOPE = {name: scope for name, _, scope, _ in TYPES}
EVENT_REACH = 60       # 사건형: 단서가 두 이름에서 떨어질 수 있는 최대 글자 수
PAIR_GAP = 25          # 두 사람형: 두 이름 사이 최대 글자 수
_NEGATION = re.compile(r'^.{0,6}(?:않|아니|못하|불허|不許|勿)')
_PAREN = re.compile(r'\s*[(（][^()（）]{1,30}[)）]')   # 괄호 속 한자 풀이: 희빈(禧嬪) 장씨(張氏) → 희빈 장씨
# 표시하는 종류: 규칙을 정할 때 쓰지 않은 대상의 근거 문장을 직접 읽어 70% 이상 맞은 종류만(docs/관계도_방법.md).
# 1차(사건형/두 사람형 구분 후) → 단서·어순 규칙을 고친 뒤 이미 판정한 문장을 뺀 새 표본으로 2차 판정:
#  관직·임명 12/12·5/6, 의례·장소 11/12·6/6, 명령·보고 10/12·6/6, 처벌·대립 10/12·5/6,
#  외교·사신 6/12→12/12, 군사·전쟁 9/12→7/10('공격'을 뺀 뒤 0건 틀림), 혈연·가족 6/12→9/12 → 표시
#  천거·협력 7/11 → 엄격한 규칙에서는 거의 안 나옴, 학문·사승 0/12, 책봉·혼인 약 3/12 → 표시하지 않음
SHOWN = ('처벌·대립', '군사·전쟁', '외교·사신', '명령·보고', '관직·임명', '혈연·가족', '의례·장소')
COLORS = {name: color for name, color, _, _ in TYPES}
COLORS[UNKNOWN] = '#B9B1A2'
ALL = [name for name, _, _, _ in TYPES]
NAMES = list(SHOWN) + [UNKNOWN]
_CUES = [(name, cue) for name, _, _, words in TYPES for cue in words.split()]


def _positions(text, word):
    word = re.sub(r'\s+', '', word or '')
    if not word:
        return []
    pat = r'\s*'.join(re.escape(ch) for ch in word)
    return [m.start() for m in re.finditer(pat, text)]


_HANGUL = re.compile(r'[가-힣]')
# 한글 단서 바로 뒤에 올 수 있는 글자(조사·어미·활용). 이 밖의 한글이 붙으면 다른 낱말의 일부(조공립·문하시중·도와 달라서)
_AFTER_OK = set('을를이가은는의에로와과도만하한했할함되된됨된다며고여서으니였였')


def _cue_positions(text, cue, avoid):
    """단서 낱말 위치. 한글 단서는 낱말 첫머리일 때만(앞 글자가 한글이면 다른 말의 일부: 잡아들이게·홍명하),
    두 표현(이름) 자리와 겹치면 빼기(박영호의 직함 '사사(司事)'가 처벌 '사사'로 잡히지 않게)."""
    out = []
    for pos in _positions(text, cue):
        if _HANGUL.match(cue[0]) and pos > 0 and _HANGUL.match(text[pos - 1]):
            continue
        nxt = text[pos + len(cue):pos + len(cue) + 1]
        if _HANGUL.match(cue[-1]) and nxt and _HANGUL.match(nxt) and nxt not in _AFTER_OK:
            continue
        if any(lo <= pos < hi for lo, hi in avoid):
            continue
        out.append(pos)
    return out


def classify(evidence, a, b, kinds=None):
    """근거 문장과 두 표현 → (종류, 단서 낱말 또는 ''). kinds: 볼 종류(기본은 화면에 보이는 종류만)."""
    kinds = set(SHOWN if kinds is None else kinds)
    raw = str(evidence or '')
    text = _PAREN.sub('', raw)
    pa, pb = _positions(text, a), _positions(text, b)
    if not (pa and pb):           # 이름이 괄호 속 한자로만 나오면 원문 그대로
        text = raw
        pa, pb = _positions(text, a), _positions(text, b)
    la, lb = len(re.sub(r'\s+', '', a or '')), len(re.sub(r'\s+', '', b or ''))
    both = bool(pa and pb)
    if both:
        i, j = min(((x, y) for x in pa for y in pb), key=lambda p: abs(p[0] - p[1]))
        (first, lf), (second, ls) = sorted(((i, la), (j, lb)))
        anchor_lo, anchor_hi = first, second
        first_end, second_end = first + lf, second + ls
    else:
        found = pa or pb
        anchor_lo = anchor_hi = found[0] if found else len(text) // 2
    avoid = [(p, p + n) for n, ps in ((la, pa), (lb, pb)) for p in ps]
    best = None
    for name, cue in _CUES:
        if name not in kinds:
            continue
        scope = SCOPE[name]
        if scope != 'event' and not both:
            continue
        for pos in _cue_positions(text, cue, avoid):
            end = pos + len(cue)
            if _NEGATION.match(text[end:]):     # '추천하지 않음'처럼 부정이면 단서가 아님
                continue
            inside = anchor_lo <= pos <= anchor_hi
            d = 0 if inside else min(abs(pos - anchor_lo), abs(pos - anchor_hi))
            if scope == 'event':
                ok = d <= EVENT_REACH
            elif scope == 'between':
                ok = inside and anchor_hi - anchor_lo <= PAIR_GAP + la
            elif scope == 'mid':     # 'A의 아들 B'(이름 바로 뒤 단서, 단서 바로 뒤 이름) / 'B는 A의 아들'(두 이름 사이엔 은·는·이·가만)
                ok = ((0 <= pos - first_end <= 2 and 0 <= second - end <= 3)
                      or (0 <= pos - second_end <= 2
                          and re.fullmatch(r'\s*(?:은|는|이|가|,)?\s*', text[first_end:second]) is not None))
            else:                    # after: A를 B로 삼다
                ok = second - first_end <= 14 and 0 <= pos - second_end <= 8
            if not ok:
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
