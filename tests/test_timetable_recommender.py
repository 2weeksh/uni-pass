from unittest.mock import MagicMock

import pytest

from app.services import schedule as sched
from app.services.timetable_recommender import (
    TimetableRecommenderService,
    CandidateOffering,
)


def make_offering(code, credits, key, schedule, section="01", building=None):
    return CandidateOffering(
        course_code=code,
        name=f"과목{code}",
        credits=credits,
        area_type=key,
        deficiency_key=key,
        section=section,
        professor="교수",
        schedule=schedule,
        building_name=building or sched.parse_building(schedule),
        slots=sched.parse_schedule(schedule),
    )


class FakeLLM:
    # 임베딩은 비활성으로 두어 공유 RagService가 의미검색(네트워크)을 타지 않게 한다.
    embeddings_enabled = False

    def __init__(self, enabled=True, result=None):
        self.enabled = enabled
        self._result = result
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        return self._result

    def embed(self, _text):
        return None


@pytest.fixture
def candidates():
    # A·C는 월1로 충돌. A+B+D = 9학점, 3개 영역 커버(최적).
    return [
        make_offering("CSE101", 3, "전공필수", "월1(공6호관 101)"),
        make_offering("CSE201", 3, "전공선택", "화2(공6호관 102)"),
        make_offering("GE110", 3, "기초교양_사고와표현", "월1(한빛관 201)"),
        make_offering("GE220", 3, "균형교양_인간과문화", "수3(인문관 301)"),
    ]


def _service(llm):
    return TimetableRecommenderService(MagicMock(), llm=llm)


def test_greedy_excludes_conflicts_and_dupes(candidates):
    svc = _service(FakeLLM(enabled=False))
    pool = svc._generate_pool(candidates, target_min=6, target_max=9,
                              prefer_no_early=False, optimize_walking=False)
    assert pool
    for tt in pool:
        # 시간 충돌 없음
        used = []
        for o in tt.offerings:
            assert not sched.has_conflict(used, o.slots)
            used.extend(o.slots)
        # 같은 과목 중복 없음
        codes = [o.course_code for o in tt.offerings]
        assert len(codes) == len(set(codes))


def test_greedy_respects_credit_range(candidates):
    svc = _service(FakeLLM(enabled=False))
    pool = svc._generate_pool(candidates, target_min=6, target_max=9,
                              prefer_no_early=False, optimize_walking=False)
    for tt in pool:
        assert 6 <= tt.total_credits <= 9


def test_best_timetable_covers_most_areas(candidates):
    svc = _service(FakeLLM(enabled=False))
    pool = svc._generate_pool(candidates, target_min=6, target_max=9,
                              prefer_no_early=False, optimize_walking=False)
    # 점수 내림차순 정렬이므로 첫 번째가 가장 많은 영역을 커버해야 함
    assert len(pool[0].covered_deficiencies) == 3


def test_recommend_fallback_without_llm(candidates, monkeypatch):
    svc = _service(FakeLLM(enabled=False))
    monkeypatch.setattr(svc, "_collect_candidates", lambda *a, **k: candidates)

    timetables, llm_used = svc.recommend(
        deficiency_map={"전공필수": 3}, target_min=6, target_max=9, num_alternatives=2
    )
    assert llm_used is False
    assert timetables
    assert all(t.rationale for t in timetables)  # 자동 사유가 채워짐


def test_recommend_uses_llm_selection(candidates, monkeypatch):
    llm = FakeLLM(enabled=True, result={"selected": [{"index": 0, "rationale": "AI 추천 사유"}]})
    svc = _service(llm)
    monkeypatch.setattr(svc, "_collect_candidates", lambda *a, **k: candidates)

    timetables, llm_used = svc.recommend(
        deficiency_map={"전공필수": 3}, target_min=6, target_max=9, num_alternatives=2
    )
    assert llm_used is True
    assert llm.calls == 1
    assert timetables[0].rationale == "AI 추천 사유"


def test_llm_invalid_selection_falls_back(candidates, monkeypatch):
    # 존재하지 않는 index만 반환 → 폴백
    llm = FakeLLM(enabled=True, result={"selected": [{"index": 999}]})
    svc = _service(llm)
    monkeypatch.setattr(svc, "_collect_candidates", lambda *a, **k: candidates)

    timetables, llm_used = svc.recommend(
        deficiency_map={"전공필수": 3}, target_min=6, target_max=9, num_alternatives=2
    )
    assert llm_used is False
    assert timetables


def test_recommend_empty_when_no_candidates(monkeypatch):
    svc = _service(FakeLLM(enabled=False))
    monkeypatch.setattr(svc, "_collect_candidates", lambda *a, **k: [])
    timetables, llm_used = svc.recommend(deficiency_map={"전공필수": 3})
    assert timetables == []
    assert llm_used is False


def test_collect_candidates_excludes_taken_and_unscheduled():
    db = MagicMock()
    svc = TimetableRecommenderService(db, llm=FakeLLM(enabled=False))

    # recommender가 두 과목을 추천한다고 가정
    svc.recommender.recommend_courses = MagicMock(return_value={
        "전공필수": [
            {"course_code": "CSE101", "name": "자료구조", "credits": 3, "area_type": "전공필수"},
            {"course_code": "CSE999", "name": "이미들음", "credits": 3, "area_type": "전공필수"},
        ]
    })

    # CSE101은 시간 있는 분반, (CSE999는 taken_codes로 제외되어 조회 안 됨)
    off = MagicMock(section="01", professor="김교수",
                    schedule="월3,수3(공6호관 101)", building_name=None, is_cancelled=False)
    db.query.return_value.filter.return_value.all.return_value = [off]

    result = svc._collect_candidates(
        {"전공필수": 3}, department="컴퓨터공학과", semester="2026-1",
        taken_codes={"CSE999"},
    )
    codes = {c.course_code for c in result}
    assert "CSE101" in codes
    assert "CSE999" not in codes
    assert result[0].slots  # 시간 슬롯 파싱됨
