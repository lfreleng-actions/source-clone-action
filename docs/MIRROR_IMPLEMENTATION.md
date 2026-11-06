<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 Matthew Watkins <mwatkins@linuxfoundation.org>
-->

# Mirror Mode Implementation Notes

## Overview

This document describes the implementation of `--mirror` mode in the
source-clone-action tool. The `--mirror` flag creates bare Git repositories
with complete metadata, including all branches, tags, refs, and notes.
Mirror mode is opt-in for `clone`, which makes working copies by default,
and always used by the `mirror` command.

## Implementation Date

2025-01-24

## Motivation

Backups and migrations need complete metadata from remote repositories,
which Git's `--mirror` option provides. Release 4.0 made it opt-in for
`clone`: most people who clone a server want working copies they can `cd`
into, and mirroring to GitHub has its own command, `mirror`, which keeps
using bare mirrors because it pushes with `git push --mirror`.

## Changes Made

### 1. Core Model Changes (`src/source_clone/models.py`)

**Added Field:**

- `mirror: bool = False` - Field in the `Config` class; `True` selects
  mirror mode

**Validation Logic:**

- Added `__post_init__` validation to check for incompatible options
- When `mirror=True` and user provides `depth`: system sets `depth` to
  `None` with a warning
- When `mirror=True` and user provides `branch`: system sets `branch` to
  `None` with a warning
- These options are mutually exclusive according to Git's constraints

### 2. Worker Module Changes (`src/source_clone/worker.py`)

**Modified Method:** `_build_clone_command()`

**Behavior:**

- **Mirror Mode:** Adds `--mirror` flag to git clone command
- **Non-Mirror Mode (default):** Uses `--no-hardlinks`, `--quiet`,
  optional `--depth`, and optional `--branch`

### 3. GitHub Worker Changes (`src/source_clone/github_worker.py`)

**Modified Functions:**

- `_clone_with_git()` - Updated to support `--mirror` flag
- `_clone_with_gh_cli()` - Updated to pass `--mirror` through to gh CLI

**Behavior:** Same branching logic as worker.py - mirror mode uses
`--mirror`, non-mirror mode allows depth/branch options.

### 4. CLI Changes (`src/source_clone/cli.py`)

**Added Option to `clone` Command:**

```python
mirror: bool | None = typer.Option(
    None,
    "--mirror/--no-mirror",
    help=(
        "clone makes working copies by default. --mirror makes bare mirrors "
        "instead (git clone --mirror): every ref, tag and branch, with no "
        "working tree, for backups or later mirroring. Incompatible with "
        "--depth and --branch."
    ),
    envvar="GERRIT_MIRROR",
)
```

**Tri-State Behavior:**

- `None` (default): Uses the value from config file/environment variables,
  or falls back to the `Config.mirror=False` default (working copies)
- `--mirror`: Explicitly enables mirror mode
  (overrides config file/environment)
- `--no-mirror`: Explicitly disables mirror mode
  (overrides config file/environment)

This tri-state design allows CLI flags to override configuration sources
while still respecting config files and environment variables when the
user does not provide a CLI flag.

**Added Option to `mirror` Command:**

```python
mirror: bool = typer.Option(
    True,
    "--mirror/--no-mirror",
    help=(
        "Use git clone --mirror for complete repository metadata "
        "(all refs, tags, branches). Creates bare repository. "
        "Incompatible with --depth and --branch."
    ),
    envvar="GERRIT_MIRROR",
)
```

Note: The `mirror` command defaults to `True` directly, and builds its
`Config` with that value, since it pushes with `git push --mirror`. It does
not inherit the `Config.mirror=False` default that `clone` uses.

### 5. Configuration Management Changes (`src/source_clone/config.py`)

**Added to `load_config()` signature:**

- `mirror: bool | None = None` parameter

**Environment Variable Support:**

- Added `GERRIT_MIRROR` environment variable in
  `_load_clone_behavior_env_vars()`

### 6. GitHub Actions Interface (`action.yaml`)

**Added Input:**

```yaml
mirror:
  description: >-
    The action clones working copies by default ("false"). "true"
    clones bare mirrors with git clone --mirror...
  required: false
  default: "false"
```

The clone step passes `--mirror` for `true`, and `--no-mirror` otherwise,
always one or the other so the result never depends on the CLI release
`uvx` resolves. `auto` mirrors Gerrit sources and makes working copies of
GitHub sources.

### 7. Documentation Changes (`README.md`)

- Features list: working copies by default, full bare mirrors via `--mirror`
- Clone examples: `--depth`/`--branch` without flags, and a `--mirror` backup
- Section "Working Copies and Mirrors", and "Upgrading to 4.0"
- Updated GitHub Action examples

### 8. Test Coverage

**Config Tests (`tests/test_config.py`):**

- `test_load_config_mirror_default_false`
- `test_load_config_mirror_explicit_false`
- `test_load_config_mirror_incompatible_with_depth`
- `test_load_config_mirror_incompatible_with_branch`
- `test_load_config_mirror_from_env`

**Worker Tests (`tests/test_worker.py`):**

- `test_build_clone_command_with_mirror`
- `test_build_clone_command_without_mirror`
- `test_build_clone_command_with_depth_no_mirror`
- `test_build_clone_command_with_branch_no_mirror`

**Default and Compatibility Tests
(`tests/test_clone_default_working_copy.py`):**

- The working-copy default, end to end and in the git and gh commands
- Each route to mirror mode: `--mirror`, `GERRIT_MIRROR=true`, config file
- The `mirror` command still cloning bare mirrors
- An existing bare mirror refreshed as a mirror, with one notice

## Usage Examples

### CLI Usage

**Default (Working Copies - no flag specified):**

```bash
source-clone clone --host gerrit.example.org
# Creates working copies (uses Config.mirror=False default)
```

**Explicit Mirror Mode:**

```bash
source-clone clone --host gerrit.example.org --mirror
# Explicitly enables mirror mode (overrides config file if present)
```

**Non-Mirror Mode:**

```bash
source-clone clone --host gerrit.example.org --no-mirror
# Creates working copies even when config or environment selects mirror mode
```

**With Config File:**

```bash
# If config.yaml has: mirror: false
source-clone clone --host gerrit.example.org
# Uses config file setting (non-mirror mode)

# Override config file to use mirror mode
source-clone clone --host gerrit.example.org --mirror
```

**With Environment Variable:**

```bash
# Environment variable sets the default
export GERRIT_MIRROR=false
source-clone clone --host gerrit.example.org
# Uses environment setting (non-mirror mode)

# CLI flag overrides environment
source-clone clone --host gerrit.example.org --mirror
```

### GitHub Actions Usage

**Default (Working Copies - no input specified):**

```yaml
- uses: lfreleng-actions/source-clone-action@v1
  with:
    host: gerrit.example.org
    # No mirror input = "false", which passes --no-mirror
```

**Explicit Mirror Mode:**

```yaml
- uses: lfreleng-actions/source-clone-action@v1
  with:
    host: gerrit.example.org
    mirror: true
```

**Non-Mirror Mode:**

```yaml
- uses: lfreleng-actions/source-clone-action@v1
  with:
    host: gerrit.example.org
    mirror: false
```

## Important Considerations

### Bare Repositories

Mirror mode creates **bare repositories** with no working directory:

- ✅ Perfect for backups and archival
- ✅ Ideal for re-mirroring to other servers
- ✅ Contains complete Git metadata
- ❌ No working tree to browse files

### Incompatibilities

The `--mirror` flag is incompatible with:

- `--depth` (shallow clones)
- `--branch` (specific branch clones)

When you enable mirror mode and provide these options, the system ignores
the incompatible options with a warning.

### Configuration Precedence

The mirror option follows standard configuration precedence:

1. **CLI flags** (highest): `--mirror` or `--no-mirror`
2. **Environment variables**: `GERRIT_MIRROR=true` or `GERRIT_MIRROR=false`
3. **Config file**: `mirror: true` or `mirror: false`
4. **Default** (lowest): `Config.mirror=False`

This allows users to:

- Set defaults in config files or environment variables
- Override on a per-command basis with CLI flags
- Get working copies when they do not specify a preference

### Backward Compatibility

Release 4.0 made working copies the `clone` default, a **breaking change**
for anyone relying on bare mirrors without asking for them. Every name
keeps its meaning: `--mirror/--no-mirror`, `GERRIT_MIRROR`, the `mirror`
config-file key and the Action's `mirror` input. Older pinned Action
commits pass `--mirror` or `--no-mirror` explicitly, so they keep getting
what they asked for.

**Migration Path:**

- Users who want bare mirrors: Add `--mirror`, set `GERRIT_MIRROR=true`,
  add `mirror: true` to the config file, or set the Action input
  `mirror: true`
- Users who passed `--no-mirror`: Nothing to change; `clone` still accepts it
- Existing trees are not converted: `clone` refreshes a bare mirror it
  finds as a mirror and prints one notice with the count

## Related Files

**Modified:**

- `src/source_clone/models.py`
- `src/source_clone/worker.py`
- `src/source_clone/github_worker.py`
- `src/source_clone/cli.py`
- `src/source_clone/config.py`
- `action.yaml`
- `README.md`
- `tests/test_config.py`
- `tests/test_worker.py`

**Documentation:**

- This file (`MIRROR_IMPLEMENTATION.md`)
