import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.db import CourseOffering
from app.services.recommender import RecommenderService
from app.services.llm_client import LLMClient
from app.services.rag_service import RagService
from app.services import schedule as sched


@dataclass
class CandidateOffering:
    """추천 후보가 되는 (과목 × 분반) 단위. 시간 슬롯이 파싱된 것만 후보가 된다."""
    course_code: str
    name: str
    credits: int
    area_type: str
    deficiency_key: str            # 이 후보가 채워주는 부족 영역 키
    section: Optional[str]
    professor: Optional[str]
    schedule: Optional[str]
    building_name: Optional[str]
    slots: List[sched.TimeSlot] = field(default_factory=list)


@dataclass
class Timetable:
    offerings: List[CandidateOffering]
    rationale: str = ""

    @property
    def total_credits(self) -> int:
        return sum(o.credits for o in self.offerings)

    @property
    def covered_deficiencies(self) -> List[str]:
        # 등장 순서를 유지하며 중복 제거
        seen, out = set(), []
        for o in self.offerings:
            if o.deficiency_key not in seen:
                seen.add(o.deficiency_key)
                out.append(o.deficiency_key)
        return out

    def course_codes(self) -> frozenset:
        return frozenset(o.course_code for o in self.offerings)


class TimetableRecommenderService:
    """부족 영역 기반으로 시간 충돌 없는 추천 시간표(2~3안)를 생성한다.

    하이브리드 구조:
      - 코드가 후보 수집·시간 충돌·학점 범위 등 하드 제약을 처리해 유효 조합 풀을 만든다.
      - LLM은 풀 안에서 선택/순위 + 추천 사유만 담당한다.
      - LLM 결과는 다시 코드로 검증하고, 실패/키없음 시 결정론적 상위 N개로 폴백한다.
    """

    def __init__(self, db: Session, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or LLMClient()
        self.recommender = RecommenderService(db)
        # RAG는 추천기와 동일한 LLM 인스턴스를 공유한다 (임베딩/생성 키 일원화).
        self.rag = RagService(db, llm=self.llm)

    # ------------------------------------------------------------------ public
    def recommend(
        self,
        deficiency_map: Dict[str, object],
        department: Optional[str] = None,
        semester: str = "2026-1",
        taken_codes: Optional[set] = None,
        target_min: int = 15,
        target_max: int = 18,
        prefer_no_early: bool = False,
        optimize_walking: bool = False,
        num_alternatives: int = 3,
    ) -> Tuple[List[Timetable], bool]:
        """(추천 시간표 목록, llm_used) 반환."""
        taken_codes = taken_codes or set()

        offerings = self._collect_candidates(deficiency_map, department, semester, taken_codes)
        if not offerings:
            return [], False

        # 결정론적으로 충돌 없는 유효 조합 풀 생성 (점수 내림차순)
        pool = self._generate_pool(
            offerings, target_min, target_max, prefer_no_early, optimize_walking
        )
        if not pool:
            return [], False

        # LLM이 풀에서 선택/순위 + 사유 생성, 실패 시 결정론적 상위 N개 폴백
        selected, llm_used = self._select_with_llm(
            pool, deficiency_map, num_alternatives,
            prefer_no_early, optimize_walking, department=department,
        )
        return selected, llm_used

    # -------------------------------------------------------------- candidates
    def _collect_candidates(
        self,
        deficiency_map: Dict[str, object],
        department: Optional[str],
        semester: str,
        taken_codes: set,
    ) -> List[CandidateOffering]:
        """부족 영역별 추천 과목(RecommenderService 재사용)에 학기별 개설 분반/시간을 결합한다."""
        # {deficiency_key: [course dict, ...]}
        per_area = self.recommender.recommend_courses(deficiency_map, department)

        candidates: List[CandidateOffering] = []
        seen_section = set()  # (course_code, section) 중복 방지
        for key, courses in per_area.items():
            for c in courses:
                code = c["course_code"]
                if code in taken_codes:
                    continue
                offerings = (
                    self.db.query(CourseOffering)
                    .filter(
                        CourseOffering.course_code == code,
                        CourseOffering.semester == semester,
                        CourseOffering.is_cancelled == False,
                    )
                    .all()
                )
                for o in offerings:
                    slots = sched.parse_schedule(o.schedule)
                    if not slots:
                        # 시간 정보가 없으면 충돌 검사가 불가능하므로 제외
                        continue
                    dedup = (code, o.section)
                    if dedup in seen_section:
                        continue
                    seen_section.add(dedup)
                    candidates.append(CandidateOffering(
                        course_code=code,
                        name=c["name"],
                        credits=c["credits"],
                        area_type=c["area_type"],
                        deficiency_key=key,
                        section=o.section,
                        professor=o.professor,
                        schedule=o.schedule,
                        building_name=o.building_name or sched.parse_building(o.schedule),
                        slots=slots,
                    ))
        return candidates

    # --------------------------------------------------------------- pool gen
    def _generate_pool(
        self,
        offerings: List[CandidateOffering],
        target_min: int,
        target_max: int,
        prefer_no_early: bool,
        optimize_walking: bool,
    ) -> List[Timetable]:
        """여러 탐색 순서로 그리디 조합을 만들어 충돌 없는 유효 시간표 풀을 구성한다."""
        # 부족 영역 우선순위: 전공 → 그 외. (전공 부족이 보통 더 치명적)
        def area_priority(o: CandidateOffering) -> int:
            return 0 if o.deficiency_key.startswith("전공") else 1

        # 탐색 순서 시드들: 영역 다양성 우선 / 전공 우선 / 학점 큰 과목 우선 / 역순
        orderings = [
            sorted(offerings, key=lambda o: (area_priority(o), o.deficiency_key, -o.credits)),
            sorted(offerings, key=lambda o: (area_priority(o), -o.credits)),
            sorted(offerings, key=lambda o: (-o.credits, area_priority(o))),
            list(reversed(offerings)),
        ]

        pool: List[Timetable] = []
        seen: set = set()
        for ordered in orderings:
            tt = self._greedy(ordered, target_min, target_max)
            if tt is None:
                continue
            key = tt.course_codes()
            if key in seen:
                continue
            seen.add(key)
            pool.append(tt)

        # 점수 내림차순 정렬 (커버리지↑, 학점적정, 선호 위반↓)
        pool.sort(
            key=lambda t: self._score(t, target_min, target_max, prefer_no_early, optimize_walking),
            reverse=True,
        )
        return pool

    def _greedy(
        self, ordered: List[CandidateOffering], target_min: int, target_max: int
    ) -> Optional[Timetable]:
        chosen: List[CandidateOffering] = []
        used_codes: set = set()
        used_slots: List[sched.TimeSlot] = []
        for o in ordered:
            if o.course_code in used_codes:
                continue
            if sum(x.credits for x in chosen) + o.credits > target_max:
                continue
            if sched.has_conflict(used_slots, o.slots):
                continue
            chosen.append(o)
            used_codes.add(o.course_code)
            used_slots.extend(o.slots)
        total = sum(o.credits for o in chosen)
        if total < target_min:
            return None
        return Timetable(offerings=chosen)

    def _score(
        self,
        t: Timetable,
        target_min: int,
        target_max: int,
        prefer_no_early: bool,
        optimize_walking: bool,
    ) -> float:
        score = 0.0
        # 1순위: 채워주는 부족 영역 수
        score += 100 * len(t.covered_deficiencies)
        # 2순위: 목표 학점 범위 안이면 가점
        if target_min <= t.total_credits <= target_max:
            score += 20
        # 선호: 1교시 회피
        if prefer_no_early:
            early = sum(1 for o in t.offerings if sched.is_early_morning(o.slots))
            score -= 5 * early
        # 선호: 동선(요일별 사용 건물 수가 많을수록 감점)
        if optimize_walking:
            score -= 2 * self._building_changes(t)
        return score

    @staticmethod
    def _building_changes(t: Timetable) -> int:
        """요일별로 1개를 초과하는 사용 건물 수의 합 (이동 부담 프록시)."""
        by_day: Dict[str, set] = {}
        for o in t.offerings:
            if not o.building_name:
                continue
            for s in o.slots:
                by_day.setdefault(s.day, set()).add(o.building_name)
        return sum(max(0, len(b) - 1) for b in by_day.values())

    # ----------------------------------------------------------------- LLM
    def _select_with_llm(
        self,
        pool: List[Timetable],
        deficiency_map: Dict[str, object],
        num_alternatives: int,
        prefer_no_early: bool,
        optimize_walking: bool,
        department: Optional[str] = None,
    ) -> Tuple[List[Timetable], bool]:
        # LLM에 넘길 후보는 상위 일부로 제한 (토큰 절약)
        limited = pool[: max(num_alternatives * 3, 6)]

        if self.llm.enabled:
            result = self.llm.complete_json(
                self._llm_system_prompt(),
                self._llm_user_prompt(
                    limited, deficiency_map, num_alternatives,
                    prefer_no_early, optimize_walking, department=department,
                ),
            )
            picked = self._parse_llm_selection(result, limited)
            if picked:
                return picked[:num_alternatives], True

        # 폴백: 결정론적 상위 N개 + 자동 사유
        fallback = limited[:num_alternatives]
        for t in fallback:
            t.rationale = self._auto_rationale(t)
        return fallback, False

    def _parse_llm_selection(
        self, result: Optional[dict], limited: List[Timetable]
    ) -> List[Timetable]:
        """LLM 응답 {selected:[{index, rationale}]}를 검증해 시간표에 매핑한다."""
        if not result or "selected" not in result:
            return []
        out: List[Timetable] = []
        seen = set()
        for item in result.get("selected", []):
            try:
                idx = int(item["index"])
            except (KeyError, ValueError, TypeError):
                continue
            if idx < 0 or idx >= len(limited) or idx in seen:
                continue
            seen.add(idx)
            t = limited[idx]
            t.rationale = str(item.get("rationale", "")).strip() or self._auto_rationale(t)
            out.append(t)
        return out

    @staticmethod
    def _auto_rationale(t: Timetable) -> str:
        areas = ", ".join(t.covered_deficiencies) if t.covered_deficiencies else "선택 영역"
        return f"부족 영역({areas})을 시간 충돌 없이 채우는 {t.total_credits}학점 구성입니다."

    @staticmethod
    def _llm_system_prompt() -> str:
        return (
            "너는 대학 수강신청 어드바이저다. 제공된 후보 시간표들은 모두 시간 충돌이 없고 "
            "학점 범위를 만족하도록 코드로 사전 검증되었다. 너는 후보의 index만 골라 순위를 매기고 "
            "각 추천에 대해 한국어로 간결한 추천 사유를 작성한다. "
            "regulations_context가 제공된 경우 해당 학칙·규정 내용을 근거로 추천 사유에 반영하라. "
            "후보에 없는 과목을 만들어내지 말고, 반드시 주어진 index 중에서만 선택하라. JSON으로만 답하라."
        )

    def _llm_user_prompt(
        self,
        limited: List[Timetable],
        deficiency_map: Dict[str, object],
        num_alternatives: int,
        prefer_no_early: bool,
        optimize_walking: bool,
        department: Optional[str] = None,
    ) -> str:
        candidates = []
        for i, t in enumerate(limited):
            candidates.append({
                "index": i,
                "total_credits": t.total_credits,
                "covered_deficiencies": t.covered_deficiencies,
                "courses": [
                    {
                        "code": o.course_code,
                        "name": o.name,
                        "credits": o.credits,
                        "area": o.area_type,
                        "schedule": o.schedule,
                    }
                    for o in t.offerings
                ],
            })
        payload: Dict[str, object] = {
            "deficiency_map": deficiency_map,
            "preferences": {
                "prefer_no_early": prefer_no_early,
                "optimize_walking": optimize_walking,
            },
            "num_alternatives": num_alternatives,
            "candidates": candidates,
            "response_format": {
                "selected": [{"index": "후보 index(int)", "rationale": "한국어 추천 사유"}]
            },
        }
        # RAG 컨텍스트 주입 — 검색 결과 없으면 필드 생략
        rag_context = self.rag.build_context_for_deficiencies(deficiency_map, department=department)
        if rag_context:
            payload["regulations_context"] = rag_context

        return json.dumps(payload, ensure_ascii=False)
