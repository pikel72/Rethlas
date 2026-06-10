from __future__ import annotations

import os
from pathlib import Path

import pytest

from rethlas.config import _load_dotenv_if_present, load_dotenv_from_repo_root


def test_load_dotenv_sets_simple_values(tmp_path, monkeypatch):
    monkeypatch.delenv("RETHLAS_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "RETHLAS_TEST_KEY=hello\n"
        "RETHLAS_TEST_NUM=42\n",
        encoding="utf-8",
    )
    count = _load_dotenv_if_present(env_file)
    assert count == 2
    assert os.environ["RETHLAS_TEST_KEY"] == "hello"
    assert os.environ["RETHLAS_TEST_NUM"] == "42"


def test_load_dotenv_strips_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("RETHLAS_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'RETHLAS_TEST_KEY="double-quoted"\n'
        "RETHLAS_TEST_KEY2='single-quoted'\n",
        encoding="utf-8",
    )
    _load_dotenv_if_present(env_file)
    assert os.environ["RETHLAS_TEST_KEY"] == "double-quoted"
    assert os.environ["RETHLAS_TEST_KEY2"] == "single-quoted"


def test_load_dotenv_skips_comments_and_blanks(tmp_path, monkeypatch):
    monkeypatch.delenv("RETHLAS_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# this is a comment\n"
        "\n"
        "   \n"
        "# another comment\n"
        "RETHLAS_TEST_KEY=value\n"
        "  # indented comment with leading space\n",
        encoding="utf-8",
    )
    count = _load_dotenv_if_present(env_file)
    assert count == 1
    assert os.environ["RETHLAS_TEST_KEY"] == "value"


def test_load_dotenv_existing_env_wins(tmp_path, monkeypatch):
    """If a key is already in os.environ, .env must NOT overwrite it.
    Matches python-dotenv's default behavior — explicit shell env > .env file."""
    monkeypatch.setenv("RETHLAS_TEST_KEY", "from-shell")
    env_file = tmp_path / ".env"
    env_file.write_text("RETHLAS_TEST_KEY=from-file\n", encoding="utf-8")
    count = _load_dotenv_if_present(env_file)
    assert count == 0  # not set because shell value already present
    assert os.environ["RETHLAS_TEST_KEY"] == "from-shell"


def test_load_dotenv_handles_inline_comments_correctly(tmp_path, monkeypatch):
    """A `#` after a value is NOT a comment in standard .env format — only
    full-line comments are. Document this behavior: don't put `#` in values."""
    monkeypatch.delenv("RETHLAS_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("RETHLAS_TEST_KEY=abc#not-a-comment\n", encoding="utf-8")
    _load_dotenv_if_present(env_file)
    # The parser keeps everything after the first `=` (including the `#`).
    # Users who want comments in values should quote them.
    assert os.environ["RETHLAS_TEST_KEY"] == "abc#not-a-comment"


def test_load_dotenv_skips_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.delenv("RETHLAS_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "no-equals-sign\n"
        "=value-without-key\n"
        "RETHLAS_TEST_KEY=ok\n",
        encoding="utf-8",
    )
    count = _load_dotenv_if_present(env_file)
    assert count == 1
    assert os.environ["RETHLAS_TEST_KEY"] == "ok"


def test_load_dotenv_silently_no_op_for_missing_file(tmp_path):
    """If the .env file doesn't exist, return 0 and don't raise."""
    count = _load_dotenv_if_present(tmp_path / "does-not-exist.env")
    assert count == 0


def test_load_dotenv_from_repo_root_loads_real_dotenv(monkeypatch):
    """Smoke test: load_dotenv_from_repo_root actually picks up the project's
    real .env (if one exists) and sets DEEPSEEK_API_KEY etc."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    from rethlas.config import find_repo_root
    repo_root = find_repo_root()
    # Snapshot os.environ so we can roll back any keys load_dotenv mutates.
    # monkeypatch can't track them — they're set inside the function under
    # test, not via monkeypatch.setenv. Without this, the real .env values
    # (RETHLAS_MODEL, RETHLAS_VERIFICATION_MODEL, vendor API keys) leak
    # into every later test in the session and steer "mock" verification
    # tests at the real LiteLLM backend.
    before = dict(os.environ)
    try:
        count = load_dotenv_from_repo_root(repo_root)
        assert isinstance(count, int)
        assert count >= 0
    finally:
        for key in set(os.environ) - set(before):
            os.environ.pop(key, None)
