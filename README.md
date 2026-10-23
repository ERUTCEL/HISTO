# 역사자료 연구실 v3.1 — 디자인 개선

v3 실행파일(`역사자료연구실-v3.exe`)의 **기능은 그대로 두고 화면만 다시 디자인**한 버전입니다.
디자인 시안: [Claude Design 캔버스 「역사자료 연구실 v3.1 리디자인」](https://claude.ai/artifact/SNRQfUW6gRB6Epwk5fe5PU) (통합 검색 · 자료원 연결 · 내 문헌 · 문헌 결과 4개 화면).

| 변경 전 (v3) | 변경 후 (v3.1) |
|---|---|
| ![](docs/screenshots/before-search.png) | ![](docs/screenshots/after-search.png) |
| ![](docs/screenshots/before-sources.png) | ![](docs/screenshots/after-sources.png) |
| ![](docs/screenshots/before-library.png) | ![](docs/screenshots/after-library.png) |

결과 창과 관계 지도: [after-results.png](docs/screenshots/after-results.png), [after-relations.png](docs/screenshots/after-relations.png)

## 무엇이 바뀌었나

**디자인 방향** — 한지 바탕(#F4F1EA), 먹색 사이드바(#1E2630), 쪽빛 강조(#27507A), 낙관(붉은 도장) 로고.

- **글꼴**: Pretendard(SIL OFL 1.1)를 EXE에 내장했습니다. 맑은 고딕이 없는 PC에서도 같은 모양으로 보입니다.
- **고해상도 화면**: Windows 배율 125%·150%에서 글자가 흐릿하던 문제를 고쳤습니다(DPI 인식 + 여백 자동 배율). 창 크기도 화면에 맞춰 줄어듭니다.
- **제목·숫자 크기 버그 수정**: v3은 전역 글꼴 설정 때문에 큰 제목(23pt)과 통계 숫자가 실제로는 10pt로 보였습니다. 이제 의도한 크기로 나옵니다.
- **사이드바**: 금색 표시선이 있는 메뉴, 마우스를 올리면 강조, 붉은 도장 로고.
- **페이지 머리말**: 화면 제목(큰 글씨) + 한 줄 설명 + 오른쪽 상태 표시(대기 중 / 수집 중).
- **통합 검색**: 통계 4칸을 한 카드로 묶음, 연구 대상 입력칸 확대(Enter로 대상 적용), 검색 방식을 드롭다운 대신 **세그먼트 버튼 5개**로 바꾸고 방식별 설명을 바로 아래에 표시, "자료원 7곳 선택됨 · 변경" 링크.
- **진행 막대**: 쉬는 동안 화면에 고정돼 있던 초록 막대를 없애고, 수집 중일 때만 하단 상태 표시줄에 나타납니다.
- **자료원 연결**: 체크박스를 ✕ 대신 ✓로 직접 그림, 범위 배지(목록·본문 / 서지·요약 / 공식 사이트)를 색으로 구분하고 상단에 범례 추가.
- **내 문헌**: 보관함 정보와 버튼을 한 줄로, 줄무늬 표, 문헌이 없을 때 안내 문구.
- **문헌 결과 창**: 목록과 본문을 **좌우 나란히** 배치(기존 위·아래), 본문 영역에 점수·관점·연대·상태 배지와 제목·메타·근거·본문을 서식으로 구분.
- **관계 후보 지도**: 창 폭에 맞춰 중앙 정렬, 선택한 연결을 쪽빛으로 강조, 연결을 클릭하면 근거 표시.
- **한글 입력**: 입력 중(조합 중)인 글자가 작게 보였다가 커지던 현상을 고쳤습니다. 입력칸에 들어갈 때 IME 조합 글꼴을 그 칸의 글꼴·크기로 맞춥니다.
- **뒤로가기**: 메인 창 제목 옆 `← 이전 화면`(Alt+←), 결과 창 `← 연구실로`·관계 지도 `← 결과로`(Esc).
- 화살표 없는 얇은 스크롤바, 1px 테두리 버튼, 포커스 시 쪽빛 테두리.

**바뀌지 않은 것** — 수집기(`collector`), 자료원 연결(`source_connectors`), 분석(`analysis_engine`), 브라우저 연결(`browser_links`)은 원본 EXE의 코드를 바이트 그대로 사용합니다. 수집 결과 폴더 형식도 같아서 기존 결과를 그대로 읽습니다.

## 폴더

```
src/app.py             메인 창 (원본 바이트코드에서 로직을 복원한 뒤 화면만 새로 구성)
src/result_viewer.py   문헌 결과 창 · 관계 후보 지도 (동일)
src/ui_theme.py        색상·글꼴·스타일과 화면 조립 도우미
src/fonts/             Pretendard Regular/SemiBold/Bold + OFL 라이선스
tools/repack.py        원본 EXE에 새 모듈·글꼴을 넣어 v3.1 EXE를 만드는 스크립트
tools/carchive.py      PyInstaller 아카이브 읽기 도우미
docs/screenshots/      변경 전후 화면
```

## EXE 만들기

원본 v3 EXE의 PyInstaller 아카이브에서 `app`, `ui_theme`, `result_viewer`만 교체하고 `tkinter.font`(v3에는 없던 표준 모듈)와 글꼴을 추가합니다. 나머지 1,000여 개 항목은 바이트 그대로 복사합니다. **Python 3.10**으로 실행해야 합니다(EXE에 든 python310.dll과 바이트코드 버전이 같아야 함).

```bash
python3.10 tools/repack.py 역사자료연구실-v3.exe src 역사자료연구실-v3.1.exe
```

또는 Windows에서 PyInstaller로 원래 프로젝트를 다시 빌드할 때는 `src/`의 세 파일로 교체하고, `--add-data "fonts;fonts"`를 추가하면 됩니다.

## 확인한 것

- 원본 앱의 자체 점검(`--smoke-test`)을 원본 EXE와 새 EXE에서 **Wine(Windows 실행 환경)으로 실제 실행**해 결과를 비교: 자료원 수, 통합 자료원 수, 화면 4개, 수집 실행·종료 메시지, 결과 창, 관계 지도 항목이 모두 동일.
- 테스트용 결과 폴더(가짜 문헌 40건)로 결과 창의 목록 내용·필터(관점/검색어/자료원/점수)·관계 연결 수를 원본과 비교: 모두 동일.
- 재패킹 도구 검증: 아무것도 바꾸지 않고 재패킹하면 원본과 내용이 같은 EXE가 나옴(직렬화 플래그 1바이트 차이만 있음).

## 주의

- 실제 Windows PC가 아니라 Wine에서 확인했습니다. 첫 실행 시 SmartScreen 경고가 뜰 수 있는 것은 원본과 같습니다(서명 없음).
- Pretendard에는 한자가 없어서 한자는 Windows가 다른 글꼴로 자동 대체해 표시합니다.
- 원본에 함께 있던 `상세사용법.md`의 소스 실행 예(`collector.py`)는 이 폴더에 없는 원본 소스를 기준으로 합니다.

| 파일 | SHA-256 |
|---|---|
| 역사자료연구실-v3.exe (원본) | `f6f46bcaa4f0efd834781cf6778ffe565752fd42320850f34a6c745fe28a0086` |
| 역사자료연구실-v3.1.exe | `9d5315c2bc051bc115bd2fd83ca5c0b28123ee42a0c9d8a87039d5bc5dd99b69` |
