from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import sys
import time
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple


DEFAULT_DB_PATH = "bm25.sqlite"


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
}


DEFAULT_EXTS = {
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".py",
    ".go",
    ".rs",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".zsh",
    ".bash",
    ".md",
    ".txt",
    ".rst",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".mk",
    ".make",
    "makefile",
}


DEFAULT_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "he",
    "her",
    "his",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "not",
    "of",
    "on",
    "or",
    "our",
    "s",
    "she",
    "so",
    "t",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "you",
    "your",
}


_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _split_identifier(token: str) -> List[str]:
    # Split snake_case and camelCase-ish tokens.
    parts: List[str] = []
    for p in token.split("_"):
        if not p:
            continue
        # Split camel case boundaries: "RedisModule_Load" -> ["Redis", "Module", "Load"]
        start = 0
        for i in range(1, len(p)):
            c0 = p[i - 1]
            c1 = p[i]
            if c0.islower() and c1.isupper():
                parts.append(p[start:i])
                start = i
            elif c0.isalpha() and c1.isdigit():
                parts.append(p[start:i])
                start = i
            elif c0.isdigit() and c1.isalpha():
                parts.append(p[start:i])
                start = i
        parts.append(p[start:])
    return [x for x in parts if len(x) >= 2]


def simple_stem(term: str) -> str:
    # Very small, dependency-free stemmer. Off by default.
    # Not Porter; just enough to reduce obvious English variants.
    t = term
    if len(t) <= 3:
        return t
    for suf in ("'s",):
        if t.endswith(suf):
            t = t[: -len(suf)]
    for suf in ("ing", "ers", "er", "edly", "edly", "ed", "ly", "es", "s"):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            t = t[: -len(suf)]
            break
    return t


def tokenize(text: str, *, stem: bool = False, stopwords: Optional[set[str]] = None) -> List[str]:
    out: List[str] = []
    sw = stopwords or set()
    for m in _WORD_RE.finditer(text):
        tok = m.group(0)
        for part in _split_identifier(tok):
            t = part.lower()
            if stem:
                t = simple_stem(t)
            if len(t) < 2 or t in sw:
                continue
            out.append(t)
    return out


def is_probably_text_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
        return b"\x00" not in chunk
    except OSError:
        return False


def should_index_file(path: Path, exts: set[str]) -> bool:
    name = path.name.lower()
    suf = path.suffix.lower()
    return (name in exts) or (suf in exts)


def iter_files(root: Path, exts: set[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            if not p.is_file():
                continue
            if not should_index_file(p, exts):
                continue
            if not is_probably_text_file(p):
                continue
            yield p


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def file_sig(path: Path) -> Tuple[int, int]:
    st = path.stat()
    # ns mtime is nicer but not always available; int seconds is fine.
    return (int(st.st_mtime), int(st.st_size))


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    try:
        with path.open("rb") as f:
            while True:
                b = f.read(1024 * 1024)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return ""


@dataclass
class SearchHit:
    score: float
    path: str
    docid: int
    line: Optional[int] = None
    snippet: str = ""


def bm25_idf(n_docs: int, df: int) -> float:
    return math.log(((n_docs - df + 0.5) / (df + 0.5)) + 1.0)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    # Pragmas chosen for "fast enough" without surprising durability tradeoffs.
    con.execute("PRAGMA foreign_keys=ON;")
    con.execute("PRAGMA busy_timeout=5000;")
    # Avoid temp files for GROUP BY / sorting where possible (more portable).
    con.execute("PRAGMA temp_store=MEMORY;")
    # WAL is great, but some filesystems/sandboxes don't support it reliably.
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.OperationalError:
        pass
    return con


def init_db(con: sqlite3.Connection) -> None:
    """
    SQLite schema notes:
      - docs: one row per file, keyed by integer docid.
      - postings: one row per (term, docid) with tf.
      - terms: df cache (derived from postings), rebuilt on index update.
    """
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          k TEXT PRIMARY KEY,
          v TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS docs (
          docid INTEGER PRIMARY KEY,
          path TEXT NOT NULL UNIQUE,
          len INTEGER NOT NULL,
          mtime INTEGER NOT NULL,
          size INTEGER NOT NULL,
          sha1 TEXT NOT NULL,
          path_tokens TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS terms (
          term TEXT PRIMARY KEY,
          df INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS postings (
          term TEXT NOT NULL,
          docid INTEGER NOT NULL,
          tf INTEGER NOT NULL,
          PRIMARY KEY(term, docid),
          FOREIGN KEY(docid) REFERENCES docs(docid) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_postings_term ON postings(term);
        CREATE INDEX IF NOT EXISTS idx_docs_path ON docs(path);
        """
    )


def _doc_len(con: sqlite3.Connection) -> Tuple[int, float]:
    row = con.execute("SELECT COUNT(*), COALESCE(AVG(len), 0.0) FROM docs").fetchone()
    return (int(row[0]), float(row[1]))


def _path_tokens(rel: str, *, stem: bool, stopwords: set[str]) -> List[str]:
    return tokenize(rel.replace(os.sep, " "), stem=stem, stopwords=stopwords)


@dataclass(frozen=True)
class _IndexTask:
    root: str
    rel: str
    stem: bool
    use_stopwords: bool


@dataclass(frozen=True)
class _IndexedDoc:
    rel: str
    mtime: int
    size: int
    sha1: str
    tf: Dict[str, int]
    path_tokens: List[str]


def _index_one_file(task: _IndexTask) -> Optional[_IndexedDoc]:
    # Runs in a worker thread: read + tokenize + tf.
    root_s, rel, stem, use_stopwords = task.root, task.rel, task.stem, task.use_stopwords
    path = Path(root_s) / rel
    try:
        mtime, size = file_sig(path)
    except OSError:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    sha1 = sha1_bytes(data)
    text = data.decode("utf-8", errors="replace")
    sw = DEFAULT_STOPWORDS if use_stopwords else set()
    toks = tokenize(text, stem=stem, stopwords=sw)
    if not toks:
        return None
    tf = Counter(toks)
    path_toks = _path_tokens(rel, stem=stem, stopwords=sw)
    return _IndexedDoc(rel=rel, mtime=mtime, size=size, sha1=sha1, tf=dict(tf), path_tokens=path_toks)


@dataclass
class IndexOptions:
    exts: set[str]
    stem: bool = False
    stopwords: bool = True
    workers: int = max(1, (os.cpu_count() or 2) - 1)


class _Progress:
    def __init__(self, *, enabled: bool, total: int) -> None:
        self.enabled = enabled
        self.total = total
        self.done = 0
        self.failed = 0
        self._t0 = time.monotonic()
        self._last_print = 0.0
        self._isatty = sys.stderr.isatty()

    def _fmt_eta(self) -> str:
        if self.done <= 0:
            return "eta ?"
        elapsed = time.monotonic() - self._t0
        rate = self.done / max(elapsed, 1e-6)
        remaining = max(self.total - self.done, 0)
        eta = remaining / max(rate, 1e-6)
        if eta >= 3600:
            return f"eta {eta/3600:.1f}h"
        if eta >= 60:
            return f"eta {eta/60:.1f}m"
        return f"eta {eta:.0f}s"

    def update(self, *, inc_done: int = 0, inc_failed: int = 0, phase: str = "indexing") -> None:
        if not self.enabled:
            return
        self.done += inc_done
        self.failed += inc_failed

        now = time.monotonic()
        # Don't spam; keep it smooth enough on big repos.
        if (now - self._last_print) < 0.05 and self.done < self.total:
            return
        self._last_print = now

        pct = (100.0 * self.done / self.total) if self.total else 100.0
        eta = self._fmt_eta()

        if self._isatty:
            width = shutil.get_terminal_size((100, 20)).columns
            # Keep some room for counters and ETA.
            bar_w = max(10, min(40, width - 55))
            filled = int(round((self.done / self.total) * bar_w)) if self.total else bar_w
            filled = max(0, min(bar_w, filled))
            bar = "[" + ("=" * filled) + (" " * (bar_w - filled)) + "]"
            msg = f"{phase} {bar} {pct:5.1f}% {self.done}/{self.total}  fail {self.failed}  {eta}"
        else:
            msg = f"{phase}: {self.done}/{self.total} ({pct:5.1f}%), failed {self.failed}, {eta}"

        if self._isatty:
            width = shutil.get_terminal_size((100, 20)).columns
            sys.stderr.write("\r" + msg[: max(0, width - 1)].ljust(max(0, width - 1)))
            sys.stderr.flush()
        else:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def finish(self, *, phase: str = "done") -> None:
        if not self.enabled:
            return
        if self._isatty:
            # Force a final render even if throttling would skip it.
            self._last_print = 0.0
            self.update(phase=phase)
            sys.stderr.write("\n")
            sys.stderr.flush()


def index(
    *,
    db_path: Path,
    root: Path,
    opts: IndexOptions,
    incremental: bool = True,
    progress: bool = False,
) -> Dict[str, int]:
    root = root.resolve()
    db_path = Path(db_path)

    con = _connect(db_path)
    init_db(con)
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('root', ?)", (str(root),))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('version', '2')")
    con.commit()

    # Build set of candidate rel paths.
    rels: List[str] = []
    for p in iter_files(root, opts.exts):
        try:
            rels.append(str(p.resolve().relative_to(root)))
        except Exception:
            continue
    prog = _Progress(enabled=progress, total=0)
    if progress:
        sys.stderr.write(f"scan: {len(rels)} candidate files under {root}\n")

    existing: Dict[str, Tuple[int, int, str, int]] = {}
    for row in con.execute("SELECT path, mtime, size, sha1, docid FROM docs"):
        existing[str(row[0])] = (int(row[1]), int(row[2]), str(row[3]), int(row[4]))

    to_remove = set(existing.keys()) - set(rels)
    to_consider = rels

    to_index: List[str] = []
    unchanged = 0

    if incremental:
        for rel in to_consider:
            p = root / rel
            try:
                mtime, size = file_sig(p)
            except OSError:
                continue
            ex = existing.get(rel)
            if ex and ex[0] == mtime and ex[1] == size:
                unchanged += 1
                continue
            # Even if the file changed, it may be empty/non-text and yield no tokens.
            # We still consider it "unchanged" for future runs by updating docs metadata.
            to_index.append(rel)
    else:
        to_index = list(to_consider)

    prog = _Progress(enabled=progress, total=len(to_index))
    if progress:
        sys.stderr.write(
            f"plan: {len(to_index)} to index, {unchanged} unchanged, {len(to_remove)} to remove\n"
        )

    # Remove docs and their postings first (including stale docs).
    if to_remove:
        con.execute("BEGIN")
        for rel in to_remove:
            row = con.execute("SELECT docid FROM docs WHERE path=?", (rel,)).fetchone()
            if not row:
                continue
            docid = int(row[0])
            con.execute("DELETE FROM docs WHERE docid=?", (docid,))
        con.execute("COMMIT")

    # For changed docs, delete old postings and reinsert.
    if to_index:
        con.execute("BEGIN")
        for rel in to_index:
            row = con.execute("SELECT docid FROM docs WHERE path=?", (rel,)).fetchone()
            if row:
                con.execute("DELETE FROM postings WHERE docid=?", (int(row[0]),))
        con.execute("COMMIT")

    sw = DEFAULT_STOPWORDS if opts.stopwords else set()

    indexed = 0
    failed = 0
    skipped_empty = 0
    # Parallel content work, single-writer DB updates.
    tasks = [_IndexTask(root=str(root), rel=rel, stem=opts.stem, use_stopwords=opts.stopwords) for rel in to_index]
    results: List[_IndexedDoc] = []
    if tasks:
        # Use threads instead of processes since some environments (including
        # sandboxes) restrict multiprocessing primitives like semaphores.
        with ThreadPoolExecutor(max_workers=opts.workers) as ex:
            futs = [ex.submit(_index_one_file, t) for t in tasks]
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                except Exception:
                    failed += 1
                    prog.update(inc_failed=1, phase="indexing")
                    continue
                if r is None:
                    # File produced no tokens (empty / not decodable / binary). Update docs
                    # metadata so incremental runs won't keep retrying it.
                    skipped_empty += 1
                    prog.update(inc_failed=1, phase="indexing")
                    continue
                results.append(r)
                prog.update(inc_done=1, phase="indexing")
    prog.finish(phase="indexing")

    if skipped_empty:
        have = {d.rel for d in results}
        con.execute("BEGIN")
        for rel in to_index:
            if rel in have:
                continue
            # Store a zero-len doc record so incremental runs won't keep retrying
            # files that yield no tokens (empty / whitespace-only / etc).
            p = root / rel
            try:
                mtime, size = file_sig(p)
            except OSError:
                continue
            sha1 = sha1_file(p)
            path_tokens = " ".join(_path_tokens(rel, stem=opts.stem, stopwords=sw))
            con.execute(
                """
                INSERT INTO docs(path, len, mtime, size, sha1, path_tokens)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  len=excluded.len,
                  mtime=excluded.mtime,
                  size=excluded.size,
                  sha1=excluded.sha1,
                  path_tokens=excluded.path_tokens
                """,
                (rel, 0, int(mtime), int(size), sha1, path_tokens),
            )
        con.execute("COMMIT")

    if progress and results:
        sys.stderr.write(f"db: writing {len(results)} docs\n")
    con.execute("BEGIN")
    for doc in results:
        rel, mtime, size, sha1, tf, path_toks = (
            doc.rel,
            doc.mtime,
            doc.size,
            doc.sha1,
            doc.tf,
            doc.path_tokens,
        )
        dl = int(sum(tf.values()))
        path_tokens = " ".join(path_toks)
        con.execute(
            """
            INSERT INTO docs(path, len, mtime, size, sha1, path_tokens)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
              len=excluded.len,
              mtime=excluded.mtime,
              size=excluded.size,
              sha1=excluded.sha1,
              path_tokens=excluded.path_tokens
            """,
            (rel, dl, int(mtime), int(size), sha1, path_tokens),
        )
        docid = int(con.execute("SELECT docid FROM docs WHERE path=?", (rel,)).fetchone()[0])
        con.executemany(
            "INSERT OR REPLACE INTO postings(term, docid, tf) VALUES(?,?,?)",
            ((term, docid, int(freq)) for term, freq in tf.items() if term not in sw),
        )
        indexed += 1
    con.execute("COMMIT")

    # Recompute df table from postings only if we changed anything.
    if indexed or len(to_remove) or skipped_empty:
        if progress:
            sys.stderr.write("db: rebuilding term document-frequencies\n")
        con.execute("BEGIN")
        con.execute("DELETE FROM terms")
        con.execute("INSERT INTO terms(term, df) SELECT term, COUNT(*) FROM postings GROUP BY term")
        con.execute("COMMIT")

    total_docs, avgdl = _doc_len(con)
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('avgdl', ?)", (str(avgdl),))
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('total_docs', ?)", (str(total_docs),))
    con.commit()
    con.close()

    return {
        "total_docs": total_docs,
        "avgdl": avgdl,
        "indexed": indexed,
        "unchanged": unchanged,
        "removed": len(to_remove),
        "failed": failed,
    }


def _get_meta(con: sqlite3.Connection, key: str, default: str = "") -> str:
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return str(row[0]) if row else default


def index_status(*, db_path: Path, cwd: Path) -> Dict[str, object]:
    """
    Return status information for an index DB relative to the given cwd.
    """
    p = Path(db_path)
    if not p.exists():
        return {
            "exists": False,
            "indexed": False,
            "reason": "index file does not exist",
            "db_path": str(p),
            "root": None,
            "total_docs": 0,
        }

    con = _connect(p)
    try:
        init_db(con)
        root = _get_meta(con, "root", "")
        total_docs = int(_get_meta(con, "total_docs", "0") or "0")
    finally:
        con.close()

    if not root:
        return {
            "exists": True,
            "indexed": False,
            "reason": "index exists but has no root metadata",
            "db_path": str(p),
            "root": None,
            "total_docs": total_docs,
        }

    root_path = Path(root).resolve()
    cwd_path = Path(cwd).resolve()

    # Consider current directory indexed if it is the same directory or
    # a child of the indexed root.
    indexed = cwd_path == root_path or root_path in cwd_path.parents
    reason = "ok" if indexed else "current directory is outside indexed root"

    return {
        "exists": True,
        "indexed": indexed,
        "reason": reason,
        "db_path": str(p),
        "root": str(root_path),
        "total_docs": total_docs,
    }


def search(
    *,
    db_path: Path,
    query: str,
    k: int = 10,
    k1: float = 1.2,
    b: float = 0.75,
    stem: bool = False,
    stopwords: bool = True,
    path_boost: float = 1.5,
    path_filter: Optional[str] = None,
    exts_filter: Optional[set[str]] = None,
) -> Tuple[str, List[SearchHit]]:
    con = _connect(Path(db_path))
    init_db(con)
    root = _get_meta(con, "root", ".")
    total_docs = int(_get_meta(con, "total_docs", "0") or "0")
    avgdl = float(_get_meta(con, "avgdl", "0") or "0") or 1.0
    if total_docs <= 0:
        con.close()
        return (root, [])

    sw = DEFAULT_STOPWORDS if stopwords else set()

    # Support | alternation: split on |, tokenize each part, and also
    # regex-match raw alternatives against the terms table.
    if "|" in query:
        alternatives = [a.strip() for a in query.split("|") if a.strip()]
        q_terms: List[str] = []
        for alt in alternatives:
            q_terms.extend(tokenize(alt, stem=stem, stopwords=sw))
        # Also regex-match the lowered alternatives directly against index terms
        # to catch cases where the user typed exact tokens (e.g. "load|parse").
        try:
            pat = "|".join(re.escape(a.lower()) for a in alternatives if a)
            rx = re.compile(pat)
            for row in con.execute("SELECT term FROM terms"):
                t = str(row[0])
                if rx.fullmatch(t) and t not in q_terms:
                    q_terms.append(t)
        except re.error:
            pass
        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: List[str] = []
        for t in q_terms:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        q_terms = deduped
    else:
        q_terms = tokenize(query, stem=stem, stopwords=sw)

    if not q_terms:
        con.close()
        return (root, [])

    doc_scores: Dict[int, float] = defaultdict(float)
    doc_paths: Dict[int, str] = {}
    doc_lens: Dict[int, int] = {}
    doc_path_tokens: Dict[int, set[str]] = {}

    for term in q_terms:
        row = con.execute("SELECT df FROM terms WHERE term=?", (term,)).fetchone()
        if not row:
            continue
        df = int(row[0])
        idf = bm25_idf(total_docs, df)

        # Pull postings joined with doc metadata in one query (avoid N+1).
        if path_filter:
            q = """
                SELECT p.docid, p.tf, d.path, d.len, d.path_tokens
                FROM postings p
                JOIN docs d ON d.docid = p.docid
                WHERE p.term = ? AND d.path LIKE ?
            """
            params = (term, f"%{path_filter}%")
        else:
            q = """
                SELECT p.docid, p.tf, d.path, d.len, d.path_tokens
                FROM postings p
                JOIN docs d ON d.docid = p.docid
                WHERE p.term = ?
            """
            params = (term,)

        for docid, tf, path, dlen, path_tokens in con.execute(q, params):
            docid = int(docid)
            tf = int(tf)
            p = str(path)
            if exts_filter:
                suf = Path(p).suffix.lower()
                if (Path(p).name.lower() not in exts_filter) and (suf not in exts_filter):
                    continue
            if docid not in doc_lens:
                doc_paths[docid] = p
                doc_lens[docid] = int(dlen)
                doc_path_tokens[docid] = set(str(path_tokens).split())

            dl = doc_lens[docid] or 1
            denom = tf + k1 * (1.0 - b + b * (dl / avgdl))
            score = idf * (tf * (k1 + 1.0)) / denom
            if term in doc_path_tokens.get(docid, ()):
                score *= path_boost
            doc_scores[docid] += score

    if not doc_scores:
        con.close()
        return (root, [])

    top = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)[:k]
    hits = [SearchHit(score=float(score), path=doc_paths.get(docid, ""), docid=int(docid)) for docid, score in top]
    con.close()
    return (root, hits)


def snippet_with_line(path: Path, terms: Sequence[str], max_len: int = 220) -> Tuple[Optional[int], str]:
    text = read_text(path)
    if not text:
        return (None, "")
    lower = text.lower()
    pos = None
    for t in terms:
        p = lower.find(t)
        if p != -1 and (pos is None or p < pos):
            pos = p
    if pos is None:
        line = text.splitlines()[0] if text else ""
        return (1, line[:max_len])

    # Find line boundaries and number.
    line_start = text.rfind("\n", 0, pos)
    line_start = 0 if line_start == -1 else line_start + 1
    line_end = text.find("\n", pos)
    line_end = len(text) if line_end == -1 else line_end
    line = text[line_start:line_end].strip()
    line_no = text.count("\n", 0, line_start) + 1
    if len(line) > max_len:
        # Trim around match.
        rel = pos - line_start
        start = max(0, rel - max_len // 3)
        end = min(len(line), start + max_len)
        line = line[start:end].strip()
    return (line_no, re.sub(r"\s+", " ", line))


def highlight(s: str, terms: Sequence[str], *, color: bool) -> str:
    if not color or not terms:
        return s
    # Simple case-insensitive highlight.
    out = s
    for t in sorted(set(terms), key=len, reverse=True):
        if len(t) < 2:
            continue
        out = re.sub(
            re.escape(t),
            lambda m: f"\x1b[1;31m{m.group(0)}\x1b[0m",
            out,
            flags=re.IGNORECASE,
        )
    return out


