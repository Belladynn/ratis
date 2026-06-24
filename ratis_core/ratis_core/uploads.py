from __future__ import annotations

import magic
from fastapi import HTTPException, UploadFile

_BASE_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
_PDF_MIME = frozenset({"application/pdf"})

# Valid WebP frame-chunk FourCC values that follow the `WEBP` form-type at
# offset 12. A genuine WebP container always carries exactly one of these.
_WEBP_CHUNK_FOURCC = frozenset({b"VP8 ", b"VP8L", b"VP8X"})

# Maps every supported MIME type to its canonical file extension.
MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _looks_like_webp(content: bytes) -> bool:
    """Manual WebP magic-signature check.

    Libmagic 5.x (the version shipped with macOS `file(1)` and many Linux
    distros via `file-5.41`) does NOT reliably identify WebP bytes — even for
    valid Pillow-encoded WebP it reports `RIFF (little-endian) data` /
    `application/octet-stream` instead of `image/webp`. WebP support exists
    in libmagic since 5.37 (2019), but only when the magic database includes
    the VP8/VP8L/VP8X sub-chunk patterns, which many distro builds omit.

    To avoid hardening the deployment surface (we'd have to ship a curated
    libmagic database in every Docker image), we accept WebP via a manual
    16-byte signature check when libmagic falls back to octet-stream :
    `RIFF<4 bytes size>WEBP` is the WebP container header, immediately
    followed at offset 12 by the first chunk FourCC. We require that chunk
    to be a valid WebP frame type (`VP8 ` / `VP8L` / `VP8X`) — a bare
    `RIFF....WEBP` padded with junk (which no real WebP encoder produces)
    is therefore rejected here rather than slipping through to Pillow.
    Net effect : we trade strict early-rejection for a robust cross-platform
    validator. See KP-87 for the full root-cause trace.
    """
    return (
        len(content) >= 16
        and content[0:4] == b"RIFF"
        and content[8:12] == b"WEBP"
        and content[12:16] in _WEBP_CHUNK_FOURCC
    )


def validate_image_upload(
    image: UploadFile,
    *,
    allow_pdf: bool = False,
    max_size_bytes: int,
) -> tuple[bytes, str]:
    """Read and validate an uploaded image (or PDF).

    Returns (content_bytes, real_mime_type).
    Raises HTTPException 422 on type mismatch or size exceeded.

    Args:
        image: The FastAPI UploadFile to validate.
        allow_pdf: When True, application/pdf is accepted in addition to images.
        max_size_bytes: Maximum allowed file size in bytes.
    """
    allowed = _BASE_MIME | _PDF_MIME if allow_pdf else _BASE_MIME

    declared_mime = image.content_type or ""
    if declared_mime not in allowed:
        raise HTTPException(status_code=422, detail="unsupported_file_type")

    content = image.file.read()
    if len(content) > max_size_bytes:
        raise HTTPException(status_code=422, detail="file_too_large")

    real_mime = magic.from_buffer(content[:2048], mime=True)
    if real_mime not in allowed:
        # WebP fallback : libmagic 5.x sometimes reports `application/octet-stream`
        # for valid WebP bytes (see KP-87 + `_looks_like_webp` docstring). We
        # only honour the fallback when the user *declared* image/webp AND the
        # bytes match the WebP container signature — so a spoofed EXE / WAV /
        # other RIFF-family file still gets rejected because they lack the
        # `WEBP` FourCC at offset 8.
        if declared_mime == "image/webp" and _looks_like_webp(content):
            real_mime = "image/webp"
        else:
            raise HTTPException(status_code=422, detail="unsupported_file_type")

    return content, real_mime
