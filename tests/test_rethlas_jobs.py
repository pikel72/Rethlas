from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from rethlas.jobs import (
    DEFAULT_STOP_GRACE_SECONDS,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_STOPPED,
    JOB_STATUS_STOPPING,
    Job,
    JobRegistry,
    default_jobs_dir,
    format_job_line,
    is_pid_alive,
    start_job,
    stop_job,
    utc_now_iso,
)


def _make_registry(tmp_path: Path) -> JobRegistry:
    return JobRegistry(default_jobs_dir(tmp_path))


def test_registry_roundtrip(tmp_path):
    registry = _make_registry(tmp_path)
    job = Job(
        job_id="example",
        problem_id="example",
        role="generation",
        model="mock-generation",
        pid=1234,
        status=JOB_STATUS_RUNNING,
        started_at_utc=utc_now_iso(),
        command=["python", "-m", "rethlas.cli", "run", "example"],
        log_dir="agents/generation/logs/example",
        result_dir="agents/generation/results/example",
    )
    registry.write(job)
    loaded = registry.get("example")
    assert loaded is not None
    assert loaded.pid == 1234
    assert loaded.status == JOB_STATUS_RUNNING
    assert loaded.command == job.command


def test_registry_list_orders_by_started_at_desc(tmp_path):
    registry = _make_registry(tmp_path)
    for index, started in enumerate([
        "2026-06-01T00:00:00+00:00",
        "2026-06-08T00:00:00+00:00",
        "2026-06-05T00:00:00+00:00",
    ]):
        registry.write(
            Job(
                job_id=f"job-{index}",
                problem_id=f"job-{index}",
                role="generation",
                model="mock-generation",
                pid=None,
                status=JOB_STATUS_RUNNING,
                started_at_utc=started,
                command=[],
                log_dir="",
                result_dir="",
            )
        )
    listed = registry.list()
    assert [job.job_id for job in listed] == ["job-1", "job-2", "job-0"]


def test_registry_update_marks_terminal(tmp_path):
    registry = _make_registry(tmp_path)
    registry.write(
        Job(
            job_id="example",
            problem_id="example",
            role="generation",
            model="mock-generation",
            pid=9999,
            status=JOB_STATUS_RUNNING,
            started_at_utc=utc_now_iso(),
            command=[],
            log_dir="",
            result_dir="",
        )
    )
    updated = registry.update("example", status=JOB_STATUS_STOPPED, ended_at_utc=utc_now_iso())
    assert updated is not None
    assert updated.status == JOB_STATUS_STOPPED
    assert updated.ended_at_utc is not None


def test_registry_find_running_for_problem(tmp_path):
    registry = _make_registry(tmp_path)
    registry.write(
        Job(
            job_id="ns/ns",
            problem_id="ns/ns",
            role="generation",
            model="x",
            pid=1,
            status=JOB_STATUS_RUNNING,
            started_at_utc=utc_now_iso(),
            command=[],
            log_dir="",
            result_dir="",
        )
    )
    registry.write(
        Job(
            job_id="modrep",
            problem_id="modrep",
            role="generation",
            model="x",
            pid=2,
            status=JOB_STATUS_STOPPED,
            started_at_utc=utc_now_iso(),
            command=[],
            log_dir="",
            result_dir="",
        )
    )
    found = registry.find_running_for_problem("ns/ns")
    assert found is not None
    assert found.job_id == "ns/ns"
    assert registry.find_running_for_problem("modrep") is None
    assert registry.find_running_for_problem("missing") is None


def test_format_job_line_includes_status_and_pid():
    job = Job(
        job_id="example",
        problem_id="example",
        role="generation",
        model="mock-generation",
        pid=42,
        status=JOB_STATUS_RUNNING,
        started_at_utc="2026-06-08T14:00:00+00:00",
        command=[],
        log_dir="",
        result_dir="",
    )
    line = format_job_line(job)
    assert "example" in line
    assert "running" in line
    assert "42" in line


def test_is_pid_alive_handles_invalid_inputs():
    assert is_pid_alive(None) is False
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False
    # A pid that almost certainly doesn't exist; the call must not raise.
    assert is_pid_alive(2**30) in (True, False)


def test_start_job_rejects_duplicate_running(tmp_path):
    registry = _make_registry(tmp_path)
    sleep_path = Path(sys.executable)
    sleep_args = ["-c", "import time; time.sleep(2)"]
    job = start_job(
        registry,
        job_id="dup",
        problem_id="dup",
        role="generation",
        model="mock-generation",
        command=[str(sleep_path), *sleep_args],
        log_dir=tmp_path / "logs/dup",
        result_dir=tmp_path / "results/dup",
        cwd=tmp_path,
    )
    try:
        with pytest.raises(FileExistsError):
            start_job(
                registry,
                job_id="dup",
                problem_id="dup",
                role="generation",
                model="mock-generation",
                command=[str(sleep_path), *sleep_args],
                log_dir=tmp_path / "logs/dup",
                result_dir=tmp_path / "results/dup",
                cwd=tmp_path,
            )
    finally:
        # Make sure we don't leave a sleep running.
        stop_job(registry, job.job_id, grace_seconds=1.0, hard_grace_seconds=1.0)


def test_stop_job_terminates_running_subprocess(tmp_path):
    registry = _make_registry(tmp_path)
    sleep_command = [
        sys.executable,
        "-c",
        "import time, sys; sys.stdout.write('hello\\n'); sys.stdout.flush(); time.sleep(60)",
    ]
    job = start_job(
        registry,
        job_id="stop-me",
        problem_id="stop-me",
        role="generation",
        model="mock-generation",
        command=sleep_command,
        log_dir=tmp_path / "logs/stop-me",
        result_dir=tmp_path / "results/stop-me",
        cwd=tmp_path,
    )
    # Give the child a moment to start writing.
    time.sleep(0.2)
    assert is_pid_alive(job.pid) is True
    method = stop_job(registry, "stop-me", grace_seconds=2.0, hard_grace_seconds=2.0)
    assert method in {"signal", "terminate", "kill"}
    after = registry.get("stop-me")
    assert after is not None
    assert after.status == JOB_STATUS_STOPPED
    assert after.ended_at_utc is not None


def test_stop_job_handles_dead_pid_gracefully(tmp_path):
    registry = _make_registry(tmp_path)
    registry.write(
        Job(
            job_id="ghost",
            problem_id="ghost",
            role="generation",
            model="mock-generation",
            pid=2**30,  # not running
            status=JOB_STATUS_RUNNING,
            started_at_utc=utc_now_iso(),
            command=[],
            log_dir="",
            result_dir="",
        )
    )
    method = stop_job(registry, "ghost", grace_seconds=0.5, hard_grace_seconds=0.5)
    assert method == "signal"
    after = registry.get("ghost")
    assert after is not None
    assert after.status == JOB_STATUS_STOPPED


def test_stop_job_raises_for_unknown_job(tmp_path):
    registry = _make_registry(tmp_path)
    with pytest.raises(KeyError):
        stop_job(registry, "missing")


def test_stop_job_raises_when_already_terminal(tmp_path):
    registry = _make_registry(tmp_path)
    registry.write(
        Job(
            job_id="done",
            problem_id="done",
            role="generation",
            model="mock-generation",
            pid=None,
            status=JOB_STATUS_STOPPED,
            started_at_utc=utc_now_iso(),
            ended_at_utc=utc_now_iso(),
            command=[],
            log_dir="",
            result_dir="",
        )
    )
    with pytest.raises(RuntimeError):
        stop_job(registry, "done")


def test_start_job_persists_command_and_paths(tmp_path):
    registry = _make_registry(tmp_path)
    job = start_job(
        registry,
        job_id="rec",
        problem_id="rec",
        role="generation",
        model="mock-generation",
        command=[sys.executable, "-c", "import time; time.sleep(0.5)"],
        log_dir=tmp_path / "logs/rec",
        result_dir=tmp_path / "results/rec",
        cwd=tmp_path,
    )
    try:
        persisted = registry.get("rec")
        assert persisted is not None
        assert persisted.command[0] == sys.executable
        assert persisted.log_dir == str(tmp_path / "logs/rec")
        assert persisted.result_dir == str(tmp_path / "results/rec")
        assert persisted.status == JOB_STATUS_RUNNING
    finally:
        stop_job(registry, "rec", grace_seconds=1.0, hard_grace_seconds=1.0)


def test_default_jobs_dir_under_generation(tmp_path):
    assert default_jobs_dir(tmp_path) == tmp_path / "jobs"


def test_job_is_terminal_property():
    running = Job(
        job_id="x", problem_id="x", role="g", model="m", pid=1,
        status=JOB_STATUS_RUNNING, started_at_utc=utc_now_iso(),
    )
    stopping = Job(
        job_id="x", problem_id="x", role="g", model="m", pid=1,
        status=JOB_STATUS_STOPPING, started_at_utc=utc_now_iso(),
    )
    stopped = Job(
        job_id="x", problem_id="x", role="g", model="m", pid=1,
        status=JOB_STATUS_STOPPED, started_at_utc=utc_now_iso(),
    )
    failed = Job(
        job_id="x", problem_id="x", role="g", model="m", pid=1,
        status=JOB_STATUS_FAILED, started_at_utc=utc_now_iso(),
    )
    assert not running.is_terminal
    assert not stopping.is_terminal
    assert stopped.is_terminal
    assert failed.is_terminal
