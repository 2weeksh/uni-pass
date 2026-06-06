from pydantic import BaseModel, Field
from typing import List, Optional


class GraduationQuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, description="졸업요건에 대한 질문")
    year: Optional[str] = Field(default=None, description="검색할 학년도. 예: 2025")
    department: Optional[str] = Field(default=None, description="학과명. 예: 컴퓨터공학과")
    top_k: int = Field(default=5, ge=1, le=10, description="답변에 사용할 검색 결과 수")


class RagSource(BaseModel):
    year: str
    department: Optional[str] = None
    page: int
    source: str
    score: float
    snippet: str


class GraduationQuestionResponse(BaseModel):
    answer: str
    sources: List[RagSource] = Field(default_factory=list)
