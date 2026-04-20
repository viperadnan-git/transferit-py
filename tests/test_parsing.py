"""Tests for parse_duration, MegaAPI.parse_xh, MegaAPI.derive_password."""

from __future__ import annotations

import pytest

from transferit import MegaAPI
from transferit._transfer import (
    MAX_EXPIRY_SECONDS,
    MIN_EXPIRY_SECONDS,
    cast_expiry_seconds,
    humanise_duration,
    parse_duration,
)


class TestParseDuration:
    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("30s", 30),
            ("5m", 300),
            ("2h", 7200),
            ("7d", 86400 * 7),
            ("1w", 86400 * 7),
            ("1y", 86400 * 365),
            ("3600", 3600),  # bare int → seconds
            ("1y6m", 86400 * 365 + 60 * 6),  # compound
            ("2h30m", 2 * 3600 + 30 * 60),
            ("1d 12h", 86400 + 12 * 3600),  # spaces OK
            ("1H", 3600),  # case-insensitive
        ],
    )
    def test_valid(self, inp, expected):
        assert parse_duration(inp) == expected

    @pytest.mark.parametrize("inp", ["", "   ", "abc", "10x", "5.5s", "1y-"])
    def test_invalid_raises(self, inp):
        with pytest.raises(ValueError):
            parse_duration(inp)

    def test_none_raises(self):
        with pytest.raises(ValueError):
            parse_duration(None)  # type: ignore[arg-type]


class TestHumaniseDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0s"),
            (-10, "0s"),
            (1, "1s"),
            (60, "1m"),
            (3600, "1h"),
            (86400, "1d"),
            (86400 * 7, "1w"),
            (86400 * 365, "1y"),
            (86400 * 370, "1y5d"),
        ],
    )
    def test_humanise(self, seconds, expected):
        assert humanise_duration(seconds) == expected


class TestCastExpirySeconds:
    @pytest.mark.parametrize("value", [None, 0])
    def test_empty_returns_none(self, value):
        # Both `None` and `0` mean "no expiry" — caller should omit the field.
        assert cast_expiry_seconds(value) is None

    def test_in_range_passes_through(self):
        assert cast_expiry_seconds(3600) == 3600
        assert cast_expiry_seconds(MIN_EXPIRY_SECONDS) == MIN_EXPIRY_SECONDS
        assert cast_expiry_seconds(MAX_EXPIRY_SECONDS) == MAX_EXPIRY_SECONDS

    @pytest.mark.parametrize("seconds", [-1, MAX_EXPIRY_SECONDS + 1, 10**12])
    def test_out_of_range_raises(self, seconds):
        with pytest.raises(ValueError):
            cast_expiry_seconds(seconds)


class TestParseXh:
    @pytest.mark.parametrize(
        "inp,expected",
        [
            ("abcABC012345", "abcABC012345"),
            ("https://transfer.it/t/abcABC012345", "abcABC012345"),
            ("https://transfer.it/t/abcABC012345/", "abcABC012345"),
            ("https://transfer.it/t/abcABC012345?foo=bar", "abcABC012345"),
            ("https://transfer.it/t/abcABC012345#frag", "abcABC012345"),
            ("/t/abcABC012345", "abcABC012345"),
        ],
    )
    def test_valid(self, inp, expected):
        assert MegaAPI.parse_xh(inp) == expected

    @pytest.mark.parametrize(
        "inp",
        [
            "",
            "too-short",
            "way-way-way-too-long-for-an-xh",
            "http://nope.example/t/abcABC012345?",  # actually valid — xh is 12 chars base64url
        ],
    )
    def test_invalid_or_still_parses(self, inp):
        # The last one contains a valid 12-char handle — document that it IS accepted.
        if inp and "abcABC012345" in inp:
            assert MegaAPI.parse_xh(inp) == "abcABC012345"
        else:
            with pytest.raises(ValueError):
                MegaAPI.parse_xh(inp)


class TestDerivePassword:
    def test_deterministic(self):
        xh = "abcABC012345"
        assert MegaAPI.derive_password(xh, "hunter2") == MegaAPI.derive_password(
            xh, "hunter2"
        )

    def test_different_password_different_output(self):
        xh = "abcABC012345"
        assert MegaAPI.derive_password(xh, "a") != MegaAPI.derive_password(xh, "b")

    def test_different_xh_different_salt(self):
        # Same password, different xh → different token (salt from xh).
        a = MegaAPI.derive_password("aaaaaaaaaaaa", "pw")
        b = MegaAPI.derive_password("bbbbbbbbbbbb", "pw")
        assert a != b

    def test_token_is_base64url_of_32_bytes(self):
        from base64 import urlsafe_b64decode

        token = MegaAPI.derive_password("abcABC012345", "pw")
        # 32 bytes = 43 base64url chars (without padding).
        assert len(token) == 43
        raw = urlsafe_b64decode(token + "=")
        assert len(raw) == 32
