"""File-backed job registry for Rethlas background runs.

A "job" is a single background run of a Rethlas command. Jobs are recorded
under ``agents/generation/jobs/{job_id}/job.json`` plus a ``stdout.log`` and
``stderr.log``. The first implementation uses ``job_id == problem_id`` and
rejects duplicate running jobs for the same problem; parallel attempts
(timestamped job ids) can be added later without changing the on-disk
shape.

This module only handles process bookkeeping — it does not understand the
content of the run. The child process writes events to
``agents/generation/logs/{problem_id}/events.jsonl`` (the existing
``events.append_event`` path) and the registry just points at that.

Cross-platform stop semantics:

- POSIX:  start the child in its own session (``start_new_session=True``) so
  signals delivered to the process group reach only this run, then escalate
  ``SIGINT`` -> ``SIGTERM`` -> ``SIGKILL``.
- Windows: start the child with ``CREATE_NEW_PROCESS_GROUP`` so the child
  has its own console group, then escalate
  ``CTRL_BREAK_EVENT`` -> ``TerminateProcess`` -> kill.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional


JOB_STATUS_RUNNING = "running"
JOB_STATUS_STOPPING = "stopping"
JOB_STATUS_STOPPED = "stopped"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"

TERMINAL_STATUSES = {
    JOB_STATUS_STOPPED,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
}

DEFAULT_STOP_GRACE_SECONDS = 3.0
DEFAULT_STOP_POLL_SECONDS = 0.1
DEFAULT_STOP_HARD_GRACE_SECONDS = 2.0


@dataclass
class Job:
    job_id: str
    problem_id: str
    role: str
    model: str
    pid: Optional[int]
    status: str
    started_at_utc: str
    ended_at_utc: Optional[str] = None
    command: List[str] = field(default_factory=list)
    log_dir: str = ""
    result_dir: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobRegistry:
    """File-backed job store. One JSON file per job under ``jobs_dir``."""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def stdout_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "stdout.log"

    def stderr_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "stderr.log"

    def get(self, job_id: str) -> Optional[Job]:
        path = self.job_path(job_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return _job_from_dict(payload)

    def list(self) -> List[Job]:
        if not self.jobs_dir.is_dir():
            return []
        jobs: List[Job] = []
        for path in sorted(self.jobs_dir.rglob("job.json")):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            jobs.append(_job_from_dict(payload))
        jobs.sort(key=lambda job: job.started_at_utc, reverse=True)
        return jobs

    def write(self, job: Job) -> None:
        target = self._job_dir(job.job_id)
        target.mkdir(parents=True, exist_ok=True)
        self.job_path(job.job_id).write_text(
            json.dumps(asdict(job), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        ended_at_utc: Optional[str] = None,
        pid: Optional[int] = None,
    ) -> Optional[Job]:
        job = self.get(job_id)
        if job is None:
            return None
        if status is not None:
            job.status = status
        if ended_at_utc is not None:
            job.ended_at_utc = ended_at_utc
        if pid is not None:
            job.pid = pid
        self.write(job)
        return job

    def find_running_for_problem(self, problem_id: str) -> Optional[Job]:
        for job in self.list():
            if job.problem_id == problem_id and not job.is_terminal:
                return job
        return None


def _job_from_dict(payload: dict) -> Job:
    extra = payload.pop("extra", None) or {}
    return Job(extra=extra, **payload)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_jobs_dir(generation_dir: Path) -> Path:
    return generation_dir / "jobs"


def start_job(
    registry: JobRegistry,
    *,
    job_id: str,
    problem_id: str,
    role: str,
    model: str,
    command: list[str],
    log_dir: Path,
    result_dir: Path,
    cwd: Path,
    env: Optional[dict] = None,
) -> Job:
    """Start a background subprocess for a job and record it in the registry.

    ``command`` should be a list of argv items (no shell). ``cwd`` is the
    working directory for the child; ``env`` is merged with ``os.environ``
    (caller-supplied keys win).
    """
    existing = registry.get(job_id)
    if existing is not None and not existing.is_terminal:
        raise FileExistsError(
            f"job {job_id!r} is already {existing.status} (pid={existing.pid})"
        )

    job_dir = registry._job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = registry.stdout_path(job_id)
    stderr_log = registry.stderr_path(job_id)
    stdout_handle = stdout_log.open("ab", buffering=0)
    stderr_handle = stderr_log.open("ab", buffering=0)
    child_env = dict(os.environ)
    if env:
        child_env.update(env)

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    popen_kwargs = {
        "args": command,
        "cwd": str(cwd),
        "env": child_env,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
        popen_kwargs["close_fds"] = True

    process = subprocess.Popen(**popen_kwargs)
    job = Job(
        job_id=job_id,
        problem_id=problem_id,
        role=role,
        model=model,
        pid=process.pid,
        status=JOB_STATUS_RUNNING,
        started_at_utc=utc_now_iso(),
        command=list(command),
        log_dir=str(log_dir),
        result_dir=str(result_dir),
    )
    registry.write(job)
    return job


def is_pid_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            # ProcessSnapshot is not always available; fall back to OpenProcess.
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        # POSIX
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    except Exception:
        return False


def stop_job(
    registry: JobRegistry,
    job_id: str,
    *,
    grace_seconds: float = DEFAULT_STOP_GRACE_SECONDS,
    hard_grace_seconds: float = DEFAULT_STOP_HARD_GRACE_SECONDS,
) -> str:
    """Stop a running job using a graceful->terminate->kill escalation.

    Returns the method that finally stopped the job: ``"signal"`` (SIGINT /
    Ctrl+Break), ``"terminate"`` (SIGTERM / ``TerminateProcess``), or
    ``"kill"`` (``SIGKILL`` / fallback). Raises ``KeyError`` if the job is
    not known and ``RuntimeError`` if the job was already terminal.
    """
    job = registry.get(job_id)
    if job is None:
        raise KeyError(f"unknown job {job_id!r}")
    if job.is_terminal:
        raise RuntimeError(f"job {job_id!r} is already {job.status}")
    if job.pid is None or not is_pid_alive(job.pid):
        registry.update(
            job_id,
            status=JOB_STATUS_STOPPED,
            ended_at_utc=utc_now_iso(),
        )
        return "signal"

    registry.update(job_id, status=JOB_STATUS_STOPPING)
    method = _send_graceful(job.pid)
    if method and _wait_for_exit(job.pid, grace_seconds):
        registry.update(
            job_id,
            status=JOB_STATUS_STOPPED,
            ended_at_utc=utc_now_iso(),
        )
        return method
    method = _send_terminate(job.pid)
    if method and _wait_for_exit(job.pid, hard_grace_seconds):
        registry.update(
            job_id,
            status=JOB_STATUS_STOPPED,
            ended_at_utc=utc_now_iso(),
        )
        return method
    _send_kill(job.pid)
    _wait_for_exit(job.pid, hard_grace_seconds)
    registry.update(
        job_id,
        status=JOB_STATUS_STOPPED,
        ended_at_utc=utc_now_iso(),
    )
    return "kill"


def _send_graceful(pid: int) -> Optional[str]:
    """Send Ctrl+C/SIGINT to the process group. Returns the method used or
    ``None`` if the signal is not supported on this platform."""
    try:
        if os.name == "nt":
            if hasattr(signal, "CTRL_BREAK_EVENT"):
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                return "signal"
            return None
        # POSIX: deliver to the whole process group so children inherit.
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return None
        try:
            os.killpg(pgid, signal.SIGINT)
        except ProcessLookupError:
            return None
        return "signal"
    except (OSError, PermissionError):
        return None


def _send_terminate(pid: int) -> Optional[str]:
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if not handle:
                return None
            try:
                if kernel32.TerminateProcess(handle, 1):
                    return "terminate"
            finally:
                kernel32.CloseHandle(handle)
            return None
        os.kill(pid, signal.SIGTERM)
        return "terminate"
    except (OSError, PermissionError, ProcessLookupError):
        return None


def _send_kill(pid: int) -> None:
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
            return
        os.kill(pid, signal.SIGKILL)
    except (OSError, PermissionError, ProcessLookupError):
        return


def _wait_for_exit(pid: int, grace_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(DEFAULT_STOP_POLL_SECONDS)
    return not is_pid_alive(pid)


def format_job_line(job: Job) -> str:
    """One-line description of a job for ``rethlas jobs`` output."""
    pid = str(job.pid) if job.pid is not None else "-"
    ended = job.ended_at_utc or "-"
    return (
        f"{job.job_id:<32} status={job.status:<10} pid={pid:<7} "
        f"model={job.model:<24} started={job.started_at_utc} ended={ended}"
    )


def list_jobs(generation_dir: Path) -> List[Job]:
    """Convenience wrapper for the CLI."""
    return JobRegistry(default_jobs_dir(generation_dir)).list()


def get_job(generation_dir: Path, job_id: str) -> Optional[Job]:
    return JobRegistry(default_jobs_dir(generation_dir)).get(job_id)


def find_jobs_dir_for_problem(generation_dir: Path, problem_id: str) -> Path:
    """Resolve a job id from a problem id, given the current single-job-per-problem
    convention. Returns the jobs dir regardless of whether the job exists.
    """
    return default_jobs_dir(generation_dir)
