"""관리자 등급: 최종 → 일반/학과 키 발급, 학과 관리자는 자기 학과 행사만."""
import pytest
from fastapi.testclient import TestClient

SUPER = "super-secret-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import admin, db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(admin, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(admin, "ADMIN_TOKEN", SUPER)
    from app.main import app
    return TestClient(app)


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_roles(client):
    S = bearer(SUPER)
    assert client.get("/api/admin/me", headers=S).json()["role"] == "super"
    assert client.get("/api/admin/me", headers=bearer("nope")).status_code == 401

    cs = client.post("/api/admin/departments", headers=S, json={"name": "컴퓨터공학과", "campus": "삼척"}).json()["id"]
    ee = client.post("/api/admin/departments", headers=S, json={"name": "전자공학과", "campus": "삼척"}).json()["id"]

    # 최종 → 일반 관리자 키
    general = client.post("/api/admin/keys", headers=S, json={"role": "general", "label": "학생처"}).json()["key"]
    G = bearer(general)
    assert client.get("/api/admin/me", headers=G).json()["role"] == "general"
    # 일반 관리자는 일반 관리자 키를 못 만듦, 학과 관리자 키는 만듦
    assert client.post("/api/admin/keys", headers=G, json={"role": "general"}).status_code == 403
    assert client.post("/api/admin/keys", headers=G, json={"role": "department"}).status_code == 400  # 학과 없음
    r = client.post("/api/admin/keys", headers=G, json={"role": "department", "department_id": cs})
    dept_key, dept_key_id = r.json()["key"], r.json()["id"]
    D = bearer(dept_key)
    me = client.get("/api/admin/me", headers=D).json()
    assert me["role"] == "department" and me["department"]["name"] == "컴퓨터공학과"

    # 학과 관리자: 학과 추가·키 발급·크롤링 불가
    assert client.post("/api/admin/departments", headers=D, json={"name": "x", "campus": "삼척"}).status_code == 403
    assert client.post("/api/admin/keys", headers=D, json={"role": "department", "department_id": cs}).status_code == 403
    assert client.post("/api/admin/crawl", headers=D).status_code == 403

    # 학과 관리자: 학과를 안 적으면 자기 학과, 남의 학과는 거부
    mine = client.post("/api/admin/events", headers=D, json={"title": "컴공 세미나", "event_start": "2099-01-10"})
    assert mine.status_code == 200, mine.text
    assert client.post("/api/admin/events", headers=D, json={"department_id": ee, "title": "x"}).status_code == 403
    others = client.post("/api/admin/events", headers=G, json={"department_id": ee, "title": "전자 행사"}).json()["id"]
    other_pid = int(others.split("-")[1])
    assert client.put(f"/api/admin/events/{other_pid}", headers=D, json={"title": "뺏기"}).status_code == 403
    assert client.delete(f"/api/admin/events/{other_pid}", headers=D).status_code == 403

    # 관리 목록: 학과 관리자는 자기 학과 것만, 일반 관리자는 전부
    assert [e["title"] for e in client.get("/api/admin/events", headers=D).json()] == ["컴공 세미나"]
    assert len(client.get("/api/admin/events", headers=G).json()) == 2

    # 학과 관리자 키가 남아 있으면 학과 삭제 불가
    assert client.delete(f"/api/admin/departments/{cs}", headers=S).status_code == 409

    # 키 목록·폐기: 일반 관리자는 학과 키만 보임
    assert {k["role"] for k in client.get("/api/admin/keys", headers=G).json()} == {"department"}
    assert client.delete(f"/api/admin/keys/{dept_key_id}", headers=G).status_code == 200
    assert client.get("/api/admin/me", headers=D).status_code == 401  # 폐기된 키
