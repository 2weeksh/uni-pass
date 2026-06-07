"""regulations 테이블 시드 스크립트.

두 가지 소스:
  1. data/regulations_seed.json — 큐레이션된 학칙 산문 (주 데이터)
  2. data/raw_requirements/*.pdf — 학과별 이수학점 한 줄 산문 (보조 데이터)

idempotent: (title, source_tag) 중복이면 skip.
content_vector는 DB GENERATED ALWAYS 컬럼이라 insert 시 포함하지 않는다.
embedding(VECTOR(1536)) 컬럼은 시드 후 backfill_embeddings()로 채운다
(OPENAI_API_KEY 없으면 자동 skip → tsvector 키워드 검색만 사용).
"""

import glob
import json
import os
import argparse
from datetime import date

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.db import Regulation
from app.services.llm_client import LLMClient
from app.services.parser import RequirementParser


def _already_exists(db, title: str, source_tag: str) -> bool:
    return (
        db.query(Regulation)
        .filter(Regulation.title == title, Regulation.source_tag == source_tag)
        .first()
        is not None
    )


def seed_from_json(db, json_path: str) -> int:
    """JSON 파일에서 학칙 산문을 읽어 insert. 삽입된 건수 반환."""
    with open(json_path, encoding="utf-8") as f:
        items = json.load(f)

    count = 0
    for item in items:
        title = item["title"]
        source_tag = item.get("source_tag", "학칙")
        if _already_exists(db, title, source_tag):
            continue
        reg = Regulation(
            title=title,
            content=item["content"],
            major=item.get("major"),
            source_tag=source_tag,
            effective_date=date.fromisoformat(item["effective_date"]),
            is_active=True,
        )
        db.add(reg)
        count += 1

    db.commit()
    return count


def seed_from_pdfs(db, pdf_dir: str) -> int:
    """졸업요건 PDF에서 학과별 이수학점을 한 줄 산문으로 변환해 insert. 삽입된 건수 반환."""
    parser = RequirementParser()
    # 모든 학과 목록을 대표 PDF에서 추출
    pdfs = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))

    # 학과명 목록 — 파서가 지원하는 대표 학과들
    target_depts = [
        "컴퓨터공학과", "전자공학과", "소프트웨어학과", "수학과", "물리학과",
        "경영학과", "경제학과", "국어국문학과", "영어영문학과",
    ]

    count = 0
    for pdf_path in pdfs:
        year_tag = os.path.basename(pdf_path).replace("이수학점표_", "").replace("학년도.pdf", "")
        try:
            admission_year = int(year_tag)
        except ValueError:
            continue

        for dept in target_depts:
            try:
                result = parser.parse(pdf_path, target_dept=dept)
            except Exception:
                continue
            if not result:
                continue

            ge = result.get("general_education", {})
            mb = result.get("major_base", {})
            total = result.get("total_credits", 130)
            tracks = result.get("tracks", {})
            primary = tracks.get("기본전공", {})

            content = (
                f"{admission_year}학번 {dept} 졸업요건: "
                f"기초교양 {ge.get('기초교양', 0)}학점, "
                f"균형교양 {ge.get('균형교양', 0)}학점, "
                f"교양계 {ge.get('교양계', 0)}학점, "
                f"전공필수 {mb.get('최소전공_필수', 0)}학점, "
                f"전공선택 {mb.get('최소전공_선택', 0)}학점, "
                f"심화전공 {primary.get('심화전공', 0)}학점, "
                f"자유선택 {primary.get('자유선택', 0)}학점, "
                f"총 {total}학점 이수 시 졸업 가능."
            )
            title = f"{admission_year}학번 {dept} 졸업 이수요건"
            source_tag = f"이수학점표_{year_tag}"

            if _already_exists(db, title, source_tag):
                continue

            reg = Regulation(
                title=title,
                content=content,
                major=dept,
                source_tag=source_tag,
                effective_date=date(admission_year, 3, 1),
                is_active=True,
            )
            db.add(reg)
            count += 1

    db.commit()
    return count


def backfill_embeddings(db, llm: LLMClient = None) -> int:
    """embedding이 비어있는 규정에 대해 임베딩을 생성·저장한다. 저장 건수 반환.

    - OPENAI_API_KEY 없으면 0건 반환(의미검색 비활성 → tsvector 폴백).
    - pgvector 미설치/embedding 컬럼 부재 등 DB 오류 시에도 graceful하게 0 처리.
    """
    llm = llm or LLMClient()
    if not llm.embeddings_enabled:
        print("  임베딩 backfill: 건너뜀 (OPENAI_API_KEY 없음 → tsvector 검색만 사용)")
        return 0

    try:
        rows = db.execute(text(
            "SELECT id, title, content FROM regulations "
            "WHERE embedding IS NULL AND is_active = TRUE"
        )).fetchall()
    except Exception:
        db.rollback()
        print("  임베딩 backfill: embedding 컬럼 조회 실패 (pgvector/스키마 확인 필요) → 건너뜀")
        return 0

    count = 0
    for row in rows:
        vec = llm.embed(f"{row.title}\n{row.content}")
        if not vec:
            continue
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        try:
            db.execute(
                text("UPDATE regulations SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": vec_literal, "id": row.id},
            )
            count += 1
        except Exception:
            db.rollback()
            print("  임베딩 backfill: UPDATE 실패 → 중단")
            return count
    db.commit()
    return count


def run(skip_pdfs: bool = False):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
    json_path = os.path.join(base_dir, "data", "regulations_seed.json")
    pdf_dir = os.path.join(base_dir, "data", "raw_requirements")

    db = SessionLocal()
    try:
        print("=== regulations 시드 시작 ===")
        n1 = seed_from_json(db, json_path)
        print(f"  학칙 산문 JSON: {n1}건 삽입")
        if skip_pdfs:
            n2 = 0
            print("  졸업요건 PDF 보조 시드: 건너뜀 (--skip-pdfs)")
        else:
            n2 = seed_from_pdfs(db, pdf_dir)
            print(f"  졸업요건 PDF 보조 시드: {n2}건 삽입")
        total = n1 + n2
        print(f"  합계: {total}건 삽입 (중복 skip 포함)")
        print(f"  현재 테이블 총 행 수: {db.query(Regulation).count()}")
        n3 = backfill_embeddings(db)
        print(f"  임베딩 backfill: {n3}건 생성 (의미검색용)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="regulations 테이블 시드")
    parser.add_argument(
        "--skip-pdfs",
        action="store_true",
        help="data/regulations_seed.json만 적재하고 raw_requirements PDF 보조 시드는 건너뜁니다.",
    )
    args = parser.parse_args()
    run(skip_pdfs=args.skip_pdfs)
