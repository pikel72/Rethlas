from __future__ import annotations

from pathlib import Path

from rethlas.config import load_config
from rethlas.events import append_event
from rethlas.viewer import (
    ResultStatus,
    _problem_status_for_viewer,
    build_results_viewer,
)


def test_problem_status_verified_when_blueprint_verified_exists(tmp_path):
    results_dir = tmp_path / "results" / "p1"
    results_dir.mkdir(parents=True)
    (results_dir / "blueprint_verified.md").write_text("# ok", encoding="utf-8")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    append_event(logs_dir / "p1", "verification_finished", {"verdict": "correct"})

    status = _problem_status_for_viewer("p1", tmp_path / "results", logs_dir)
    assert status.badge == "verified"
    assert status.latest_event_type == "verification_finished"
    assert status.has_events


def test_problem_status_draft_when_only_blueprint_exists(tmp_path):
    results_dir = tmp_path / "results" / "p1"
    results_dir.mkdir(parents=True)
    (results_dir / "blueprint.md").write_text("# draft", encoding="utf-8")
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    append_event(logs_dir / "p1", "artifact_written", {"draft_path": "x"})

    status = _problem_status_for_viewer("p1", tmp_path / "results", logs_dir)
    assert status.badge == "draft"


def test_problem_status_missing_when_no_results(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    status = _problem_status_for_viewer("p1", tmp_path / "results", logs_dir)
    assert status.badge == "missing"
    assert status.latest_event_type is None
    assert status.last_update_display == "never"


def test_problem_status_handles_no_log_dir(tmp_path):
    results_dir = tmp_path / "results" / "p1"
    results_dir.mkdir(parents=True)
    (results_dir / "blueprint.md").write_text("# draft", encoding="utf-8")
    status = _problem_status_for_viewer("p1", tmp_path / "results", tmp_path / "logs")
    assert status.badge == "draft"
    assert status.last_update_display != "never"


def test_build_results_viewer_renders_badges(tmp_path, monkeypatch):
    # Use a real config to keep the implementation honest, but redirect
    # generation_dir to a tmp path so we don't touch the real viewer.
    config = load_config()
    fake_generation = tmp_path / "generation"
    (fake_generation / "data").mkdir(parents=True)
    (fake_generation / "data" / "example.md").write_text("# example problem", encoding="utf-8")
    (fake_generation / "results" / "example").mkdir(parents=True)
    (fake_generation / "results" / "example" / "blueprint_verified.md").write_text(
        "# ok", encoding="utf-8"
    )
    logs_root = fake_generation / "logs" / "example"
    logs_root.mkdir(parents=True)
    append_event(logs_root, "run_started", {})
    append_event(logs_root, "verification_finished", {"verdict": "correct"})

    # Monkeypatch the generation dir on the existing config.
    from rethlas import config as config_module
    from dataclasses import replace

    fake_paths = replace(config.paths, generation_dir=fake_generation)
    fake_config = replace(config, paths=fake_paths)
    monkeypatch.setattr(config_module, "load_config", lambda repo_root=None: fake_config)

    # Build against the fake config and inspect the produced HTML.
    import rethlas.cli as cli_module

    monkeypatch.setattr(cli_module, "load_config", lambda repo_root=None: fake_config)
    build = build_results_viewer(fake_config)

    index_html = (build.output_dir / "index.html").read_text(encoding="utf-8")
    assert 'class="badge badge-verified">verified<' in index_html
    assert "latest event: verification_finished" in index_html
    assert "events.jsonl" in index_html
    assert build.page_count == 1


def test_result_status_dataclass_fields():
    status = ResultStatus(
        problem_id="p1",
        source=None,
        badge="missing",
        latest_event_type=None,
        latest_event_at=None,
        last_update_display="never",
    )
    assert status.problem_id == "p1"
    assert not status.has_events
