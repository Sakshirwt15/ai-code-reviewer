from app.diff_parser import chunk_diff, is_reviewable, parse_diff

DIFF = """diff --git a/app/a.py b/app/a.py
index 1..2 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1,3 +1,4 @@
 import os
-x = 1
+x = 2
+y = 3
 print(x)
diff --git a/package-lock.json b/package-lock.json
index 1..2 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,1 +1,1 @@
-{}
+{"a": 1}
diff --git a/logo.png b/logo.png
new file mode 100644
Binary files /dev/null and b/logo.png differ
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-gone = True
"""


def test_parse_line_numbers():
    files = parse_diff(DIFF)
    assert [f.path for f in files] == ["app/a.py", "package-lock.json", "logo.png", "old.py"]
    a = files[0]
    assert a.additions == 2 and a.deletions == 1
    assert a.added_lines() == {2, 3}
    assert a.commentable_lines() == {1, 2, 3, 4}  # ctx 1, add 2, add 3, ctx 4


def test_reviewable_filters_noise():
    a, lock, png, deleted = parse_diff(DIFF)
    assert is_reviewable(a)
    assert not is_reviewable(lock)
    assert not is_reviewable(png)
    assert not is_reviewable(deleted)


def test_dashes_inside_hunk_do_not_break_parser():
    text = """diff --git a/a.sql b/a.sql
--- a/a.sql
+++ b/a.sql
@@ -1,2 +1,2 @@
--- comment line removed
+-- comment line added
 SELECT 1;
"""
    f = parse_diff(text)[0]
    assert f.deletions == 1 and f.additions == 1


def test_chunking_splits_large_diffs_and_tracks_lines():
    body = "\n".join(f"+line number {i} " + "x" * 40 for i in range(200))
    text = f"diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -0,0 +1,200 @@\n{body}\n"
    files = parse_diff(text)
    chunks = chunk_diff(files, max_chars=2000)
    assert len(chunks) > 3
    seen = set().union(*(c.lines["big.py"] for c in chunks))
    assert seen == set(range(1, 201))  # nothing lost
    assert all(len(c.text) < 2600 for c in chunks)


def test_rendered_chunk_shows_new_line_numbers_and_redacts_secrets():
    text = """diff --git a/c.py b/c.py
--- a/c.py
+++ b/c.py
@@ -1,1 +1,2 @@
 import os
+PASSWORD = "hunter2hunter2"
"""
    chunk = chunk_diff(parse_diff(text))[0]
    assert "    2 + PASSWORD" in chunk.text
    assert "hunter2" not in chunk.text and "[REDACTED_SECRET]" in chunk.text


def test_wrong_hunk_counts_never_swallow_next_file():
    text = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,9 +1,9 @@\n+x\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1,1 +1,1 @@\n+y\n")
    assert [f.path for f in parse_diff(text)] == ["a.py", "b.py"]
