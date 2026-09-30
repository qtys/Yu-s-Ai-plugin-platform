import json
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app import database, pixel_motion
from app.prop_motion import PropPerformance

def performance():
    return {"title":"小杯喝水", "intent":"漂浮杯连接嘴部", "prop_name":"原创小杯", "duration_ms":7000,
            "paths":[{"segments":[[-20,0],[20,0],[18,50],[-18,50]],"closed":True},
                     {"segments":[[-20,0],[0,-5,20,0]],"closed":False}],
            "frames":[{"at":at,"anchor":anchor,"x":x,"y":y,"angle":angle,"opacity":opacity,"label":"动作阶段"}
                      for at,anchor,x,y,angle,opacity in [(0,"stage",100,70,0,0),(.2,"stage",100,70,0,1),(.4,"mouth",0,0,0,1),(.6,"mouth",0,0,18,1),(.8,"mouth",0,0,0,1),(1,"stage",100,70,0,0)]]}

def test_rejects_unsafe_geometry_and_false_contact():
    PropPerformance.model_validate(performance())
    for change in (lambda p:p["paths"][0].update(fill="url(javascript:alert(1))"),
                   lambda p:p["paths"][0]["segments"][0].__setitem__(0,float("inf")),
                   lambda p:p["frames"][2].update(x=2),
                   lambda p:p["frames"][-1].update(opacity=1),
                   lambda p:p.update(script="code")):
        p=performance();change(p)
        with pytest.raises(ValidationError):PropPerformance.model_validate(p)

@pytest.mark.parametrize('anchor',['left_eye','right_eye','forehead','stage'])
def test_other_anchors_do_not_require_drinking_or_tilt(anchor):
    p=performance()
    for f in p['frames']:
        if f['anchor']=='mouth':f.update(anchor=anchor,angle=0)
    assert PropPerformance.model_validate(p)
    p['frames'][0]['head_angle']=4
    with pytest.raises(ValidationError):PropPerformance.model_validate(p)

def test_real_generation_path_returns_model_vector_and_preserves_chat(tmp_path,monkeypatch):
    monkeypatch.delenv("YUS_AI_PIXEL_CONFIG_DB",raising=False)
    monkeypatch.setattr(database,"DATA_DIR",tmp_path);monkeypatch.setattr(database,"DB_PATH",tmp_path/"test.db");database.init_db()
    with database.connect() as db:db.execute("UPDATE settings SET api_key='test-secret',base_url='https://model.test'")
    original=httpx.AsyncClient
    def respond(request):
        body=json.loads(request.content)
        assert "没有手" in body["messages"][0]["content"]
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":json.dumps(performance())}}],"usage":{"total_tokens":30}})
    monkeypatch.setattr(pixel_motion.httpx,"AsyncClient",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    app=FastAPI();app.include_router(pixel_motion.router)
    with TestClient(app) as client:
        r=client.post('/api/experiments/pixel-motion',json={"mode":"prop","content":"喝口水"})
        assert r.status_code==200
        assert r.json()["source"]=="model"
        assert r.json()["scene"]["paths"][0]["stroke"]=="#a68553"
        assert "test-secret" not in r.text
    with database.connect() as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==0
