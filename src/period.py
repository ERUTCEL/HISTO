"""기록 연대 채우기(화면 표시·연대 거르기용). 점수에는 쓰지 않습니다.

기록의 날짜는 일이 있었던 날짜일 뿐(실록도 사후에 편찬), 시기로 가중치를 주는 것은 맞지 않아 쓰지 않습니다.
- 연대가 없는 기록은 사이트 ID에서 연대를 읽어 채웁니다.
  실록  ksa_115…  → 숙종 15년 → 1689
  승정원일기 SJW-F45… → 영조 45년 → 1769
  신문  npjj_1936_… → 1936
"""
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


def target_era(rows, other_kind=lambda r: None):
    """대상 이름이 직접 나오는 사료(연구 문헌·후대 서술 제외)가 가장 많은 시대. 근거가 3건 미만이면 None."""
    counts = [0] * len(ERAS)
    for r in rows:
        a = r['analysis']
        y = a.get('year')
        if not y or a['score'] < 60 or other_kind(r):
            continue
        if not any('직접명칭' in x for x in a.get('score_reasons', [])):
            continue
        try:
            counts[era_of(y)] += 1
        except (TypeError, ValueError):
            continue
    best = max(range(len(ERAS)), key=lambda i: counts[i])
    return best if counts[best] >= 3 else None


def apply_era(rows, era):
    """대상 시대와 다른 시대의 기록은 점수를 낮춥니다(근거 문구에 숫자 없음)."""
    if era is None:
        return
    for r in rows:
        a = r['analysis']
        y = a.get('year')
        if not y or a.get('era_adjusted'):
            continue
        try:
            d = abs(era_of(y) - era)
        except (TypeError, ValueError):
            continue
        cut = ERA_PENALTY.get(d, ERA_FAR_PENALTY)
        if cut:
            a['score'] -= cut
            a.setdefault('score_reasons', []).append(
                ('대상과 이웃한 시대의 기록' if d == 1 else '대상과 다른 시대의 기록') + f' -{cut}')
            a['era_adjusted'] = True
