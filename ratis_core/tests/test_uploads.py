"""Unit tests for ratis_core.uploads — image-upload validation helpers."""

from __future__ import annotations

from ratis_core.uploads import _looks_like_webp


# A bare RIFF/WEBP container header with a valid chunk FourCC at offset 12.
def _webp_header(fourcc: bytes) -> bytes:
    return b"RIFF" + b"\x20\x00\x00\x00" + b"WEBP" + fourcc + b"\x00" * 64


class TestLooksLikeWebp:
    def test_valid_vp8_chunk_accepted(self):
        assert _looks_like_webp(_webp_header(b"VP8 ")) is True

    def test_valid_vp8l_chunk_accepted(self):
        assert _looks_like_webp(_webp_header(b"VP8L")) is True

    def test_valid_vp8x_chunk_accepted(self):
        assert _looks_like_webp(_webp_header(b"VP8X")) is True

    def test_too_short_rejected(self):
        assert _looks_like_webp(b"RIFF\x00\x00\x00\x00WEBP") is False

    def test_non_riff_rejected(self):
        assert _looks_like_webp(_webp_header(b"VP8 ").replace(b"RIFF", b"MZ\x90\x00", 1)) is False

    def test_wrong_form_type_rejected(self):
        # RIFF container that is not WEBP (e.g. WAVE).
        bad = b"RIFF" + b"\x20\x00\x00\x00" + b"WAVE" + b"VP8 " + b"\x00" * 64
        assert _looks_like_webp(bad) is False

    def test_riff_webp_without_valid_chunk_fourcc_rejected(self):
        """A bare ``RIFF....WEBP`` header padded with junk must be rejected.

        Without the chunk-FourCC check at offset 12, any RIFF-family file
        spoofed with the ``WEBP`` form-type would slip through the libmagic
        fallback (KP-87). A real WebP always carries a VP8/VP8L/VP8X chunk.
        """
        assert _looks_like_webp(_webp_header(b"JUNK")) is False
