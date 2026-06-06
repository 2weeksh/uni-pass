from app.services.graduation_rag import _chunk_text, _cosine_similarity


def test_chunk_text_splits_long_text_with_overlap():
    text = "가" * 100 + "\n\n" + "나" * 100 + "\n\n" + "다" * 100

    chunks = _chunk_text(text, max_chars=180, overlap=20)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert "가" in chunks[0]


def test_cosine_similarity_returns_expected_value():
    assert _cosine_similarity([1.0, 0.0], (1.0, 0.0)) == 1.0
    assert _cosine_similarity([1.0, 0.0], (0.0, 1.0)) == 0.0
