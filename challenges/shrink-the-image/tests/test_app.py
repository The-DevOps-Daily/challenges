from app import app


def test_health():
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_slugify():
    client = app.test_client()
    resp = client.get("/slugify", query_string={"text": "Shrink The Image!"})
    assert resp.status_code == 200
    assert resp.get_json()["slug"] == "shrink-the-image"


def test_slugify_needs_text():
    client = app.test_client()
    assert client.get("/slugify").status_code == 400
