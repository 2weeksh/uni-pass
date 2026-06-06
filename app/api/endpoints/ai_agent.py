from fastapi import APIRouter, HTTPException

from app.models.ai_agent import GraduationQuestionRequest, GraduationQuestionResponse
from app.services.gemini_client import GeminiClientError
from app.services.graduation_rag import GraduationRagService


router = APIRouter(
    prefix="/api/agent",
    tags=["Graduation AI Agent"],
)


@router.post("/graduation/ask", response_model=GraduationQuestionResponse)
async def ask_graduation_agent(request: GraduationQuestionRequest):
    """
    졸업요건 PDF를 RAG로 검색한 뒤 Gemini가 근거 기반 답변을 생성합니다.
    """
    try:
        service = GraduationRagService()
        answer, sources = service.answer_question(
            request.question,
            year=request.year,
            department=request.department,
            top_k=request.top_k,
        )
        return GraduationQuestionResponse(answer=answer, sources=sources)
    except GeminiClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"졸업요건 AI 에이전트 오류: {str(e)}")
