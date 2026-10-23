# 사용: python make_test_corpus.py <폴더> <문헌 수>  — 텍스트 분석 기능 시험용 가상 사료(실제 사료 아님)와 정답 주제(truth.json)를 만듭니다.
"""가상 사료 코퍼스(테스트 전용). 주제별 어휘 + 공통 어휘 + 잡음, 규칙 관점 키워드는 일부 문헌에만 노출."""
import sqlite3, json, sys, random
from pathlib import Path
out = Path(sys.argv[1]); n = int(sys.argv[2]); out.mkdir(exist_ok=True)
rng = random.Random(42)
TOP_EXTRA = {
 '정치사': ['동궁의 위호를 의논하였다', '붕당의 다툼이 거세졌다', '왕대비가 언문 교서를 내렸다', '사사를 명하는 전교가 있었다', '종친의 지위를 높이자고 청하였다', '대리청정을 명하였다', '역모를 국문하였다'],
 '사회사': ['도망한 천인을 추쇄하였다', '유민을 진휼하였다', '흉년에 곡식을 나누어 주었다', '역을 피해 떠도는 자가 많았다', '사족과 상민의 다툼을 다스렸다', '과부의 재가를 의논하였다', '장시의 물가가 올랐다'],
 '제도사': ['사목을 새로 정하였다', '수직의 정원과 급료를 규정하였다', '규식에 따라 시행하였다', '향사의 의주를 마련하였다', '등록에 올려 준행하게 하였다', '전례를 상고하여 정례로 삼았다', '아문의 소관을 나누었다'],
}
TOP = {
 '정치사': ['왕위를 잇는 문제를 의논하였다', '세자 책봉을 청하였다', '추숭하는 예를 논하였다', '환국 이후 조정이 바뀌었다', '대신들이 왕권을 두고 다투었다', '당론이 갈려 상소가 이어졌다', '왕자의 위호를 정하였다'],
 '사회사': ['궁방에 딸린 노비를 조사하였다', '백성의 민전을 침탈한 일을 아뢰었다', '호적에 올린 신분을 바로잡았다', '혼인을 금하는 영을 내렸다', '궁방전의 세를 거두는 폐단을 말하였다', '노비 추쇄를 중지하였다', '향촌의 백성이 굶주렸다'],
 '제도사': ['예조에서 제향 절목을 마련하였다', '호조가 수봉관의 급료를 정하였다', '관직의 품계를 올렸다', '정식을 고쳐 반포하였다', '조세 수취 규정을 의논하였다', '제사에 쓸 물품을 마련하였다', '육상궁 수직의 정원을 정하였다'],
}
RULE_KW = {'정치사': ['세자', '책봉', '추숭', '환국', '왕위'], '사회사': ['노비', '백성', '민전', '궁방', '혼인'], '제도사': ['예조', '호조', '정식', '절목', '제향']}
COMMON = ['숙빈 최씨의 일로', '영조가 하교하기를', '숙종 때의 전례를 살펴', '승지가 아뢰기를', '대신이 의논하여', '육상궁에 나아가', '소령원을 살피고', '비변사가 아뢰었다']
SRC = [('sillok', '숙종실록'), ('sillok', '영조실록'), ('sjw', '승정원일기'), ('history', '비변사등록'), ('jsg', '장서각'), ('itkc', '고전번역서')]
db = out / 'collection.sqlite3'; db.unlink(missing_ok=True)
c = sqlite3.connect(db)
c.execute('CREATE TABLE documents (key TEXT PRIMARY KEY, data TEXT, status TEXT, error TEXT)')
c.execute('CREATE TABLE matches (key TEXT, term TEXT)')
truth = {}
for i in range(n):
    k = rng.choice([1, 1, 1, 2])
    topics = rng.sample(list(TOP), k)
    sid, coll = rng.choice(SRC)
    year = rng.choice([1694, 1701, 1718, 1725, 1739, 1744, 1753, 1765, 1772])
    sents = [rng.choice(COMMON)]
    for t in topics:
        sents += rng.sample(TOP[t], 1) + rng.sample(TOP_EXTRA[t], 2)
    sents += rng.sample(COMMON, 2)
    rng.shuffle(sents)
    body = ' '.join(sents) + '. ' + rng.choice(['윤허하였다.', '그대로 따랐다.', '알았다고 답하였다.'])
    has_body = rng.random() < 0.7
    t0 = topics[0]
    title = f'{coll} {year}년 기사 — ' + rng.choice(['숙빈 최씨', '육상궁', '淑嬪崔氏', '소령원']) + ' 관련 ' + rng.choice(['논의', '계사', '전교', '상소'])
    snippet = ' '.join(rng.sample(sents, 2))
    row = {'title': title, 'source': coll, 'url': f'https://example.org/{sid}/{i}', 'kind': sid, 'source_ids': [sid],
           'snippet': snippet, 'body': body if has_body else '', 'metadata': {'date': f'{year}년'}, 'download_supported': True}
    key = f'{sid}-{i}'; truth[key] = topics
    c.execute('INSERT INTO documents VALUES (?,?,?,?)', (key, json.dumps(row, ensure_ascii=False), 'complete' if has_body else 'pending', None))
    c.execute('INSERT INTO matches VALUES (?,?)', (key, '숙빈 최씨'))
c.commit(); c.close()
(out / 'truth.json').write_text(json.dumps(truth, ensure_ascii=False), encoding='utf-8')
print('docs', n)
