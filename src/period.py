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
