# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**searxh** — a zero-dependency BM25 search CLI for local code/docs. Indexes text files into SQLite and ranks results with BM25. Published as `searxh` on PyPI; installs two entry points: `sx` and `searxh`.

## Commands

```bash
# Install (editable)
uv pip install -e .

# Run tests
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -q

# Build package
uv build

# CLI usage
sx index .              # build/update index (incremental)
sx index . --full       # full rebuild
sx status               # check if cwd is covered by an index
sx "query terms"        # BM25 search (shorthand, snippets on by default)
sx search "query"       # BM25 search (explicit subcommand)
sx "query" src/acl.c    # BM25 search scoped to path
sx "A|B|C"              # alternation: searches for tokens from each alternative
```

## Architecture

Two core modules under `src/sx_search/`:

- **`engine.py`** — all indexing and search logic. Key public API:
  - `index(db_path, root, opts, incremental, progress)` — scans files, tokenizes with `tokenize()`, builds inverted index in SQLite. Uses `ThreadPoolExecutor` for parallel file processing.
  - `search(db_path, query, ...)` — computes BM25 scores from the `postings`/`terms`/`docs` tables, returns `(root, List[SearchHit])`. Supports `|` alternation: splits on `|`, tokenizes each alternative, and also regex-matches against the terms table.
  - `index_status(db_path, cwd)` — checks whether cwd falls under the indexed root.
  - `tokenize(text, stem, stopwords)` — regex word extraction + `_split_identifier()` for snake_case/camelCase splitting.
  - `snippet_with_line()`, `highlight()` — result display helpers.

- **`cli.py`** — argparse CLI. `main()` handles subcommand form (`sx index`, `sx search`, `sx status`) and shorthand form (`sx "query"` or `sx "query" path`). Shorthand enables `--snippet` by default. When a second positional arg is given, it becomes the `--path` filter.

SQLite schema (created by `init_db()`): tables `meta`, `docs`, `terms`, `postings`. Incremental indexing compares mtime/size to skip unchanged files.

Compatibility wrapper: `src/bm25tool.py` (import shim) re-exports from `sx_search`.

## Conventions

- No third-party runtime dependencies. stdlib only.
- Python 3.9+ (`from __future__ import annotations` used for older typing).
- Use `uv` for package management.
- Tests live in `tests/test_bm25tool.py` using `unittest`; they create temp directories, index, and search against them.
- The `skills/sx/SKILL.md` defines a Claude Code skill for running `sx` searches.
