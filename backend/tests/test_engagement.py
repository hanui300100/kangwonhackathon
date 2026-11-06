"""조회수·좋아요·배너 순위, 중요 표시(상단 고정), 팝업."""
import json

import pytest
from fastapi.testclient import TestClient

SUPER = "super-secret-key"
S = {"Authorization": f"Bearer {SUPER}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import admin, db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(admin, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(admin, "ADMIN_TOKEN", SUPER)
    # 크롤링한 공고처럼 보이는 테스트 데이터 3건 (모두 진행 중)
    conn = db.connect()
    for pid, title, img in [(1, "장학금 A", "https://www.kangwon.ac.kr/a.png"), (2, "장학금 B", None), (3, "장학금 C", None)]:
        conn.execute(
            "INSERT INTO notices (board_id, post_id, title, campuses, posted_at, category, deadline, deadline_estimated, images)"
            " VALUES (721, ?, ?, ',춘천,', ?, '장학금', '2099-12-31', 0, ?)",
            (pid, title, f"2026-10-0{pid}", json.dumps([img] if img else [])),
        )
    conn.commit()
    conn.close()
    from app.main import app
    return TestClient(app)


def titles(client, **params):
    return [i["title"] for i in client.get("/api/notices", params=params).json()["items"]]


def test_views_likes_rankings(client):
    for _ in range(3):
        client.post("/api/notices/721-2/view")
    client.post("/api/notices/721-3/view")
    assert client.post("/api/notices/721-3/like").json() == {"site_views": 1, "likes": 1}
    client.post("/api/notices/721-3/like")
    assert client.delete("/api/notices/721-3/like").json()["likes"] == 1
    client.delete("/api/notices/721-1/like")  # 좋아요 없는데 취소해도 음수가 안 됨
    assert client.get("/api/notices/721-1").json()["likes"] == 0
    assert client.post("/api/notices/721-999/view").status_code == 404

    r = client.get("/api/rankings").json()
    assert [n["title"] for n in r["views"]] == ["장학금 B", "장학금 C"]  # 조회수 순, 0회는 제외
    assert [n["title"] for n in r["likes"]] == ["장학금 C"]
    assert r["views"][0]["site_views"] == 3 and r["total_views"] == 4
    assert len(r["deadline"]) == 3


def test_important_pins_to_top(client):
    assert titles(client, category="장학금") == ["장학금 C", "장학금 B", "장학금 A"]  # 최신순
    assert client.put("/api/admin/notices/721-1/flags", json={"important": True}).status_code == 401
    r = client.put("/api/admin/notices/721-1/flags", headers=S, json={"important": True})
    assert r.status_code == 200 and r.json()["important"] is True
    assert titles(client, category="장학금") == ["장학금 A", "장학금 C", "장학금 B"]  # 중요 글이 맨 위
    assert client.get("/api/notices/721-1").json()["important"] is True
    client.put("/api/admin/notices/721-1/flags", headers=S, json={"important": False})
    assert titles(client, category="장학금")[0] == "장학금 C"


def test_popup(client):
    assert client.get("/api/popups").json() == []
    # 사진 없는 글은 팝업 불가
    assert client.put("/api/admin/notices/721-2/flags", headers=S, json={"popup": True}).status_code == 400
    r = client.put("/api/admin/notices/721-1/flags", headers=S, json={"popup": True})
    assert r.json()["popup_image"] == "https://www.kangwon.ac.kr/a.png"  # 첫 번째 사진이 대표 이미지
    pops = client.get("/api/popups").json()
    assert len(pops) == 1 and pops[0]["id"] == "721-1" and pops[0]["key"].startswith("721-1@")
    # 중요 표시를 바꿔도 팝업은 그대로
    client.put("/api/admin/notices/721-1/flags", headers=S, json={"important": True})
    assert client.get("/api/popups").json()[0]["key"] == pops[0]["key"]
    client.put("/api/admin/notices/721-1/flags", headers=S, json={"popup": False})
    assert client.get("/api/popups").json() == []


def test_department_admin_flags_only_own_events(client):
    dept = client.post("/api/admin/departments", headers=S, json={"name": "컴공", "campus": "삼척"}).json()["id"]
    key = client.post("/api/admin/keys", headers=S, json={"role": "department", "department_id": dept}).json()["key"]
    D = {"Authorization": f"Bearer {key}"}
    ev = client.post("/api/admin/events", headers=D, json={"title": "세미나", "event_start": "2099-01-01"}).json()["id"]
    assert client.put(f"/api/admin/notices/{ev}/flags", headers=D, json={"important": True}).status_code == 200
    assert client.put("/api/admin/notices/721-1/flags", headers=D, json={"important": True}).status_code == 403


def test_notices_by_ids_for_liked_list(client):
    r = client.get("/api/notices/by-ids", params={"ids": "721-3,721-1,721-999,bad"}).json()
    assert [i["id"] for i in r["items"]] == ["721-3", "721-1"]  # 요청한 순서 유지
    assert r["missing"] == ["721-999"]
    client.delete("/api/admin/notices/721-3", headers=S)          # 삭제(숨김)한 공고는 빠짐
    r = client.get("/api/notices/by-ids", params={"ids": "721-3,721-1"}).json()
    assert [i["id"] for i in r["items"]] == ["721-1"] and r["missing"] == ["721-3"]
