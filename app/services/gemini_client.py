import os
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv


class GeminiClientError(RuntimeError):
    pass


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        generation_model: str | None = None,
        embedding_model: str | None = None,
        timeout: int = 30,
    ):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        load_dotenv(os.path.join(base_dir, ".env"))
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise GeminiClientError("GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.")

        self.generation_model = generation_model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self.embedding_model = embedding_model or os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
        self.timeout = timeout
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def embed_text(self, text: str) -> List[float]:
        url = f"{self.base_url}/{self.embedding_model}:embedContent"
        payload: Dict[str, Any] = {
            "model": f"models/{self.embedding_model}",
            "content": {"parts": [{"text": text}]},
        }
        data = self._post(url, payload)
        values = data.get("embedding", {}).get("values")
        if not isinstance(values, list):
            embeddings = data.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                values = embeddings[0].get("values")
        if not isinstance(values, list):
            raise GeminiClientError("Gemini embedding 응답에서 벡터를 찾을 수 없습니다.")
        return [float(value) for value in values]

    def generate_answer(self, prompt: str) -> str:
        url = f"{self.base_url}/{self.generation_model}:generateContent"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.9,
            },
        }
        data = self._post(url, payload)
        candidates = data.get("candidates") or []
        if not candidates:
            raise GeminiClientError("Gemini 답변 후보가 비어 있습니다.")

        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise GeminiClientError("Gemini 답변 텍스트가 비어 있습니다.")
        return text

    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise GeminiClientError(f"Gemini API 오류({response.status_code}): {response.text}")
        return response.json()
