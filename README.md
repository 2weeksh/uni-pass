# 강원대학교 졸업 사정 시스템: uni-pass (Backend)
FastAPI 기반의 졸업 사정 및 수강신청 시뮬레이션 서버입니다. 고정밀 PDF 파싱 기술과 지능형 추천 로직을 통해 학생들의 졸업 준비를 돕습니다.

## 목차
- [기술 스택](#기술-스택)
- [프로젝트 구조](#프로젝트-구조)
- [아키텍처 설명](#아키텍처-설명)
- [설치 방법](#설치-방법)
- [실행 방법](#실행-방법)
- [API 문서화 (Swagger)](#api-문서화-swagger)
- [개발 가이드](#개발-가이드)

---

## 기술 스택

### Backend Framework
- **FastAPI 0.110+** - 비동기 Python 웹 프레임워크
- **Pydantic v2** - 데이터 검증 및 직렬화
- **SQLAlchemy 2.0** - Python SQL Toolkit 및 ORM

### PDF Parsing & Data Processing
- **pdfplumber 0.11+** - 정밀한 PDF 텍스트 및 표 추출 (Grid-Aware Parsing)
- **Pandas** - 엑셀 데이터 처리 및 변환
- **Python-Multipart** - 파일 업로드 처리

### Database
- **PostgreSQL 15+** - 메인 관계형 데이터베이스
- **Psycopg2-binary** - PostgreSQL 데이터베이스 어댑터

---

## 프로젝트 구조
```text
uni-pass/
├── app/
│   ├── api/
│   │   └── endpoints/       # API 라우터 (파싱, 평가 엔진)
│   ├── core/
│   │   ├── database.py      # DB 연결 및 세션 설정
│   │   └── config.py        # 환경 설정
│   ├── models/
│   │   ├── db.py            # SQLAlchemy ORM 모델
│   │   ├── graduation.py    # 졸업 요건 Pydantic 모델
│   │   └── transcript.py    # 성적표/시간표 Pydantic 모델
│   ├── services/            # 핵심 비즈니스 로직 (Service Layer)
│   │   ├── validator.py     # 졸업 사정 및 학점 분석 엔진
│   │   ├── recommender.py   # 지능형 과목 추천 로직
│   │   └── timetable_parser.py # 고정밀 시간표 파서
│   └── utils/               # 유틸리티 레이어
│       ├── transcript_parsing.py # 성적표 PDF 정밀 파싱
│       └── excel_to_db.py   # 개설 과목 데이터 적재 스크립트
├── data/                    # 엑셀 데이터 및 학칙 PDF
├── db/
│   └── schema.sql           # 원본 SQL 스키마
├── tests/                   # Pytest 테스트 코드
├── pyproject.toml           # uv 프로젝트 설정
├── requirements.txt         # pip 의존성 목록
└── README.md
```

---

## 아키텍처 설명

### 3-Layer Architecture
본 프로젝트는 관심사의 분리를 위해 3계층 아키텍처를 따릅니다:

**Request → API Layer (Endpoints) → Service Layer (Business Logic) → Data Access (ORM/Models) → Database**

1.  **API Layer (`app/api/`):** HTTP 요청을 수신하고 Service Layer를 호출하여 결과를 반환합니다.
2.  **Service Layer (`app/services/`):** 졸업 사정 룰, 추천 알고리즘, PDF 파싱 엔진 등 핵심 비즈니스 로직이 위치합니다.
3.  **Model Layer (`app/models/`):** 데이터베이스 테이블 정의 및 데이터 검증 스키마를 관리합니다.

---

## 설치 방법

### 1. 프로젝트 클론
```bash
git clone [repository-url]
cd uni-pass
```

### 2. 가상환경 및 의존성 설치
본 프로젝트는 `uv`를 사용하지만 `pip`로도 설치 가능합니다.
```bash
# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

### 3. 데이터베이스 및 데이터 적재
1.  로컬에 **PostgreSQL** 설치 후 **`unipass`** 데이터베이스를 생성합니다.
2.  `app/core/database.py`의 `DATABASE_URL` 설정을 확인합니다.
3.  초기 데이터(개설 과목)를 적재합니다:
```bash
$env:PYTHONPATH = "."; python app/utils/excel_to_db.py
```

---

## 실행 방법

### 개발 서버 실행
```bash
$env:PYTHONPATH = "."; uvicorn app.main:app --reload
```
서버가 실행되면 다음 주소로 접속 가능합니다:
- **메인 페이지:** [http://localhost:8000/](http://localhost:8000/)
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)

---

## API 문서화 (Swagger)

### 주요 엔드포인트 설명
- **`POST /api/transcript/parse`**: 성적표 PDF에서 이수 내역 자동 추출
- **`POST /api/timetable/parse`**: 시간표 PDF에서 수강 계획 자동 추출
- **`POST /api/graduation/evaluate`**: 통합 졸업 사정 분석 및 맞춤 과목 추천
- **`POST /api/agent/graduation/ask`**: 학년도별 졸업요건 PDF를 RAG로 검색하고 Gemini로 근거 기반 답변 생성

### 졸업요건 AI 에이전트
`.env`에 `GEMINI_API_KEY`를 설정한 뒤 아래처럼 질문할 수 있습니다.

```bash
curl -X POST http://localhost:8000/api/agent/graduation/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "컴퓨터공학과 2025학년도 기본전공 졸업학점은 몇 학점이야?",
    "year": "2025",
    "department": "컴퓨터공학과",
    "top_k": 5
  }'
```

선택 환경변수:
- `GEMINI_MODEL`: 답변 생성 모델. 기본값 `gemini-3.5-flash`
- `GEMINI_EMBEDDING_MODEL`: 검색 embedding 모델. 기본값 `gemini-embedding-2`

---

## 개발 가이드

### 새로운 분석 룰 추가하기
- `app/services/validator.py`의 `analyze()` 및 `_check_detailed_requirements()` 메서드에 새로운 학칙 로직을 추가합니다.

### 테스트 실행
```bash
# 전체 테스트 실행
pytest

# 성적표 파싱 테스트 실행 (data 폴더에 PDF 필요)
$env:PYTHONPATH = "."; pytest -s tests/test_transcript_parsing.py
```

---

## 장점
1.  **Zero-Input 경험:** 사용자의 수동 입력을 최소화하고 PDF 업로드만으로 모든 분석을 완료합니다.
2.  **고정밀 파싱:** Grid-Aware 알고리즘을 통해 복잡한 표 구조의 PDF를 100%에 가까운 정확도로 읽어냅니다.
3.  **지능형 추천:** 단순 분석을 넘어 부족한 학점을 채울 수 있는 실제 개설 과목을 실시간으로 제안합니다.

## 라이선스
MIT License

## 기여하기
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request
