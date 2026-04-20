"""Unit tests for the pure-crypto primitives in transferit._crypto."""

from __future__ import annotations

import secrets

import pytest

from transferit._crypto import (
    CHUNKMAP,
    ONE_MB,
    a32_to_b64,
    a32_to_bytes,
    attr_key,
    b64_to_a32,
    b64url_decode,
    b64url_encode,
    bytes_to_a32,
    condense_macs,
    crc32b,
    decrypt_attr,
    decrypt_key_ecb,
    encrypt_attr,
    encrypt_chunk_and_mac,
    encrypt_key_ecb,
    rand_a32,
)

# ---------- base64url ----------


class TestBase64Url:
    @pytest.mark.parametrize("data", [b"", b"\x00", b"hello", b"\xff" * 32])
    def test_roundtrip(self, data):
        assert b64url_decode(b64url_encode(data)) == data

    def test_no_padding_chars(self):
        assert "=" not in b64url_encode(b"hi")

    def test_urlsafe_alphabet(self):
        enc = b64url_encode(bytes(range(256)))
        assert "+" not in enc
        assert "/" not in enc


# ---------- a32 helpers ----------


class TestA32:
    def test_bytes_roundtrip(self):
        data = [0xDEADBEEF, 0x01020304, 0, 0xFFFFFFFF]
        assert bytes_to_a32(a32_to_bytes(data)) == data

    def test_bytes_is_big_endian(self):
        assert a32_to_bytes([0x12345678]) == b"\x12\x34\x56\x78"

    def test_b64_roundtrip(self):
        data = [1, 2, 3, 4]
        assert b64_to_a32(a32_to_b64(data)) == data

    def test_bytes_pads_non_aligned_length(self):
        # Odd-length inputs are zero-padded to the next uint32 boundary.
        assert bytes_to_a32(b"\x01") == [0x01_00_00_00]

    def test_rand_a32_length(self):
        assert len(rand_a32(6)) == 6
        assert all(0 <= x <= 0xFFFFFFFF for x in rand_a32(10))

    def test_rand_a32_is_non_deterministic(self):
        # Two independent draws of 4 uint32s colliding is essentially impossible.
        assert rand_a32(4) != rand_a32(4)


# ---------- AES-ECB key wrap ----------


class TestKeyWrap:
    def test_encrypt_decrypt_roundtrip(self):
        key = secrets.token_bytes(16)
        plain = [1, 2, 3, 4, 5, 6, 7, 8]
        assert decrypt_key_ecb(key, encrypt_key_ecb(key, plain)) == plain

    def test_block_size(self):
        # Encrypts 4 uint32s (128 bits) at a time — 8 inputs → 8 outputs.
        key = secrets.token_bytes(16)
        out = encrypt_key_ecb(key, [0] * 8)
        assert len(out) == 8


# ---------- attr key reduction (XOR folding) ----------


class TestAttrKey:
    def test_folder_key_is_passthrough_when_only_4_elements(self):
        # Tail treated as zero — attr_key is just the first four uint32s.
        k = [0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD]
        assert attr_key(k) == a32_to_bytes(k)

    def test_file_key_xor_reduction(self):
        # For files the 4-element attr key is [k0^k4, k1^k5, k2^k6, k3^k7].
        k = [
            0x11111111,
            0x22222222,
            0x33333333,
            0x44444444,
            0xAAAAAAAA,
            0xBBBBBBBB,
            0xCCCCCCCC,
            0xDDDDDDDD,
        ]
        expected = a32_to_bytes([k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]])
        assert attr_key(k) == expected


# ---------- node attribute encryption ----------


class TestAttrs:
    def test_encrypt_decrypt_roundtrip(self):
        key = [1, 2, 3, 4]
        attrs = {"n": "hello.txt"}
        blob = encrypt_attr(attrs, key)
        dec = decrypt_attr(b64url_encode(blob), key)
        assert dec == attrs

    def test_decrypt_rejects_wrong_key(self):
        blob = encrypt_attr({"n": "secret.txt"}, [1, 2, 3, 4])
        assert decrypt_attr(b64url_encode(blob), [9, 9, 9, 9]) is None

    def test_output_is_aes_block_aligned(self):
        # CBC pads to 16-byte boundary.
        blob = encrypt_attr({"n": "a"}, [1, 2, 3, 4])
        assert len(blob) % 16 == 0


# ---------- AES-CTR + CBC-MAC per-chunk ----------


class TestChunkEncryption:
    def test_ctr_is_deterministic(self):
        ul_key = list(range(6))
        data = secrets.token_bytes(4096)
        ct1, mac1 = encrypt_chunk_and_mac(data, ul_key, 0)
        ct2, mac2 = encrypt_chunk_and_mac(data, ul_key, 0)
        assert ct1 == ct2
        assert mac1 == mac2

    def test_ctr_offset_changes_ciphertext(self):
        ul_key = list(range(6))
        data = secrets.token_bytes(4096)
        ct0, _ = encrypt_chunk_and_mac(data, ul_key, 0)
        ct16, _ = encrypt_chunk_and_mac(data, ul_key, 16)
        # Same plaintext, different counter start → different ciphertext.
        assert ct0 != ct16

    def test_empty_chunk_mac_is_nonce_doubled(self):
        ul_key = [1, 2, 3, 4, 0xAAAA_AAAA, 0xBBBB_BBBB]
        ct, mac = encrypt_chunk_and_mac(b"", ul_key, 0)
        # No ciphertext; MAC equals nonce||nonce as a 4-element a32 list.
        assert ct == b""
        assert mac == [0xAAAA_AAAA, 0xBBBB_BBBB, 0xAAAA_AAAA, 0xBBBB_BBBB]

    def test_rejects_oversize_chunk(self):
        with pytest.raises(AssertionError):
            encrypt_chunk_and_mac(b"\x00" * (ONE_MB + 1), list(range(6)), 0)

    def test_condense_macs_empty(self):
        assert condense_macs([], list(range(6))) == [0, 0, 0, 0]

    def test_condense_macs_non_empty_produces_4_elements(self):
        macs = [[1, 2, 3, 4], [5, 6, 7, 8]]
        out = condense_macs(macs, list(range(6)))
        assert len(out) == 4


# ---------- chunk map ----------


class TestChunkmap:
    def test_eight_pre_1mb_entries(self):
        # FileUploadReader.chunkmap layout: 8 ramp-up chunks ending at 1 MiB.
        assert len(CHUNKMAP) == 8

    def test_first_chunk_is_128_kib(self):
        assert CHUNKMAP[0] == 128 * 1024

    def test_last_entry_is_1_mib(self):
        last_offset = max(CHUNKMAP)
        assert CHUNKMAP[last_offset] == ONE_MB

    def test_cumulative_position_matches_key(self):
        # Keys are cumulative: each key equals sum of previous values.
        cumulative = 0
        for pos in sorted(CHUNKMAP):
            assert pos == cumulative
            cumulative += CHUNKMAP[pos]


# ---------- CRC-32 ----------


class TestCrc32:
    def test_matches_zlib(self):
        import zlib

        assert crc32b(b"hello") == zlib.crc32(b"hello") & 0xFFFF_FFFF

    def test_seedable(self):
        mid = crc32b(b"hel")
        assert crc32b(b"lo", mid) == crc32b(b"hello")
