# 사용: python make_test_corpus_mtl.py <폴더> <문헌 수> — 7·8강 시험용 가상 사료(주제·자료 유형 정답, 시대별 표기 차이)
"""가상 사료(시험 전용): 주제 3종 + 자료 유형 4종 정답, 시대에 따라 표기가 바뀌는 도메인 차이 포함."""
import sqlite3, json, sys, random
from pathlib import Path
out = Path(sys.argv[1]); n = int(sys.argv[2]); out.mkdir(exist_ok=True)
rng = random.Random(42)
# (한글 표기, 한문 표기) — 앞 시대(1700년 이전)는 한문 표기 비율이 높음
TOP = {
 '정치사': [('세자 책봉을 청하였다', '請冊封世子'), ('추숭하는 예를 논하였다', '議追崇之禮'), ('동궁의 위호를 의논하였다', '議東宮位號'),
          ('붕당의 다툼이 거세졌다', '朋黨之爭甚'), ('대리청정을 명하였다', '命代理聽政'), ('역모를 국문하였다', '鞫問逆謀'), ('종친의 지위를 높였다', '尊宗親之位')],
 '사회사': [('궁방에 딸린 노비를 조사하였다', '査宮房奴婢'), ('백성의 민전을 침탈하였다', '侵奪民田'), ('도망한 천인을 추쇄하였다', '推刷逃亡賤人'),
          ('유민을 진휼하였다', '賑恤流民'), ('흉년에 곡식을 나누었다', '凶年分穀'), ('과부의 재가를 의논하였다', '議寡婦再嫁'), ('장시의 물가가 올랐다', '場市物價騰')],
 '제도사': [('예조에서 제향 절목을 마련하였다', '禮曹磨鍊祭享節目'), ('수직의 정원과 급료를 정하였다', '定守直員額料'), ('사목을 새로 정하였다', '新定事目'),
          ('향사의 의주를 마련하였다', '磨鍊享祀儀注'), ('전례를 상고하여 정례로 삼았다', '考前例爲定例'), ('관직의 품계를 올렸다', '陞官職品階'), ('조세 규정을 의논하였다', '議租稅規式')],
}
TYPES = {'상소': ['엎드려 아뢰옵건대', '伏以', '신이 감히 아룁니다'], '전교': ['전교하기를', '傳曰', '하교하기를'],
         '계사': ['아뢰기를', '啓曰', '라고 하니 윤허하였다'], '논의': ['대신이 의논하여', '議于大臣', '여러 의견을 모아']}
COMMON = ['숙빈 최씨의 일로', '숙종 때의 전례를 살펴', '육상궁에 나아가', '소령원을 살피고', '비변사가 아뢰었다', '淑嬪崔氏', '毓祥宮']
SRC = [('sillok', '숙종실록'), ('sillok', '영조실록'), ('sjw', '승정원일기'), ('history', '비변사등록'), ('jsg', '장서각'), ('itkc', '고전번역서')]
db = out / 'collection.sqlite3'; db.unlink(missing_ok=True)
c = sqlite3.connect(db)
c.execute('CREATE TABLE documents (key TEXT PRIMARY KEY, data TEXT, status TEXT, error TEXT)')
c.execute('CREATE TABLE matches (key TEXT, term TEXT)')
truth = {}
for i in range(n):
    topics = rng.sample(list(TOP), rng.choice([1, 1, 1, 2]))
    dtype = rng.choice(list(TYPES))
    sid, coll = rng.choice(SRC)
    year = rng.choice([1680, 1689, 1694, 1701, 1718, 1725, 1739, 1744, 1753, 1765, 1772, 1786])
    hanja_p = 0.75 if year < 1700 else 0.1
    pick = lambda pair: pair[1] if rng.random() < hanja_p else pair[0]
    sents = [rng.choice(COMMON), rng.choice(TYPES[dtype])]
    if rng.random() < 0.6: sents.append(rng.choice(TYPES[dtype]))
    for t in topics:
        sents += [pick(p) for p in rng.sample(TOP[t], 3)]
    sents += rng.sample(COMMON, 2)
    rng.shuffle(sents)
    body = ' '.join(sents) + '. ' + rng.choice(['윤허하였다.', '그대로 따랐다.', '알았다고 답하였다.'])
    has_body = rng.random() < 0.75
    title = f'{coll} {year}년 기사 — ' + rng.choice(['숙빈 최씨', '육상궁', '淑嬪崔氏', '소령원']) + ' 관련 ' + (dtype if rng.random() < 0.5 else '기사')
    row = {'title': title, 'source': coll, 'url': f'https://example.org/{sid}/{i}', 'kind': sid, 'source_ids': [sid],
           'snippet': ' '.join(rng.sample(sents, 2)), 'body': body if has_body else '', 'metadata': {'date': f'{year}년'}, 'download_supported': True}
    key = f'{sid}-{i}'; truth[key] = {'주제': topics, '자료 유형': [dtype], 'year': year}
    c.execute('INSERT INTO documents VALUES (?,?,?,?)', (key, json.dumps(row, ensure_ascii=False), 'complete' if has_body else 'pending', None))
    c.execute('INSERT INTO matches VALUES (?,?)', (key, '숙빈 최씨'))
c.commit(); c.close()
(out / 'truth.json').write_text(json.dumps(truth, ensure_ascii=False), encoding='utf-8')
print('docs', n)
