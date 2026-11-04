# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""SOURCE_CLONE_TOKEN comes first, and a rejected token names its source.

A GERRIT_CLONE_TOKEN left over from Gerrit use outranks a valid
GITHUB_TOKEN.  The run then fails authentication, and the error has to
say which variable it read, without printing any part of a token.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from source_clone.cli import app
from source_clone.error_codes import ExitCode
from source_clone.github_api import GitHubAuthError
from source_clone.github_discovery import discover_github_repositories
from source_clone.github_transport import resolve_github_token
from source_clone.models import Config, SourceType

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ActionCloneStep

GITHUB_HOST = "github.com/example-org"
TOKEN_VARIABLES = ("SOURCE_CLONE_TOKEN", "GERRIT_CLONE_TOKEN", "GITHUB_TOKEN")
STALE = "stale-gerrit-clone-value"
VALID = "valid-github-value"
PREFERRED = "source-clone-value"


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every environment variable a GitHub token comes from."""
    for name in TOKEN_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _succeed(_config: Config) -> MagicMock:
    """Return an empty successful batch result."""
    now = datetime.now()
    return MagicMock(
        started_at=now,
        completed_at=now,
        total_count=0,
        success_count=0,
        failed_count=0,
        skipped_count=0,
        refreshed_count=0,
        results=[],
        success_rate=100.0,
    )


def _clone(output: Path, *extra: str) -> tuple[int, str, MagicMock]:
    """Run ``clone`` with cloning stubbed; return code, output, stub."""
    args = [
        "clone",
        "--host",
        GITHUB_HOST,
        "--output-path",
        str(output),
        "--disable-log-file",
        *extra,
    ]
    with patch("source_clone.cli.clone_repositories", side_effect=_succeed) as stub:
        result = CliRunner().invoke(app, args)
    return result.exit_code, result.output, stub


def _github_config(tmp_path: Path, **overrides: Any) -> Config:
    """Return a GitHub source configuration."""
    return Config(
        host=GITHUB_HOST,
        source_type=SourceType.GITHUB,
        github_org="example-org",
        path=tmp_path,
        **overrides,
    )


def _rejected(config: Config) -> str:
    """Run discovery against a GitHub that rejects the token."""
    api = MagicMock()
    api.__enter__.return_value = api
    api.list_owner_repos_graphql.side_effect = GitHubAuthError(
        "GitHub rejected the token (HTTP 401); check it is valid and unexpired"
    )
    with (
        patch("source_clone.github_discovery.GitHubAPI", return_value=api),
        pytest.raises(GitHubAuthError) as raised,
    ):
        discover_github_repositories(config)
    return str(raised.value)


class TestPrecedence:
    """SOURCE_CLONE_TOKEN, then GERRIT_CLONE_TOKEN, then GITHUB_TOKEN."""

    def test_source_clone_token_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SOURCE_CLONE_TOKEN beats both older variables."""
        monkeypatch.setenv("SOURCE_CLONE_TOKEN", PREFERRED)
        monkeypatch.setenv("GERRIT_CLONE_TOKEN", STALE)
        monkeypatch.setenv("GITHUB_TOKEN", VALID)

        assert resolve_github_token(None) == (PREFERRED, "SOURCE_CLONE_TOKEN")

    def test_cli_reads_source_clone_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """clone takes SOURCE_CLONE_TOKEN and records it as the source."""
        monkeypatch.setenv("SOURCE_CLONE_TOKEN", PREFERRED)
        monkeypatch.setenv("GERRIT_CLONE_TOKEN", STALE)

        exit_code, output, stub = _clone(tmp_path)

        assert exit_code == 0, output
        config = stub.call_args.args[0]
        assert config.github_token == PREFERRED
        assert config.github_token_source == "SOURCE_CLONE_TOKEN"

    def test_cli_still_reads_gerrit_clone_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Earlier action.yaml revisions export GERRIT_CLONE_TOKEN."""
        monkeypatch.setenv("GERRIT_CLONE_TOKEN", STALE)

        exit_code, output, stub = _clone(tmp_path)

        assert exit_code == 0, output
        assert stub.call_args.args[0].github_token_source == "GERRIT_CLONE_TOKEN"

    def test_missing_token_names_every_source(self, tmp_path: Path) -> None:
        """The configuration error lists SOURCE_CLONE_TOKEN too."""
        exit_code, output, stub = _clone(tmp_path)

        assert exit_code == ExitCode.CONFIGURATION_ERROR
        stub.assert_not_called()
        flat = " ".join(output.split())
        for source in ("--github-token", *TOKEN_VARIABLES):
            assert source in flat


class TestRejectedTokenNamesItsSource:
    """An authentication error says where the token came from."""

    def test_stale_gerrit_clone_token_over_valid_github_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The error names both variables and neither value."""
        monkeypatch.setenv("GERRIT_CLONE_TOKEN", STALE)
        monkeypatch.setenv("GITHUB_TOKEN", VALID)

        message = _rejected(_github_config(tmp_path))

        assert "token from GERRIT_CLONE_TOKEN" in message
        assert "GITHUB_TOKEN is also set" in message
        assert STALE not in message
        assert VALID not in message

    def test_gerrit_clone_token_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without GITHUB_TOKEN there is nothing else to point at."""
        monkeypatch.setenv("GERRIT_CLONE_TOKEN", STALE)

        message = _rejected(_github_config(tmp_path))

        assert "token from GERRIT_CLONE_TOKEN" in message
        assert "GITHUB_TOKEN" not in message
        assert STALE not in message

    @pytest.mark.parametrize("variable", TOKEN_VARIABLES)
    def test_each_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
    ) -> None:
        """A token from the environment names its variable."""
        monkeypatch.setenv(variable, PREFERRED)

        message = _rejected(_github_config(tmp_path))

        assert f"token from {variable}" in message
        assert PREFERRED not in message

    @pytest.mark.parametrize(
        "source", ["--github-token", "SOURCE_CLONE_TOKEN", "GERRIT_CLONE_TOKEN"]
    )
    def test_recorded_source_of_configured_token(
        self, tmp_path: Path, source: str
    ) -> None:
        """A token the CLI resolved keeps the source it recorded."""
        config = _github_config(
            tmp_path, github_token=PREFERRED, github_token_source=source
        )

        message = _rejected(config)

        assert f"token from {source}" in message
        assert PREFERRED not in message


def test_action_exports_source_clone_token(clone_step: ActionCloneStep) -> None:
    """The action hands its token over as SOURCE_CLONE_TOKEN.

    GERRIT_CLONE_TOKEN is set too, for CLI releases that predate
    SOURCE_CLONE_TOKEN; the token itself never reaches the log.
    """
    step = clone_step.run(INPUT_GITHUB_TOKEN=PREFERRED)

    assert step.returncode == 0, step.output
    assert step.tokens == ["SOURCE_CLONE_TOKEN", "GERRIT_CLONE_TOKEN"]
    assert step.args is not None
    assert PREFERRED not in step.args
    assert PREFERRED not in step.output
