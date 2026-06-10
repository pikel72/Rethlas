from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from fastmcp import FastMCP
except ImportError:  # pragma: no cover - dependency should be installed via requirements
    FastMCP = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"

THEOREM_SEARCH_URL = "https://leansearch.net/thm/search"
THEOREM_SEARCH_TASK = (
    "Given a math statement, retrieve useful references, such as theorems, "
    "lemmas, and definitions, that are useful for solving the given problem."
)

VERIFY_PROOF_URL = "http://127.0.0.1:8091/verify"
MAX_CONTEXT_CHARS = 12000
MAX_ITEM_CHARS = 2000
DOWNLOADS_ROOT = REPO_ROOT / "downloads"

CHANNEL_FILES: Dict[str, str] = {
    "immediate_conclusions": "immediate_conclusions.jsonl",
    "toy_examples": "toy_examples.jsonl",
    "counterexamples": "counterexamples.jsonl",
    "big_decisions": "big_decisions.jsonl",
    "subgoals": "subgoals.jsonl",
    "proof_steps": "proof_steps.jsonl",
    "failed_paths": "failed_paths.jsonl",
    "verification_reports": "verification_reports.jsonl",
    "branch_states": "branch_states.jsonl",
    "events": "events.jsonl",
}

NOTE_TYPE_CHANNELS: Dict[str, str] = {
    "conclusion": "immediate_conclusions",
    "source_note": "big_decisions",
    "subgoal": "subgoals",
    "proof_step": "proof_steps",
    "failed_path": "failed_paths",
    "decision": "big_decisions",
    "verification_report": "verification_reports",
}

NOTE_REQUIRED_FIELDS: Dict[str, set[str]] = {
    "conclusion": {"statement"},
    "source_note": {"source_id", "summary"},
    "subgoal": {"goal"},
    "proof_step": {"statement"},
    "failed_path": {"reason"},
    "decision": {"decision"},
    "verification_report": {"verdict"},
}


def _requests_module():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "The Python requests package is required for network-backed math tools. "
            "Install the generation MCP requirements or use a Python environment with requests and urllib3."
        ) from exc
    return requests


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_problem_component(raw: str) -> str:
    cleaned = re.sub(r"\s+", "_", raw.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned


def sanitize_problem_id(raw: str) -> str:
    """Return a safe problem id while preserving relative path components."""
    normalized = raw.strip().replace("\\", "/")
    parts: List[str] = []
    for part in normalized.split("/"):
        stripped = part.strip()
        if stripped in {"", "."}:
            continue
        if stripped == "..":
            raise ValueError("problem_id must not contain '..' path components")
        cleaned = _sanitize_problem_component(stripped)
        if cleaned:
            parts.append(cleaned)
    return "/".join(parts) or "problem"

def build_problem_id(source: str, identifier: str) -> str:
    return sanitize_problem_id(f"{source}_{identifier}")


def _resolve_path(path_str: str) -> Path:
    candidate = Path(path_str)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate.resolve()


def _problem_dir(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    problem_dir = (MEMORY_ROOT / sanitized_problem_id).resolve()
    memory_root = MEMORY_ROOT.resolve()
    if not problem_dir.is_relative_to(memory_root):
        raise ValueError("problem_id resolves outside memory root")
    return problem_dir


def _problem_file(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    problem_file = (REPO_ROOT / "data" / f"{sanitized_problem_id}.md").resolve()
    data_root = (REPO_ROOT / "data").resolve()
    if not problem_file.is_relative_to(data_root):
        raise ValueError("problem_id resolves outside data root")
    if not problem_file.is_file():
        raise FileNotFoundError(f"Problem file not found: data/{sanitized_problem_id}.md")
    return problem_file


def _reference_dir(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    reference_dir = (REPO_ROOT / "data" / f"{sanitized_problem_id}.refs").resolve()
    data_root = (REPO_ROOT / "data").resolve()
    if not reference_dir.is_relative_to(data_root):
        raise ValueError("problem_id resolves outside data root")
    return reference_dir


def _result_dir(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    result_dir = (REPO_ROOT / "results" / sanitized_problem_id).resolve()
    result_root = (REPO_ROOT / "results").resolve()
    if not result_dir.is_relative_to(result_root):
        raise ValueError("problem_id resolves outside results root")
    return result_dir


def _log_dir(problem_id: str) -> Path:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    log_dir = (REPO_ROOT / "logs" / sanitized_problem_id).resolve()
    log_root = (REPO_ROOT / "logs").resolve()
    if not log_dir.is_relative_to(log_root):
        raise ValueError("problem_id resolves outside logs root")
    return log_dir


def _bounded_text(text: str, max_chars: int = MAX_CONTEXT_CHARS) -> Dict[str, Any]:
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    truncated = len(text) > max_chars
    return {
        "text": text[:max_chars],
        "chars": len(text),
        "returned_chars": min(len(text), max_chars),
        "truncated": truncated,
    }


def _read_text_file(path: Path, max_chars: int) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    payload = _bounded_text(text, max_chars=max_chars)
    payload["path"] = str(path.relative_to(REPO_ROOT))
    return payload


def _latest_jsonl_entries(path: Path, limit: int = 5) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    items = list(_iter_jsonl(path))
    return items[-limit:]


def _compact_record(item: Dict[str, Any], max_chars: int = MAX_ITEM_CHARS) -> Dict[str, Any]:
    record = item.get("record", item)
    text = json.dumps(record, ensure_ascii=False)
    return {
        "timestamp_utc": item.get("timestamp_utc"),
        "excerpt": text[:max_chars],
        "chars": len(text),
        "truncated": len(text) > max_chars,
    }


def _reference_summary(reference_dir: Path, max_files: int = 8, max_chars_per_file: int = 1200) -> Dict[str, Any]:
    if not reference_dir.is_dir():
        return {"exists": False, "files": []}
    files: List[Dict[str, Any]] = []
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".tex"}:
            continue
        if len(files) >= max_files:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        files.append(
            {
                "path": str(path.relative_to(reference_dir)),
                "chars": len(text),
                "excerpt": text[:max_chars_per_file],
                "truncated": len(text) > max_chars_per_file,
            }
        )
    return {"exists": True, "files": files}


def _safe_source_id(raw: str) -> str:
    source_id = raw.strip()
    if not source_id:
        raise ValueError("source_id must be non-empty")
    source_id = source_id.removeprefix("arXiv:").removeprefix("arxiv:")
    match = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", source_id)
    if match:
        return match.group(1)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", source_id).strip("._")
    if not cleaned:
        raise ValueError("source_id does not contain a usable identifier")
    return cleaned


def _download_paths(source_id: str) -> tuple[Path, Path]:
    safe_id = _safe_source_id(source_id)
    return DOWNLOADS_ROOT / f"{safe_id}.pdf", DOWNLOADS_ROOT / f"{safe_id}.txt"


def _extract_pdf_to_text(pdf_path: Path, text_path: Path) -> Dict[str, Any]:
    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        return {"ok": False, "error": "pdftotext is not installed"}
    text_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [pdftotext, "-layout", str(pdf_path), str(text_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr.strip() or "pdftotext failed"}
    return {"ok": True, "text_path": str(text_path.relative_to(REPO_ROOT))}


def _download_arxiv_pdf(source_id: str, pdf_path: Path, timeout_seconds: int = 60) -> Dict[str, Any]:
    safe_id = _safe_source_id(source_id)
    url = f"https://arxiv.org/pdf/{safe_id}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    requests = _requests_module()
    response = requests.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    pdf_path.write_bytes(response.content)
    return {"ok": True, "url": url, "pdf_path": str(pdf_path.relative_to(REPO_ROOT))}


def _focus_excerpts(text: str, focus_query: str, max_chars: int = MAX_CONTEXT_CHARS) -> List[Dict[str, Any]]:
    terms = [token for token in _tokenize_bm25(focus_query) if len(token) >= 3]
    if not terms:
        return [{"offset": 0, **_bounded_text(text, max_chars=max_chars)}]
    lower = text.lower()
    windows: List[tuple[int, int]] = []
    window_size = max(1200, min(4000, max_chars // 2))
    for term in terms[:8]:
        idx = lower.find(term.lower())
        if idx < 0:
            continue
        start = max(0, idx - window_size // 2)
        end = min(len(text), idx + window_size // 2)
        windows.append((start, end))
    if not windows:
        return [{"offset": 0, **_bounded_text(text, max_chars=max_chars)}]

    merged: List[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1] + 200:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    excerpts: List[Dict[str, Any]] = []
    remaining = max_chars
    for start, end in merged:
        if remaining <= 0:
            break
        chunk = text[start:end]
        if len(chunk) > remaining:
            chunk = chunk[:remaining]
        excerpts.append(
            {
                "offset": start,
                "text": chunk,
                "chars": end - start,
                "returned_chars": len(chunk),
                "truncated": len(chunk) < end - start,
            }
        )
        remaining -= len(chunk)
    return excerpts


def _normalize_search_result(item: Dict[str, str], index: int, purpose: str) -> Dict[str, Any]:
    arxiv_id = item.get("arxiv_id", "")
    theorem = item.get("theorem", "")
    title = item.get("title", "")
    source_id = arxiv_id or item.get("theorem_id", "") or f"result_{index}"
    relevance_note = (
        f"Candidate {purpose} result"
        + (f" from arXiv:{arxiv_id}" if arxiv_id else "")
        + (f": {title}" if title else "")
    )
    return {
        "result_id": f"r{index}",
        "source_id": source_id,
        "title": title,
        "statement": theorem,
        "arxiv_id": arxiv_id,
        "theorem_id": item.get("theorem_id", ""),
        "source_url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
        "relevance_note": relevance_note,
    }


def _channel_path(problem_id: str, channel: str) -> Path:
    if channel not in CHANNEL_FILES:
        allowed = ", ".join(sorted(CHANNEL_FILES))
        raise ValueError(f"Unknown channel '{channel}'. Allowed channels: {allowed}")
    return _problem_dir(problem_id) / CHANNEL_FILES[channel]


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _tokenize_bm25(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]+", text.lower())


def _bm25_score_documents(
    query: str,
    documents: List[List[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    query_tokens = _tokenize_bm25(query)
    if not query_tokens or not documents:
        return [0.0 for _ in documents]

    query_term_counts = Counter(query_tokens)
    document_frequencies: Counter[str] = Counter()
    document_term_counts = [Counter(document) for document in documents]
    document_lengths = [len(document) for document in documents]
    avg_doc_length = sum(document_lengths) / len(document_lengths) if document_lengths else 0.0
    total_documents = len(documents)

    for document in documents:
        for token in set(document):
            document_frequencies[token] += 1

    scores: List[float] = []
    for doc_counts, doc_length in zip(document_term_counts, document_lengths):
        score = 0.0
        norm = k1 * (1.0 - b + b * (doc_length / avg_doc_length)) if avg_doc_length > 0 else k1
        for token, query_tf in query_term_counts.items():
            term_frequency = doc_counts.get(token, 0)
            if term_frequency <= 0:
                continue
            document_frequency = document_frequencies.get(token, 0)
            idf = math.log(1.0 + ((total_documents - document_frequency + 0.5) / (document_frequency + 0.5)))
            numerator = term_frequency * (k1 + 1.0)
            denominator = term_frequency + norm
            score += query_tf * idf * (numerator / denominator)
        scores.append(score)

    return scores
def search_arxiv_theorems(
    query: str,
    num_results: int = 10,
    endpoint: str = THEOREM_SEARCH_URL,
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    if not query.strip():
        raise ValueError("query must be non-empty")
    if num_results <= 0:
        raise ValueError("num_results must be > 0")

    payload = {
        "query": query,
        "task": THEOREM_SEARCH_TASK,
        "num_results": num_results,
    }

    requests = _requests_module()
    response = requests.post(endpoint, json=payload, timeout=timeout_seconds)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        raise ValueError("The theorem endpoint must return a JSON list")

    normalized: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": str(item.get("title", "")),
                "theorem": str(item.get("theorem", "")),
                "arxiv_id": str(item.get("arxiv_id", "")),
                "theorem_id": str(item.get("theorem_id", "")),
            }
        )

    return {
        "query": query,
        "count": len(normalized),
        "results": normalized,
        "endpoint": endpoint,
    }


def search_math_results(
    problem_id: str,
    query: str,
    purpose: str = "background",
    num_results: int = 10,
) -> Dict[str, Any]:
    allowed_purposes = {"background", "lemma", "counterexample", "definition", "repair"}
    if purpose not in allowed_purposes:
        raise ValueError(f"purpose must be one of {', '.join(sorted(allowed_purposes))}")
    raw = search_arxiv_theorems(query=query, num_results=num_results)
    results = [
        _normalize_search_result(item, index, purpose)
        for index, item in enumerate(raw.get("results", []), start=1)
    ]
    record_math_note(
        problem_id=problem_id,
        note_type="source_note",
        content={
            "source_id": "search_math_results",
            "summary": f"{purpose} search for: {query}",
            "query": query,
            "purpose": purpose,
            "count": len(results),
            "result_ids": [result["result_id"] for result in results],
            "sources": [
                {
                    "result_id": result["result_id"],
                    "source_id": result["source_id"],
                    "title": result["title"],
                    "arxiv_id": result["arxiv_id"],
                    "theorem_id": result["theorem_id"],
                }
                for result in results
            ],
        },
        branch_id="root",
    )
    return {
        "problem_id": sanitize_problem_id(problem_id),
        "query": query,
        "purpose": purpose,
        "count": len(results),
        "results": results,
        "raw_endpoint": raw.get("endpoint"),
    }


def fetch_math_source(
    problem_id: str,
    source_id: str,
    focus_query: str,
    max_chars: int = 16000,
) -> Dict[str, Any]:
    safe_id = _safe_source_id(source_id)
    pdf_path, text_path = _download_paths(safe_id)
    status: List[Dict[str, Any]] = []

    if not text_path.is_file() and pdf_path.is_file():
        status.append({"step": "extract_cached_pdf", **_extract_pdf_to_text(pdf_path, text_path)})

    if not text_path.is_file():
        try:
            status.append({"step": "download_pdf", **_download_arxiv_pdf(safe_id, pdf_path)})
        except Exception as exc:
            return {
                "problem_id": sanitize_problem_id(problem_id),
                "source_id": safe_id,
                "ok": False,
                "status": status + [{"step": "download_pdf", "ok": False, "error": str(exc)}],
            }
        status.append({"step": "extract_downloaded_pdf", **_extract_pdf_to_text(pdf_path, text_path)})

    if not text_path.is_file():
        return {
            "problem_id": sanitize_problem_id(problem_id),
            "source_id": safe_id,
            "ok": False,
            "status": status,
        }

    text = text_path.read_text(encoding="utf-8", errors="replace")
    excerpts = _focus_excerpts(text, focus_query=focus_query, max_chars=max_chars)
    payload = {
        "problem_id": sanitize_problem_id(problem_id),
        "source_id": safe_id,
        "ok": True,
        "text_path": str(text_path.relative_to(REPO_ROOT)),
        "pdf_path": str(pdf_path.relative_to(REPO_ROOT)) if pdf_path.is_file() else "",
        "focus_query": focus_query,
        "text_chars": len(text),
        "excerpts": excerpts,
        "status": status,
    }
    record_math_note(
        problem_id=problem_id,
        note_type="source_note",
        content={
            "source_id": safe_id,
            "summary": f"Fetched source context for focus query: {focus_query}",
            "text_path": payload["text_path"],
            "pdf_path": payload["pdf_path"],
            "focus_query": focus_query,
            "excerpt_count": len(excerpts),
        },
        branch_id="root",
    )
    return payload


def list_problem_references(problem_id: str) -> Dict[str, Any]:
    reference_dir = _reference_dir(problem_id)
    if not reference_dir.is_dir():
        return {
            "problem_id": sanitize_problem_id(problem_id),
            "reference_dir": str(reference_dir.relative_to(REPO_ROOT)),
            "exists": False,
            "files": [],
        }
    files: List[Dict[str, Any]] = []
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(reference_dir)
        files.append(
            {
                "path": rel.as_posix(),
                "suffix": path.suffix.lower(),
                "chars": path.stat().st_size,
                "readable": path.suffix.lower() in {".md", ".txt", ".tex", ".pdf"},
            }
        )
    return {
        "problem_id": sanitize_problem_id(problem_id),
        "reference_dir": str(reference_dir.relative_to(REPO_ROOT)),
        "exists": True,
        "files": files,
    }


def read_problem_reference(
    problem_id: str,
    relative_path: str,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> Dict[str, Any]:
    if not relative_path.strip():
        raise ValueError("relative_path must be non-empty")
    reference_dir = _reference_dir(problem_id)
    if not reference_dir.is_dir():
        raise FileNotFoundError(f"Reference directory not found for problem_id={problem_id}")
    raw_path = Path(relative_path.replace("\\", "/"))
    if raw_path.is_absolute() or any(part == ".." for part in raw_path.parts):
        raise ValueError("relative_path must stay inside the problem reference directory")
    target = (reference_dir / raw_path).resolve()
    if not target.is_relative_to(reference_dir.resolve()):
        raise ValueError("relative_path resolves outside the problem reference directory")
    if target.suffix.lower() == ".pdf":
        extracted = reference_dir / ".extracted" / raw_path.with_suffix(".txt")
        if extracted.is_file():
            target = extracted.resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Reference file not found: {relative_path}")
    if target.suffix.lower() not in {".md", ".txt", ".tex"}:
        raise ValueError("reference file must be .md, .txt, .tex, or a PDF with extracted text")
    payload = _read_text_file(target, max_chars=max_chars)
    payload.update(
        {
            "problem_id": sanitize_problem_id(problem_id),
            "requested_path": relative_path,
            "reference_dir": str(reference_dir.relative_to(REPO_ROOT)),
        }
    )
    return payload


def read_run_context(
    problem_id: str,
    include_draft: bool = True,
    include_recent_events: bool = True,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> Dict[str, Any]:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    problem_file = _problem_file(sanitized_problem_id)
    reference_dir = _reference_dir(sanitized_problem_id)
    result_dir = _result_dir(sanitized_problem_id)
    memory_dir = _problem_dir(sanitized_problem_id)
    log_dir = _log_dir(sanitized_problem_id)

    blueprint = result_dir / "blueprint.md"
    verified = result_dir / "blueprint_verified.md"
    latest_verifier = _latest_jsonl_entries(memory_dir / CHANNEL_FILES["verification_reports"], limit=1)

    memory_sections: Dict[str, List[Dict[str, Any]]] = {}
    for channel in ["failed_paths", "big_decisions", "proof_steps", "branch_states"]:
        memory_sections[channel] = [
            _compact_record(item)
            for item in _latest_jsonl_entries(memory_dir / CHANNEL_FILES[channel], limit=5)
        ]

    context: Dict[str, Any] = {
        "problem_id": sanitized_problem_id,
        "problem_file": str(problem_file.relative_to(REPO_ROOT)),
        "problem_statement": _read_text_file(problem_file, max_chars=max_chars),
        "references": _reference_summary(reference_dir),
        "results": {
            "result_dir": str(result_dir.relative_to(REPO_ROOT)),
            "blueprint_exists": blueprint.is_file(),
            "blueprint_verified_exists": verified.is_file(),
        },
        "latest_verification_report": _compact_record(latest_verifier[-1]) if latest_verifier else None,
        "memory": memory_sections,
    }
    if include_draft and blueprint.is_file():
        context["draft"] = _read_text_file(blueprint, max_chars=max_chars)
    if include_recent_events:
        context["recent_events"] = _latest_jsonl_entries(log_dir / "events.jsonl", limit=12)
    return context


def _extract_error_detail(response) -> str:
    """Pull a useful message out of a 4xx/5xx response. FastAPI returns
    ``{"detail": "..."}`` for HTTPException; we prefer that field. Fall back
    to the raw body so non-FastAPI errors (uvicorn HTML, proxy gateway pages)
    still surface their cause instead of being swallowed."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
        if detail is not None:
            return str(detail)
    text = (response.text or "").strip()
    if text:
        return text[:1000]
    return "(empty body)"


def verify_proof_service(
    statement: str,
    proof: str,
    endpoint: str = VERIFY_PROOF_URL,
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    if not statement.strip():
        raise ValueError("statement must be non-empty")
    if not isinstance(proof, str):
        raise ValueError("proof must be markdown text")
    if not proof.strip():
        raise ValueError("proof markdown must be non-empty")

    payload = {
        "statement": statement,
        "proof": proof,
    }

    requests = _requests_module()
    response = requests.post(endpoint, json=payload, timeout=timeout_seconds)
    if response.status_code >= 400:
        # ``raise_for_status`` would lose FastAPI's ``detail`` body, leaving
        # callers with only "500 Internal Server Error" and no hint about why
        # verification actually failed (e.g. a missing verification.json or a
        # codex CLI usage-limit error visible only in log.md). Surface the
        # detail so agent_loop's run_failed message is self-explanatory.
        raise RuntimeError(
            f"verification service returned {response.status_code} from {endpoint}: "
            f"{_extract_error_detail(response)}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ValueError("verification service returned non-JSON response") from exc

    if not isinstance(body, dict):
        raise ValueError("verification service must return a JSON object")

    return {
        "statement": statement,
        "verification_report": body.get("verification_report", {}),
        "verdict": body.get("verdict"),
        "repair_hints": body.get("repair_hints"),
        "endpoint": endpoint,
    }


def memory_init(
    problem_id: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sanitized_problem_id = sanitize_problem_id(problem_id)
    problem_dir = _problem_dir(sanitized_problem_id)
    problem_dir.mkdir(parents=True, exist_ok=True)

    created_files: Dict[str, str] = {}
    for channel, filename in CHANNEL_FILES.items():
        channel_path = problem_dir / filename
        channel_path.touch(exist_ok=True)
        created_files[channel] = str(channel_path)

    meta_path = problem_dir / "meta.json"
    existing_meta: Dict[str, Any] = {}
    if meta_path.exists() and meta_path.stat().st_size > 0:
        with meta_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                existing_meta = loaded

    merged_meta: Dict[str, Any] = {
        "problem_id": sanitized_problem_id,
        "created_at_utc": existing_meta.get("created_at_utc", _utc_now()),
        "updated_at_utc": _utc_now(),
    }
    merged_meta.update(existing_meta)
    if meta:
        merged_meta.update(meta)

    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(merged_meta, handle, indent=2, ensure_ascii=False)

    return {
        "problem_id": sanitized_problem_id,
        "memory_dir": str(problem_dir),
        "meta_path": str(meta_path),
        "channels": created_files,
    }


def memory_append(
    problem_id: str,
    channel: str,
    record: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")

    memory_init(problem_id)

    entry = {
        "timestamp_utc": _utc_now(),
        "channel": channel,
        "record": record,
    }
    target = _channel_path(problem_id, channel)
    _append_jsonl(target, entry)

    if channel != "events":
        event_entry = {
            "timestamp_utc": _utc_now(),
            "event_type": "memory_append",
            "channel": channel,
        }
        _append_jsonl(_channel_path(problem_id, "events"), event_entry)

    return {
        "status": "ok",
        "channel": channel,
        "path": str(target),
        "entry": entry,
    }


def memory_search(
    problem_id: str,
    query: str,
    channels: Optional[List[str]] = None,
    limit_per_channel: int = 10,
) -> Dict[str, Any]:
    if not query.strip():
        raise ValueError("query must be non-empty")
    if limit_per_channel <= 0:
        raise ValueError("limit_per_channel must be > 0")

    if channels is None:
        search_channels = [name for name in CHANNEL_FILES if name != "events"]
    else:
        search_channels = channels

    results_by_channel: Dict[str, Dict[str, Any]] = {}
    total_results = 0
    for channel in search_channels:
        path = _channel_path(problem_id, channel)
        items = list(_iter_jsonl(path))
        documents = [json.dumps(item, ensure_ascii=False) for item in items]
        tokenized_documents = [_tokenize_bm25(document) for document in documents]
        scores = _bm25_score_documents(query, tokenized_documents)

        ranked_results: List[Dict[str, Any]] = []
        for item, score in sorted(
            zip(items, scores),
            key=lambda pair: (
                -pair[1],
                pair[0].get("timestamp_utc", ""),
            ),
        ):
            if score <= 0:
                continue
            ranked_results.append(
                {
                    "score": score,
                    "item": item,
                }
            )
            if len(ranked_results) >= limit_per_channel:
                break

        results_by_channel[channel] = {
            "count": len(ranked_results),
            "results": ranked_results,
        }
        total_results += len(ranked_results)

    return {
        "problem_id": sanitize_problem_id(problem_id),
        "query": query,
        "channels": search_channels,
        "limit_per_channel": limit_per_channel,
        "count": total_results,
        "results_by_channel": results_by_channel,
    }


def record_math_note(
    problem_id: str,
    note_type: str,
    content: Dict[str, Any],
    branch_id: str = "root",
) -> Dict[str, Any]:
    if note_type not in NOTE_TYPE_CHANNELS:
        raise ValueError(f"Unknown note_type '{note_type}'. Allowed: {', '.join(sorted(NOTE_TYPE_CHANNELS))}")
    if not isinstance(content, dict):
        raise ValueError("content must be a JSON object")
    missing = sorted(NOTE_REQUIRED_FIELDS.get(note_type, set()) - set(content))
    if missing:
        raise ValueError(f"note_type '{note_type}' requires field(s): {', '.join(missing)}")
    channel = NOTE_TYPE_CHANNELS[note_type]
    record = {
        "note_type": note_type,
        "branch_id": branch_id or "root",
        **content,
    }
    result = memory_append(problem_id=problem_id, channel=channel, record=record)
    return {
        "status": "ok",
        "problem_id": sanitize_problem_id(problem_id),
        "note_type": note_type,
        "channel": channel,
        "path": result["path"],
        "record": record,
        "timestamp_utc": result["entry"]["timestamp_utc"],
    }


def search_memory(
    problem_id: str,
    query: str,
    note_types: Optional[List[str]] = None,
    limit: int = 8,
) -> Dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    selected_note_types = note_types or ["conclusion", "source_note", "subgoal", "proof_step", "failed_path", "decision", "verification_report"]
    unknown = sorted(set(selected_note_types) - set(NOTE_TYPE_CHANNELS))
    if unknown:
        raise ValueError(f"Unknown note_type(s): {', '.join(unknown)}")
    channels = sorted({NOTE_TYPE_CHANNELS[note_type] for note_type in selected_note_types})
    raw = memory_search(problem_id=problem_id, query=query, channels=channels, limit_per_channel=limit)
    hits_by_type: Dict[str, List[Dict[str, Any]]] = {note_type: [] for note_type in selected_note_types}
    channel_to_types: Dict[str, List[str]] = {}
    for note_type in selected_note_types:
        channel_to_types.setdefault(NOTE_TYPE_CHANNELS[note_type], []).append(note_type)

    for channel, payload in raw.get("results_by_channel", {}).items():
        for result in payload.get("results", []):
            item = result.get("item", {})
            record = item.get("record", {})
            note_type = record.get("note_type")
            if note_type not in selected_note_types:
                fallback_types = channel_to_types.get(channel, [])
                note_type = fallback_types[0] if fallback_types else channel
            compact = _compact_record(item)
            compact.update(
                {
                    "score": result.get("score", 0),
                    "channel": channel,
                    "note_type": note_type,
                }
            )
            hits_by_type.setdefault(note_type, []).append(compact)

    total = 0
    for note_type, hits in hits_by_type.items():
        hits.sort(key=lambda hit: -float(hit.get("score", 0)))
        del hits[limit:]
        total += len(hits)

    return {
        "problem_id": sanitize_problem_id(problem_id),
        "query": query,
        "note_types": selected_note_types,
        "limit": limit,
        "count": total,
        "hits_by_type": hits_by_type,
    }


def branch_update(
    problem_id: str,
    branch_id: str,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    payload = {
        "branch_id": branch_id,
        "state": state,
    }
    return memory_append(problem_id, "branch_states", payload)


def build_mcp_app() -> Optional[Any]:
    if FastMCP is None:
        return None

    app = FastMCP("reasoning-agent")

    @app.tool(name="search_arxiv_theorems")
    def _tool_search_arxiv_theorems(
        query: str,
        num_results: int = 10,
    ) -> Dict[str, Any]:
        return search_arxiv_theorems(query=query, num_results=num_results)

    @app.tool(name="search_math_results")
    def _tool_search_math_results(
        problem_id: str,
        query: str,
        purpose: str = "background",
        num_results: int = 10,
    ) -> Dict[str, Any]:
        return search_math_results(
            problem_id=problem_id,
            query=query,
            purpose=purpose,
            num_results=num_results,
        )

    @app.tool(name="fetch_math_source")
    def _tool_fetch_math_source(
        problem_id: str,
        source_id: str,
        focus_query: str,
        max_chars: int = 16000,
    ) -> Dict[str, Any]:
        return fetch_math_source(
            problem_id=problem_id,
            source_id=source_id,
            focus_query=focus_query,
            max_chars=max_chars,
        )

    @app.tool(name="read_run_context")
    def _tool_read_run_context(
        problem_id: str,
        include_draft: bool = True,
        include_recent_events: bool = True,
        max_chars: int = MAX_CONTEXT_CHARS,
    ) -> Dict[str, Any]:
        return read_run_context(
            problem_id=problem_id,
            include_draft=include_draft,
            include_recent_events=include_recent_events,
            max_chars=max_chars,
        )

    @app.tool(name="list_problem_references")
    def _tool_list_problem_references(problem_id: str) -> Dict[str, Any]:
        return list_problem_references(problem_id=problem_id)

    @app.tool(name="read_problem_reference")
    def _tool_read_problem_reference(
        problem_id: str,
        relative_path: str,
        max_chars: int = MAX_CONTEXT_CHARS,
    ) -> Dict[str, Any]:
        return read_problem_reference(
            problem_id=problem_id,
            relative_path=relative_path,
            max_chars=max_chars,
        )

    @app.tool(name="verify_proof_service")
    def _tool_verify_proof_service(
        statement: str,
        proof: str,
    ) -> Dict[str, Any]:
        return verify_proof_service(statement=statement, proof=proof)

    @app.tool(name="memory_init")
    def _tool_memory_init(
        problem_id: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return memory_init(problem_id=problem_id, meta=meta)

    @app.tool(name="memory_append")
    def _tool_memory_append(
        problem_id: str,
        channel: str,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        return memory_append(problem_id=problem_id, channel=channel, record=record)

    @app.tool(name="memory_search")
    def _tool_memory_search(
        problem_id: str,
        query: str,
        channels: Optional[List[str]] = None,
        limit_per_channel: int = 10,
    ) -> Dict[str, Any]:
        return memory_search(
            problem_id=problem_id,
            query=query,
            channels=channels,
            limit_per_channel=limit_per_channel,
        )

    @app.tool(name="record_math_note")
    def _tool_record_math_note(
        problem_id: str,
        note_type: str,
        content: Dict[str, Any],
        branch_id: str = "root",
    ) -> Dict[str, Any]:
        return record_math_note(
            problem_id=problem_id,
            note_type=note_type,
            content=content,
            branch_id=branch_id,
        )

    @app.tool(name="search_memory")
    def _tool_search_memory(
        problem_id: str,
        query: str,
        note_types: Optional[List[str]] = None,
        limit: int = 8,
    ) -> Dict[str, Any]:
        return search_memory(
            problem_id=problem_id,
            query=query,
            note_types=note_types,
            limit=limit,
        )

    @app.tool(name="branch_update")
    def _tool_branch_update(
        problem_id: str,
        branch_id: str,
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        return branch_update(problem_id=problem_id, branch_id=branch_id, state=state)

    return app


APP = build_mcp_app()


def main() -> None:
    if APP is None:
        raise SystemExit(
            "fastmcp is not installed. Install requirements from mcp/requirements.txt first."
        )
    APP.run()


if __name__ == "__main__":
    main()
