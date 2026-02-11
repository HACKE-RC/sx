# BM25 Search Tool

This repo includes a small, dependency-free BM25 indexer and search CLI.

## Quick Start

Build an index (SQLite DB):
```bash
./search index .
```

Search:
```bash
./search "replication backlog"
```

## Useful Flags

Index:
- `./search index . --full`: full rebuild (ignore incremental checks)
- `./search index . --ext .c,.h,.md`: restrict which files get indexed
- `./search index . --workers 8`: parallel file parsing (threads)

Search:
- `./search --snippet --color "aof fsync"`: snippet with optional ANSI highlighting
- `./search --path src/ "cluster"`: only results whose path contains a substring
- `./search --ext .c,.h "dict"`: restrict results to certain extensions/names
- `./search --json "term"`: machine-readable output

## Notes

- The index is stored in `bm25.sqlite` by default.
- Indexing is incremental: only changed files are reprocessed, removed files are deleted from the index.
- Tokenization splits `snake_case` and simple `camelCase` identifiers so code symbols are searchable.

