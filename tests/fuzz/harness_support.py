# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""What every fuzz harness here shares: input decoding, verdicts and seeds.

A harness keeps its check a plain function of the input bytes, so the
unit suite can replay its seeds without the fuzzing engine installed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: Separates the text fields of one input.  It is never valid UTF-8, so
#: no field can contain it.
FIELD_SEPARATOR = b"\xff"


class ContractViolation(AssertionError):
    """Raised when the code under test breaks the contract a harness checks."""


def require(condition: bool, message: str) -> None:
    """Raise :class:`ContractViolation` with *message* unless *condition*.

    Args:
        condition: Whether the contract held.
        message: What broke, including the input that broke it.

    Raises:
        ContractViolation: If *condition* is false.
    """
    if not condition:
        raise ContractViolation(message)


def text_fields(data: bytes, count: int) -> list[str]:
    """Split *data* into exactly *count* text fields.

    Missing fields are empty, and the last field keeps any further
    separators' content.  Invalid UTF-8 decodes to U+FFFD, so the
    result depends on the bytes alone.

    Args:
        data: Raw fuzz input.
        count: Number of fields wanted.

    Returns:
        The decoded fields.
    """
    parts = data.split(FIELD_SEPARATOR, count - 1)
    parts.extend([b""] * (count - len(parts)))
    return [part.decode("utf-8", "replace") for part in parts]


def keep(text: str, allowed: str, default: str = "") -> str:
    """*text* reduced to the characters in *allowed*, or *default* if none.

    Args:
        text: Field to filter.
        allowed: Characters to keep.
        default: Value when nothing is left.

    Returns:
        The filtered text.
    """
    return "".join(char for char in text if char in allowed) or default


def encode_seed(selector: int, *fields: str) -> bytes:
    """Build an input that selects check *selector* with *fields*.

    Args:
        selector: Index of the check the first byte selects.
        fields: Text fields, in order.

    Returns:
        Input bytes in the layout :func:`text_fields` reads.
    """
    return bytes([selector]) + FIELD_SEPARATOR.join(
        field.encode("utf-8") for field in fields
    )


def libfuzzer_arguments(argv: Sequence[str], seeds: Iterable[bytes]) -> list[str]:
    """The arguments to hand libFuzzer: *argv*, plus a seed directory.

    Unless an argument names an existing file, which libFuzzer then runs
    once, the seeds are written to a fresh temporary directory appended
    after any directories given, so libFuzzer still writes the inputs it
    keeps to the first one.

    Args:
        argv: The harness's own ``sys.argv``.
        seeds: Seed inputs.

    Returns:
        The argument vector for ``atheris.Setup``.
    """
    if any(Path(argument).is_file() for argument in argv[1:]):
        return list(argv)
    seed_directory = Path(tempfile.mkdtemp(prefix="fuzz-seeds-"))
    for index, seed in enumerate(seeds):
        (seed_directory / f"seed-{index:03d}").write_bytes(seed)
    return [*argv, str(seed_directory)]
