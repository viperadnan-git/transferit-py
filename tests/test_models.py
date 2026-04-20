"""Tests for TransferNode / TransferInfo dataclasses + their conversions."""

from __future__ import annotations

from transferit import TransferInfo, TransferNode
from transferit._models import DownloadResult, UploadResult


class TestTransferNode:
    def test_from_dict_file(self):
        n = TransferNode.from_dict(
            {
                "h": "abcdefgh",
                "p": "PARENT12",
                "t": 0,
                "s": 1234,
                "ts": 1700000000,
                "k": [1, 2, 3, 4, 5, 6, 7, 8],
                "name": "report.pdf",
            }
        )
        assert n.handle == "abcdefgh"
        assert n.parent == "PARENT12"
        assert n.is_file
        assert not n.is_folder
        assert n.size == 1234
        assert n.name == "report.pdf"

    def test_from_dict_folder(self):
        n = TransferNode.from_dict(
            {
                "h": "folder01",
                "p": "",
                "t": 1,
                "ts": 1700000000,
                "k": [1, 2, 3, 4],
                "name": "docs",
            }
        )
        assert n.is_folder
        assert not n.is_file
        assert n.size is None

    def test_from_dict_missing_optionals(self):
        n = TransferNode.from_dict({"h": "x", "t": 0, "k": []})
        assert n.parent == ""
        assert n.size is None
        assert n.timestamp is None
        assert n.name is None

    def test_to_json_dict_drops_raw_and_key(self):
        n = TransferNode.from_dict(
            {
                "h": "abcdefgh",
                "p": "",
                "t": 0,
                "s": 10,
                "ts": 1700000000,
                "k": [1, 2, 3, 4, 5, 6, 7, 8],
                "name": "f.txt",
            }
        )
        out = n.to_json_dict()
        assert "raw" not in out
        assert "key" not in out
        assert out["kind"] == "file"

    def test_to_json_dict_folder_kind_is_folder(self):
        n = TransferNode.from_dict({"h": "x", "t": 1, "k": [], "name": "sub"})
        assert n.to_json_dict()["kind"] == "folder"


class TestTransferInfo:
    def test_from_dict_populates_all_fields(self):
        raw = {
            "se": "me@x.com",
            "pw": 1,
            "z": "zip12345",
            "zp": 0,
            "title": "My Transfer",
            "message": "hi",
            "total_bytes": 5_000_000,
            "file_count": 3,
            "folder_count": 2,
        }
        info = TransferInfo.from_dict(
            "abcABC012345",
            raw,
            url="https://transfer.it/t/abcABC012345",
            root_handle="ROOT0001",
        )
        assert info.xh == "abcABC012345"
        assert info.url.endswith("abcABC012345")
        assert info.root_handle == "ROOT0001"
        assert info.sender == "me@x.com"
        assert info.password_protected is True
        assert info.zip_handle == "zip12345"
        assert info.zip_pending is False
        assert info.total_bytes == 5_000_000
        assert info.file_count == 3
        assert info.folder_count == 2

    def test_to_json_dict_has_stable_shape(self):
        info = TransferInfo.from_dict(
            "xh1234567890",
            {"total_bytes": 1, "file_count": 1, "folder_count": 1},
            url="https://transfer.it/t/xh1234567890",
        )
        out = info.to_json_dict()
        expected = {
            "xh",
            "url",
            "root_handle",
            "title",
            "sender",
            "message",
            "password_protected",
            "zip_handle",
            "zip_pending",
            "total_bytes",
            "file_count",
            "folder_count",
        }
        assert set(out) == expected


class TestResults:
    def test_upload_result_stringifies_to_url(self):
        r = UploadResult(
            xh="a1b2c3d4e5f6",
            url="https://transfer.it/t/a1b2c3d4e5f6",
            title="demo",
            total_bytes=100,
            file_count=1,
            folder_count=0,
        )
        assert str(r) == "https://transfer.it/t/a1b2c3d4e5f6"

    def test_download_result_json_shape(self):
        r = DownloadResult(
            xh="xh1234567890",
            output_dir="/tmp",
            paths=["/tmp/a.txt"],
            skipped=[],
            total_bytes=10,
        )
        out = r.to_json_dict()
        assert out["xh"] == "xh1234567890"
        assert out["paths"] == ["/tmp/a.txt"]
        assert out["skipped"] == []
