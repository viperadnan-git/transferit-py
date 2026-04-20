"""
End-to-end integration test — hits the real transfer.it/bt7 servers.

Opt-in: run with ``TRANSFERIT_ONLINE_TESTS=1 pytest``.  Otherwise skipped.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

import pytest

from transferit import Transferit

pytestmark = pytest.mark.network


def _sha1(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha1").hexdigest()


def test_single_file_roundtrip(tmp_path: Path) -> None:
    """Upload a random blob, read it back, assert byte-for-byte equality."""
    src = tmp_path / "random.bin"
    src.write_bytes(secrets.token_bytes(2_500_000))  # 2.5 MiB — multi-chunk
    sha_before = _sha1(src)

    with Transferit() as tx:
        result = tx.upload(src, title="pytest roundtrip")
        assert result.total_bytes == src.stat().st_size
        assert result.file_count == 1
        assert result.url.startswith("https://transfer.it/t/")

        nodes = tx.info(result.url)
        files = [n for n in nodes if n.is_file]
        assert len(files) == 1
        assert files[0].name == src.name
        assert files[0].size == src.stat().st_size

        out = tmp_path / "dl"
        dl = tx.download(result.url, out)
        assert dl.total_bytes == src.stat().st_size
        assert dl.paths == [str(out / src.name)]

    assert _sha1(out / src.name) == sha_before


def test_folder_roundtrip(tmp_path: Path) -> None:
    """Upload a nested folder, download into a fresh dir, diff the trees."""
    src = tmp_path / "src"
    (src / "sub1" / "nested").mkdir(parents=True)
    (src / "sub2").mkdir()

    files = {
        "readme.txt": b"root file\n",
        "sub1/data.txt": b"sub1 data\n",
        "sub1/nested/blob.bin": secrets.token_bytes(300_000),
        "sub2/note.md": b"# note\n",
    }
    for rel, payload in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)

    hashes_before = {rel: hashlib.sha1(data).hexdigest() for rel, data in files.items()}

    with Transferit() as tx:
        result = tx.upload(src, title="pytest folder")
        assert result.file_count == 4

        out = tmp_path / "dl"
        tx.download(result.url, out)

    for rel, expected in hashes_before.items():
        got = _sha1(out / rel)
        assert got == expected, f"{rel}: sha1 mismatch"


def test_metadata_roundtrip(tmp_path: Path) -> None:
    """xm fields set at upload time should round-trip through metadata()."""
    src = tmp_path / "m.bin"
    src.write_bytes(b"meta test")

    with Transferit() as tx:
        result = tx.upload(
            src,
            title="metadata test",
            sender="pytest@example.invalid",
            message="hello from ci",
            expiry="1h",
        )
        meta = tx.metadata(result.url)

    assert meta.title == "metadata test"
    assert meta.sender == "pytest@example.invalid"
    assert meta.message == "hello from ci"
    assert meta.password_protected is False
    assert meta.total_bytes == src.stat().st_size


def test_metadata_password_protected_no_pw(tmp_path: Path) -> None:
    """Metadata for a password-protected transfer is readable without pw —
    matches web-UI behaviour where the landing page shows title/sender/size
    before the password prompt."""
    src = tmp_path / "p.bin"
    src.write_bytes(b"guarded")

    with Transferit() as tx:
        result = tx.upload(
            src,
            title="pw-protected meta",
            sender="pytest@example.invalid",
            password="s3cret",
            expiry="1h",
        )
        # NO password passed to metadata() — should still succeed.
        meta = tx.metadata(result.url)

    assert meta.password_protected is True
    assert meta.title == "pw-protected meta"
    assert meta.sender == "pytest@example.invalid"
    assert meta.total_bytes == src.stat().st_size
