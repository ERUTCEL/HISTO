"""연구 대상의 다른 이름·한자 표기 만들기('대상 적용'에서 검색어 칸을 채울 때).

- 성 + 호칭(장희빈, 희빈 장씨, 숙의 김씨 …) → 장희빈 · 희빈 장씨 · 張禧嬪 · 禧嬪張氏
- 왕·궁·관청 등 사료 용어(영조, 육상궁 …) → 한자 표기
- 모은 문헌에 '한글(漢字)'로 함께 적힌 이름(송시열(宋時烈) …) → 그 한자 표기
성의 한자가 여럿인 경우(정 鄭/丁 등)는 가장 흔한 것을 씁니다. 틀리면 검색어 칸에서 고치면 됩니다.
"""
import collections
import re

import hanja_input

# 내명부·왕실 호칭(긴 것부터 맞춰 봅니다)
TITLES = sorted('''빈 희빈 숙빈 경빈 영빈 의빈 수빈 화빈 안빈 창빈 인빈 귀인 소의 숙의 소용 숙용 소원 숙원 상궁 왕후 왕비 대비
세자빈 빈궁 후궁 부부인 군부인 현주 옹주 공주 대군'''.split(), key=len, reverse=True)

TITLE_HANJA = {'빈': '嬪', '희빈': '禧嬪', '숙빈': '淑嬪', '경빈': '敬嬪', '영빈': '暎嬪', '의빈': '宜嬪', '수빈': '綬嬪',
               '화빈': '和嬪', '안빈': '安嬪', '창빈': '昌嬪', '인빈': '仁嬪', '귀인': '貴人', '소의': '昭儀', '숙의': '淑儀',
               '소용': '昭容', '숙용': '淑容', '소원': '昭媛', '숙원': '淑媛', '상궁': '尙宮', '왕후': '王后', '왕비': '王妃',
               '대비': '大妃', '세자빈': '世子嬪', '빈궁': '嬪宮', '후궁': '後宮', '부부인': '府夫人', '군부인': '郡夫人',
               '현주': '縣主', '옹주': '翁主', '공주': '公主', '대군': '大君'}

# 흔한 성의 대표 한자
SURNAME_HANJA = {'김': '金', '이': '李', '박': '朴', '최': '崔', '정': '鄭', '강': '姜', '조': '趙', '윤': '尹', '장': '張',
                 '임': '林', '한': '韓', '오': '吳', '서': '徐', '신': '申', '권': '權', '황': '黃', '안': '安', '송': '宋',
                 '류': '柳', '유': '柳', '홍': '洪', '전': '全', '고': '高', '문': '文', '양': '梁', '손': '孫', '배': '裵',
                 '백': '白', '허': '許', '남': '南', '심': '沈', '노': '盧', '하': '河', '곽': '郭', '성': '成', '차': '車',
                 '주': '朱', '우': '禹', '구': '具', '민': '閔', '진': '陳', '나': '羅', '엄': '嚴', '채': '蔡', '원': '元',
                 '천': '千', '방': '方', '공': '孔', '현': '玄', '변': '邊', '여': '呂', '추': '秋', '도': '都', '소': '蘇',
                 '석': '石', '선': '宣', '설': '薛', '마': '馬', '길': '吉', '위': '魏', '표': '表', '명': '明', '기': '奇',
                 '반': '潘', '왕': '王', '금': '琴', '옥': '玉', '육': '陸', '인': '印', '맹': '孟', '제': '諸', '모': '牟'}


def _split_person(name):
    """'장희빈'·'희빈장씨' → ('장', '희빈'). 해당 없으면 None."""
    s = re.sub(r'\s+', '', name)
    for t in TITLES:
        # 성 + 호칭: 장희빈
        if len(s) == len(t) + 1 and s.endswith(t) and s[0] in SURNAME_HANJA:
            return s[0], t
        # 호칭 + 성 + 씨: 희빈장씨
        if len(s) == len(t) + 2 and s.startswith(t) and s.endswith('씨') and s[len(t)] in SURNAME_HANJA:
            return s[len(t)], t
    return None


def suggest(name):
    """→ 검색어로 쓸 다른 이름들(입력한 이름 포함, 겹침 없음, 중요한 순서)."""
    name = (name or '').strip()
    if not name:
        return []
    out = [name]

    def add(x):
        if x and re.sub(r'\s+', '', x) not in {re.sub(r'\s+', '', y) for y in out}:
            out.append(x)
    person = _split_person(name)
    if person:
        sur, title = person
        add(f'{sur}{title}')
        add(f'{title} {sur}씨')
        hs, ht = SURNAME_HANJA.get(sur), TITLE_HANJA.get(title)
        if hs and ht:
            add(f'{hs}{ht}')
            add(f'{ht}{hs}氏')
    compact = re.sub(r'\s+', '', name)
    for h in hanja_input.HISTORY.get(compact, []):
        add(h)
    for h, _ in hanja_input.corpus_pairs.get(compact, {}).most_common(1) if compact in hanja_input.corpus_pairs else []:
        add(h)
    return out


# ── 사료 원문에 쓰인 한자 이름 찾기 ──────────────────────────────
# 사료(실록·승정원일기·문집 원문)는 이름을 한자로 씁니다(宋時烈). 한글 이름만으로는 점수 엔진이
# 이름을 찾지 못해 사료가 0점이 되므로, 모은 문헌의 제목·요약에서 '글자마다 음이 맞는' 한자 표기를 찾습니다.
# 예: 송시열 → 宋(송)時(시)烈(렬/열). 두음법칙(렬↔열, 리↔이, 녀↔여) 때문에 첫소리 ㄴ·ㄹ·ㅇ은 같게 봅니다.
_CJK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff]')
_by_sound = None


def _sound(syl):
    code = ord(syl) - 0xAC00
    if not 0 <= code < 11172:
        return None
    initial, rest = divmod(code, 588)
    return ('ㄴㄹㅇ' if initial in (2, 5, 11) else initial, rest)


def _sound_index():
    global _by_sound
    if _by_sound is None:
        hanja_input._load()
        index = {}
        for syl, items in (hanja_input._singles or {}).items():
            key = _sound(syl) if len(syl) == 1 else None
            if key:
                index.setdefault(key, set()).update(h for h, _ in items)
        _by_sound = index
    return _by_sound


def hanja_from_texts(name, texts, limit=2):
    """한글 이름 → 문헌에 실제로 2번 이상 나온, 음이 글자마다 맞는 한자 표기(많이 나온 순)."""
    syls = re.sub(r'\s+', '', name or '')
    if not 2 <= len(syls) <= 8 or not all('가' <= c <= '힣' for c in syls):
        return []
    index = _sound_index()
    allowed = [index.get(_sound(c), set()) for c in syls]
    if not all(allowed):
        return []
    n, found = len(syls), collections.Counter()
    for text in texts:
        s = re.sub(r'\s+', '', str(text or ''))
        for i in range(len(s) - n + 1):
            if s[i] in allowed[0] and all(s[i + j] in allowed[j] for j in range(1, n)):
                found[s[i:i + n]] += 1
    if not found:
        return []
    top = found.most_common(1)[0][1]
    if n == 2:   # 두 글자는 우연히 겹치는 말이 많아(묘청 → 卯晴 '묘시에 맑음') 가장 많이 나온 하나만
        limit = 1
    return [h for h, c in found.most_common(limit) if c >= 2 and c >= top * 0.2]


def add_hanja_aliases(profile, rows):
    """프로필의 한글 직접 이름마다 문헌에서 찾은 한자 표기를 더합니다(이미 있으면 그대로). → 더한 표기 목록"""
    names = list(profile.get('direct_aliases') or [])
    have = {re.sub(r'\s+', '', x) for x in names}
    texts = [' '.join(str(r.get(k) or '') for k in ('title', 'snippet')) for r in rows]
    added = []
    def has_hanja(name):   # 이미 같은 길이의 한자 표기가 있는 이름(장희빈 → 張禧嬪)은 건너뜀
        n = len(re.sub(r'\s+', '', name))
        return any(len(x) == n and all(_CJK.match(c) for c in x) for x in have)
    for name in names:
        if has_hanja(name):
            continue
        for h in hanja_from_texts(name, texts):
            if h not in have:
                have.add(h)
                added.append(h)
    if added:
        profile['direct_aliases'] = names + added
    return added
