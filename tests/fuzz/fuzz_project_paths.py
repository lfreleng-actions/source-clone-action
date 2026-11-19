# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Fuzz harness: project names to clone paths.

Contracts checked, in the code's own words:

- :mod:`source_clone.path_naming` "Rejects names that could escape the
  clone root (traversal sequences, absolute paths, reserved git names)
  and rewrites the remainder into a name that is safe on every
  supported platform."  So whatever :func:`get_project_path` returns for
  a name it accepts lies strictly inside the base, and the part it adds
  carries none of the characters :func:`sanitize_project_name` replaces.
- :func:`validate_project_name` validates "that a project name is safe
  for filesystem use", raising ``PathValidationError`` otherwise.  A
  name it accepts is relative and has no ``..`` component.
- :func:`source_clone.pathing.get_relative_path` returns the "Relative
  path, or original path if not under base".

The oracles judge containment with :func:`posixpath.normpath` and path
components, never with the code under test.

Run ``python tests/fuzz/fuzz_project_paths.py`` to fuzz from the seeds,
or name input files to replay them once.
"""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

_ROOT = Path(__file__).resolve().parents[2]
for _entry in (_ROOT, _ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from source_clone.path_errors import PathValidationError  # noqa: E402
from source_clone.path_naming import (  # noqa: E402
    get_project_path,
    validate_project_name,
)
from source_clone.pathing import get_relative_path  # noqa: E402
from tests.fuzz.harness_support import (  # noqa: E402
    encode_seed,
    libfuzzer_arguments,
    require,
    text_fields,
)

#: Where the derived paths are rooted; never created.
BASE = Path("/fuzz/base")

#: What sanitising replaces, so a sanitised name must not contain it.
UNSAFE_CHARACTERS = '<>:"|?*\0\\'


def check_project_path(data: bytes) -> None:
    """A path derived from any accepted name stays strictly inside the base.

    Args:
        data: One text field, the project name.

    Raises:
        ContractViolation: If the path leaves the base or keeps an
            unsafe character.
    """
    (name,) = text_fields(data, 1)
    try:
        path = get_project_path(name, BASE)
    except PathValidationError:
        return
    # as_posix() so a native Windows path is judged by the same rules.
    base = BASE.as_posix()
    derived = path.as_posix()
    normalised = posixpath.normpath(derived)
    require(
        normalised.startswith(base + "/"),
        f"project name {name!r} gives {derived!r}, outside {base!r}",
    )
    added = derived.removeprefix(base + "/")
    require(
        not any(char in added for char in UNSAFE_CHARACTERS),
        f"project name {name!r} keeps an unsafe character in {added!r}",
    )


def check_validated_name(data: bytes) -> None:
    """A name :func:`validate_project_name` accepts cannot climb out of a base.

    Args:
        data: One text field, the project name.

    Raises:
        ContractViolation: If an absolute or traversing name is accepted.
    """
    (name,) = text_fields(data, 1)
    try:
        validate_project_name(name)
    except PathValidationError:
        return
    parts = name.split("/")
    require(not name.startswith("/"), f"accepted absolute name {name!r}")
    require(".." not in parts, f"accepted traversing name {name!r}")
    require(
        posixpath.normpath(posixpath.join(str(BASE), name)) != str(BASE),
        f"accepted name {name!r} that resolves to the base itself",
    )


def _same_components(
    path: PurePath, left: tuple[str, ...], right: tuple[str, ...]
) -> bool:
    """Compare path components as *path*'s flavour does.

    The :mod:`pathlib` documentation says Windows paths are
    case-insensitive, and :class:`~pathlib.PureWindowsPath` folds case
    with :meth:`str.lower`; POSIX paths compare exactly.
    """
    if isinstance(path, PureWindowsPath):
        return [part.lower() for part in left] == [part.lower() for part in right]
    return left == right


def check_relative_path(data: bytes) -> None:
    """:func:`get_relative_path` is relative to the base exactly when under it.

    Args:
        data: Two text fields: a path, and whether to root it in the base.

    Raises:
        ContractViolation: If the result is not the documented one.
    """
    raw, rooted = text_fields(data, 2)
    path = BASE / raw if rooted else Path(raw)
    result = get_relative_path(path, BASE)
    base_parts = PurePosixPath(BASE.as_posix()).parts
    parts = PurePosixPath(path.as_posix()).parts
    if _same_components(path, parts[: len(base_parts)], base_parts):
        expected = PurePosixPath(*parts[len(base_parts) :])
        require(
            PurePosixPath(result.as_posix()) == expected,
            f"{str(path)!r} under {str(BASE)!r} gave {str(result)!r}",
        )
    else:
        require(
            result == path,
            f"{str(path)!r}, not under {str(BASE)!r}, gave {str(result)!r}",
        )


CHECKS = (check_project_path, check_validated_name, check_relative_path)


def check(data: bytes) -> None:
    """Run the check the first byte of *data* selects on the rest.

    Args:
        data: Raw fuzz input.

    Raises:
        ContractViolation: If the selected contract breaks.
    """
    if data:
        CHECKS[data[0] % len(CHECKS)](data[1:])


SEEDS = (
    encode_seed(0, "group/subgroup/project"),
    # Found by this harness: these came out as "/etc", replacing the base.
    encode_seed(0, "../etc"),
    encode_seed(0, "..//etc"),
    encode_seed(0, ".../etc"),
    encode_seed(0, "..\\etc"),
    encode_seed(0, " ../etc/passwd "),
    encode_seed(0, "a/../../b/./c/.."),
    encode_seed(0, ".github"),
    encode_seed(0, 'CON<>:"|?*\\name'),
    encode_seed(0, "releng/" + "deeply/nested/" * 12 + "project.git"),
    encode_seed(1, "group/project"),
    encode_seed(1, "a/../b"),
    encode_seed(1, "..x/y.."),
    encode_seed(2, "a/b", "rooted"),
    encode_seed(2, "/elsewhere/a", ""),
    encode_seed(2, "/fuzz/base/../x", ""),
    # POSIX paths compare case-sensitively, so this is not under the base.
    encode_seed(2, "/FUZZ/base/a", ""),
)


def main() -> None:
    """Fuzz :func:`check` from the seeds, or replay named inputs."""
    import atheris  # noqa: PLC0415
    from atheris.instrument_bytecode import instrument_all  # noqa: PLC0415

    instrument_all()
    atheris.Setup(libfuzzer_arguments(sys.argv, SEEDS), check)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
