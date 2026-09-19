"""Shared pytest fixtures: deterministic images and a subprocess CLI runner."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import Dict, Optional, Sequence

import pytest

import fixtures as fx

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Environment keys that would otherwise change colour/protocol decisions.
_SCRUBBED = (
    "TERM_PROGRAM",
    "GHOSTTY_BIN_DIR",
    "COLORTERM",
    "NO_COLOR",
    "COLUMNS",
    "LINES",
    "KITTY_WINDOW_ID",
    "TERM",
)


@pytest.fixture(scope="session")
def images() -> Dict[str, "object"]:
    """Every fixture image, built once per session."""

    return {name: factory() for name, factory in fx.FIXTURES.items()}


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> pathlib.Path:
    return tmp_path_factory.mktemp("ascii-art-fixtures")


@pytest.fixture(scope="session")
def paths(fixture_dir) -> Dict[str, pathlib.Path]:
    """Fixture files on disk (PNG, GIF, EXIF-tagged JPEG)."""

    return fx.write_all(fixture_dir)


class CliResult:
    def __init__(self, completed: subprocess.CompletedProcess) -> None:
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr

    @property
    def out(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def err(self) -> str:
        return self.stderr.decode("utf-8", "replace")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CliResult(rc={self.returncode}, out={self.out[:80]!r}, err={self.err[:80]!r})"


@pytest.fixture
def run():
    """Run the CLI in a subprocess with a scrubbed environment."""

    def _run(
        args: Sequence[str],
        *,
        env: Optional[Dict[str, str]] = None,
        stdin: bytes = b"",
        timeout: float = 120,
    ) -> CliResult:
        environ = dict(os.environ)
        for key in _SCRUBBED:
            environ.pop(key, None)
        if env:
            environ.update(env)
        completed = subprocess.run(
            [sys.executable, "-m", "ascii_art", *args],
            input=stdin,
            capture_output=True,
            cwd=str(REPO),
            env=environ,
            timeout=timeout,
        )
        return CliResult(completed)

    return _run


@pytest.fixture(scope="session")
def baselines() -> dict:
    import json

    path = pathlib.Path(__file__).parent / "baselines.json"
    return json.loads(path.read_text())
