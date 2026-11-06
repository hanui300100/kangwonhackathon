"""앱 사용자 프로필 · 나에게 맞는 공고 · 알림 API.

로그인 없이 앱이 처음 실행될 때 서버가 기기 ID(device_id)를 발급하고, 앱은 그걸 저장해 둔다.
프로필은 적합도 계산(matching.py)에만 쓰이며 이름·연락처 같은 개인정보는 받지 않는다.
"""
import json
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import db, matching
from .config import CAMPUSES

router = APIRouter(prefix="/api", tags=["사용자 프로필·맞춤 공고"])

GRADES = [1, 2, 3, 4, 5, 6]


class ProfileIn(BaseModel):
    device_id: str | None = Field(None, description="처음 등록할 때는 비움 → 서버가 발급")
    role: Literal["user", "admin"] = "user"
    admin_key: str | None = Field(None, description="관리자를 고른 경우 확인용 (서버에 저장하지 않음)")
    user_type: Literal["student", "resident"]
    campus: str | None = Field(None, description="학생: 춘천/삼척/강릉/원주")
    department: str | None = Field(None, max_length=50, description="학생: 학과")
    grade: int | None = Field(None, description="학생: 학년 1~6")
    region: str | None = Field(None, description="주민: 춘천/삼척/강릉/원주/기타")
    gender: Literal["M", "F", "N"] | None = Field(None, description="주민: M/F/N(밝히지 않음)")
    age: int | None = Field(None, ge=1, le=120, description="주민: 나이")
    interests: list[str] = Field(default_factory=list, description="관심 주제 (여러 개)")

    @field_validator("interests")
    @classmethod
    def _interests(cls, v):
        bad = [t for t in v if t not in matching.TOPIC_NAMES]
        if bad:
            raise ValueError(f"알 수 없는 주제: {bad}. 가능: {matching.TOPIC_NAMES}")
        return list(dict.fromkeys(v))

    def checked(self) -> dict:
        if self.user_type == "student":
            if self.campus not in CAMPUSES:
                raise HTTPException(422, f"학생은 캠퍼스를 골라 주세요 ({CAMPUSES})")
            if self.grade is not None and self.grade not in GRADES:
                raise HTTPException(422, "학년은 1~6")
            return {"role": self.role, "user_type": "student", "campus": self.campus,
                    "department": (self.department or "").strip(), "grade": self.grade, "interests": self.interests}
        if self.region not in [*CAMPUSES, "기타"]:
            raise HTTPException(422, f"주민은 지역을 골라 주세요 ({CAMPUSES + ['기타']})")
        return {"role": self.role, "user_type": "resident", "region": self.region,
                "gender": self.gender, "age": self.age, "interests": self.interests}


@router.get("/profile-options", summary="사용자 정보 입력 화면에 쓸 선택지")
def profile_options():
    return {
        "campuses": CAMPUSES,
        "regions": [*CAMPUSES, "기타"],
        "grades": GRADES,
        "genders": [{"key": "M", "label": "남성"}, {"key": "F", "label": "여성"}, {"key": "N", "label": "밝히지 않음"}],
        "topics": matching.TOPIC_NAMES,
        "threshold": matching.MATCH_THRESHOLD,
    }


@router.post("/profiles", summary="프로필 등록·수정 (최초 실행, 설정)")
def save_profile(body: ProfileIn):
    profile = body.checked()
    if body.role == "admin":
        # 관리자 키가 맞는지만 확인하고, 키 자체는 저장하지 않음
        from .admin import current_admin
        current_admin(authorization=f"Bearer {body.admin_key or ''}", x_admin_token="")
    device_id = body.device_id or uuid.uuid4().hex
    now = datetime.now().isoformat(timespec="seconds")
    conn = db.connect()
    try:
        if not conn.execute("SELECT 1 FROM user_profiles WHERE device_id=?", (device_id,)).fetchone():
            # 새 사용자: 가입 전에 보낸 관리자 알림은 받은 것으로 처리
            conn.execute("INSERT OR IGNORE INTO broadcast_reads (device_id, broadcast_id, skipped) SELECT ?, id, 1 FROM broadcasts", (device_id,))
        conn.execute(
            """INSERT INTO user_profiles (device_id, profile, created_at, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(device_id) DO UPDATE SET profile=excluded.profile, updated_at=excluded.updated_at""",
            (device_id, json.dumps(profile, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"device_id": device_id, "profile": profile}


def _profile(conn, device_id: str) -> dict:
    row = conn.execute("SELECT profile FROM user_profiles WHERE device_id=?", (device_id,)).fetchone()
    if not row:
        raise HTTPException(404, "등록된 프로필이 없습니다. 사용자 정보를 다시 입력해 주세요")
    return json.loads(row[0])


@router.get("/profiles/{device_id}", summary="내 프로필")
def get_profile(device_id: str):
    conn = db.connect()
    try:
        return {"device_id": device_id, "profile": _profile(conn, device_id)}
    finally:
        conn.close()


@router.delete("/profiles/{device_id}", summary="내 프로필·알림 기록 삭제")
def delete_profile(device_id: str):
    conn = db.connect()
    conn.execute("DELETE FROM user_profiles WHERE device_id=?", (device_id,))
    conn.execute("DELETE FROM notifications WHERE device_id=?", (device_id,))
    conn.commit()
    conn.close()
    return {"deleted": device_id}


@router.get("/me/matches", summary="나에게 맞는 공고 (진행 중, 적합도 높은 순)")
def my_matches(device_id: str, min_score: int = Query(matching.MATCH_THRESHOLD, ge=0, le=100),
               size: int = Query(50, ge=1, le=200)):
    conn = db.connect()
    try:
        profile = _profile(conn, device_id)
        if matching.tag_missing(conn, limit=300):  # 아직 태그 없는 공고가 있으면 이때 계산
            conn.commit()
        today = date.today().isoformat()
        rows = conn.execute(
            """SELECT v.*, t.tags AS tags_json FROM notice_view v
               JOIN notice_tags t ON t.board_id=v.board_id AND t.post_id=v.post_id
               WHERE v.hidden=0 AND v.deadline >= ?""", (today,)).fetchall()
        depts = {d["id"]: d for d in db.list_departments(conn)}
        scored = []
        for r in rows:
            s = matching.score(profile, json.loads(r["tags_json"]), r["title"], r["content_text"] or "")
            if s >= min_score:
                scored.append((s, r))
        # 관리자가 중요 표시한 공고를 먼저 (일반 목록과 같게), 그다음 적합도 높은 순
        scored.sort(key=lambda x: (-x[1]["important"], -x[0], x[1]["deadline"] or ""))
        items = []
        for s, r in scored[:size]:
            d = db.row_to_dict(r, departments=depts)
            d["match_score"] = s
            items.append(d)
        return {"total": len(scored), "threshold": min_score, "items": items}
    finally:
        conn.close()


def _broadcasts_for(conn, device_id: str, profile: dict) -> list:
    """이 사용자가 대상이고 아직 안 받은 관리자 알림 (가입 전 알림은 가입할 때 받은 것으로 처리됨)"""
    rows = conn.execute(
        """SELECT b.* FROM broadcasts b
           WHERE NOT EXISTS (SELECT 1 FROM broadcast_reads r WHERE r.device_id=? AND r.broadcast_id=b.id)
           ORDER BY b.id""", (device_id,)).fetchall()
    out = []
    for b in rows:
        if b["user_type"] and b["user_type"] != profile.get("user_type"):
            continue
        if b["target"] == "campus" and b["campus"] not in (profile.get("campus"), profile.get("region")):
            continue
        if b["target"] == "department":
            d = conn.execute("SELECT name, campus FROM departments WHERE id=?", (b["department_id"],)).fetchone()
            if not matching.is_my_department(profile, dict(d) if d else None):
                continue
        out.append(b)
    return out


@router.get("/me/notifications", summary="새 알림 가져오기: 맞춤 공고·내 학과 소식·관리자 알림 (가져간 알림은 다시 안 줌)")
def my_notifications(device_id: str, peek: bool = Query(False, description="true면 읽음 처리 안 함")):
    conn = db.connect()
    try:
        profile = _profile(conn, device_id)
        rows = conn.execute(
            """SELECT n.board_id, n.post_id, n.score, n.reason, v.title, v.category, v.hidden FROM notifications n
               JOIN notice_view v ON v.board_id=n.board_id AND v.post_id=n.post_id
               WHERE n.device_id=? AND n.delivered=0 ORDER BY n.reason='department' DESC, n.score DESC LIMIT 20""",
            (device_id,)).fetchall()
        items = [{"kind": r["reason"], "id": db.make_id(r["board_id"], r["post_id"]), "title": r["title"],
                  "category": r["category"], "score": r["score"]} for r in rows if not r["hidden"]]
        casts = _broadcasts_for(conn, device_id, profile)
        # 관리자 알림을 맨 앞에
        items = [{"kind": "admin", "id": b["notice_id"], "title": b["title"], "body": b["body"], "score": None,
                  "broadcast_id": b["id"]} for b in casts] + items
        if not peek:
            conn.execute("UPDATE notifications SET delivered=1 WHERE device_id=? AND delivered=0", (device_id,))
            conn.executemany("INSERT OR IGNORE INTO broadcast_reads (device_id, broadcast_id) VALUES (?,?)",
                             [(device_id, b["id"]) for b in casts])
            conn.commit()
        return {"items": items}
    finally:
        conn.close()
