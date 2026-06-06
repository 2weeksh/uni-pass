from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

import pdfplumber

from app.models.ai_agent import RagSource
from app.services.gemini_client import GeminiClient

_INDEX_CACHE: dict[tuple, List[IndexedChunk]] = {}


@dataclass(frozen=True)
class RequirementChunk:
    text: str
    year: str
    department: Optional[str]
    page: int
    source: str


@dataclass(frozen=True)
class IndexedChunk:
    chunk: RequirementChunk
    embedding: tuple[float, ...]


class GraduationRagService:
    def __init__(
        self,
        gemini_client: GeminiClient | None = None,
        requirements_dir: str | None = None,
    ):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        self.requirements_dir = requirements_dir or os.path.join(base_dir, "data", "raw_requirements")
        self.gemini_client = gemini_client or GeminiClient()

    def answer_question(
        self,
        question: str,
        year: str | None = None,
        department: str | None = None,
        top_k: int = 5,
    ) -> tuple[str, List[RagSource]]:
        indexed_chunks = self._load_index(year=year, department=department)
        if not indexed_chunks:
            raise ValueError("검색 가능한 졸업요건 PDF 내용을 찾지 못했습니다.")

        query = _prepare_query(question, year=year, department=department)
        query_embedding = self.gemini_client.embed_text(query)
        ranked = sorted(
            (
                (_cosine_similarity(query_embedding, indexed.embedding), indexed.chunk)
                for indexed in indexed_chunks
            ),
            key=lambda item: item[0],
            reverse=True,
        )[:top_k]

        sources = [
            RagSource(
                year=chunk.year,
                department=chunk.department,
                page=chunk.page,
                source=chunk.source,
                score=round(score, 4),
                snippet=_compact_text(chunk.text, max_chars=450),
            )
            for score, chunk in ranked
        ]
        prompt = self._build_prompt(question, ranked)
        return self.gemini_client.generate_answer(prompt), sources

    def _load_index(self, year: str | None, department: str | None) -> List[IndexedChunk]:
        cache_key = _cache_key(
            self.requirements_dir,
            year,
            department,
            self.gemini_client.embedding_model,
        )
        if cache_key not in _INDEX_CACHE:
            _INDEX_CACHE[cache_key] = [
                IndexedChunk(chunk=chunk, embedding=tuple(self.gemini_client.embed_text(chunk.text)))
                for chunk in build_requirement_chunks(
                    self.requirements_dir,
                    year=_normalize_year(year),
                    department=_normalize_department(department),
                )
            ]
        return _INDEX_CACHE[cache_key]

    def _build_prompt(self, question: str, ranked_chunks: List[tuple[float, RequirementChunk]]) -> str:
        context = "\n\n".join(
            (
                f"[출처 {idx}] {chunk.year}학년도 {chunk.department or '전체'} "
                f"{chunk.source} p.{chunk.page}\n{_compact_text(chunk.text, max_chars=2500)}"
            )
            for idx, (_score, chunk) in enumerate(ranked_chunks, start=1)
        )
        return f"""너는 강원대학교 졸업요건 안내 AI 에이전트야.
아래 검색된 졸업요건 문맥만 근거로 한국어로 답해.
문맥에 없는 내용은 추측하지 말고, 확인이 필요하다고 말해.
답변에는 가능한 경우 학년도, 학과, 학점 수, 전공 트랙명을 구체적으로 포함해.

질문:
{question}

검색 문맥:
{context}
"""


def build_requirement_chunks(
    requirements_dir: str,
    year: str | None = None,
    department: str | None = None,
) -> List[RequirementChunk]:
    target_year = _normalize_year(year)
    target_department = _normalize_department(department)
    chunks: List[RequirementChunk] = []

    for pdf_path in sorted(_iter_requirement_pdfs(requirements_dir)):
        pdf_year = _year_from_filename(pdf_path)
        if target_year and pdf_year != target_year:
            continue
        source = os.path.basename(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                page_chunks = _department_windows(text, target_department) if target_department else _chunk_text(text)
                for chunk_text in page_chunks:
                    chunk_department = _detect_department(chunk_text, target_department)
                    chunks.append(
                        RequirementChunk(
                            text=_prepare_document(chunk_text, source=source, page=page_index),
                            year=pdf_year or "unknown",
                            department=chunk_department or target_department,
                            page=page_index,
                            source=source,
                        )
                    )
    return chunks


def _department_windows(text: str, department: str | None) -> List[str]:
    if not department:
        return _chunk_text(text)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized_lines = [re.sub(r"\s+", "", line) for line in lines]
    windows = []
    header = "\n".join(lines[:4])
    for index, normalized_line in enumerate(normalized_lines):
        if department in normalized_line:
            start = max(0, index - 8)
            end = min(len(lines), index + 6)
            body = "\n".join(lines[start:end])
            windows.append(f"{header}\n{body}" if header and header not in body else body)
    return windows


def _cache_key(
    requirements_dir: str,
    year: str | None,
    department: str | None,
    embedding_model: str,
) -> tuple:
    pdf_meta = tuple(
        (path, os.path.getmtime(path), os.path.getsize(path))
        for path in sorted(_iter_requirement_pdfs(requirements_dir))
    )
    return (pdf_meta, _normalize_year(year), _normalize_department(department), embedding_model)


def _iter_requirement_pdfs(requirements_dir: str) -> Iterable[str]:
    if not os.path.isdir(requirements_dir):
        return []
    return (
        os.path.join(requirements_dir, filename)
        for filename in os.listdir(requirements_dir)
        if filename.lower().endswith(".pdf")
    )


def _chunk_text(text: str, max_chars: int = 1800, overlap: int = 250) -> List[str]:
    cleaned = _compact_text(text, max_chars=None)
    if not cleaned:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", cleaned) if part.strip()]
    if len(cleaned) <= max_chars:
        return [cleaned]
    if paragraphs and max(len(part) for part in paragraphs) < max_chars:
        chunks = []
        current = ""
        for paragraph in paragraphs:
            if len(current) + len(paragraph) + 2 > max_chars and current:
                chunks.append(current.strip())
                current = current[-overlap:]
            current = f"{current}\n\n{paragraph}".strip()
        if current:
            chunks.append(current.strip())
        return chunks

    chunks = []
    step = max_chars - overlap
    for start in range(0, len(cleaned), step):
        chunks.append(cleaned[start : start + max_chars].strip())
    return [chunk for chunk in chunks if chunk]


def _compact_text(text: str, max_chars: int | None = 900) -> str:
    compacted = re.sub(r"[ \t]+", " ", text)
    compacted = re.sub(r"\n{3,}", "\n\n", compacted).strip()
    if max_chars is not None and len(compacted) > max_chars:
        return compacted[: max_chars - 1].rstrip() + "..."
    return compacted


def _year_from_filename(path: str) -> str | None:
    match = re.search(r"(\d{4})학년도", os.path.basename(path))
    return match.group(1) if match else None


def _normalize_year(year: str | None) -> str | None:
    if not year:
        return None
    match = re.search(r"\d{4}", str(year))
    return match.group(0) if match else None


def _normalize_department(department: str | None) -> str | None:
    if not department:
        return None
    return re.sub(r"\s+", "", department.strip())


def _detect_department(text: str, target_department: str | None) -> str | None:
    if target_department and target_department in re.sub(r"\s+", "", text):
        return target_department
    match = re.search(r"([가-힣A-Za-z]+(?:학과|학부|전공))", text)
    return match.group(1) if match else None


def _prepare_query(question: str, year: str | None, department: str | None) -> str:
    filters = " ".join(part for part in [_normalize_year(year), _normalize_department(department)] if part)
    return f"task: question answering | query: {filters} {question}".strip()


def _prepare_document(text: str, source: str, page: int) -> str:
    return f"title: {source} p.{page} | text: {text}"


def _cosine_similarity(left: List[float], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
