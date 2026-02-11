from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sx_search import engine as bm25tool


def parse_exts(s: str | None) -> set[str]:
    if not s:
        return set(bm25tool.DEFAULT_EXTS)
    exts = set()
    for part in s.split(","):
        p = part.strip().lower()
        if not p:
            continue
        exts.add(p)
    return exts


def cmd_index(args: argparse.Namespace) -> int:
    opts = bm25tool.IndexOptions(
        exts=parse_exts(args.ext),
        stem=args.stem,
        stopwords=not args.no_stopwords,
        workers=args.workers,
    )
    stats = bm25tool.index(
        db_path=Path(args.out),
        root=Path(args.root),
        opts=opts,
        incremental=not args.full,
        progress=not args.no_progress,
    )
    print(
        "Indexed {indexed} docs (unchanged {unchanged}, removed {removed}, failed {failed}); total {total_docs} (avgdl {avgdl:.1f}) -> {out}".format(
            **stats, out=args.out
        )
    )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    exts = parse_exts(args.ext) if args.ext else None
    root, hits = bm25tool.search(
        db_path=Path(args.index),
        query=args.query,
        k=args.k,
        k1=args.k1,
        b=args.b,
        stem=args.stem,
        stopwords=not args.no_stopwords,
        path_boost=args.path_boost,
        path_filter=args.path,
        exts_filter=exts,
    )
    if not hits:
        print("No results.")
        return 1

    q_terms = bm25tool.tokenize(
        args.query,
        stem=args.stem,
        stopwords=(bm25tool.DEFAULT_STOPWORDS if not args.no_stopwords else set()),
    )
    color = args.color and sys.stdout.isatty()

    if args.json:
        out = []
        for h in hits:
            p = Path(root) / h.path
            line_no, snip = (None, "")
            if args.snippet:
                line_no, snip = bm25tool.snippet_with_line(p, q_terms)
            out.append(
                {
                    "score": h.score,
                    "path": h.path,
                    "line": line_no,
                    "snippet": snip,
                }
            )
        print(json.dumps(out, indent=2))
        return 0

    for rank, h in enumerate(hits, 1):
        p = Path(root) / h.path
        line_no, snip = (None, "")
        if args.snippet:
            line_no, snip = bm25tool.snippet_with_line(p, q_terms)
            snip = bm25tool.highlight(snip, q_terms, color=color)

        loc = h.path
        if line_no is not None:
            loc = f"{loc}:{line_no}"
        print(f"{rank:>2}. {h.score:>8.4f}  {loc}")
        if snip:
            print(f"    {snip}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    info = bm25tool.index_status(db_path=Path(args.index), cwd=Path.cwd())
    if info["indexed"]:
        print(
            f"Indexed: yes\n"
            f"Index: {info['db_path']}\n"
            f"Root: {info['root']}\n"
            f"Docs: {info['total_docs']}"
        )
        return 0

    print(
        f"Indexed: no\n"
        f"Index: {info['db_path']}\n"
        f"Reason: {info['reason']}\n"
        f"Root: {info['root']}\n"
        f"Docs: {info['total_docs']}"
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="search", add_help=True)
    p.add_argument("--index", default=bm25tool.DEFAULT_DB_PATH, help=f"Index DB path (default: {bm25tool.DEFAULT_DB_PATH})")
    p.add_argument("--k", type=int, default=10, help="Top K results (default: 10)")
    p.add_argument("--k1", type=float, default=1.2, help="BM25 k1 (default: 1.2)")
    p.add_argument("--b", type=float, default=0.75, help="BM25 b (default: 0.75)")
    p.add_argument("--stem", action="store_true", help="Enable a tiny stemmer (default: off)")
    p.add_argument("--no-stopwords", action="store_true", help="Disable stopword filtering")
    p.add_argument("--path", default=None, help="Only return results whose path contains this string")
    p.add_argument("--ext", default=None, help="Filter search results to these extensions/names (comma-separated)")
    p.add_argument("--path-boost", type=float, default=1.5, help="Boost matches in file path tokens (default: 1.5)")
    p.add_argument("--snippet", action="store_true", help="Show a short snippet (with line number)")
    p.add_argument("--json", action="store_true", help="Emit JSON results")
    p.add_argument("--color", action="store_true", help="Highlight matches in snippets (ANSI)")

    sub = p.add_subparsers(dest="cmd")
    p_index = sub.add_parser("index", help="Build/update index")
    p_index.add_argument("root", nargs="?", default=".", help="Root directory to index (default: .)")
    p_index.add_argument("--out", default=bm25tool.DEFAULT_DB_PATH, help=f"Output DB path (default: {bm25tool.DEFAULT_DB_PATH})")
    p_index.add_argument("--ext", default=None, help="Comma-separated extensions/names to index (e.g. .c,.h,.md,makefile)")
    p_index.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="Indexing workers")
    p_index.add_argument("--full", action="store_true", help="Full reindex (ignore incremental checks)")
    p_index.add_argument("--stem", action="store_true", help="Enable a tiny stemmer (default: off)")
    p_index.add_argument("--no-stopwords", action="store_true", help="Disable stopword filtering")
    p_index.add_argument("--no-progress", action="store_true", help="Disable progress output")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Search query (alternate form; you can also do: search \"terms\")")
    p_search.add_argument("query", help="Query string")
    p_search.set_defaults(func=cmd_search)

    p_status = sub.add_parser("status", help="Check whether current directory is covered by the index")
    p_status.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv
    p = build_parser()

    # Support: ./search "terms" (plus global flags).
    global_p = argparse.ArgumentParser(prog="search", add_help=False)
    global_p.add_argument("--index", default=bm25tool.DEFAULT_DB_PATH)
    global_p.add_argument("--k", type=int, default=10)
    global_p.add_argument("--k1", type=float, default=1.2)
    global_p.add_argument("--b", type=float, default=0.75)
    global_p.add_argument("--stem", action="store_true")
    global_p.add_argument("--no-stopwords", action="store_true")
    global_p.add_argument("--path", default=None)
    global_p.add_argument("--ext", default=None)
    global_p.add_argument("--path-boost", type=float, default=1.5)
    global_p.add_argument("--snippet", action="store_true")
    global_p.add_argument("--json", action="store_true")
    global_p.add_argument("--color", action="store_true")
    global_p.add_argument("-h", "--help", action="store_true")

    g, rest = global_p.parse_known_args(argv[1:])
    if g.help:
        p.parse_args(["--help"])
        return 0
    if rest and rest[0] in {"index", "search", "status"}:
        args = p.parse_args(argv[1:])
        if not hasattr(args, "func"):
            p.print_help()
            return 2
        return args.func(args)
    if rest:
        ns = argparse.Namespace(
            index=g.index,
            k=g.k,
            k1=g.k1,
            b=g.b,
            stem=g.stem,
            no_stopwords=g.no_stopwords,
            path=g.path,
            ext=g.ext,
            path_boost=g.path_boost,
            snippet=(True if not g.snippet else True),  # default on for shorthand
            json=g.json,
            color=g.color,
            query=" ".join(rest),
        )
        return cmd_search(ns)

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
