# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""``clone`` makes working copies by default; ``--mirror`` opts in.

Every route that asked for a bare mirror before still gets one: the
``--mirror`` flag, ``GERRIT_MIRROR=true`` and ``mirror: true`` in a
config file.  The ``mirror`` command keeps cloning bare mirrors, since
it pushes with ``git push --mirror``.  An existing bare mirror is
refreshed as one, never converted, and the run says so once.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from source_clone import cli_options_mirror
from source_clone.cli import app
from source_clone.cli_mirror_setup import build_config as build_mirror_config
from source_clone.clone_utils import build_base_clone_command
from source_clone.config import ConfigManager
from source_clone.github_gh_cli import _build_gh_clone_command
from source_clone.models import Config, Project, ProjectState, SourceType
from tests.test_clone_fresh_checkout import _cloning_from, _commit, _git

if TYPE_CHECKING:
    from pathlib import Path

GERRIT_HOST = "gerrit.example.org"
GITHUB_HOST = "github.com/example-org"

#: Marks a test that holds once clone makes working copies by default.
working_copy_default = pytest.mark.xfail(
    strict=True, reason="clone still makes bare mirrors by default"
)

#: The phrase the existing-mirror notice is counted by.
NOTICE = "existing bare mirror(s)"


@pytest.fixture(autouse=True)
def _no_mirror_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GERRIT_MIRROR", raising=False)


def _github_config(tmp_path: Path, **kwargs: Any) -> Config:
    return Config(
        host=GITHUB_HOST,
        source_type=SourceType.GITHUB,
        github_org="example-org",
        path=tmp_path,
        **kwargs,
    )


def _github_project() -> Project:
    return Project(
        name="example-org/repo",
        state=ProjectState.ACTIVE,
        source_type=SourceType.GITHUB,
    )


def _flat(text: str) -> str:
    """*text* without Rich's colours, box drawing and line wrapping."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return re.sub(r"\s+", " ", re.sub(r"[│╭╮╰╯─]", " ", text))


def _is_bare(repo: Path) -> bool:
    return _git("rev-parse", "--is-bare-repository", cwd=repo) == "true"


class TestDefault:
    @working_copy_default
    def test_config_defaults_to_working_copies(self) -> None:
        assert Config(host=GERRIT_HOST).mirror is False

    @working_copy_default
    def test_loaded_config_defaults_to_working_copies(self) -> None:
        assert ConfigManager().load_config(host=GERRIT_HOST).mirror is False


class TestGitCommand:
    @working_copy_default
    def test_default_has_no_mirror(self, tmp_path: Path) -> None:
        cmd = build_base_clone_command(
            "ssh://x/p", tmp_path / "p", Config(host=GERRIT_HOST)
        )

        assert "--mirror" not in cmd

    @working_copy_default
    def test_default_keeps_depth_and_branch(self, tmp_path: Path) -> None:
        config = Config(host=GERRIT_HOST, depth=10, branch="main")

        cmd = build_base_clone_command("ssh://x/p", tmp_path / "p", config)

        assert cmd[cmd.index("--depth") + 1] == "10"
        assert cmd[cmd.index("--branch") + 1] == "main"

    def test_mirror_opt_in_is_unchanged(self, tmp_path: Path) -> None:
        config = Config(host=GERRIT_HOST, mirror=True, depth=10, branch="main")

        cmd = build_base_clone_command("ssh://x/p", tmp_path / "p", config)

        assert "--mirror" in cmd
        assert "--depth" not in cmd
        assert "--branch" not in cmd


class TestGhCommand:
    @working_copy_default
    def test_default_has_no_mirror(self, tmp_path: Path) -> None:
        cmd = _build_gh_clone_command(
            _github_project(), _github_config(tmp_path), tmp_path / "repo"
        )

        assert "--mirror" not in cmd

    @working_copy_default
    def test_default_keeps_depth_and_branch(self, tmp_path: Path) -> None:
        config = _github_config(tmp_path, depth=10, branch="main")

        cmd = _build_gh_clone_command(_github_project(), config, tmp_path / "repo")

        assert cmd[-5:] == ["--", "--depth", "10", "--branch", "main"]

    def test_mirror_opt_in_is_unchanged(self, tmp_path: Path) -> None:
        config = _github_config(tmp_path, mirror=True)

        cmd = _build_gh_clone_command(_github_project(), config, tmp_path / "repo")

        assert cmd[-2:] == ["--", "--mirror"]


class TestDepthAndBranch:
    @working_copy_default
    def test_default_keeps_them_without_a_warning(self) -> None:
        logger = MagicMock()
        with patch("source_clone.logging.get_logger", return_value=logger):
            config = Config(host=GERRIT_HOST, depth=10, branch="main")

        assert (config.depth, config.branch) == (10, "main")
        logger.warning.assert_not_called()

    def test_mirror_still_clears_them_with_a_warning(self) -> None:
        logger = MagicMock()
        with patch("source_clone.logging.get_logger", return_value=logger):
            config = Config(host=GERRIT_HOST, mirror=True, depth=10, branch="main")

        assert (config.depth, config.branch) == (None, None)
        assert logger.warning.call_count == 2


class TestMirrorCommand:
    """``source-clone mirror`` pushes with --mirror, so it clones bare."""

    def test_option_defaults_to_mirroring(self) -> None:
        assert cli_options_mirror.MIRROR.default is True

    def test_its_config_mirrors(self, tmp_path: Path) -> None:
        captured: list[Any] = []
        with patch(
            "source_clone.cli_mirror.run_mirror",
            side_effect=lambda request, _session: captured.append(request),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "mirror",
                    "--host",
                    GERRIT_HOST,
                    "--org",
                    "example-org",
                    "--output-path",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0, result.output
        (request,) = captured
        assert request.mirror is True
        assert build_mirror_config(request, None).mirror is True


class TestHelp:
    @working_copy_default
    def test_help_describes_the_default(self) -> None:
        result = CliRunner().invoke(app, ["clone", "--help"])

        assert result.exit_code == 0, result.output
        text = _flat(result.output)
        assert "working copies by default" in text
        assert "bare mirrors" in text


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    path = tmp_path / "up"
    path.mkdir()
    _git("init", "-q", "-b", "main", str(path))
    _commit(path, "file.txt", "one\n")
    return path


def _clone(
    upstream: Path,
    tree: Path,
    *options: str,
    names: tuple[str, ...] = ("proj",),
    env: dict[str, str] | None = None,
    log_file: Path | None = None,
) -> Any:
    log_options = ["--log-file", str(log_file)] if log_file else ["--disable-log-file"]
    projects = [Project(name, ProjectState.ACTIVE) for name in names]
    with (
        patch(
            "source_clone.clone_manager.discover_projects",
            return_value=(projects, {"skipped": 0}),
        ),
        patch(
            "source_clone.clone_orchestrator.CloneManager.clone_projects",
            _cloning_from(upstream),
        ),
    ):
        return CliRunner().invoke(
            app,
            [
                "clone",
                "--host",
                GERRIT_HOST,
                "--output-path",
                str(tree),
                *log_options,
                *options,
            ],
            env=env,
        )


class TestEndToEnd:
    @working_copy_default
    def test_default_clone_is_a_working_copy(
        self, upstream: Path, tmp_path: Path
    ) -> None:
        tree = tmp_path / "tree"

        result = _clone(upstream, tree)

        assert result.exit_code == 0, result.output
        assert not _is_bare(tree / "proj")
        assert (tree / "proj" / "file.txt").read_text() == "one\n"

    @pytest.mark.parametrize(
        ("options", "env", "bare"),
        [
            (("--mirror",), None, True),
            ((), {"GERRIT_MIRROR": "true"}, True),
            (("--config-file", "{config}"), None, True),
            (("--no-mirror",), None, False),
        ],
        ids=["flag", "environment", "config-file", "no-mirror"],
    )
    def test_each_route_still_works(
        self,
        upstream: Path,
        tmp_path: Path,
        options: tuple[str, ...],
        env: dict[str, str] | None,
        bare: bool,
    ) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("mirror: true\n")
        tree = tmp_path / "tree"

        result = _clone(
            upstream,
            tree,
            *(option.format(config=config_file) for option in options),
            env=env,
        )

        assert result.exit_code == 0, result.output
        assert _is_bare(tree / "proj") is bare


class TestMixedTree:
    """An existing bare mirror is refreshed as a mirror, not converted."""

    @pytest.fixture
    def tree(self, upstream: Path, tmp_path: Path) -> Path:
        tree = tmp_path / "tree"
        tree.mkdir()
        _git("clone", "-q", "--mirror", upstream.as_uri(), str(tree / "old"))
        return tree

    @working_copy_default
    def test_mirror_is_refreshed_and_new_projects_are_working_copies(
        self, upstream: Path, tree: Path
    ) -> None:
        head = _commit(upstream, "file.txt", "two\n")

        with patch("source_clone.clone_stages.logger") as logger:
            result = _clone(upstream, tree, names=("old", "new"))

        assert result.exit_code == 0, result.output
        assert _is_bare(tree / "old")
        assert _git("rev-parse", "refs/heads/main", cwd=tree / "old") == head
        assert not _is_bare(tree / "new")
        assert (tree / "new" / "file.txt").read_text() == "two\n"
        assert _flat(result.output).count(NOTICE) == 1
        text = _flat(result.output)
        assert "Found 1 existing bare mirror(s)" in text
        assert "--mirror" in text
        logged = [str(c.args[0]) for c in logger.info.call_args_list]
        assert [m for m in logged if NOTICE in m]

    @working_copy_default
    def test_verbose_prints_the_notice_once_and_logs_it(
        self, upstream: Path, tree: Path, tmp_path: Path
    ) -> None:
        log_file = tmp_path / "clone.log"

        # Wide enough that Rich's log columns never split the phrase
        result = _clone(
            upstream,
            tree,
            "--verbose",
            names=("old", "new"),
            env={"COLUMNS": "500"},
            log_file=log_file,
        )

        assert result.exit_code == 0, result.output
        assert _flat(result.output).count(NOTICE) == 1
        assert log_file.read_text(encoding="utf-8").count(NOTICE) == 1

    @working_copy_default
    @pytest.mark.parametrize("options", [(), ("--no-refresh",)])
    def test_notice_does_not_claim_a_refresh(
        self, upstream: Path, tree: Path, options: tuple[str, ...]
    ) -> None:
        # The notice prints before any refresh runs, and a refresh can be
        # skipped or fail, so it may only say the mirrors were kept.
        result = _clone(upstream, tree, *options, names=("old", "new"))

        assert result.exit_code == 0, result.output
        text = _flat(result.output)
        assert "existing bare mirror(s) under" in text
        notice = text[text.index("existing bare mirror(s) under") :]
        notice = notice[: notice.index("working copies.")]
        assert "kept as mirrors" in notice
        assert "refreshed" not in notice

    def test_quiet_suppresses_the_notice(self, upstream: Path, tree: Path) -> None:
        result = _clone(upstream, tree, "--quiet", names=("old", "new"))

        assert result.exit_code == 0, result.output
        assert NOTICE not in _flat(result.output)
        assert _is_bare(tree / "old")

    def test_mirror_mode_gives_no_notice(self, upstream: Path, tree: Path) -> None:
        result = _clone(upstream, tree, "--mirror", names=("old", "new"))

        assert result.exit_code == 0, result.output
        assert NOTICE not in _flat(result.output)
        assert _is_bare(tree / "new")
