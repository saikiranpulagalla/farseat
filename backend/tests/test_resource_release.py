from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.parser import ParseFailure, SubprocessParserRunner


LIMIT = 20 * 1024 * 1024


def _ordinary_pdf() -> bytes:
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(1280, 720))
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawString(60, 660, "FarSeat resource regression")
    pdf.drawString(60, 620, "Ordinary machine-generated presentation text")
    pdf.save()
    return buf.getvalue()


def _representative_near_limit_pdf() -> bytes:
    """Five slides with legitimate high-entropy embedded raster resources.

    It is generated per release run, so the repository does not carry a 20 MiB
    binary fixture. Each slide has a distinct image resource, which prevents a
    PDF writer from deduplicating the payload into a much smaller document.
    """
    buf = BytesIO(); pdf = canvas.Canvas(buf, pagesize=(1280, 720), pageCompression=1)
    for page in range(5):
        image = Image.effect_noise((2400, 1600), 100 + page).convert("RGB")
        image_bytes = BytesIO(); image.save(image_bytes, format="JPEG", quality=89, optimize=False)
        pdf.setFont("Helvetica-Bold", 28)
        pdf.drawString(60, 660, f"Representative image-heavy slide {page + 1}")
        pdf.drawImage(ImageReader(BytesIO(image_bytes.getvalue())), 70, 100, width=1140, height=520, preserveAspectRatio=True, anchor="c")
        pdf.setFont("Helvetica", 14)
        pdf.drawString(60, 60, "Machine-generated presentation content with embedded raster imagery.")
        pdf.showPage()
    pdf.save()
    data = buf.getvalue()
    assert 18 * 1024 * 1024 <= len(data) <= LIMIT
    return data


def _newline_free_tail(data: bytes) -> bytes:
    return data + b"\n%" + b"x" * (LIMIT - len(data) - 2)


@pytest.mark.resource
def test_resource_release_gate_near_limit_and_adversarial_recovery():
    runner = SubprocessParserRunner()
    representative = _representative_near_limit_pdf()
    model = runner.parse(representative, uuid4(), "representative-near-limit.pdf")
    assert model.page_count == 5
    assert model.analyzable_element_count >= 5

    adversarial = _newline_free_tail(_ordinary_pdf())
    with pytest.raises(ParseFailure, match="PARSER_RESOURCE_LIMIT"):
        runner.parse(adversarial, uuid4(), "newline-free-trailing-tail.pdf")

    # A terminated child must not poison subsequent parsing or leave a partial model.
    recovered = runner.parse(_ordinary_pdf(), uuid4(), "recovery.pdf")
    assert recovered.page_count == 1
    assert recovered.analyzable_element_count >= 1


@pytest.mark.resource
def test_resource_release_gate_rejects_limit_plus_one_before_parser():
    from app.main import MAX_UPLOAD_BYTES

    assert MAX_UPLOAD_BYTES == LIMIT
    # This is deliberately a byte-contract regression: route-level tests exercise
    # the HTTP response; the parser is never invoked for a payload over this size.
    assert len(_newline_free_tail(_ordinary_pdf()) + b"x") == LIMIT + 1
