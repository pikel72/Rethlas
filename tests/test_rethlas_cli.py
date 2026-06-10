from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from io import BytesIO
from types import SimpleNamespace

from rethlas import cli


def _config(tmp_path):
    verification_dir = tmp_path / "verification"
    verification_dir.mkdir()
    return SimpleNamespace(
        paths=SimpleNamespace(verification_dir=verification_dir),
        verification=SimpleNamespace(
            host="127.0.0.1",
            port=8091,
            base_url="http://127.0.0.1:8091",
        ),
    )


def test_start_verifier_background_spawns_and_polls_until_healthy(tmp_path, monkeypatch):
    config = _config(tmp_path)
    health_results = iter([False, True])
    popen_calls = []

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(**kwargs):
        popen_calls.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr(cli, "verifier_health", lambda _url: next(health_results))
    monkeypatch.setattr(cli, "_verification_python", lambda _config, *, auto_setup=False: sys.executable)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ok, message = cli._start_verifier_background(config, timeout_seconds=1)

    assert ok is True
    assert message == "started verifier pid=4321"
    assert len(popen_calls) == 1
    assert popen_calls[0]["args"] == [
        sys.executable,
        "-m",
        "uvicorn",
        "api.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8091",
    ]
    assert popen_calls[0]["cwd"] == str(config.paths.verification_dir)
    assert (config.paths.verification_dir / "results" / "server" / "stdout.log").is_file()
    assert (config.paths.verification_dir / "results" / "server" / "stderr.log").is_file()


def test_start_verifier_background_reports_early_exit(tmp_path, monkeypatch):
    config = _config(tmp_path)

    class FakeProcess:
        pid = 4321

        def poll(self):
            return 3

    monkeypatch.setattr(cli, "verifier_health", lambda _url: False)
    monkeypatch.setattr(cli, "_verification_python", lambda _config, *, auto_setup=False: sys.executable)
    monkeypatch.setattr(subprocess, "Popen", lambda **_kwargs: FakeProcess())

    ok, message = cli._start_verifier_background(config, timeout_seconds=1)

    assert ok is False
    assert "verifier exited early with code 3" in message
    assert "stderr.log" in message


def test_ensure_verifier_for_run_skips_mock_provider(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        cli,
        "_start_verifier_background",
        lambda _config: (_ for _ in ()).throw(AssertionError("should not start")),
    )
    monkeypatch.setattr(cli, "verifier_health", lambda _url: False)

    assert cli._ensure_verifier_for_run(config, SimpleNamespace(provider_kind="mock")) is True


def test_ensure_verifier_for_run_starts_non_mock_provider(tmp_path, monkeypatch, capsys):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "verifier_health", lambda _url: False)
    monkeypatch.setattr(cli, "_start_verifier_background", lambda _config: (True, "started verifier pid=5"))

    assert cli._ensure_verifier_for_run(config, SimpleNamespace(provider_kind="litellm")) is True
    output = capsys.readouterr().out
    assert "verifier not reachable; starting local verification service" in output
    assert "verifier: started verifier pid=5" in output


def test_install_requirements_repairs_python_without_pip(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    requirements = tmp_path / "requirements.txt"
    python.write_text("", encoding="utf-8")
    requirements.write_text("uvicorn>=0\n", encoding="utf-8")
    can_import_results = iter([False, True])
    run_calls = []

    def fake_run(command, **kwargs):
        run_calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli, "_python_can_import", lambda _python, module: next(can_import_results) if module == "pip" else True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    cli._install_requirements(python, requirements)

    assert run_calls == [
        [str(python), "-m", "ensurepip", "--upgrade"],
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
    ]


def _fake_urlopen(payload, status=200):
    """Build a ``urllib.request.urlopen`` stand-in that returns a fake response
    with ``payload`` as the body JSON (or raw bytes)."""
    body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")

    class FakeResponse(BytesIO):
        def __init__(self):
            super().__init__(body)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()
            return False

    def fake_urlopen(url, timeout=None):
        return FakeResponse()

    return fake_urlopen


def test_verifier_status_returns_health_payload(monkeypatch):
    payload = {
        "status": "ok",
        "model_profile": "deepseek",
        "model": "deepseek-v4-pro",
        "provider": "litellm",
        "provider_kind": "litellm",
    }
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(payload))

    result = cli.verifier_status("http://127.0.0.1:8091")
    assert result == payload


def test_verifier_status_returns_none_when_unreachable(monkeypatch):
    def boom(_url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert cli.verifier_status("http://127.0.0.1:8091") is None


def test_verifier_status_returns_none_on_non_json_body(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(b"<html></html>"))
    assert cli.verifier_status("http://127.0.0.1:8091") is None


def test_verifier_banner_matches_when_models_agree(capsys):
    plan = SimpleNamespace(model_profile="deepseek")
    status = {"model_profile": "deepseek", "model": "deepseek-v4-pro", "provider": "litellm", "provider_kind": "litellm"}

    cli._print_verifier_banner("http://127.0.0.1:8091", plan, status=status)

    out = capsys.readouterr().out
    assert "verifier reachable: true" in out
    assert "verifier model: deepseek (litellm/deepseek-v4-pro)" in out
    assert "WARNING" not in out


def test_verifier_banner_warns_on_model_mismatch(capsys):
    plan = SimpleNamespace(model_profile="deepseek")
    status = {"model_profile": "gpt-5.5", "model": "gpt-5.5", "provider": "codex", "provider_kind": "codex-cli"}

    cli._print_verifier_banner("http://127.0.0.1:8091", plan, status=status)

    out = capsys.readouterr().out
    assert "verifier model: gpt-5.5 (codex/gpt-5.5)" in out
    assert "WARNING" in out
    assert "deepseek" in out  # what generation expects
    assert "gpt-5.5" in out  # what verifier is using
    assert "restart" in out.lower()


def test_verifier_banner_surfaces_model_error(capsys):
    plan = SimpleNamespace(model_profile="deepseek")
    status = {"model_profile": None, "model": None, "provider": None, "provider_kind": None, "model_error": "DEEPSEEK_API_KEY missing"}

    cli._print_verifier_banner("http://127.0.0.1:8091", plan, status=status)

    out = capsys.readouterr().out
    assert "verifier model: <unresolved>" in out
    assert "DEEPSEEK_API_KEY missing" in out


def test_verifier_banner_reports_unreachable_when_status_none(capsys):
    plan = SimpleNamespace(model_profile="deepseek")
    cli._print_verifier_banner("http://127.0.0.1:8091", plan, status=None)

    out = capsys.readouterr().out
    assert "verifier reachable: false" in out
    assert "verifier model:" not in out  # nothing to show if it's down
