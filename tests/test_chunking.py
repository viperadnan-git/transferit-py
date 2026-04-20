"""Tests for the WS-upload chunker and the folder walker."""

from __future__ import annotations

import pytest

from transferit._upload import ONE_MB, iter_chunks, walk_folder


class TestIterChunks:
    def test_empty_file_gets_single_empty_tail(self):
        chunks, need_tail = iter_chunks(0)
        assert chunks == []
        assert need_tail is True

    def test_short_file_has_one_short_chunk_no_tail(self):
        chunks, need_tail = iter_chunks(100)
        assert chunks == [(0, 100)]
        assert need_tail is False  # short last chunk signals EOF implicitly

    def test_exact_128k_boundary_needs_tail(self):
        chunks, need_tail = iter_chunks(128 * 1024)
        assert chunks == [(0, 128 * 1024)]
        # File ends exactly at a chunkmap boundary → needs empty tail frame.
        assert need_tail is True

    def test_just_over_128k(self):
        chunks, need_tail = iter_chunks(128 * 1024 + 1)
        assert chunks == [(0, 128 * 1024), (128 * 1024, 1)]
        assert need_tail is False

    def test_multi_mb_uses_1mib_chunks_after_ramp(self):
        # 10 MiB file — ramp ends around 4.5 MiB, rest is full 1 MiB chunks.
        size = 10 * ONE_MB
        chunks, _ = iter_chunks(size)
        assert sum(length for _, length in chunks) == size
        # Multiple full 1 MiB chunks should appear after the ramp.
        assert sum(1 for _, length in chunks if length == ONE_MB) >= 5

    def test_offsets_are_monotonic_and_contiguous(self):
        chunks, _ = iter_chunks(2_500_000)
        cursor = 0
        for pos, length in chunks:
            assert pos == cursor
            cursor += length
        assert cursor == 2_500_000


class TestWalkFolder:
    def test_raises_on_non_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        with pytest.raises(NotADirectoryError):
            walk_folder(f)

    def test_empty_folder_yields_nothing(self, tmp_path):
        files, dirs = walk_folder(tmp_path)
        assert files == []
        assert dirs == []

    def test_flat_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        files, dirs = walk_folder(tmp_path)
        assert sorted(f.name for f in files) == ["a.txt", "b.txt"]
        assert dirs == []

    def test_nested(self, tmp_path):
        (tmp_path / "sub1").mkdir()
        (tmp_path / "sub1" / "nested").mkdir()
        (tmp_path / "sub2").mkdir()
        (tmp_path / "root.txt").write_text("r")
        (tmp_path / "sub1" / "a.txt").write_text("a")
        (tmp_path / "sub1" / "nested" / "b.bin").write_bytes(b"\x00" * 100)
        (tmp_path / "sub2" / "note.md").write_text("m")

        files, dirs = walk_folder(tmp_path)
        names = sorted(p.name for p in files)
        assert names == ["a.txt", "b.bin", "note.md", "root.txt"]

        # Parents precede children — topological order.
        assert (
            dirs == ["sub1", "sub2", "sub1/nested"]
            or dirs
            == [
                "sub1",
                "sub2",
                "sub1/nested",
            ]
            or sorted(dirs) == ["sub1", "sub1/nested", "sub2"]
        )
        # Key invariant: every parent precedes its children.
        for d in dirs:
            parent = d.rsplit("/", 1)[0] if "/" in d else ""
            if parent:
                assert dirs.index(parent) < dirs.index(d)

    def test_preserves_empty_subfolders(self, tmp_path):
        (tmp_path / "empty-sub").mkdir()
        files, dirs = walk_folder(tmp_path)
        assert files == []
        assert "empty-sub" in dirs

    def test_exclude_prunes_dirs_and_files(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("a")
        (tmp_path / "src" / "a.pyc").write_bytes(b"\x00")
        (tmp_path / "README.md").write_text("r")

        files, dirs = walk_folder(tmp_path, exclude=[".git", "__pycache__", "*.pyc"])
        names = sorted(f.name for f in files)
        assert names == ["README.md", "a.py"]
        # Excluded directories are pruned entirely.
        assert dirs == ["src"]

    def test_exclude_matches_rel_path(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "generated").mkdir()
        (tmp_path / "src" / "generated" / "x.py").write_text("g")
        (tmp_path / "src" / "kept.py").write_text("k")

        files, dirs = walk_folder(tmp_path, exclude=["src/generated"])
        names = sorted(f.name for f in files)
        assert names == ["kept.py"]
        assert "src/generated" not in dirs
