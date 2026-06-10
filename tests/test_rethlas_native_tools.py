from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.generation.mcp import server
from rethlas.config import load_config
from rethlas.tools import build_generation_tool_registry


def test_generation_registry_exposes_native_math_tools():
    registry = build_generation_tool_registry(load_config())
    for name in {
        "read_run_context",
        "read_problem_reference",
        "search_math_results",
        "fetch_math_source",
        "record_math_note",
        "search_memory",
    }:
        assert name in registry.names


def test_search_math_results_normalizes_and_records(monkeypatch):
    def fake_search_arxiv_theorems(query: str, num_results: int = 10):
        return {
            "query": query,
            "count": 1,
            "results": [
                {
                    "title": "Useful Lemma",
                    "theorem": "Every finite group of prime order is cyclic.",
                    "arxiv_id": "1234.5678",
                    "theorem_id": "Lemma 2.1",
                }
            ],
            "endpoint": "fake",
        }

    monkeypatch.setattr(server, "search_arxiv_theorems", fake_search_arxiv_theorems)
    result = server.search_math_results(
        problem_id="pytest_native_tools",
        query="finite group prime order cyclic",
        purpose="lemma",
        num_results=3,
    )

    assert result["count"] == 1
    assert result["results"][0]["source_id"] == "1234.5678"
    assert result["results"][0]["source_url"] == "https://arxiv.org/abs/1234.5678"


def test_fetch_math_source_uses_cached_text():
    result = server.fetch_math_source(
        problem_id="pytest_native_tools",
        source_id="1404.4445",
        focus_query="divergence free periodic functions",
        max_chars=1200,
    )

    assert result["ok"] is True
    assert result["text_path"].replace("\\", "/") == "downloads/1404.4445.txt"
    assert result["excerpts"]
    assert result["excerpts"][0]["returned_chars"] <= 1200


def test_record_math_note_validates_required_fields():
    with pytest.raises(ValueError, match="requires field"):
        server.record_math_note(
            problem_id="pytest_native_tools",
            note_type="conclusion",
            content={"confidence": 0.5},
        )

    result = server.record_math_note(
        problem_id="pytest_native_tools",
        note_type="conclusion",
        content={"statement": "A nontrivial subgroup of a prime-order group is the whole group."},
        branch_id="root",
    )
    assert result["channel"] == "immediate_conclusions"


def test_search_memory_returns_compact_math_hits():
    server.record_math_note(
        problem_id="pytest_native_tools",
        note_type="failed_path",
        content={"reason": "The attempted proof assumed commutativity without proof."},
        branch_id="bad_branch",
    )

    result = server.search_memory(
        problem_id="pytest_native_tools",
        query="commutativity proof failed",
        note_types=["failed_path"],
        limit=3,
    )

    assert result["count"] >= 1
    hit = result["hits_by_type"]["failed_path"][0]
    assert hit["note_type"] == "failed_path"
    assert "commutativity" in hit["excerpt"]


def _fake_requests_module(*, status_code: int, body, body_is_json: bool = True):
    """Build a fake ``requests`` module whose ``post`` returns a canned
    response. Used to exercise ``verify_proof_service`` against simulated
    verifier responses without needing the real HTTP server."""
    body_text = body if isinstance(body, str) else __import__("json").dumps(body)

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = body_text

        def json(self):
            if body_is_json:
                return body
            raise ValueError("not json")

        def raise_for_status(self):
            if status_code >= 400:
                raise RuntimeError(
                    f"{status_code} Server Error: Internal Server Error for url: fake"
                )

    posted = {}

    def post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        posted["timeout"] = timeout
        return FakeResponse()

    return SimpleNamespace(post=post), posted


def test_verify_proof_service_surfaces_fastapi_detail_on_500(monkeypatch):
    """When the verifier returns 500 with FastAPI's ``{"detail": "..."}`` body,
    the client must surface that detail in its exception so callers can see
    *why* verification failed (e.g. 'verification output was not found at ...')
    instead of a generic 'Internal Server Error'."""
    fake_requests, _posted = _fake_requests_module(
        status_code=500,
        body={"detail": "verification output was not found at results/x/verification.json. See log at results/x/log.md"},
    )
    monkeypatch.setattr(server, "_requests_module", lambda: fake_requests)

    with pytest.raises(RuntimeError) as excinfo:
        server.verify_proof_service(statement="S", proof="P")

    message = str(excinfo.value)
    assert "500" in message
    assert "verification output was not found" in message
    assert "log.md" in message


def test_verify_proof_service_falls_back_to_body_text_when_not_json(monkeypatch):
    """If the verifier returns a non-JSON body (e.g. uvicorn's default HTML
    error page), the client should fall back to the raw response body rather
    than swallowing the failure detail."""
    fake_requests, _ = _fake_requests_module(
        status_code=502,
        body="<html>Bad Gateway</html>",
        body_is_json=False,
    )
    monkeypatch.setattr(server, "_requests_module", lambda: fake_requests)

    with pytest.raises(RuntimeError) as excinfo:
        server.verify_proof_service(statement="S", proof="P")

    message = str(excinfo.value)
    assert "502" in message
    assert "Bad Gateway" in message


def test_verify_proof_service_returns_payload_on_success(monkeypatch):
    """Sanity check that the 200 path is unchanged."""
    payload = {
        "verification_report": {"summary": "ok", "critical_errors": [], "gaps": []},
        "verdict": "correct",
        "repair_hints": "",
    }
    fake_requests, posted = _fake_requests_module(status_code=200, body=payload)
    monkeypatch.setattr(server, "_requests_module", lambda: fake_requests)

    result = server.verify_proof_service(statement="Stmt", proof="Proof markdown")

    assert result["verdict"] == "correct"
    assert result["verification_report"]["summary"] == "ok"
    assert posted["json"] == {"statement": "Stmt", "proof": "Proof markdown"}
