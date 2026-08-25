"""agent_core.genai_client -- the Gemma thought-filter (docs/SPIKE.md trap 1),
the Vertex-Gemma-unavailable fallback discovered running this work order (see
genai_client.gemma_classify's docstring), and extraction retry-on-invalid.

No real network calls: every test monkeypatches `_generate_with_retry`.
"""

from __future__ import annotations

from agent_core import genai_client


class FakePart:
    def __init__(self, text, thought=False):
        self.text = text
        self.thought = thought
        self.function_call = None
        self.function_response = None


class FakeContent:
    def __init__(self, parts):
        self.parts = parts


class FakeCandidate:
    def __init__(self, parts):
        self.content = FakeContent(parts)


class FakeResponse:
    def __init__(self, parts):
        self.candidates = [FakeCandidate(parts)]
        self.text = "".join(p.text for p in parts)


def test_document_types_match_contract_enum():
    assert genai_client.DOCUMENT_TYPES == (
        "bill",
        "itemized_bill",
        "denial_letter",
        "collection_notice",
        "gfe",
        "income_proof",
    )


def test_answer_text_filters_thought_parts():
    """docs/SPIKE.md trap 1: concatenating thought+answer restates the prompt.
    Filtering thought=True took accuracy 0/5 -> 5/5 with no prompt change."""
    resp = FakeResponse([FakePart("long chain of reasoning...", thought=True), FakePart("bill")])
    assert genai_client._answer_text(resp) == "bill"


def test_answer_text_handles_no_thought_parts():
    resp = FakeResponse([FakePart("bill")])
    assert genai_client._answer_text(resp) == "bill"


def test_answer_text_empty_candidates_falls_back_to_text_attr():
    class Empty:
        candidates = []
        text = "fallback text"

    assert genai_client._answer_text(Empty()) == "fallback text"


def test_gemma_classify_success_no_fallback(monkeypatch):
    def fake_generate(model, contents, config_obj, retries=1):
        assert model == genai_client.config.GEMMA_MODEL
        return FakeResponse([FakePart("reasoning", thought=True), FakePart("bill")])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemma_classify("a bill")
    assert result == {
        "label": "bill",
        "raw": "bill",
        "model": genai_client.config.GEMMA_MODEL,
        "error": None,
        "fallback_model_used": False,
    }


def test_gemma_classify_falls_back_to_gemini_when_gemma_unreachable(monkeypatch):
    """The finding from this work order: Gemma-4 404s via Vertex in this
    project. gemma_classify must degrade to Gemini rather than crash Reader."""
    calls = []

    def fake_generate(model, contents, config_obj, retries=1):
        calls.append(model)
        if model == genai_client.config.GEMMA_MODEL:
            raise RuntimeError("404 NOT_FOUND: publisher model not found")
        return FakeResponse([FakePart("denial_letter")])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemma_classify("a denial letter")
    assert result["label"] == "denial_letter"
    assert result["fallback_model_used"] is True
    assert result["error"] is None
    assert "gemma unavailable" in result["model"]
    assert calls == [genai_client.config.GEMMA_MODEL, genai_client.config.GEMINI_MODEL]


def test_gemma_classify_both_models_fail_returns_unknown_not_raise(monkeypatch):
    def fake_generate(model, contents, config_obj, retries=1):
        raise RuntimeError("boom")

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemma_classify("x")
    assert result["label"] == "unknown"
    assert result["fallback_model_used"] is True
    assert "boom" in result["error"]


def test_gemma_classify_unparseable_label_falls_back_to_substring_match(monkeypatch):
    def fake_generate(model, contents, config_obj, retries=1):
        return FakeResponse([FakePart("This looks like a collection_notice to me.")])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemma_classify("x")
    assert result["label"] == "collection_notice"


def test_gemini_extract_json_success(monkeypatch):
    def fake_generate(model, contents, config_obj, retries=1):
        return FakeResponse([FakePart('{"amount_cents": 4500}')])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemini_extract_json("text", {"type": "object"}, "extract")
    assert result == {"amount_cents": 4500}


def test_gemini_extract_json_invalid_then_valid_retries(monkeypatch):
    calls = {"n": 0}

    def fake_generate(model, contents, config_obj, retries=1):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse([FakePart("not json")])
        return FakeResponse([FakePart('{"ok": true}')])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemini_extract_json("text", {"type": "object"}, "extract")
    assert result == {"ok": True}
    assert calls["n"] == 2


def test_gemini_extract_json_invalid_json_after_retry_flags_error(monkeypatch):
    def fake_generate(model, contents, config_obj, retries=1):
        return FakeResponse([FakePart("still not json")])

    monkeypatch.setattr(genai_client, "_generate_with_retry", fake_generate)
    result = genai_client.gemini_extract_json("text", {"type": "object"}, "extract")
    assert "_extraction_error" in result
