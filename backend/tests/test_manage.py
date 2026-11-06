"""관리자 사이트 기능: 수집 공고 수정·삭제(숨김)·복원, 정렬, 중요·팝업 목록, 캠퍼스별 시청 분류."""
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
    conn = db.connect()
    rows = [  # (board, post, title, campuses, category, posted, image)
        (721, 1, "장학 A", ",춘천,", "장학금", "2026-10-01", "https://www.kangwon.ac.kr/a.png"),
        (721, 2, "장학 B", ",춘천,", "장학금", "2026-10-02", None),
        (721, 3, "장학 C", ",춘천,", "장학금", "2026-10-03", None),
        (9002, 10, "춘천 축제", ",춘천,", "춘천시", "2026-10-04", None),
    ]
    for b, p, t, c, cat, d, img in rows:
        conn.execute(
            "INSERT INTO notices (board_id, post_id, title, campuses, posted_at, category, deadline, deadline_estimated, images)"
            " VALUES (?,?,?,?,?,?, '2099-12-31', 1, ?)", (b, p, t, c, d, cat, json.dumps([img] if img else [])))
    conn.commit()
    conn.close()
    from app.main import app
    return TestClient(app)


titles = lambda client, **p: [i["title"] for i in client.get("/api/notices", params=p).json()["items"]]  # noqa: E731


def test_sort(client):
    for _ in range(2):
        client.post("/api/notices/721-1/view")
    client.post("/api/notices/721-2/view")
    assert titles(client, category="장학금") == ["장학 C", "장학 B", "장학 A"]              # 최신순(기본)
    assert titles(client, category="장학금", sort="oldest") == ["장학 A", "장학 B", "장학 C"]
    assert titles(client, category="장학금", sort="views") == ["장학 A", "장학 B", "장학 C"]
    # 중요 표시는 어떤 정렬이든 맨 위
    client.put("/api/admin/notices/721-3/flags", headers=S, json={"important": True})
    assert titles(client, category="장학금", sort="oldest")[0] == "장학 C"
    assert titles(client, flag="important") == ["장학 C"]


def test_edit_overrides_survive_recrawl(client):
    r = client.put("/api/admin/notices/721-2", headers=S, json={"title": "장학 B (수정)", "category": "행사", "deadline": "2099-01-15"})
    assert r.status_code == 200, r.text
    d = client.get("/api/notices/721-2").json()
    assert (d["title"], d["category"], d["deadline"], d["deadline_estimated"], d["edited"]) == ("장학 B (수정)", "행사", "2099-01-15", False, True)
    assert titles(client, category="행사") == ["장학 B (수정)"]
    # 크롤러가 같은 글을 다시 저장해도 고친 값 유지
    from app import db
    from app.deadline import DeadlineResult
    from app.parser import Detail, ListItem
    conn = db.connect()
    db.upsert(conn, 721, ListItem(2, "장학 B", [ "춘천"], "", None, 0, False), Detail(), "장학금", DeadlineResult(None, True))
    conn.commit(); conn.close()
    assert client.get("/api/notices/721-2").json()["title"] == "장학 B (수정)"
    # 비우면 원래 값으로
    client.put("/api/admin/notices/721-2", headers=S, json={"title": None, "category": None, "deadline": None})
    assert client.get("/api/notices/721-2").json()["title"] == "장학 B"
    # 잘못된 분류·날짜, 권한 없음
    assert client.put("/api/admin/notices/721-2", headers=S, json={"category": "없는분류"}).status_code == 422
    assert client.put("/api/admin/notices/721-2", json={"title": "x"}).status_code == 401


def test_delete_hides_and_restore(client):
    client.put("/api/admin/notices/721-1/flags", headers=S, json={"popup": True, "important": True})
    assert client.delete("/api/admin/notices/721-1").status_code == 401
    r = client.delete("/api/admin/notices/721-1", headers=S)
    assert r.json() == {"deleted": "721-1", "hidden": True}
    assert "장학 A" not in titles(client)                         # 일반 목록에서 빠짐
    assert client.get("/api/notices/721-1").status_code == 404    # 일반 사용자는 못 봄
    assert client.get("/api/notices/721-1", headers=S).json()["hidden"] is True  # 관리자는 봄
    assert client.get("/api/popups").json() == []                 # 숨기면 팝업도 내려감
    assert client.get("/api/notices", params={"hidden": True}).status_code == 401
    assert [i["title"] for i in client.get("/api/notices", params={"hidden": True}, headers=S).json()["items"]] == ["장학 A"]
    assert client.post("/api/admin/notices/721-1/restore", headers=S).status_code == 200
    assert "장학 A" in titles(client)


def test_department_event_delete_via_notices(client):
    dept = client.post("/api/admin/departments", headers=S, json={"name": "컴공", "campus": "춘천"}).json()["id"]
    ev = client.post("/api/admin/events", headers=S, json={"department_id": dept, "title": "세미나"}).json()["id"]
    assert client.put(f"/api/admin/notices/{ev}", headers=S, json={"title": "x"}).status_code == 400  # 학과 행사는 /events 로
    assert client.delete(f"/api/admin/notices/{ev}", headers=S).json()["hidden"] is False         # 완전 삭제
    assert client.get(f"/api/notices/{ev}").status_code == 404


def test_city_category_meta(client):
    m = client.get("/api/meta").json()
    assert m["city_category_by_campus"] == {"삼척": "삼척시", "춘천": "춘천시", "강릉": "강릉시", "원주": "원주시"}
    assert {"춘천시", "강릉시", "원주시", "삼척시"} <= set(m["categories"])
    assert titles(client, campus="춘천", category="춘천시") == ["춘천 축제"]
