# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""The action's ``mirror: auto`` default, and the CLI's mirror warning.

A bare mirror suits Gerrit backup and migration, but a GitHub source is
usually wanted as working trees for tools that read files.  The action
picks per source; the CLI keeps mirroring by default and says how to
keep ``--depth`` and ``--branch`` when mirror mode drops them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from source_clone.models import Config, SourceType
from tests.conftest import ACTION_YAML

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import ActionCloneStep

GITHUB_HOST = "github.com/example-org"
GERRIT_HOST = "gerrit.example.org"
_PENDING = pytest.mark.xfail(strict=True, reason="mirror: auto is not implemented yet")


def _mirror_flags(args: list[str] | None) -> list[str]:
    assert args is not None, "the step never ran the CLI"
    return [arg for arg in args if arg in ("--mirror", "--no-mirror")]


@_PENDING
def test_action_defaults_to_auto() -> None:
    """New action commits resolve mirroring per source."""
    inputs = yaml.safe_load(ACTION_YAML.read_text())["inputs"]

    assert inputs["mirror"]["default"] == "auto"


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        pytest.param({"INPUT_HOST": GITHUB_HOST}, "--no-mirror", marks=_PENDING),
        pytest.param(
            {"INPUT_HOST": "https://github.com/example-org"},
            "--no-mirror",
            marks=_PENDING,
        ),
        ({"INPUT_HOST": GERRIT_HOST}, "--mirror"),
        ({"INPUT_HOST": GERRIT_HOST, "INPUT_MIRROR": ""}, "--mirror"),
        pytest.param(
            {"INPUT_HOST": GITHUB_HOST, "INPUT_MIRROR": ""},
            "--no-mirror",
            marks=_PENDING,
        ),
        pytest.param(
            {"INPUT_HOST": "git.example.org/org", "INPUT_SOURCE_TYPE": "GitHub"},
            "--no-mirror",
            marks=_PENDING,
        ),
        ({"INPUT_HOST": GITHUB_HOST, "INPUT_SOURCE_TYPE": "gerrit"}, "--mirror"),
    ],
)
def test_auto_resolves_per_source(
    clone_step: ActionCloneStep, inputs: dict[str, str], expected: str
) -> None:
    """auto mirrors Gerrit sources and clones GitHub working trees."""
    step = clone_step.run(**inputs)

    assert step.returncode == 0, step.output
    assert _mirror_flags(step.args) == [expected]


@pytest.mark.parametrize("host", [GITHUB_HOST, GERRIT_HOST])
@pytest.mark.parametrize(
    ("mirror", "expected"), [("true", "--mirror"), ("false", "--no-mirror")]
)
def test_explicit_values_keep_their_meaning(
    clone_step: ActionCloneStep, host: str, mirror: str, expected: str
) -> None:
    """true and false win over the source."""
    step = clone_step.run(INPUT_HOST=host, INPUT_MIRROR=mirror)

    assert step.returncode == 0, step.output
    assert _mirror_flags(step.args) == [expected]


@pytest.mark.parametrize(("option", "value"), [("depth", 1), ("branch", "main")])
@_PENDING
def test_cli_warning_names_no_mirror_for_github(
    tmp_path: Path, option: str, value: Any
) -> None:
    """A GitHub source in mirror mode says how to keep the option."""
    logger = MagicMock()
    with patch("source_clone.logging.get_logger", return_value=logger):
        config = Config(
            host=GITHUB_HOST,
            source_type=SourceType.GITHUB,
            github_org="example-org",
            path=tmp_path,
            mirror=True,
            **{option: value},
        )

    assert getattr(config, option) is None
    warnings = [c.args[0] for c in logger.warning.call_args_list]
    assert [w for w in warnings if f"--{option}" in w], warnings
    assert all("--no-mirror" in w for w in warnings if f"--{option}" in w)


def test_cli_still_mirrors_by_default(tmp_path: Path) -> None:
    """Only the action's default changes; the CLI's does not."""
    config = Config(
        host=GITHUB_HOST,
        source_type=SourceType.GITHUB,
        github_org="example-org",
        path=tmp_path,
    )

    assert config.mirror is True
