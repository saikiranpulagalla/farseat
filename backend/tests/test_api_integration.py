from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.main import app


def make_pdf(text='API WORKS'):
    b=BytesIO(); c=canvas.Canvas(b,pagesize=(400,300)); c.drawString(50,200,text); c.save(); return b.getvalue()


def test_primary_upload_analyze_detail_flow_uses_production_subprocess_parser():
    client=TestClient(app)
    up=client.post('/api/presentations',files={'file':('api.pdf',make_pdf(),'application/pdf')})
    assert up.status_code==200,up.text
    p=up.json(); token=p['capability_token']; pid=p['presentation_id']
    req={
      'schema_version':'1.2','request_id':str(uuid4()),'presentation_id':pid,
      'display':{'width_m':4.0,'height_m':2.25,'center_x_m':0.0,'bottom_z_m':0.0},
      'room':{'width_m':8.0,'depth_m':10.0},
      'seat_grid':{'rows':1,'seats_per_row':1,'first_row_distance_m':4.5,'row_spacing_m':1.0,'seat_spacing_m':1.0,'center_x_m':0.0},
      'profile':'PUBLIC_AVIXA_BDM_REFERENCE_V1',
      'renderer_manifest':[{'page_number':1,'rotation_deg':0,'viewport_width':400.0,'viewport_height':300.0}],
    }
    an=client.post('/api/analyze',headers={'X-FarSeat-Token':token},json=req)
    assert an.status_code==200,an.text
    aid=an.json()['analysis_id']
    detail=client.get(f'/api/analyses/{aid}/seats/r1-s1',headers={'X-FarSeat-Token':token})
    assert detail.status_code==200,detail.text
    assert detail.json()['seat']['seat_id']=='r1-s1'
    assert detail.json()['details']


def test_wrong_capability_cannot_read_presentation():
    client=TestClient(app)
    up=client.post('/api/presentations',files={'file':('api.pdf',make_pdf(),'application/pdf')})
    p=up.json()
    r=client.get(f"/api/presentations/{p['presentation_id']}",headers={'X-FarSeat-Token':'wrong'})
    assert r.status_code==404


def test_validation_errors_preserve_farseat_error_shape():
    client=TestClient(app)
    up=client.post('/api/presentations',files={'file':('api.pdf',make_pdf(),'application/pdf')})
    assert up.status_code==200
    p=up.json(); token=p['capability_token']; pid=p['presentation_id']
    req={
      'schema_version':'1.2','request_id':str(uuid4()),'presentation_id':pid,
      'display':{'width_m':-1.0,'height_m':2.25,'center_x_m':0.0,'bottom_z_m':0.0},
      'room':{'width_m':8.0,'depth_m':10.0},
      'seat_grid':{'rows':1,'seats_per_row':1,'first_row_distance_m':4.5,'row_spacing_m':1.0,'seat_spacing_m':1.0,'center_x_m':0.0},
      'profile':'PUBLIC_AVIXA_BDM_REFERENCE_V1','renderer_manifest':[],
    }
    r=client.post('/api/analyze',headers={'X-FarSeat-Token':token},json=req)
    assert r.status_code==422
    assert r.json()=={'detail':{'code':'INVALID_REQUEST'}}
