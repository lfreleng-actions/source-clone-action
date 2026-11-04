# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Project name lists read from files.

``--include-projects-file`` and ``--exclude-projects-file`` take a file
of project names or globs, separated by commas, spaces or new lines.  A
``#`` starts a comment that runs to the end of its line.  A file that names nothing is an error rather
than an empty filter: an empty include list means "clone everything",
so a list emptied by mistake would otherwise widen the run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from source_clone.model_project_filters import normalize_project_list

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class ProjectListFileError(ValueError):
    """A project list file is unreadable or names no project."""


def read_project_list_file(path: Path) -> list[str]:
    """Return the project names and globs a list file contains.

    Args:
        path: File to read

    Returns:
        Patterns in file order, normalised as ``--include-projects``
        normalises its values

    Raises:
        ProjectListFileError: The file cannot be read or names nothing
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectListFileError(f"Cannot read project list file {path}: {e}") from e
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    patterns = normalize_project_list(lines)
    if not patterns:
        raise ProjectListFileError(
            f"Project list file {path} names no project; refusing to treat "
            "it as no filter"
        )
    return patterns


def merge_project_lists(
    patterns: Iterable[str] | None, files: Iterable[Path] | None
) -> list[str] | None:
    """Combine patterns given directly with those read from files.

    Args:
        patterns: Patterns from the command line
        files: Project list files, read in order

    Returns:
        The patterns followed by each file's entries, or None when
        neither supplies anything

    Raises:
        ProjectListFileError: A file is unreadable or names nothing
    """
    merged = list(patterns or [])
    for path in files or []:
        merged.extend(read_project_list_file(path))
    return merged or None
