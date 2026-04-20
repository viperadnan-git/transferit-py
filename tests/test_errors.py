"""Tests for the MegaAPIError code table + message formatting."""

from __future__ import annotations

import pytest

from transferit import MegaAPIError


class TestKnownCode:
    def test_every_code_has_entry(self):
        # Exhaustive check: each known code yields a non-empty message + name,
        # and the constructed exception matches the table exactly.
        for code, (name, message) in MegaAPIError.CODES.items():
            assert isinstance(code, int)
            assert name
            assert message
            ex = MegaAPIError(code=code)
            assert ex.name == name
            assert str(ex) == message

    @pytest.mark.parametrize(
        "code,keyword",
        [
            (-14, "password"),  # EKEY
            (-9, "not found"),  # ENOENT
            (-8, "expired"),  # EEXPIRED
        ],
    )
    def test_message_contains_user_keyword(self, code, keyword):
        # Semantic guard: the user-facing message must keep the word the CLI
        # relies on.  `test_every_code_has_entry` would still pass if both
        # sides were changed in lock-step, so this catches subtler drift.
        assert keyword in str(MegaAPIError(code=code)).lower()


class TestUnknownCode:
    def test_unknown_code_has_generic_message(self):
        ex = MegaAPIError(code=-999)
        assert ex.code == -999
        assert ex.name == ""
        assert "999" in str(ex)

    def test_no_code_is_generic(self):
        ex = MegaAPIError()
        assert ex.code is None
        assert ex.name == ""
        assert "MEGA" in str(ex)


class TestCustomMessage:
    def test_custom_message_overrides_default(self):
        ex = MegaAPIError("something bespoke")
        assert str(ex) == "something bespoke"
        assert ex.code is None
        assert ex.name == ""

    def test_custom_message_with_code_keeps_both(self):
        ex = MegaAPIError("custom ejection", code=-14)
        assert ex.code == -14
        assert ex.name == "EKEY"  # name still resolved from code
        assert str(ex) == "custom ejection"  # but message is respected


class TestFactory:
    def test_from_code_equivalent_to_kwarg(self):
        a = MegaAPIError.from_code(-14)
        b = MegaAPIError(code=-14)
        assert str(a) == str(b)
        assert a.code == b.code
        assert a.name == b.name

    def test_catchable_as_runtime_error(self):
        with pytest.raises(RuntimeError):
            raise MegaAPIError(code=-14)
