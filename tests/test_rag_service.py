from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app.services.rag_service import RagService


class FailingSession:
    def execute(self, *_args, **_kwargs):
        raise SQLAlchemyError("database unavailable")

    def rollback(self):
        self.rolled_back = True


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class RecordingSession:
    """execute 호출을 기록하고 미리 지정한 행을 돌려주는 가짜 세션."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        return _Exec(self._rows)

    def rollback(self):
        self.rolled_back = True


class FakeLLM:
    def __init__(self, embeddings_enabled=True, vec=None):
        self.embeddings_enabled = embeddings_enabled
        self._vec = vec if vec is not None else [0.1] * 1536
        self.embed_calls = 0

    def embed(self, _text):
        self.embed_calls += 1
        return self._vec


def test_rag_search_returns_empty_list_on_database_error():
    db = FailingSession()

    results = RagService(db).search(["꿈-설계", "기초교양"], major="IT대학 컴퓨터공학과")

    assert results == []
    assert db.rolled_back is True


def test_semantic_search_skipped_when_embeddings_disabled():
    # 임베딩 비활성이면 DB를 건드리지 않고 즉시 빈 결과 (→ 키워드 폴백 신호)
    db = RecordingSession()
    svc = RagService(db, llm=FakeLLM(embeddings_enabled=False))

    results = svc.search_semantic("전공필수 부족 관련 학칙", major="컴퓨터공학과")

    assert results == []
    assert db.executed == []  # 임베딩 없으면 쿼리 자체를 안 함


def test_semantic_search_maps_vector_rows():
    rows = [SimpleNamespace(title="재수강 규정", snippet="재수강은 C+ 이하...", source_tag="학칙-재수강")]
    db = RecordingSession(rows)
    llm = FakeLLM(embeddings_enabled=True)
    svc = RagService(db, llm=llm)

    results = svc.search_semantic("재수강 어떻게 하나요", top_k=3)

    assert llm.embed_calls == 1
    assert results[0]["title"] == "재수강 규정"
    # pgvector 코사인 연산자가 들어간 쿼리가 실행됐는지 확인
    assert any("<=>" in sql for sql, _ in db.executed)


def test_build_context_falls_back_to_keyword_when_semantic_empty():
    # 임베딩 비활성 → 의미검색 빈 결과 → tsvector 키워드 검색으로 폴백해 컨텍스트 생성
    rows = [SimpleNamespace(title="전공필수 이수 규정", snippet="전공필수 과목은...", source_tag="학칙-전공이수")]
    db = RecordingSession(rows)
    svc = RagService(db, llm=FakeLLM(embeddings_enabled=False))

    context = svc.build_context_for_deficiencies({"전공필수": 6}, department="컴퓨터공학과")

    assert "전공필수 이수 규정" in context
    assert "[관련 학칙·규정]" in context
