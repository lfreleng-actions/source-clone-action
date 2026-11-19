# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Replay every fuzz seed, and show each oracle rejects a wrong answer.

The harnesses under ``tests/fuzz`` run under a fuzzing engine that the
normal suite does not install; their checks are plain functions, so the
seeds replay here without it.
"""

from __future__ import annotations

import shutil
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

import pytest

from source_clone.github_url_safety import UnsafeCloneUrlError
from tests.fuzz import fuzz_project_paths, fuzz_url_safety
from tests.fuzz.harness_support import (
    ContractViolation,
    encode_seed,
    libfuzzer_arguments,
    text_fields,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

HARNESSES = (fuzz_project_paths, fuzz_url_safety)


def _never_refuse(url: str, token: str | None = None) -> None:
    """A wrong reject_credentialed_url that accepts everything."""


def _refuse_literal_token(url: str, token: str | None = None) -> None:
    """A wrong reject_credentialed_url that never percent-decodes."""
    if token and token in url:
        raise UnsafeCloneUrlError(url)


@pytest.mark.parametrize(
    ("harness", "seed"),
    [(harness, seed) for harness in HARNESSES for seed in harness.SEEDS],
    ids=[
        f"{harness.__name__.rsplit('.', 1)[-1]}-{index}"
        for harness in HARNESSES
        for index in range(len(harness.SEEDS))
    ],
)
def test_every_seed_holds(harness: ModuleType, seed: bytes) -> None:
    harness.check(seed)


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_check_has_a_seed(harness: ModuleType) -> None:
    selected = {seed[0] % len(harness.CHECKS) for seed in harness.SEEDS}
    assert selected == set(range(len(harness.CHECKS)))


WRONG_RESULTS: list[tuple[ModuleType, str, Callable[..., Any], bytes]] = [
    (
        fuzz_project_paths,
        "get_project_path",
        lambda name, base: Path("/etc"),
        encode_seed(0, "project"),
    ),
    (
        fuzz_project_paths,
        "get_project_path",
        lambda name, base: base / "a:b",
        encode_seed(0, "project"),
    ),
    (
        fuzz_project_paths,
        "validate_project_name",
        lambda name: None,
        encode_seed(1, "../escape"),
    ),
    (
        fuzz_project_paths,
        "get_relative_path",
        lambda path, base: path,
        encode_seed(2, "a/b", "rooted"),
    ),
    (
        fuzz_project_paths,
        "get_relative_path",
        lambda path, base: Path("x"),
        encode_seed(2, "/elsewhere", ""),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _never_refuse,
        encode_seed(0, "https://h/ghp_x/r", "ghp_x"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _never_refuse,
        encode_seed(1, "", "ssh", "git", "pw", "h", "r"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _never_refuse,
        encode_seed(2, "", "https", "tok", "github.com", "o/r"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _never_refuse,
        encode_seed(3, "github.com", "repo", "q", "k"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _never_refuse,
        encode_seed(4, "https://h/", "ghp_x", "", "ee"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _refuse_literal_token,
        encode_seed(4, "https://h/", "ghp_x", "", "e"),
    ),
    (
        fuzz_url_safety,
        "reject_credentialed_url",
        _refuse_literal_token,
        encode_seed(4, "https://h/", "ghp_x", "", "ee"),
    ),
    (
        fuzz_url_safety,
        "redact_url",
        lambda url: url,
        encode_seed(5, "", "https", "u:p", "h", "/r", "", ""),
    ),
    (
        fuzz_url_safety,
        "redact_url",
        lambda url: url,
        encode_seed(6, "git", "h", "r"),
    ),
    (
        fuzz_url_safety,
        "redact_text",
        lambda text, secrets=(): text,
        encode_seed(7, "a ghp_x b", "ghp_x"),
    ),
    (
        fuzz_url_safety,
        "redact_text",
        lambda text, secrets=(): text,
        encode_seed(8, "", "https", "u:p", "h", ""),
    ),
    (
        fuzz_url_safety,
        "remote_location",
        lambda url: None,
        encode_seed(9, "", "ssh", "", "h", "22", "r", ""),
    ),
    (
        fuzz_url_safety,
        "helper_address",
        lambda url: url,
        encode_seed(10, "ext", "r"),
    ),
]


@pytest.mark.parametrize(
    ("harness", "name", "wrong", "data"),
    WRONG_RESULTS,
    ids=[f"{name}-{data[0]}" for _, name, _, data in WRONG_RESULTS],
)
def test_each_oracle_rejects_a_wrong_result(
    monkeypatch: pytest.MonkeyPatch,
    harness: ModuleType,
    name: str,
    wrong: Callable[..., Any],
    data: bytes,
) -> None:
    harness.check(data)
    monkeypatch.setattr(harness, name, wrong)
    with pytest.raises(ContractViolation):
        harness.check(data)


def test_project_paths_hold_with_native_windows_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = PureWindowsPath("/fuzz/base")
    monkeypatch.setattr(fuzz_project_paths, "BASE", base)
    fuzz_project_paths.check(encode_seed(0, "group/subgroup/project"))
    fuzz_project_paths.check(encode_seed(2, "a/b", "rooted"))
    # Windows compares paths case-insensitively, so this is under the base.
    fuzz_project_paths.check(encode_seed(2, "/FUZZ/base/a", "rooted"))


def test_fields_split_on_the_separator_and_pad() -> None:
    assert text_fields(encode_seed(0, "a", "b")[1:], 3) == ["a", "b", ""]
    assert text_fields(b"a\xffb\xffc", 2) == ["a", "b\ufffdc"]


def test_seeds_are_appended_unless_a_file_is_named(tmp_path: Path) -> None:
    arguments = libfuzzer_arguments(["harness", "-runs=0", str(tmp_path)], [b"s"])
    seed_directory = Path(arguments[-1])
    try:
        assert arguments[:3] == ["harness", "-runs=0", str(tmp_path)]
        assert [path.read_bytes() for path in seed_directory.iterdir()] == [b"s"]
    finally:
        shutil.rmtree(seed_directory)

    named = tmp_path / "input"
    named.write_bytes(b"x")
    assert libfuzzer_arguments(["harness", str(named)], [b"s"]) == [
        "harness",
        str(named),
    ]
