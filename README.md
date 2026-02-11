# sx search

Small BM25 search tool for local code/docs. No third-party dependencies.

## what it does

- Indexes text/code files into SQLite (`bm25.sqlite` by default)
- Supports incremental indexing (only changed files are reprocessed)
- Ranks with BM25
- Shows optional snippets with line numbers
- Supports path/extension filters, JSON output, and colored matches

## requirements

- Python 3.9+

## quick start

Build or update index:

```bash
./search index .
```

Search:

```bash
./search "replication backlog"
```

## command forms

```bash
./search [global-options] index [root] [index-options]
./search [global-options] search "query"
./search [global-options] "query"
```

## common examples

Full rebuild:

```bash
./search index . --full
```

Search with snippet + color:

```bash
./search --snippet --color "aof fsync"
```

Filter by path:

```bash
./search --path src/ "replication"
```

Filter by extension:

```bash
./search --ext .c,.h,.md "dict"
```

JSON output:

```bash
./search --json "cluster slots"
```

Custom index path:

```bash
./search index . --out /path/to/myindex.sqlite
./search --index /path/to/myindex.sqlite "term"
```

## key options

- `--k`: number of results (default `10`)
- `--k1`, `--b`: BM25 tuning knobs
- `--path-boost`: extra weight for path token matches (default `1.5`)
- `--stem`: enable simple stemming
- `--no-stopwords`: disable stopword filtering
- `--workers`: indexing worker threads
- `--no-progress`: hide indexing progress output

## indexing behavior

1. Scan files by extension/name and skip likely binary files.
2. Compare `mtime` and `size` with index metadata.
3. Reindex changed files and remove deleted files.
4. Update postings and document metadata in SQLite.

If a file produces no tokens (for example, empty/whitespace-only), it is saved as a zero-length doc so incremental runs do not keep retrying it.

## tests

```bash
python3 -m unittest discover -s . -p 'test_*.py' -q
```

## troubleshooting

`sqlite3.OperationalError: unable to open database file`
- Make sure the DB parent directory exists and is writable.
- Try writing the DB in the current project first:
```bash
./search index . --out ./bm25.sqlite
```

Results look weak
- Rebuild with `--full`
- Try `--stem`
- Increase `--k`
- Recheck filters (`--ext`, `--path`)

## files

- `search`: CLI entrypoint
- `bm25tool.py`: indexing/search engine
- `test_bm25tool.py`: tests
- `SEARCH.md`: short usage notes
