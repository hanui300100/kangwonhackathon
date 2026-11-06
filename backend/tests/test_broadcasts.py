"""관리자 알림 보내기 · 학과 행사를 내 학과 학생에게 알림."""
from test_matching import SUPER, client  # noqa: F401

S = {"Authorization": f"Bearer {SUPER}"}


def notes(client, dev):
    return client.get("/api/me/notifications", params={"device_id": dev}).json()["items"]


def profile(client, **kw):
    return client.post("/api/profiles", json=kw).json()["device_id"]


def test_broadcast_targets(client):
    stu = profile(client, user_type="student", campus="삼척", department="AI소프트웨어 학과")
    other = profile(client, user_type="student", campus="춘천", department="경영학과")
    res = profile(client, user_type="resident", region="삼척")
    dept = client.post("/api/admin/departments", headers=S, json={"name": "Ai소프트웨어학과", "campus": "삼척"}).json()["id"]

    assert client.post("/api/admin/broadcasts", json={"title": "x"}).status_code == 401
    assert client.post("/api/admin/broadcasts", headers=S, json={"title": "전체 공지", "body": "안녕하세요"}).status_code == 200
    assert client.post("/api/admin/broadcasts", headers=S, json={"title": "삼척만", "target": "campus", "campus": "삼척"}).status_code == 200
    assert client.post("/api/admin/broadcasts", headers=S, json={"title": "학과만", "target": "department", "department_id": dept,
                                                                 "notice_id": "721-1"}).status_code == 200
    assert client.post("/api/admin/broadcasts", headers=S, json={"title": "?", "notice_id": "721-999"}).status_code == 422

    admin_titles = lambda dev: [n["title"] for n in notes(client, dev) if n["kind"] == "admin"]
    assert admin_titles(stu) == ["전체 공지", "삼척만", "학과만"]   # 띄어쓰기·대소문자 달라도 같은 학과
    assert admin_titles(stu) == []                               # 한 번 받으면 다시 안 옴
    assert admin_titles(other) == ["전체 공지"]
    assert admin_titles(res) == ["전체 공지", "삼척만"]            # 주민은 사는 지역으로
    # 나중에 가입한 사용자는 예전 알림을 받지 않음
    late = profile(client, user_type="student", campus="삼척")
    assert admin_titles(late) == []
    lst = client.get("/api/admin/broadcasts", headers=S).json()
    assert [b["title"] for b in lst][0] == "학과만" and lst[0]["read_count"] == 1
    assert client.delete(f"/api/admin/broadcasts/{lst[0]['id']}", headers=S).status_code == 200


def test_department_admin_broadcast_is_own_department(client):
    d1 = client.post("/api/admin/departments", headers=S, json={"name": "컴공", "campus": "삼척"}).json()["id"]
    d2 = client.post("/api/admin/departments", headers=S, json={"name": "경영", "campus": "춘천"}).json()["id"]
    key = client.post("/api/admin/keys", headers=S, json={"role": "department", "department_id": d1}).json()["key"]
    D = {"Authorization": f"Bearer {key}"}
    b = client.post("/api/admin/broadcasts", headers=D, json={"title": "우리 과", "target": "all", "department_id": d2}).json()
    assert b["target"] == "department" and b["department_id"] == d1   # 전체로 보내려 해도 자기 학과로 고정
    assert client.delete(f"/api/admin/broadcasts/{b['id']}", headers=D).status_code == 200


def test_department_event_notifies_my_department(client):
    mine = profile(client, user_type="student", campus="삼척", department="컴퓨터 공학과", interests=["취업·채용"])
    other = profile(client, user_type="student", campus="삼척", department="경영학과", interests=["취업·채용"])
    dept = client.post("/api/admin/departments", headers=S, json={"name": "컴퓨터공학과", "campus": "삼척"}).json()["id"]
    ev = client.post("/api/admin/events", headers=S, json={"title": "학과 총회 안내", "department_id": dept,
                                                          "event_start": "2099-01-01"}).json()["id"]
    got = notes(client, mine)
    assert [(n["kind"], n["id"]) for n in got] == [("department", ev)]   # 적합도가 낮아도 내 학과면 알림
    assert all(n["id"] != ev for n in notes(client, other))
