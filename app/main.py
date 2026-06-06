from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import ai_agent, graduation, validator


app = FastAPI(
    title="uni-pass API",
    description="uni-pass backend documentation",
    version="0.1.0",
)

# CORS 설정 (프론트엔드 연결 시 필요)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실제 배포 시 특정 도메인으로 제한 권장
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(graduation.router)
app.include_router(validator.router)
app.include_router(ai_agent.router)

@app.get("/")
async def root():
    return {"message": "Welcome to uni-pass API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
