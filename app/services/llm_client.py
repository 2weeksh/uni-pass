import json
from typing import Optional

import requests

from app.core.config import settings


class LLMClient:
    """OpenAI/Gemini JSON completion 얇은 래퍼.

    - OpenAI 키가 있으면 OpenAI를 우선 사용합니다.
    - OpenAI 키가 없고 Gemini 키가 있으면 Gemini generateContent를 사용합니다.
    - 키가 모두 없으면 enabled=False 가 되어 상위 서비스가 결정론적 폴백을 타도록 합니다.
    - complete_json()은 JSON 응답을 강제하고 dict로 파싱해 반환합니다. 실패 시 None.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        gemini_model: Optional[str] = None,
    ):
        self.api_key = api_key if api_key is not None else settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_MODEL
        self.gemini_api_key = (
            gemini_api_key if gemini_api_key is not None else settings.GEMINI_API_KEY
        )
        self.gemini_model = gemini_model or settings.GEMINI_MODEL
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.gemini_api_key)

    @property
    def provider(self) -> Optional[str]:
        if self.api_key:
            return "openai"
        if self.gemini_api_key:
            return "gemini"
        return None

    @property
    def embeddings_enabled(self) -> bool:
        """임베딩 가능 여부. 스키마가 VECTOR(1536) 고정이라 OpenAI(text-embedding-3-small)만 사용."""
        return bool(self.api_key)

    def embed(self, text: str) -> Optional[list]:
        """텍스트를 1536차원 임베딩 벡터로 변환. 키 없거나 오류 시 None (→ tsvector 폴백)."""
        if not self.api_key or not text:
            return None
        try:
            resp = self._get_client().embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=text,
            )
            return resp.data[0].embedding
        except Exception:
            return None

    def _get_client(self):
        if self._client is None:
            # 지연 임포트: openai 미설치/키 없음 환경에서도 모듈 임포트는 가능하게.
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        """system/user 프롬프트로 JSON 응답을 받아 dict로 반환. 오류 시 None."""
        if not self.enabled:
            return None

        if self.api_key:
            result = self._complete_openai_json(system, user)
            if result is not None or not self.gemini_api_key:
                return result

        return self._complete_gemini_json(system, user)

    def _complete_openai_json(self, system: str, user: str) -> Optional[dict]:
        try:
            resp = self._get_client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.4,
            )
            content = resp.choices[0].message.content
            return json.loads(content) if content else None
        except Exception:
            # 네트워크/쿼터/파싱 오류 등은 모두 폴백 신호로 처리.
            return None

    def _complete_gemini_json(self, system: str, user: str) -> Optional[dict]:
        if not self.gemini_api_key:
            return None

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"{system}\n\n"
                                "아래 사용자 요청에 대해 JSON 객체만 반환해.\n"
                                f"{user}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.4,
                "response_mime_type": "application/json",
            },
        }

        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.gemini_api_key,
                },
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            parts = data["candidates"][0]["content"]["parts"]
            content = "".join(part.get("text", "") for part in parts)
            return json.loads(content) if content else None
        except Exception:
            return None
