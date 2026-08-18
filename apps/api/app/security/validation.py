"""Upload validation. Extensions are advisory; bytes are authoritative."""
from __future__ import annotations

import hashlib

MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"%PDF": "application/pdf",
    b"RIFF": "image/webp",          # confirmed further below
    b"PK\x03\x04": ("application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"),
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "application/msword",
}

ALLOWED_UPLOAD = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_PIXELS = 50_000_000          # guards against pixel-flood decompression


class UploadRejected(Exception):
    pass


def sniff_mime(data: bytes) -> str | None:
    for magic, mime in MAGIC.items():
        if data.startswith(magic):
            if magic == b"RIFF":
                return "image/webp" if data[8:12] == b"WEBP" else None
            return mime
    return None


def validate_upload(data: bytes, max_bytes: int) -> tuple[str, str]:
    """Return (mime, sha256) or raise with a message the user can act on."""
    if not data:
        raise UploadRejected("Fayl bo'sh")
    if len(data) > max_bytes:
        raise UploadRejected(
            f"Fayl juda katta ({len(data) // 1024 // 1024} MB). "
            f"Chegara {max_bytes // 1024 // 1024} MB")

    mime = sniff_mime(data)
    if mime is None:
        raise UploadRejected(
            "Fayl turi aniqlanmadi. JPEG, PNG, WebP yoki PDF yuklang")
    if mime not in ALLOWED_UPLOAD:
        raise UploadRejected(f"Qo'llab-quvvatlanmaydigan fayl turi: {mime}")

    if mime.startswith("image/"):
        _check_pixel_bomb(data)

    return mime, hashlib.sha256(data).hexdigest()


def _check_pixel_bomb(data: bytes) -> None:
    """A small file can decode to gigabytes of pixels. Check before decoding."""
    try:
        import io

        from PIL import Image
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
        if w * h > MAX_PIXELS:
            raise UploadRejected("Rasm o'lchami juda katta")
    except UploadRejected:
        raise
    except Exception as exc:
        raise UploadRejected("Rasm ochilmadi — fayl buzilgan bo'lishi mumkin") from exc
