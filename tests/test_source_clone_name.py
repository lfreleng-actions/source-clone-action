# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The tool is named ``source-clone``, and installs that command alone.

The distribution, command, help and version strings use the new name.
Every name an earlier release wrote into users' trees and repositories
keeps the old one, so trees ``gerrit-clone`` filtered stay recognised.
"""

from __future__ import annotations

from importlib.metadata import distribution, version
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from source_clone import cli, content_intent, content_legacy, content_policy
from source_clone.config import ConfigManager
from source_clone.file_logging import get_default_log_path

if TYPE_CHECKING:
    from pathlib import Path

DISTRIBUTION = "source-clone"


def _flat(text: str) -> str:
    """*text* with Rich's panel borders and line wrapping removed."""
    return " ".join(text.replace("│", " ").split())


class TestDistribution:
    """The installed distribution and its console scripts."""

    def test_the_distribution_version_resolves(self) -> None:
        assert version(DISTRIBUTION)

    def test_installs_only_source_clone(self) -> None:
        """Only ``source-clone``: the old ``gerrit-clone`` package owns that name.

        ``uv tool`` and ``pipx`` refuse to install a second tool providing
        a command another installed tool already provides, so a
        ``gerrit-clone`` alias here makes ``source-clone`` fail to
        install wherever the old package is installed.
        """
        scripts = {
            entry.name: entry.value
            for entry in distribution(DISTRIBUTION).entry_points
            if entry.group == "console_scripts"
        }
        assert scripts == {"source-clone": "source_clone.cli:app"}
        assert (
            next(
                entry
                for entry in distribution(DISTRIBUTION).entry_points
                if entry.name == "source-clone"
            ).load()
            is cli.app
        )


class TestUserFacingName:
    """Help and version output name ``source-clone``."""

    def test_version_names_source_clone(self) -> None:
        result = CliRunner().invoke(cli.app, ["--version"])
        assert result.exit_code == 0
        assert "source-clone version" in result.output

    def test_help_names_both_sources(self) -> None:
        result = CliRunner().invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        text = _flat(result.output)
        assert "Gerrit servers" in text
        assert "GitHub organisations" in text

    def test_app_is_named_source_clone(self) -> None:
        assert cli.app.info.name == "source-clone"

    def test_default_log_file_without_a_host(self) -> None:
        assert get_default_log_path().name == "source-clone.log"


class TestOnDiskNamesUnchanged:
    """Names written into trees keep ``gerrit-clone`` for compatibility."""

    def test_intent_directory(self) -> None:
        assert content_intent.INTENT_DIR == ".gerrit-clone"

    @pytest.mark.parametrize(
        ("constant", "key"),
        [
            ("_REMOVED", "gerrit-clone.filteredRemoves"),
            ("_TOKEN", "gerrit-clone.filteredTokenSha256"),
            ("_REDACTED", "gerrit-clone.filteredRedactsSecrets"),
            ("_EARLIER", "gerrit-clone.filteredByEarlierRelease"),
            ("_RECORDED", "gerrit-clone.filterPolicyRecorded"),
        ],
    )
    def test_git_config_keys(self, constant: str, key: str) -> None:
        assert getattr(content_policy, constant) == key

    def test_legacy_removal_subject(self) -> None:
        assert (
            content_legacy.LEGACY_REMOVAL_SUBJECT
            == "Remove filtered files for platform sync"
        )


class TestConfigDirectory:
    """``~/.config/source-clone/`` first, then ``~/.config/gerrit-clone/``."""

    @staticmethod
    def _write(home: Path, name: str, threads: int) -> None:
        directory = home / ".config" / name
        directory.mkdir(parents=True)
        (directory / "config.yaml").write_text(f"threads: {threads}\n")

    def test_the_old_directory_is_still_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        self._write(tmp_path, "gerrit-clone", 3)
        assert ConfigManager()._load_file_config() == {"threads": 3}

    def test_the_new_directory_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        self._write(tmp_path, "gerrit-clone", 3)
        self._write(tmp_path, "source-clone", 5)
        assert ConfigManager()._load_file_config() == {"threads": 5}
