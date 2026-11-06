"""관리자 API 흐름: 학과 추가 → 사진 업로드 → 행사 등록 → 공개 목록에서 보임 → 수정/삭제."""
import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 실제 DB/업로드 폴더를 건드리지 않도록 임시 폴더 사용
    from app import admin, db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(admin, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(admin, "ADMIN_TOKEN", TOKEN)
    from app.main import app
    c = TestClient(app)
    c.upload_dir = tmp_path / "uploads"
    return c


H = {"X-Admin-Token": TOKEN}


def test_admin_requires_token(client):
    assert client.post("/api/admin/departments", json={"name": "x", "campus": "춘천"}).status_code == 401
    r = client.post("/api/admin/departments", json={"name": "x", "campus": "춘천"},
                    headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_department_and_event_flow(client):
    # 학과
    r = client.post("/api/admin/departments", headers=H,
                    json={"name": "컴퓨터공학과", "college": "IT대학", "campus": "삼척"})
    assert r.status_code == 200, r.text
    dept_id = r.json()["id"]
    assert client.post("/api/admin/departments", headers=H,
                       json={"name": "컴퓨터공학과", "campus": "삼척"}).status_code == 409
    assert client.post("/api/admin/departments", headers=H,
                       json={"name": "없는캠퍼스과", "campus": "서울"}).status_code == 422

    # 업로드
    r = client.post("/api/admin/uploads", headers=H, files={"file": ("포스터.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["is_image"] and up["url"].endswith(".png")
    stored = up["url"].rsplit("/uploads/", 1)[1]
    assert (client.upload_dir / stored).read_bytes() == b"\x89PNG fake"
    assert client.post("/api/admin/uploads", headers=H,
                       files={"file": ("virus.exe", b"x", "application/octet-stream")}).status_code == 400

    # 행사 등록 (마감일 생략 → 행사 종료일이 마감일)
    r = client.post("/api/admin/events", headers=H, json={
        "department_id": dept_id, "title": "컴공 해커톤 설명회", "content_text": "1줄\n2줄",
        "event_start": "2099-11-20T14:00", "event_end": "2099-11-21T17:00", "location": "공학관 101호",
        "images": [up["url"]], "attachments": [{"name": "안내.pdf", "url": "http://x/안내.pdf"}],
    })
    assert r.status_code == 200, r.text
    event_id = r.json()["id"]
    assert event_id.startswith("0-")

    # 공개 목록/상세에서 보임
    lst = client.get("/api/notices", params={"category": "학과행사", "status": "ongoing"}).json()
    assert lst["total"] == 1
    item = lst["items"][0]
    assert item["source"] == "admin" and item["url"] is None
    assert item["campuses"] == ["삼척"] and item["deadline"] == "2099-11-21"
    assert item["department"]["name"] == "컴퓨터공학과"
    assert client.get("/api/notices", params={"department": dept_id}).json()["total"] == 1
    detail = client.get(f"/api/notices/{event_id}").json()
    assert detail["content_html"] == "<p>1줄</p><p>2줄</p>"
    assert detail["location"] == "공학관 101호" and detail["images"] == [up["url"]]
    assert client.get("/api/departments").json()[0]["event_count"] == 1

    # 학과에 행사가 있으면 학과 삭제 거부
    assert client.delete(f"/api/admin/departments/{dept_id}", headers=H).status_code == 409

    # 수정
    post_id = int(event_id.split("-")[1])
    r = client.put(f"/api/admin/events/{post_id}", headers=H, json={
        "department_id": dept_id, "title": "컴공 해커톤 설명회 (장소 변경)", "deadline": "2099-11-10"})
    assert r.status_code == 200
    detail = client.get(f"/api/notices/{event_id}").json()
    assert detail["title"].endswith("(장소 변경)") and detail["deadline"] == "2099-11-10"
    assert client.put(f"/api/admin/events/{post_id}", headers=H,
                      json={"department_id": dept_id, "title": "x", "deadline": "2099-02-30"}).status_code == 422

    # 삭제
    assert client.delete(f"/api/admin/events/{post_id}", headers=H).status_code == 200
    assert client.get(f"/api/notices/{event_id}").status_code == 404
    assert client.delete(f"/api/admin/departments/{dept_id}", headers=H).status_code == 200


def test_admin_disabled_without_token(client, monkeypatch):
    from app import admin
    monkeypatch.setattr(admin, "ADMIN_TOKEN", "")
    assert client.post("/api/admin/departments", headers=H, json={"name": "x", "campus": "춘천"}).status_code == 503
