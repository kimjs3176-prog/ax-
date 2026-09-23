# 농해수위 국정감사 온톨로지 서비스 (AgriSea Audit Ontology)

국회 **열린국회정보 「위원회 회의록」 Open API**(`ncwgseseafwbuheph`)로 **농림축산식품해양수산위원회** 회의록을 모아
회의·안건·발언·발언자·피감기관·쟁점·이행약속을 잇는 **지식그래프(온톨로지)** 로 구조화합니다.
이 그래프를 바탕으로 국정감사 준비에 쓸 **회의내용 검색 · 주요내용 정리 · 핵심안건 요약 · 기관/쟁점별 브리핑** 기능을 제공합니다.

```
열린국회정보 API ──► 회의 메타데이터(회의명·일자·안건·PDF 링크)
        │
        ▼
회의록 PDF ──► 발언 단위 파싱(◯발언자 구분, 안건 전환, 질의/답변/이행약속/자료요구 판별)
        │
        ▼
SQLite(FTS5 전문검색) ──► RDF 지식그래프(OWL/SKOS, Turtle) ──► SPARQL
        │
        ▼
웹 UI / REST API / CLI : 검색 · 회의 요약 · 쟁점 관계망 · 국감 브리핑(Markdown 내보내기)
```

## 주요 기능

| 기능 | 내용 |
|---|---|
| 회의내용 검색 | 발언 전문검색(여러 단어 AND), 발언자 유형(위원/정부)·기간 필터, 결과의 관련 쟁점 집계 |
| 주요내용 정리 | 회의별 개요, 핵심 쟁점, 키워드, 중요 문장 추출 요약 |
| 핵심안건 요약 | 안건별 발언 구간을 나눠 요약·발언자·쟁점 정리, 주요 질의–답변 쌍 선별 |
| 국감 브리핑 | 피감기관·쟁점·키워드·위원·기간 조건으로 과거 회의록을 모아 핵심 쟁점, 주요 질의와 정부 답변, **이행약속 추적표**, 관심 위원, 관련 회의를 한 번에 정리(Markdown 다운로드) |
| 쟁점 온톨로지 | 농해수위 쟁점 분류체계(SKOS, 상·하위 개념)와 쟁점–기관–위원 관계망 시각화 |
| SPARQL | 지식그래프 직접 질의(예시 질의 제공), 전체 그래프 TTL 내려받기 |
| (선택) Claude 심층 요약 | `ANTHROPIC_API_KEY`가 있으면 회의별 핵심안건·쟁점·정부 입장·국감 후속 점검사항을 LLM으로 요약 |

## 온톨로지 개요 (`ontology/agrisea.ttl`)

```
ag:Committee ◄─ag:heldBy─ ag:Meeting ─ag:hasAgendaItem─► ag:AgendaItem
                           │  (AuditMeeting / PlenaryCommitteeMeeting / SubcommitteeMeeting)
                           │─ag:audited─► ag:AuditedAgency (피감기관)
                           │─ag:hasUtterance─► ag:Utterance ─ag:spokenBy─► ag:Person
                           │                     │ (Question / Answer / Commitment / DataRequest)
                           │                     │─ag:respondsTo─► ag:Question
                           │                     │─ag:concernsIssue─► ag:Issue (skos:Concept, skos:broader)
                           │                     │─ag:mentionsOrganization─► ag:Organization
                           │                     └─ag:committedBy─► ag:Organization (이행약속 주체)
ag:Person: Legislator / Chair / GovernmentOfficial / Witness / Staff ─ag:affiliatedWith─► ag:Organization
```

- 쟁점 분류체계와 피감기관 사전은 `agrisea/lexicon.py`에 있습니다. 키워드를 추가하면 재색인 시 그래프에 반영됩니다.
- **이행약속(ag:Commitment)**: 정부·기관 답변 중 '~하겠습니다'류 약속 표현(의례적 표현 제외)을 자동 표시해 국감 결과보고서·시정처리 요구의 사후 점검 대상으로 추적합니다.

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env      # ASSEMBLY_API_KEY=발급받은_인증키 입력
```

> 인증키는 **코드나 저장소에 넣지 말고** `.env`(git에서 제외됨) 또는 환경변수로만 설정하세요.

## 사용법

### 1) 데이터 수집

```bash
# 22대 국회, 특정 회의일자의 농해수위 회의 목록 + 회의록 본문(PDF) 수집
python -m agrisea collect --dae 22 --date 2024-10-07 --fetch

# API 요청인자를 직접 추가(KEY=VALUE 반복 가능)
python -m agrisea collect --dae 22 --param CONF_DATE=2024 --fetch

# 이미 저장된 회의 중 본문 미수집 건만 다시 내려받기
python -m agrisea fetch-minutes --limit 50
```

- 수집 결과는 `var/agrisea.sqlite3`, 지식그래프는 `var/agrisea_kg.ttl`에 저장됩니다(`AGRISEA_DATA_DIR`로 변경 가능).
- 위원회 필터: 위원회명/회의명에 `농림축산식품해양수산위원회`(과거 명칭 포함)가 들어간 행만 저장합니다. 다른 위원회까지 받으려면 `--all-committees`.
- API가 요구하는 필수 인자(`DAE_NUM`, `CONF_DATE` 등)가 빠지면 API 오류 코드(`ERROR-300` 등)가 그대로 표시됩니다. 열린국회정보의 해당 API 명세에서 요청인자를 확인해 `--param`으로 넘기세요.
- 출력 필드명(`CONFER_NUM`, `TITLE`, `COMM_NAME`, `CONF_DATE`, `SUB_NAME`, `PDF_LINK_URL` 등)은 `agrisea/api_client.py`의 `FIELD_ALIASES`에서 별칭까지 흡수합니다. 실제 응답 필드가 다르면 여기에 추가하면 됩니다.

API 접근이 어려운 환경에서는 내려받은 회의록 PDF를 직접 적재할 수 있습니다.

```bash
python -m agrisea ingest 회의록.pdf --title "제418회 국회(정기회) 제1차 농림축산식품해양수산위원회" \
  --date 2024-10-07 --kind 국정감사 --agenda "농림축산식품부에 대한 국정감사"
```

### 2) 웹 서비스

```bash
python -m agrisea serve --port 8000     # http://127.0.0.1:8000
```

탭 구성: 대시보드 · 회의내용 검색 · 회의록·요약 · 쟁점 온톨로지 · 국감 브리핑 · SPARQL.
대시보드에서 바로 API 수집도 실행할 수 있습니다. 수집·적재용 `/api/admin/*` 엔드포인트에는 인증이 없으므로
외부에 공개할 때는 리버스 프록시 인증 등을 추가하세요(기본 바인딩은 `127.0.0.1`).

### 3) CLI

```bash
python -m agrisea search 쌀값 시장격리                 # 발언 검색
python -m agrisea summary <회의ID>                     # 회의 요약(JSON)
python -m agrisea summary <회의ID> --llm               # Claude 심층 요약(ANTHROPIC_API_KEY 필요)
python -m agrisea briefing --org 농촌진흥청 -o 브리핑.md
python -m agrisea briefing --issue sea --from 2024-01-01
python -m agrisea sparql "PREFIX ag: <https://w3id.org/agrisea/ontology#> SELECT (COUNT(?c) AS ?n) WHERE { ?c a ag:Commitment }"
python -m agrisea stats
```

### 4) 시연용 예시 데이터

```bash
python -m agrisea sample
```

`agrisea/data/sample/`의 회의록 4건은 **기능 시연·테스트용 가상 데이터**입니다(인물·수치 모두 실제와 무관, 회의명에 `[예시·가상]` 표기).
실제 분석 전에는 `var/` 폴더를 지우고 API로 수집한 데이터만 사용하세요.

## REST API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/stats` | 적재 현황 |
| GET | `/api/meetings?date_from=&date_to=&q=` | 회의 목록 |
| GET | `/api/meetings/{id}` | 회의 + 전체 발언 |
| GET | `/api/meetings/{id}/summary?llm=false` | 회의 요약(주요내용·핵심안건·질의답변·이행약속) |
| GET | `/api/search?q=&speaker_type=&date_from=&date_to=` | 발언 검색 |
| GET | `/api/briefing?org=&issue=&keyword=&member=&date_from=&date_to=&format=json\|md` | 국감 브리핑 |
| GET | `/api/issues`, `/api/taxonomy`, `/api/orgs`, `/api/speakers` | 쟁점·기관·발언자 집계 |
| GET | `/api/graph` | 쟁점–기관–위원 관계망 |
| POST | `/api/sparql` `{"query": "..."}` | 읽기 전용 SPARQL(SERVICE/갱신 구문 차단) |
| GET | `/api/ontology.ttl` | 지식그래프 전체(Turtle) |
| POST | `/api/admin/collect`, `/api/admin/sample` | 수집 / 예시 데이터 적재 |

## 테스트

```bash
python -m pytest -q
```

API 클라이언트(응답 파싱·페이지네이션·위원회 필터), 회의록 파서, 요약·브리핑, 온톨로지/SPARQL, 웹 API를 검사합니다.

## 한계와 참고

- 요약은 기본적으로 **규칙 기반 추출 요약**(키워드 밀도·도메인 용어·수치 가중)입니다. 자연스러운 서술형 요약이 필요하면 Claude 요약을 켜세요.
- 질의/답변/이행약속 판별은 문형 패턴 기반이라 오탐·누락이 있을 수 있습니다. 공식 결과보고서 작성 시 원문(PDF 링크)을 반드시 확인하세요.
- PDF 텍스트 추출 품질(띄어쓰기 손실 등)에 따라 발언자 인식률이 달라질 수 있어, 띄어쓰기 없는 표기(`◯홍길동위원`)도 처리하도록 패턴을 두었습니다.
