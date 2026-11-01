"""기록 연대 채우기와 큰 시대 가중치.

연도 차이로는 점수를 매기지 않습니다(실록도 사후에 편찬). 점수에는 큰 시대(고대~현대)가 같은지만 씁니다.
- 연대가 없는 기록은 사이트 ID에서 연대를 읽어 채웁니다.
  실록  ksa_115…  → 숙종 15년 → 1689
  승정원일기 SJW-F45… → 영조 45년 → 1769
  신문  npjj_1936_… → 1936
"""
import collections
import re

# 실록 왕대 코드 → 즉위 연도(재위 n년 = 즉위 연도 + n)
SILLOK_KINGS = {'aa': 1392, 'ba': 1398, 'ca': 1400, 'da': 1418, 'ea': 1450, 'fa': 1452, 'ga': 1455, 'ha': 1468,
                'ia': 1469, 'ja': 1494, 'ka': 1506, 'la': 1544, 'ma': 1545, 'na': 1567, 'nb': 1567, 'oa': 1608,
                'ob': 1608, 'pa': 1623, 'qa': 1649, 'ra': 1659, 'rb': 1659, 'sa': 1674, 'sb': 1674, 'ta': 1720,
                'tb': 1720, 'ua': 1724, 'va': 1776, 'wa': 1800, 'xa': 1834, 'ya': 1849, 'za': 1863, 'zb': 1907,
                'zc': 1910}
# 승정원일기 왕대 글자 → 즉위 연도
SJW_KINGS = {'A': 1623, 'B': 1649, 'C': 1659, 'D': 1674, 'E': 1720, 'F': 1724, 'G': 1776, 'H': 1800, 'I': 1834,
             'J': 1849, 'K': 1863, 'L': 1907}
MODERN_WORDS = ('신문', '일보', '신보', '잡지', '매일')
# ID에 날짜가 없는 자료집의 대략적인 연대(한국사DB 근현대 잡지: 1920~30년대 『개벽』·『별건곤』·『삼천리』 등)
SOURCE_YEARS = {'근현대잡지자료': 1930}



def year_from_key(row):
    key = str(row.get('key') or '') + ' ' + str(row.get('url') or '')
    m = re.search(r'sillok:[kw]([a-z]{2})_1(\d{2})', key) or re.search(r'/id/[kw]([a-z]{2})_1(\d{2})', key)
    if m and m.group(1) in SILLOK_KINGS:
        return SILLOK_KINGS[m.group(1)] + int(m.group(2))
    m = re.search(r'SJW-([A-L])(\d{2})', key)
    if m:
        return SJW_KINGS[m.group(1)] + int(m.group(2))
    m = re.search(r'_(1[89]\d{2}|20[0-2]\d)_\d{2}_\d{2}', key)   # 신문 npjj_1936_06_07
    if m:
        return int(m.group(1))
    m = re.search(r'db:(?:tcko|tcmd|tcct)_(\d{4})_', key)   # 고대사·근대사·대한민국사 연표: tcko_0413 → 413
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    m = re.search(r'db:ch_\d{3}_(\d{4})_', key)             # 주제별연표 ch_007_1135 → 1135
    if m:
        return int(m.group(1))
    return SOURCE_YEARS.get(str(row.get('source') or ''))


def fill_year(row):
    a = row['analysis']
    if not a.get('year'):
        y = year_from_key(row)
        if y:
            a['year'] = y
            a['period'] = f'{(y - 1) // 100 + 1}세기'


def is_modern_press(row):
    return any(w in str(row.get('source') or '') for w in MODERN_WORDS)


# ── 시대 가중치(연도가 아니라 큰 시대 단위) ─────────────────────────
# 연도 거리로 재면 기록의 양·사후 기록에 휘둘리므로, 큰 시대가 같은지만 봅니다.
ERAS = ('고대', '고려', '조선', '근대(대한제국)', '일제강점기', '현대')
ERA_STARTS = (918, 1392, 1897, 1910, 1945)       # 각 시대가 시작하는 해(고려~현대)
ERA_PENALTY = {0: 0, 1: 10}                        # 같은 시대 0 · 바로 이웃 시대 -10 · 그 밖 -30
ERA_FAR_PENALTY = 30


def era_of(year):
    y = int(year)
    i = 0
    while i < len(ERA_STARTS) and y >= ERA_STARTS[i]:
        i += 1
    return i


def _direct(r):
    return any('직접명칭' in x for x in r['analysis'].get('score_reasons', []))


ERA_MIN_RECORDS = 10    # 이름이 직접 나오는 사료가 이보다 적으면 판단하지 않음(보류)
ERA_EARLY_SHARE = 0.2   # '가장 이른 시대'로 인정하려면 직접 언급 사료의 20% 이상


def target_era(rows, other_kind=lambda r: None):
    """대상의 시대(또는 이웃한 두 시대)를 정합니다. 확실하지 않으면 None(보류).

    근거: 대상 이름이 직접 나오는 사료(연구·해설·문예 서술 제외)의 시대.
    연대가 있으면 연대로, 없으면 어느 시대의 사서인지로(삼국사기→고대, 고려사→고려) 셉니다.
      ① 가장 많이 나온 시대
      ② 직접 언급의 20% 이상이 나온 가장 이른 시대(몇 건뿐인 동명이인·음만 같은 말에 끌려가지 않게)
    ①과 ②가 같으면 그 시대, 이웃한 두 시대면 두 시대 모두(생애가 걸친 인물: 정몽주 고려~조선),
    두 시대 이상 떨어지거나 직접 언급이 10건 미만이면 보류합니다.
    기준(20%·10건)은 33개 대상으로 정하고, 정할 때 쓰지 않은 새 대상 15개로 따로 확인했습니다.
    → ((시작 시대, 끝 시대) 또는 None, 후보 시대들)"""
    count = collections.Counter()
    for r in rows:
        if not _direct(r) or other_kind(r):
            continue
        e = era_hint(r)
        if e is not None:
            count[e] += 1
    total = sum(count.values())
    if not count:
        return None, []
    mode = max(sorted(count), key=lambda e: count[e])
    early = next((e for e in sorted(count) if count[e] >= 2 and count[e] >= ERA_EARLY_SHARE * total), None)
    if total < ERA_MIN_RECORDS:
        return None, []   # 근거가 적으면 후보도 내지 않음(몇 건뿐인 후보는 오해를 부름)
    candidates = sorted({e for e in (mode, early) if e is not None})
    if early is None or abs(mode - early) > 1:
        return None, candidates
    return (min(mode, early), max(mode, early)), candidates


def era_label(era):
    lo, hi = era
    return ERAS[lo] if lo == hi else f'{ERAS[lo]}~{ERAS[hi]}'


def apply_era(rows, era):
    """대상 시대(시작, 끝)와 다른 시대의 기록은 점수를 낮춥니다(근거 문구에 숫자 없음)."""
    if era is None:
        return
    lo, hi = era
    for r in rows:
        a = r['analysis']
        y = a.get('year')
        if not y or a.get('era_adjusted'):
            continue
        try:
            e = era_of(y)
        except (TypeError, ValueError):
            continue
        d = 0 if lo <= e <= hi else min(abs(e - lo), abs(e - hi))
        cut = ERA_PENALTY.get(d, ERA_FAR_PENALTY)
        if cut:
            a['score'] -= cut
            a.setdefault('score_reasons', []).append(
                ('대상과 이웃한 시대의 기록' if d == 1 else '대상과 다른 시대의 기록') + f' -{cut}')
            a['era_adjusted'] = True


# 연대는 없지만 어느 시대의 사서인지로 시대를 알 수 있는 자료(사이트 ID 앞부분)
KEY_ERAS = (('db:sg_', 0), ('db:sy_', 0), ('db:kr_', 1), ('db:kj_', 1))


def era_hint(row):
    """기록의 시대: 연대가 있으면 연대로, 없으면 어느 시대 사서인지(삼국사기→고대, 고려사→고려)로."""
    y = (row.get('analysis') or {}).get('year')
    if y:
        try:
            return era_of(y)
        except (TypeError, ValueError):
            pass
    key = str(row.get('key') or '')
    for prefix, era in KEY_ERAS:
        if key.startswith(prefix):
            return era
    return None
