# Sync UX Improvements Design

**Date:** 2026-05-19
**Status:** Approved

## Problem

Two pain points with `vibedom push` / `vibedom pull`:

1. **Path confusion** — path arguments must be relative to the workspace root, but users think in terms of wherever they currently are in the terminal. Typing `app/Controllers` when you mean `src/app/Controllers` causes files to land at the wrong location in the destination.

2. **`--delete` data loss** — when `--delete` is used, files that only exist on the destination (e.g. `.env.secrets` created during container setup) are silently removed. Users have no opportunity to catch this before it happens.

Note: files listed in `sync_exclude` are already protected from `--delete` by rsync's exclude rules — this is worth communicating clearly but requires no code change.

## Design

### 1. CWD-relative path resolution

When path arguments are provided, check whether the current working directory is inside the workspace root. If it is, resolve each path relative to CWD before passing it to rsync. If CWD is outside the workspace, fall back to workspace-root-relative resolution (existing behaviour).

Always print the resolved workspace-root-relative path(s) before syncing so the user can verify:

```
Resolved: src/app/Controllers
Pulling from container...
```

If a CWD-relative path resolves outside the workspace root, reject it with a clear error (same as the existing path traversal check).

### 2. Deletion preview with `--force` bypass

When `--delete` is passed without `--force`:

1. Run a silent rsync dry-run to detect what would be deleted.
2. Parse stdout for lines beginning with `deleting `.
3. If any deletions are found, print them and prompt for confirmation before proceeding.

```
These files will be deleted from the container:
  .env.secrets
  tmp/agent-scratch.txt

Proceed? [y/N]
```

If the user declines, abort with no changes made.

`--force` / `-f` skips all confirmations: both the deletion preview prompt and the existing full-tree sync confirmation prompt. The existing `--yes` / `-y` flag is kept unchanged for backwards compatibility (it skips only the full-tree prompt).

If `--delete` is used with `--dry-run`, the deletion preview is skipped (the dry-run output already shows what would happen).

## Flags summary

| Flag | Effect |
|------|--------|
| `--delete` | Enable deletion of destination files absent from source |
| `--dry-run` / `-n` | Show what would sync, no changes (existing) |
| `--yes` / `-y` | Skip full-tree sync confirmation (existing) |
| `--force` / `-f` | Skip all confirmations (full-tree + deletion preview) |

## Files affected

- `lib/vibedom/cli.py` — `_validate_sync_paths`, `_build_rsync_cmd`, `pull`, `push`

## Out of scope

- `sync_protect` config key (deferred — `sync_exclude` already prevents deletion for known files)
- Automatic file watching / live sync
- Git-diff-based pull
