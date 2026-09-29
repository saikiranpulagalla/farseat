import json
from pathlib import Path
from uuid import uuid4
import pytest

from app.parser import parse_pdf_bytes

ROOT=Path(__file__).resolve().parents[2]

def test_bundled_demo_structural_golden():
    pdf=(ROOT/'sample'/'farseat-demo.pdf').read_bytes()
    expected=json.loads((ROOT/'sample'/'farseat-demo.expected.json').read_text())
    model=parse_pdf_bytes(pdf,uuid4(),'farseat-demo.pdf')
    assert model.page_count == expected['page_count']
    assert model.pages[4].geometry.display_aspect_ratio == pytest.approx(expected['page_5_aspect_ratio'])
    assert model.pages[5].text_coverage == expected['page_6_text_coverage']
    assert model.pages[5].non_text_content == expected['page_6_non_text_content']
    assert model.analyzable_element_count >= expected['minimum_analyzable_elements']



def test_sample_reports_image_only_slide_in_presentation_summary():
    data=(ROOT/'sample'/'farseat-demo.pdf').read_bytes()
    model=parse_pdf_bytes(data,uuid4(),'farseat-demo.pdf')
    assert model.unanalyzable_page_count >= 1
