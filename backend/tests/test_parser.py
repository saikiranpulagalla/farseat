from io import BytesIO
from uuid import uuid4

import pytest
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject

from app.parser import NormalizedChar, ExtractedChar, SubprocessParserRunner, _dedupe, _line_and_segment, _mark_combining_uncertainty, parse_pdf_bytes
from app.models import NormalizedBBox


def make_pdf(text="Hello", render_mode=None, fill_alpha=None):
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    if fill_alpha is not None:
        c.setFillAlpha(fill_alpha)
    t=c.beginText(50,200)
    if render_mode is not None:
        t.setTextRenderMode(render_mode)
    t.textLine(text); c.drawText(t); c.save(); return buf.getvalue()


def add_page_rotation(data, deg):
    r=PdfReader(BytesIO(data)); p=r.pages[0]; p.rotate(deg); out=BytesIO(); w=PdfWriter(); w.add_page(p); w.write(out); return out.getvalue()


def visual_horizontal_rotated_pdf(deg):
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300)); c.saveState(); c.translate(200,150); c.rotate(deg); c.drawString(-40,0,"NORMAL TEXT"); c.restoreState(); c.save()
    return add_page_rotation(buf.getvalue(),deg) if deg else buf.getvalue()


@pytest.mark.parametrize("deg,aspect", [(0,400/300),(90,300/400),(180,400/300),(270,300/400)])
def test_page_rotation_supports_text_that_is_visually_horizontal(deg,aspect):
    model=parse_pdf_bytes(visual_horizontal_rotated_pdf(deg),uuid4(),"r.pdf")
    page=model.pages[0]
    assert page.geometry.display_aspect_ratio == pytest.approx(aspect)
    texts=[e for e in page.elements if e.support_state == "ANALYZABLE"]
    assert len(texts)==1
    assert texts[0].text_preview == "NORMAL TEXT"


def test_rotating_horizontal_source_text_without_compensation_becomes_unsupported_vertical_text():
    model=parse_pdf_bytes(add_page_rotation(make_pdf("Vertical after rotate"),90),uuid4(),"r.pdf")
    assert model.pages[0].elements
    assert all(e.support_state == "NOT_ANALYZED" for e in model.pages[0].elements)
    assert all("UNSUPPORTED_TEXT_ROTATION" in e.reason_codes for e in model.pages[0].elements)


def test_invisible_text_never_analyzable():
    model=parse_pdf_bytes(make_pdf("Hidden",3),uuid4(),"hidden.pdf")
    assert model.pages[0].elements
    assert all(e.support_state == "NOT_ANALYZED" for e in model.pages[0].elements)
    assert any("INVISIBLE_TEXT_LAYER" in e.reason_codes for e in model.pages[0].elements)


def test_clipping_only_text_never_analyzable():
    model=parse_pdf_bytes(make_pdf("Clip",7),uuid4(),"clip.pdf")
    assert all(e.support_state == "NOT_ANALYZED" for e in model.pages[0].elements)


@pytest.mark.parametrize("alpha,reason", [(0.0,"INVISIBLE_TEXT_LAYER"),(0.01,"UNSUPPORTED_TEXT_TRANSPARENCY"),(0.5,"UNSUPPORTED_TEXT_TRANSPARENCY")])
def test_transparent_text_fails_closed(alpha,reason):
    model=parse_pdf_bytes(make_pdf("ALPHA HIDDEN",fill_alpha=alpha),uuid4(),"alpha.pdf")
    assert model.pages[0].elements
    assert all(e.support_state == "NOT_ANALYZED" for e in model.pages[0].elements)
    assert any(reason in e.reason_codes for e in model.pages[0].elements)


def test_partial_crop_clipping_is_not_analyzed():
    data=make_pdf("CLIPPED")
    r=PdfReader(BytesIO(data)); p=r.pages[0]; p.cropbox.lower_left=(55,0); p.cropbox.upper_right=(400,300)
    out=BytesIO(); w=PdfWriter(); w.add_page(p); w.write(out)
    model=parse_pdf_bytes(out.getvalue(),uuid4(),"crop.pdf")
    assert model.pages[0].elements
    assert all(e.support_state == "NOT_ANALYZED" for e in model.pages[0].elements)
    assert any("CLIPPED_TEXT_GEOMETRY" in e.reason_codes for e in model.pages[0].elements)


def test_superscript_is_associated_with_logical_run_not_tiny_standalone_element():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300));
    c.setFont("Helvetica",12); c.drawString(50,200,"E=mc"); c.setFont("Helvetica",7); c.drawString(80,207,"2"); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),"script.pdf")
    analyzed=[e for e in model.pages[0].elements if e.support_state=="ANALYZABLE"]
    assert len(analyzed)==1
    assert "E=mc" in (analyzed[0].text_preview or "") and "2" in (analyzed[0].text_preview or "")
    assert analyzed[0].element_height_ratio == pytest.approx(12/300, rel=.08)


def test_dedupe_checks_neighbor_buckets():
    ch=ExtractedChar("A",0,0,1,1,"Helvetica",12,0,0,1,1,False,False)
    a=NormalizedChar(ch,NormalizedBBox(x0=.10024,y0=.2,x1=.12,y1=.24))
    b=NormalizedChar(ch,NormalizedBBox(x0=.10026,y0=.2,x1=.12002,y1=.24))
    assert len(_dedupe([a,b]))==1


def test_zero_width_combining_mark_makes_its_base_run_not_analyzed():
    base=ExtractedChar("e",0,0,1,1,"Fixture",12,0,0,1,1,False,False)
    accent=ExtractedChar("\u0301",1,0,1,1,"Fixture",12,0,0,1,1,False,False)
    chars, isolated=_mark_combining_uncertainty([base,accent],[NormalizedChar(base,NormalizedBBox(x0=.1,y0=.1,x1=.2,y1=.2))])
    elements=_line_and_segment(chars,1)
    assert isolated is False
    assert elements[0].support_state=="NOT_ANALYZED"
    assert "UNSUPPORTED_COMBINING_SHAPING" in elements[0].reason_codes


def test_ordinary_ascii_text_remains_analyzable_after_combining_hardening():
    elements=_line_and_segment(_chars_for_text("ordinary"),1)
    assert elements and all(e.support_state=="ANALYZABLE" for e in elements)


def test_bad_page_geometry_does_not_destroy_good_pages():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300)); c.drawString(50,200,"GOOD"); c.showPage(); c.drawString(50,200,"BAD"); c.save()
    r=PdfReader(BytesIO(buf.getvalue())); r.pages[1].cropbox.lower_left=(100,100); r.pages[1].cropbox.upper_right=(100,100)
    out=BytesIO(); w=PdfWriter(); w.add_page(r.pages[0]); w.add_page(r.pages[1]); w.write(out)
    model=parse_pdf_bytes(out.getvalue(),uuid4(),"mixed.pdf")
    assert model.page_count==2
    assert model.pages[0].text_coverage=="COMPLETE"
    assert model.pages[1].text_coverage=="NONE"
    assert "ZERO_AREA_VISIBLE_FRAME" in model.pages[1].reason_codes


def test_subprocess_runner_round_trips_strict_models():
    model=SubprocessParserRunner(timeout_seconds=10,memory_limit_bytes=None).parse(make_pdf("Runner"),uuid4(),"runner.pdf")
    assert model.page_count==1
    assert model.analyzable_element_count>=1



def test_rectangular_graphics_clip_hides_text_and_restores_after_q_q():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.saveState(); path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0)
    c.drawString(100,200,"CLIPPED OUT INVISIBLE"); c.restoreState()
    c.drawString(50,150,"VISIBLE"); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),"clip-path.pdf")
    texts=[e.text_preview for e in model.pages[0].elements]
    assert texts==["VISIBLE"]
    assert model.pages[0].text_coverage=="COMPLETE"


def test_complex_clipping_path_fails_closed():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.saveState(); path=c.beginPath(); path.circle(100,100,50); c.clipPath(path,stroke=0,fill=0)
    c.drawString(80,100,"IN CIRCLE"); c.restoreState(); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),"complex-clip.pdf")
    assert model.pages[0].elements
    assert all(e.support_state=="NOT_ANALYZED" for e in model.pages[0].elements)
    assert any("UNSUPPORTED_CLIPPING_PATH" in e.reason_codes for e in model.pages[0].elements)


def _chars_for_text(text):
    return [NormalizedChar(ExtractedChar(ch, i, 10, i + .8, 20, "Fixture", 10, 0, 0, 1, 1, False, False), NormalizedBBox(x0=.1+i*.01,y0=.1,x1=.108+i*.01,y1=.2)) for i, ch in enumerate(text)]


def test_rtl_text_fails_closed_instead_of_entering_ltr_measurement():
    elements=_line_and_segment(_chars_for_text("שלום"),1)
    assert elements and all(e.support_state=="NOT_ANALYZED" for e in elements)
    assert any("UNSUPPORTED_TEXT_DIRECTION" in e.reason_codes for e in elements)


def test_unvalidated_non_latin_ltr_shaping_fails_closed():
    elements=_line_and_segment(_chars_for_text("Привет"),1)
    assert elements and all(e.support_state=="NOT_ANALYZED" for e in elements)
    assert any("UNSUPPORTED_TEXT_SHAPING" in e.reason_codes for e in elements)


def test_optional_content_document_fails_closed():
    raw=make_pdf("LAYERED TEXT")
    reader=PdfReader(BytesIO(raw)); writer=PdfWriter(); writer.add_page(reader.pages[0])
    writer._root_object.update({NameObject("/OCProperties"): DictionaryObject()})
    out=BytesIO(); writer.write(out)
    model=parse_pdf_bytes(out.getvalue(),uuid4(),"layers.pdf")
    assert model.pages[0].elements
    assert all(e.support_state=="NOT_ANALYZED" for e in model.pages[0].elements)
    assert any("UNSUPPORTED_OPTIONAL_CONTENT" in e.reason_codes for e in model.pages[0].elements)


def _rewrite_first_page_stream(raw: bytes, old: bytes, new: bytes) -> bytes:
    reader=PdfReader(BytesIO(raw)); page=reader.pages[0]
    data=page.get_contents().get_data()
    assert old in data
    from pypdf.generic import DecodedStreamObject
    stream=DecodedStreamObject(); stream.set_data(data.replace(old,new,1))
    page[NameObject('/Contents')]=stream
    out=BytesIO(); writer=PdfWriter(); writer.add_page(page); writer.write(out)
    return out.getvalue()


def test_obsolete_F_path_terminator_applies_pending_clip():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.saveState(); path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0)
    c.drawString(100,200,'HIDDEN BY F'); c.restoreState(); c.drawString(30,150,'VISIBLE'); c.save()
    raw=_rewrite_first_page_stream(buf.getvalue(), b'W* n', b'W* F')
    model=parse_pdf_bytes(raw,uuid4(),'clip-F.pdf')
    texts=[e.text_preview for e in model.pages[0].elements]
    assert texts == ['VISIBLE']


def test_form_xobject_inherits_parent_clip_and_restores_parent_state():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM1',0,0,300,250); c.drawString(100,200,'FORM HIDDEN'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM1'); c.restoreState()
    c.drawString(40,120,'VISIBLE AFTER FORM'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-parent-clip.pdf')
    texts=[e.text_preview for e in model.pages[0].elements]
    assert texts == ['VISIBLE AFTER FORM']


def test_form_xobject_inside_parent_clip_remains_analyzable():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_INSIDE',0,0,300,250); c.drawString(60,100,'FORM INSIDE'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(40,80,120,60); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM_INSIDE'); c.restoreState(); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-inside.pdf')
    element=next(e for e in model.pages[0].elements if e.text_preview=='FORM INSIDE')
    assert element.support_state=='ANALYZABLE'
    assert element.visibility_state=='VISIBLE'
    assert element.measurement_confidence=='SUPPORTED'
    assert element.logical_bbox.x0 < element.logical_bbox.x1
    assert element.logical_bbox.y0 < element.logical_bbox.y1


def test_partially_parent_clipped_form_text_is_not_analyzed():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_PARTIAL',0,0,300,250); c.setFont('Helvetica',24); c.drawString(50,100,'PARTIAL'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(55,80,200,60); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM_PARTIAL'); c.restoreState(); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-partial.pdf')
    assert model.pages[0].elements
    assert all(e.support_state=='NOT_ANALYZED' for e in model.pages[0].elements)
    assert any('CLIPPED_TEXT_GEOMETRY' in e.reason_codes for e in model.pages[0].elements)


def test_sibling_forms_do_not_leak_parent_clip_state():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_A',0,0,300,250); c.drawString(100,200,'FORM A HIDDEN'); c.endForm()
    c.beginForm('FORM_B',0,0,300,250); c.drawString(40,120,'FORM B VISIBLE'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM_A'); c.restoreState(); c.doForm('FORM_B'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-siblings.pdf')
    texts=[e.text_preview for e in model.pages[0].elements]
    assert texts==['FORM B VISIBLE']
    assert model.pages[0].elements[0].support_state=='ANALYZABLE'


def test_ordinary_form_text_remains_analyzable_without_clipping():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_NORMAL',0,0,300,250); c.drawString(40,120,'ORDINARY FORM'); c.endForm(); c.doForm('FORM_NORMAL'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-normal.pdf')
    assert [(e.text_preview,e.support_state) for e in model.pages[0].elements]==[('ORDINARY FORM','ANALYZABLE')]


def test_form_local_rectangular_clip_excludes_form_text_without_leaking_to_page():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_CHILD_CLIP',0,0,300,250)
    path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0); c.drawString(100,200,'CHILD HIDDEN'); c.endForm()
    c.doForm('FORM_CHILD_CLIP'); c.drawString(40,120,'PAGE VISIBLE'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-child-clip.pdf')
    assert [(e.text_preview,e.support_state) for e in model.pages[0].elements]==[('PAGE VISIBLE','ANALYZABLE')]


def test_form_matrix_translation_uses_page_frame_for_parent_clip():
    # Independent oracle: local text at (10, 10) with /Matrix [1 0 0 1 100 100]
    # is painted near (110, 110) in page user space, inside this parent clip.
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_MATRIX',0,0,300,250); c.drawString(10,10,'MATRIX VISIBLE'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(95,95,150,50); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM_MATRIX'); c.restoreState(); c.save()
    reader=PdfReader(BytesIO(buf.getvalue())); page=reader.pages[0]
    form=next(iter(page['/Resources']['/XObject'].values())).get_object()
    form[NameObject('/Matrix')]=ArrayObject([FloatObject(1),FloatObject(0),FloatObject(0),FloatObject(1),FloatObject(100),FloatObject(100)])
    out=BytesIO(); writer=PdfWriter(); writer.add_page(page); writer.write(out)
    model=parse_pdf_bytes(out.getvalue(),uuid4(),'form-matrix.pdf')
    element=next(e for e in model.pages[0].elements if e.text_preview=='MATRIX VISIBLE')
    assert element.support_state=='ANALYZABLE'
    assert .24 < element.logical_bbox.x0 < .31
    assert .55 < element.logical_bbox.y0 < .65


def test_nested_form_inherits_page_clip_across_dup_levels():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM_B',0,0,300,250); c.drawString(100,200,'NESTED HIDDEN'); c.endForm()
    c.beginForm('FORM_A',0,0,300,250); c.doForm('FORM_B'); c.endForm()
    c.saveState(); path=c.beginPath(); path.rect(0,0,20,20); c.clipPath(path,stroke=0,fill=0); c.doForm('FORM_A'); c.restoreState(); c.drawString(40,120,'AFTER NESTED'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'nested-form.pdf')
    assert [(e.text_preview,e.support_state) for e in model.pages[0].elements]==[('AFTER NESTED','ANALYZABLE')]


def test_form_xobject_inherits_parent_transparency():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM2',0,0,300,250); c.drawString(100,200,'FORM ALPHA'); c.endForm()
    c.saveState(); c.setFillAlpha(0); c.doForm('FORM2'); c.restoreState(); c.drawString(40,120,'VISIBLE'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-alpha.pdf')
    hidden=next(e for e in model.pages[0].elements if e.text_preview=='FORM ALPHA')
    visible=next(e for e in model.pages[0].elements if e.text_preview=='VISIBLE')
    assert hidden.support_state == 'NOT_ANALYZED'
    assert hidden.visibility_state == 'INVISIBLE'
    assert 'INVISIBLE_TEXT_LAYER' in hidden.reason_codes
    assert visible.support_state == 'ANALYZABLE'


def test_form_bbox_clips_text_outside_form_bounds():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300))
    c.beginForm('FORM3',0,0,20,20); c.drawString(100,200,'OUTSIDE FORM BBOX'); c.endForm(); c.doForm('FORM3')
    c.drawString(40,120,'VISIBLE'); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'form-bbox.pdf')
    texts=[e.text_preview for e in model.pages[0].elements]
    assert texts == ['VISIBLE']


def test_mixed_text_and_graphics_adds_nontext_warning_without_corrupting_text_measurement():
    buf=BytesIO(); c=canvas.Canvas(buf,pagesize=(400,300)); c.drawString(40,200,'SUPPORTED TEXT'); c.rect(10,10,30,30,stroke=1,fill=0); c.save()
    model=parse_pdf_bytes(buf.getvalue(),uuid4(),'mixed-graphics.pdf')
    page=model.pages[0]
    assert page.text_coverage == 'COMPLETE'
    assert page.non_text_content == 'PRESENT'
    assert 'GRAPHICAL_CONTENT_NOT_ANALYZED' in page.reason_codes
    assert any(e.support_state == 'ANALYZABLE' for e in page.elements)


def test_same_pdf_bytes_produce_identical_semantic_models():
    data=make_pdf("Deterministic parser output")
    first=parse_pdf_bytes(data,uuid4(),"same.pdf")
    second=parse_pdf_bytes(data,uuid4(),"same.pdf")
    def semantic(model):
        return [(page.geometry.display_aspect_ratio,page.text_coverage,page.non_text_content,
                 [(el.text_preview,el.support_state,el.element_height_ratio,el.reason_codes) for el in page.elements]) for page in model.pages]
    assert semantic(first)==semantic(second)
