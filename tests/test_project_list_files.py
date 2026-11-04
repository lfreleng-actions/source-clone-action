# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Project lists read from files, for clones that name many repositories.

``--include-projects-file`` and ``--exclude-projects-file`` take one
name or glob per line.  A file that names nothing must be an error:
an empty include list means "clone everything".
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from source_clone.cli import app
from source_clone.error_codes import ExitCode
from source_clone.models import BatchResult, Config, SourceType

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ActionCloneStep

GITHUB_HOST = "github.com/example-org"

LISTING = """\
# Repositories the sweep reads
releng-reusable-workflows

*-workflows   # every workflow repository
  .github
"""


def _project_list_file() -> Any:
    """Import the module under test where each test runs."""
    return importlib.import_module("source_clone.project_list_file")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


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
        "--github-token",
        "t",
        "--output-path",
        str(output),
        "--disable-log-file",
        *extra,
    ]
    with patch("source_clone.cli.clone_repositories", side_effect=_succeed) as stub:
        result = CliRunner().invoke(app, args)
    return result.exit_code, result.output, stub


class TestReadProjectListFile:
    """One name or glob per line; comments and blanks ignored."""

    def test_comments_blanks_and_globs(self, tmp_path: Path) -> None:
        """Whole-line and trailing comments go, globs stay as written."""
        path = _write(tmp_path, "repos.txt", LISTING)

        patterns = _project_list_file().read_project_list_file(path)

        assert patterns == ["releng-reusable-workflows", "*-workflows", ".github"]

    @pytest.mark.parametrize(
        "text",
        ["", "\n\n", "# nothing yet\n", "  # indented\n\n", ",\n", " , ,\n"],
    )
    def test_a_file_naming_nothing_is_an_error(self, tmp_path: Path, text: str) -> None:
        """Comment-, blank- or separator-only files never mean 'everything'."""
        module = _project_list_file()
        path = _write(tmp_path, "empty.txt", text)

        with pytest.raises(module.ProjectListFileError, match="names no project"):
            module.read_project_list_file(path)

    def test_a_missing_file_is_an_error(self, tmp_path: Path) -> None:
        """An unreadable file says which path failed."""
        module = _project_list_file()
        path = tmp_path / "absent.txt"

        with pytest.raises(module.ProjectListFileError, match=r"absent\.txt"):
            module.read_project_list_file(path)


class TestCloneOptions:
    """clone merges the files with the patterns given directly."""

    def test_include_file_merges_with_include_projects(self, tmp_path: Path) -> None:
        """Command-line patterns first, then each file in order."""
        first = _write(tmp_path, "first.txt", LISTING)
        second = _write(tmp_path, "second.txt", "extra-repo\n*-workflows\n")

        exit_code, output, stub = _clone(
            tmp_path / "out",
            "--include-projects",
            "direct",
            "--include-projects-file",
            str(first),
            "--include-projects-file",
            str(second),
        )

        assert exit_code == 0, output
        config = stub.call_args.args[0]
        assert config.include_projects == [
            "direct",
            "releng-reusable-workflows",
            "*-workflows",
            ".github",
            "extra-repo",
        ]

    def test_exclude_file(self, tmp_path: Path) -> None:
        """--exclude-projects-file feeds the exclude list."""
        path = _write(tmp_path, "skip.txt", "# archived\nold-*\n")

        exit_code, output, stub = _clone(
            tmp_path / "out",
            "--exclude-projects",
            "legacy",
            "--exclude-projects-file",
            str(path),
        )

        assert exit_code == 0, output
        assert stub.call_args.args[0].exclude_projects == ["legacy", "old-*"]

    @pytest.mark.parametrize("option", ["include", "exclude"])
    def test_an_empty_file_stops_the_run(self, tmp_path: Path, option: str) -> None:
        """A configuration error, before anything is cloned."""
        path = _write(tmp_path, "empty.txt", "# nothing\n\n")

        exit_code, output, stub = _clone(
            tmp_path / "out", f"--{option}-projects-file", str(path)
        )

        assert exit_code == ExitCode.CONFIGURATION_ERROR, output
        stub.assert_not_called()
        assert "names no project" in " ".join(output.split())


def test_manifest_records_effective_lists(tmp_path: Path) -> None:
    """The manifest carries the include and exclude lists that applied."""
    config = Config(
        host=GITHUB_HOST,
        source_type=SourceType.GITHUB,
        github_org="example-org",
        path=tmp_path,
        include_projects=["a", "*-workflows"],
        exclude_projects=["old-*"],
    )
    batch = BatchResult(config=config, results=[], started_at=datetime.now(UTC))

    recorded = batch.to_dict()["config"]

    assert recorded["include_projects"] == ["a", "*-workflows"]
    assert recorded["exclude_projects"] == ["old-*"]


def test_manifest_records_unset_lists_as_empty(tmp_path: Path) -> None:
    """A config file's null filters serialise as empty lists."""
    unset: Any = None
    config = Config(
        host=GITHUB_HOST,
        source_type=SourceType.GITHUB,
        github_org="example-org",
        path=tmp_path,
        include_projects=unset,
        exclude_projects=unset,
    )
    batch = BatchResult(config=config, results=[], started_at=datetime.now(UTC))

    recorded = batch.to_dict()["config"]

    assert recorded["include_projects"] == []
    assert recorded["exclude_projects"] == []


class TestActionInputs:
    """include-projects-file and exclude-projects-file in the action.

    Paths are relative to the workspace and refused when they resolve
    outside it.
    """

    @pytest.mark.parametrize("kind", ["include", "exclude"])
    def test_a_workspace_file_reaches_the_cli(
        self, clone_step: ActionCloneStep, kind: str
    ) -> None:
        """The CLI gets the resolved path inside the workspace."""
        listing = clone_step.workspace / "lists" / "repos.txt"
        listing.parent.mkdir()
        listing.write_text(LISTING)

        step = clone_step.run(
            **{f"INPUT_{kind.upper()}_PROJECTS_FILE": "lists/repos.txt"}
        )

        assert step.returncode == 0, step.output
        assert step.args is not None
        option = f"--{kind}-projects-file"
        assert option in step.args
        assert step.args[step.args.index(option) + 1] == str(listing.resolve())

    @pytest.mark.parametrize("kind", ["include", "exclude"])
    @pytest.mark.parametrize("how", ["dotdot", "absolute", "symlink"])
    def test_a_path_outside_the_workspace_is_refused(
        self, clone_step: ActionCloneStep, kind: str, how: str
    ) -> None:
        """'..', an absolute path or a symlink cannot leave the workspace."""
        outside = clone_step.workspace.parent / "outside.txt"
        outside.write_text(LISTING)
        given = {
            "dotdot": "../outside.txt",
            "absolute": str(outside),
            "symlink": "link.txt",
        }[how]
        if how == "symlink":
            (clone_step.workspace / "link.txt").symlink_to(outside)

        step = clone_step.run(**{f"INPUT_{kind.upper()}_PROJECTS_FILE": given})

        assert step.returncode != 0
        assert step.args is None
        assert "outside the workspace" in step.output

    def test_a_missing_file_is_refused(self, clone_step: ActionCloneStep) -> None:
        """The step stops before the CLI runs."""
        step = clone_step.run(INPUT_INCLUDE_PROJECTS_FILE="absent.txt")

        assert step.returncode != 0
        assert step.args is None
        assert "not found" in step.output

    def test_the_cli_failing_on_an_empty_file_fails_the_step(
        self, clone_step: ActionCloneStep
    ) -> None:
        """The step exits with the CLI's status, not a masked zero."""
        (clone_step.workspace / "empty.txt").write_text("# nothing yet\n")
        status = str(int(ExitCode.CONFIGURATION_ERROR))

        step = clone_step.run(INPUT_INCLUDE_PROJECTS_FILE="empty.txt", STUB_EXIT=status)

        assert step.args is not None
        assert "--include-projects-file" in step.args
        assert step.returncode == int(status), step.output
