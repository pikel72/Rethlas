from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rethlas.config import load_config  # noqa: E402
from rethlas.runtime import backend_for, build_plan, build_request, missing_runtime_dependencies  # noqa: E402

WORK_DIR = REPO_ROOT.resolve()
RESULTS_ROOT = WORK_DIR / "results"

VERIFICATION_FILENAMES = ("verification.json", "verificationt.json")


class VerifyRequest(BaseModel):
    statement: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _statement_hash(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()[:12]


def generate_run_id(statement: str) -> str:
    return f"{_utc_timestamp()}_{_statement_hash(statement)}"


def _allocate_run_id(statement: str) -> str:
    base = generate_run_id(statement)
    run_id = base
    suffix = 1
    while (RESULTS_ROOT / run_id).exists():
        suffix += 1
        run_id = f"{base}_{suffix}"
    return run_id


def _results_dir(run_id: str) -> Path:
    return RESULTS_ROOT / run_id


def _log_path(run_id: str) -> Path:
    return _results_dir(run_id) / "log.md"


def _verification_path(run_id: str) -> Optional[Path]:
    for filename in VERIFICATION_FILENAMES:
        path = _results_dir(run_id) / filename
        if path.exists():
            return path
    return None


def build_prompt(run_id: str, statement: str, proof: str) -> str:
    return (
        f"Run_id: {run_id}. "
        f"Statement: {statement}. "
        f"Proof:\n{proof}\n\n"
        "Use AGENTS.md to verify the above proof for the statement."
    )


def _validate_verification_payload(payload: Dict[str, Any], path: Path) -> None:
    report = payload.get("verification_report")
    verdict = payload.get("verdict")
    repair_hints = payload.get("repair_hints")
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail=f"verification_report at {path} must be an object")
    if verdict not in {"correct", "wrong"}:
        raise HTTPException(status_code=500, detail=f"verdict at {path} must be 'correct' or 'wrong'")
    if not isinstance(repair_hints, str):
        raise HTTPException(status_code=500, detail=f"repair_hints at {path} must be a string")
    for key in ("summary", "critical_errors", "gaps"):
        if key not in report:
            raise HTTPException(status_code=500, detail=f"verification_report.{key} is missing at {path}")
    if not isinstance(report["summary"], str):
        raise HTTPException(status_code=500, detail=f"verification_report.summary at {path} must be a string")
    if not isinstance(report["critical_errors"], list) or not isinstance(report["gaps"], list):
        raise HTTPException(status_code=500, detail=f"verification findings at {path} must be lists")
    has_findings = bool(report["critical_errors"] or report["gaps"])
    if verdict == "correct" and (has_findings or repair_hints):
        raise HTTPException(
            status_code=500,
            detail=f"correct verdict at {path} must have no findings and empty repair_hints",
        )
    if verdict == "wrong" and not repair_hints.strip():
        raise HTTPException(status_code=500, detail=f"wrong verdict at {path} requires repair_hints")


def run_runtime_verification(run_id: str, statement: str, proof: str) -> Dict[str, Any]:
    results_dir = _results_dir(run_id)
    results_dir.mkdir(parents=True, exist_ok=True)
    log_path = _log_path(run_id)
    config = load_config(PROJECT_ROOT)
    prompt = build_prompt(run_id=run_id, statement=statement, proof=proof)
    request = build_request(
        config,
        role="verification",
        cwd=config.paths.verification_dir,
        prompt=prompt,
        log_path=log_path,
    )
    plan = build_plan(config, request)
    missing = missing_runtime_dependencies(plan)
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"runtime dependencies missing for {plan.provider_name}/{plan.model_profile}: {', '.join(missing)}",
        )

    try:
        result = backend_for(request.provider).run(request, stream=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime execution failed: {exc}") from exc

    verification_path = _verification_path(run_id)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(
                f"runtime {plan.provider_name}/{plan.model_profile} failed with exit code {result.returncode}. "
                f"See log at {log_path}"
            ),
        )

    if verification_path is None:
        expected_primary = _results_dir(run_id) / VERIFICATION_FILENAMES[0]
        raise HTTPException(
            status_code=500,
            detail=(
                f"verification output was not found at {expected_primary}. "
                f"See log at {log_path}"
            ),
        )

    try:
        payload = json.loads(verification_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=500,
            detail=f"verification output at {verification_path} must be a JSON object",
        )
    _validate_verification_payload(payload, verification_path)

    return payload


app = FastAPI(title="Verification Agent API", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/verify")
def verify(request: VerifyRequest) -> Dict[str, Any]:
    run_id = _allocate_run_id(request.statement)
    return run_runtime_verification(
        run_id=run_id,
        statement=request.statement,
        proof=request.proof,
    )
