"""적합도: 공고 태깅(규칙+나이브 베이즈), 점수 계산, 프로필 API, 알림 대기열."""
import json

import pytest
from fastapi.testclient import TestClient

from app import matching

SUPER = "super-secret-key"


def test_tag_and_score_basics():
    t = matching.tag_notice(721, "2027학년도 1학기 교내 장학생 선발 안내", "재학생 대상 장학금 신청", ["삼척"])
    assert t["topics"]["장학금"] >= 0.75 and t["aud_student"] > t["aud_resident"] and t["campuses"] == ["삼척"]
    student = {"user_type": "student", "campus": "삼척", "grade": 2, "interests": ["장학금"]}
    other_campus = {**student, "campus": "춘천"}
    no_interest = {**student, "interests": ["창업"]}
    s = matching.score(student, t)
    assert s >= matching.MATCH_THRESHOLD
    assert matching.score(other_campus, t) < s * 0.5          # 다른 캠퍼스만 대상
    assert matching.score(no_interest, t) < matching.MATCH_THRESHOLD


def test_conditions_grade_gender_age_department():
    t = matching.tag_notice(715, "컴퓨터공학과 3학년 대상 취업 특강", "", ["춘천"])
    assert t["grades"] == [3]
    base = {"user_type": "student", "campus": "춘천", "interests": ["교육·강좌"]}
    assert matching.score({**base, "grade": 3}, t, "컴퓨터공학과 3학년 대상 취업 특강") > matching.score({**base, "grade": 1}, t)
    assert matching.score({**base, "grade": 3, "department": "컴퓨터공학과"}, t, "컴퓨터공학과 3학년 대상 취업 특강") >= 90

    city = matching.tag_notice(9004, "「2026년 원주 청년 재테크 교육」 참여자 모집", "만 19세~39세 원주시민", ["원주"])
    assert (city["age_min"], city["age_max"]) == (19, 39) and city["aud_resident"] > 0.8
    young = {"user_type": "resident", "region": "원주", "age": 25, "interests": ["교육·강좌"]}
    assert matching.score(young, city) >= matching.MATCH_THRESHOLD
    assert matching.score({**young, "age": 70}, city) < matching.MATCH_THRESHOLD


def test_naive_bayes_learns_from_corpus():
    docs = [("장학생 선발 안내", "장학금 지급")] * 5 + [("축제 개최 안내", "행사 공연")] * 5 + [("주차장 공사", "통제")] * 5
    m = matching.TopicModel.train(docs)
    assert m.trained()
    p = m.predict("장학생 모집", "")
    assert p["장학금"] > 0.5 and p["장학금"] > p.get("행사·축제", 0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import admin, db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(matching, "MODEL_PATH", tmp_path / "model.json")
    monkeypatch.setattr(matching, "_model", matching.TopicModel())
    monkeypatch.setattr(admin, "ADMIN_TOKEN", SUPER)
    conn = db.connect()
    for pid, title, camp in [(1, "교내 장학생 선발 안내", ",삼척,"), (2, "취업 특강 안내", ",춘천,"), (3, "도서관 휴관 안내", ",삼척,")]:
        conn.execute("INSERT INTO notices (board_id, post_id, title, campuses, posted_at, category, deadline, images, content_text)"
                     " VALUES (721,?,?,?,'2026-10-01','장학금','2099-12-31',?,'')",
                     (pid, title, camp, json.dumps(["https://www.kangwon.ac.kr/a.png"] if pid == 1 else [])))
    conn.commit(); conn.close()
    from app.main import app
    return TestClient(app)


def test_profile_matches_and_notifications(client):
    opts = client.get("/api/profile-options").json()
    assert "장학금" in opts["topics"] and opts["threshold"] == matching.MATCH_THRESHOLD
    # 검증: 학생은 캠퍼스 필수, 없는 주제 거부, 관리자는 키 필요
    assert client.post("/api/profiles", json={"user_type": "student"}).status_code == 422
    assert client.post("/api/profiles", json={"user_type": "student", "campus": "삼척", "interests": ["없는주제"]}).status_code == 422
    assert client.post("/api/profiles", json={"role": "admin", "admin_key": "wrong", "user_type": "student", "campus": "삼척"}).status_code == 401
    ok_admin = client.post("/api/profiles", json={"role": "admin", "admin_key": SUPER, "user_type": "student", "campus": "삼척"})
    assert ok_admin.status_code == 200 and "admin_key" not in ok_admin.json()["profile"]  # 키는 저장 안 함

    r = client.post("/api/profiles", json={"user_type": "student", "campus": "삼척", "grade": 2, "department": "컴퓨터공학과",
                                           "interests": ["장학금"]}).json()
    dev = r["device_id"]
    m = client.get("/api/me/matches", params={"device_id": dev}).json()
    assert [i["title"] for i in m["items"]] == ["교내 장학생 선발 안내"]
    assert m["items"][0]["match_score"] >= matching.MATCH_THRESHOLD
    assert m["items"][0]["thumbnail"] == "https://www.kangwon.ac.kr/a.png"
    # 수정(설정): 같은 device_id 로 다시 저장
    client.post("/api/profiles", json={"device_id": dev, "user_type": "student", "campus": "춘천", "interests": ["교육·강좌", "취업·채용"]})
    assert client.get(f"/api/profiles/{dev}").json()["profile"]["campus"] == "춘천"
    assert [i["title"] for i in client.get("/api/me/matches", params={"device_id": dev}).json()["items"]] == ["취업 특강 안내"]

    # 새 공고가 들어오면 맞는 사람에게만 알림 대기열
    from app import db
    conn = db.connect()
    conn.execute("INSERT INTO notices (board_id, post_id, title, campuses, posted_at, category, deadline, content_text)"
                 " VALUES (716, 9, '춘천캠퍼스 현장실습 인턴 채용 공고', ',춘천,', '2026-11-05', '채용', '2099-12-31', '재학생 인턴')")
    assert matching.queue_notifications(conn, [(716, 9)]) >= 1
    conn.commit(); conn.close()
    n = client.get("/api/me/notifications", params={"device_id": dev}).json()["items"]
    assert [i["id"] for i in n] == ["716-9"]
    assert client.get("/api/me/notifications", params={"device_id": dev}).json()["items"] == []  # 한 번만 받음
    assert client.get("/api/me/matches", params={"device_id": "nope"}).status_code == 404
    assert client.delete(f"/api/profiles/{dev}").status_code == 200


def test_list_has_match_score_with_device_id(client):
    dev = client.post("/api/profiles", json={"user_type": "student", "campus": "춘천", "interests": ["장학금"]}).json()["device_id"]
    items = client.get("/api/notices", params={"device_id": dev}).json()["items"]
    assert items and all(isinstance(i["match_score"], int) for i in items)
    assert "match_score" not in client.get("/api/notices").json()["items"][0]
    assert "match_score" not in client.get("/api/notices", params={"device_id": "nope"}).json()["items"][0]
