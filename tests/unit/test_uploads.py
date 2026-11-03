import io

import pytest
from fastapi import HTTPException
from PIL import Image

from app.routers.catalog import like_pattern, public_film
from app.routers.film_management import clean_poster
from app.routers.film_rights import content_disposition, detect_media_type, sanitize_filename


def image_bytes(fmt, size=(20, 30), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_clean_poster_reencodes_to_jpeg(fmt):
    out = clean_poster(image_bytes(fmt))
    with Image.open(io.BytesIO(out)) as image:
        assert image.format == "JPEG" and image.size == (20, 30)


def test_clean_poster_downscales_and_rejects():
    with Image.open(io.BytesIO(clean_poster(image_bytes("PNG", (3200, 1000))))) as image:
        assert image.size == (1600, 500)
    for data in (b"<svg onload=alert(1)>", image_bytes("GIF"), image_bytes("PNG")[:40]):
        with pytest.raises(HTTPException) as exc:
            clean_poster(data)
        assert exc.value.status_code == 422


def test_detect_media_type_by_magic_bytes():
    assert detect_media_type(b"%PDF-1.7\n...") == "application/pdf"
    assert detect_media_type(image_bytes("PNG")) == "image/png"
    assert detect_media_type(image_bytes("JPEG")) == "image/jpeg"
    for data in (b"", b"PDF-1.7", b"<html>%PDF-", b"GIF89a", b"PK\x03\x04", image_bytes("WEBP")):
        assert detect_media_type(data) is None


@pytest.mark.parametrize(
    "name,media,expected",
    [
        ("contrato.pdf", "application/pdf", "contrato.pdf"),
        ("../../etc/passwd", "application/pdf", "passwd.pdf"),
        ("C:\\Users\\x\\Licença.PDF", "application/pdf", "Licença.pdf"),
        ('a"b;c<d>e|f*g?h:i.png', "image/png", "abcdefghi.png"),
        ("foto.png", "image/jpeg", "foto.png.jpg"),
        ("scan.JPEG", "image/jpeg", "scan.jpeg"),
        ("..", "image/png", "comprovante.png"),
        (None, "application/pdf", "comprovante.pdf"),
        ("evil\r\nX-Header: 1.pdf", "application/pdf", "evilX-Header 1.pdf"),
    ],
)
def test_sanitize_filename(name, media, expected):
    assert sanitize_filename(name, media) == expected


def test_sanitize_filename_length_and_disposition():
    long = sanitize_filename("a" * 500 + ".pdf", "application/pdf")
    assert len(long) == 120 and long.endswith(".pdf")
    header = content_disposition("Licença 2026.pdf")
    assert header == "attachment; filename=\"Licena 2026.pdf\"; filename*=UTF-8''Licen%C3%A7a%202026.pdf"
    assert "\n" not in content_disposition(sanitize_filename("a\nb.pdf", "application/pdf"))


def test_catalog_helpers():
    assert like_pattern("100%_x") == "%100\\%\\_x%"
    assert like_pattern("a\\b") == "%a\\\\b%"
    assert public_film({"id": 1, "title": "X", "rights_status": "authorized", "rights_notes": "contrato"}) == {
        "id": 1,
        "title": "X",
        "rights_status": "authorized",
    }
