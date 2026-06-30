from fastapi.testclient import TestClient

from api import app
from db.database import upsert_models

client = TestClient(app)


def _seed():
    upsert_models([
        dict(source="printables", external_id="1", title="Cool Vase",
             source_url="https://p/1", downloads_count=8000, likes_count=600),
        dict(source="makerworld", external_id="2", title="Phone Stand",
             source_url="https://m/2", downloads_count=15000, likes_count=900),
    ])


def test_sources():
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert set(resp.json()) >= {"printables", "makerworld", "cults3d"}


def test_list_models_and_filter():
    _seed()
    resp = client.get("/models")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    only = client.get("/models", params={"source": "printables"}).json()
    assert len(only) == 1
    assert only[0]["title"] == "Cool Vase"

    by_q = client.get("/models", params={"q": "phone"}).json()
    assert len(by_q) == 1 and by_q[0]["title"] == "Phone Stand"


def test_list_models_pagination():
    _seed()
    page = client.get("/models", params={"limit": 1, "offset": 1}).json()
    assert len(page) == 1


def test_get_model_and_404():
    _seed()
    first = client.get("/models").json()[0]
    got = client.get(f"/models/{first['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == first["id"]

    assert client.get("/models/999999").status_code == 404


def test_model_history():
    upsert_models([dict(source="printables", external_id="1", title="V",
                        source_url="u1", downloads_count=10)])
    upsert_models([dict(source="printables", external_id="1", title="V",
                        source_url="u1", downloads_count=40)])
    model_id = client.get("/models").json()[0]["id"]
    hist = client.get(f"/models/{model_id}/history").json()
    assert [h["downloads_count"] for h in hist] == [10, 40]


def test_export_csv():
    _seed()
    resp = client.get("/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert "Cool Vase" in resp.text
