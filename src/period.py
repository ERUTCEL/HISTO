"""시기 가중치: 연구 대상이 활동한 시기를 모은 사료에서 추정하고, 그보다 한참 뒤(또는 앞)의 기록은 점수를 낮춥니다.

- 연대가 없는 기록은 사이트 ID에서 연대를 읽어 채웁니다.
  실록  ksa_115…  → 숙종 15년 → 1689
  승정원일기 SJW-F45… → 영조 45년 → 1769
  신문  npjj_1936_… → 1936
- 대상 시기: 대상 이름이 직접 나오는 사료(연구 문헌·근현대 신문·잡지 제외)의 연대 가운데 15%~85% 구간.
- 가중치: 시기 ±50년 안 0 · 50~150년 뒤 -20 · 150년 넘게 뒤 -40 · 50년 넘게 앞 -30(같은 이름의 다른 인물일 가능성)
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

NEAR, FAR = 50, 150
PENALTY_LATER, PENALTY_MUCH_LATER, PENALTY_EARLIER = 20, 40, 30


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


def estimate(rows, secondary=lambda r: False):
    """→ (시작, 끝) 또는 None. 대상 이름이 직접 나오는 사료의 연대 15%~85% 구간."""
    years = sorted(r['analysis']['year'] for r in rows
                   if r['analysis'].get('year') and r['analysis']['score'] >= 80
                   and any('직접명칭' in x for x in r['analysis'].get('score_reasons', []))
                   and not secondary(r) and not is_modern_press(r))
    if len(years) < 5:
        return None
    lo, hi = years[int(len(years) * 0.15)], years[min(len(years) - 1, int(len(years) * 0.85))]
    return int(lo), int(hi)


def apply(rows, era):
    if not era:
        return
    start, end = era
    for r in rows:
        a = r['analysis']
        y = a.get('year')
        if not y or a.get('period_adjusted'):
            continue
        try:
            y = int(y)
        except (TypeError, ValueError):
            continue
        cut, why = 0, ''
        # 근거 문구는 숫자 없이(판정 학습이 같은 근거로 묶어 배우도록)
        if y - end > FAR:
            cut, why = PENALTY_MUCH_LATER, '대상 시기보다 150년 넘게 뒤 기록'
        elif y - end > NEAR:
            cut, why = PENALTY_LATER, '대상 시기보다 50년 넘게 뒤 기록'
        elif start - y > NEAR:
            cut, why = PENALTY_EARLIER, '대상 시기보다 50년 넘게 앞 기록'
        if cut:
            a['score'] -= cut
            a.setdefault('score_reasons', []).append(f'{why} -{cut}')
            a['period_adjusted'] = True
