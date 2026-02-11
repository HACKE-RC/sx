import os
import tempfile
import unittest
from pathlib import Path

import bm25tool


class TestBM25Tool(unittest.TestCase):
    def test_index_and_search_basic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("redis replication backlog backlog", encoding="utf-8")
            (root / "b.txt").write_text("append only file aof fsync", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "replication.md").write_text("replication internals", encoding="utf-8")

            db = root / "idx.sqlite"
            stats = bm25tool.index(
                db_path=db,
                root=root,
                opts=bm25tool.IndexOptions(exts={".txt", ".md"}, workers=4),
                incremental=True,
            )
            self.assertEqual(stats["total_docs"], 3)

            _, hits = bm25tool.search(db_path=db, query="replication backlog", k=10)
            self.assertTrue(hits)
            # a.txt has both terms, should be very competitive.
            self.assertEqual(hits[0].path, "a.txt")

    def test_incremental_updates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("hello world", encoding="utf-8")
            (root / "b.txt").write_text("hello there", encoding="utf-8")
            db = root / "idx.sqlite"

            stats1 = bm25tool.index(
                db_path=db,
                root=root,
                opts=bm25tool.IndexOptions(exts={".txt"}, workers=4),
                incremental=True,
            )
            self.assertEqual(stats1["total_docs"], 2)

            # Reindex without changes: should see unchanged==2, indexed==0.
            stats2 = bm25tool.index(
                db_path=db,
                root=root,
                opts=bm25tool.IndexOptions(exts={".txt"}, workers=4),
                incremental=True,
            )
            self.assertEqual(stats2["unchanged"], 2)
            self.assertEqual(stats2["indexed"], 0)

            # Touch a.txt (ensure mtime changes on coarse filesystems).
            (root / "a.txt").write_text("hello world again", encoding="utf-8")
            os.utime(root / "a.txt", None)

            stats3 = bm25tool.index(
                db_path=db,
                root=root,
                opts=bm25tool.IndexOptions(exts={".txt"}, workers=4),
                incremental=True,
            )
            self.assertEqual(stats3["indexed"], 1)
            self.assertEqual(stats3["unchanged"], 1)


if __name__ == "__main__":
    unittest.main()

