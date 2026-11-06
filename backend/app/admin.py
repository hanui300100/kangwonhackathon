"""관리자 API: 관리자 키 발급, 학과 관리, 학과 행사 등록/수정/삭제, 사진·파일 업로드, 수동 크롤링.

로그인: 헤더 `Authorization: Bearer <키>` (또는 `X-Admin-Token: <키>`)

관리자 등급
  super       최종 관리자. 환경변수 ADMIN_TOKEN 값이 키   ($env:ADMIN_TOKEN="원하는값")
              → 일반·학과 관리자 키 발급, 모든 기능
  general     일반 관리자. 최종 관리자가 발급
              → 학과 관리자 키 발급, 학과 추가/수정/삭제, 모든 학과 행사, 크롤링
  department  학과 관리자. 최종/일반 관리자가 발급
              → 자기 학과 행사만 등록/수정/삭제
"""
import hashlib
import json
import html
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from . import db, matching
from .config import ADMIN_BOARD_ID, ADMIN_TOKEN, CAMPUSES, CRAWLABLE_BOARDS, MAX_UPLOAD_MB, UPLOAD_DIR
from .crawler import crawl
from .parser import readable_text

ROLE_NAMES = {"super": "최종 관리자", "general": "일반 관리자", "department": "학과 관리자"}


@dataclass
class Admin:
    role: str  # super / general / department
    department_id: int | None = None
    key_id: int | None = None  # 발급된 키면 그 번호 (최종 관리자는 None)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def current_admin(
    authorization: str = Header("", description="Bearer <관리자 키>"),
    x_admin_token: str = Header("", description="관리자 키 (Authorization 대신 써도 됨)"),
) -> Admin:
    key = authorization.removeprefix("Bearer ").strip() if authorization else x_admin_token.strip()
    if not key:
        raise HTTPException(401, "관리자 키가 필요합니다")
    if ADMIN_TOKEN and secrets.compare_digest(key, ADMIN_TOKEN):
        return Admin("super")
    conn = db.connect()
    try:
        row = db.find_admin_key(conn, hash_key(key))
    finally:
        conn.close()
    if row:
        return Admin(row["role"], row["department_id"], row["id"])
    if not ADMIN_TOKEN:
        raise HTTPException(503, "서버에 ADMIN_TOKEN 환경변수가 설정되지 않았습니다. 최종 관리자 키로 쓸 값을 설정하세요")
    raise HTTPException(401, "관리자 키가 올바르지 않습니다")


def manager(admin: Admin = Depends(current_admin)) -> Admin:
    """최종·일반 관리자만"""
    if admin.role not in ("super", "general"):
        raise HTTPException(403, "최종 또는 일반 관리자만 할 수 있습니다")
    return admin


router = APIRouter(prefix="/api/admin", tags=["관리자"])


@router.get("/me", summary="로그인한 관리자 정보 (키 확인용)")
def me(admin: Admin = Depends(current_admin)):
    conn = db.connect()
    dept = db.get_department(conn, admin.department_id) if admin.department_id else None
    conn.close()
    return {"role": admin.role, "role_name": ROLE_NAMES[admin.role], "department": dept}


# ── 관리자 키 발급 ──

class KeyIn(BaseModel):
    role: Literal["general", "department"]
    department_id: int | None = Field(None, description="학과 관리자 키일 때 담당 학과")
    label: str | None = Field(None, max_length=50, description="메모 (예: 담당자 이름)")


@router.post("/keys", summary="관리자 키 발급 (키는 이 응답에서 한 번만 보여줌)")
def create_key(body: KeyIn, admin: Admin = Depends(manager)):
    if body.role == "general" and admin.role != "super":
        raise HTTPException(403, "일반 관리자 키는 최종 관리자만 발급할 수 있습니다")
    conn = db.connect()
    try:
        if body.role == "department":
            if not body.department_id or not db.get_department(conn, body.department_id):
                raise HTTPException(400, "담당 학과를 선택하세요 (먼저 학과를 추가해야 합니다)")
        key = secrets.token_urlsafe(24)
        key_id = db.create_admin_key(conn, hash_key(key), body.role,
                                     body.department_id if body.role == "department" else None, body.label)
        conn.commit()
    finally:
        conn.close()
    return {"id": key_id, "key": key, "role": body.role, "department_id": body.department_id}


@router.get("/keys", summary="발급된 키 목록 (키 원문은 없음)")
def list_keys(admin: Admin = Depends(manager)):
    conn = db.connect()
    keys = db.list_admin_keys(conn)
    conn.close()
    if admin.role == "general":  # 일반 관리자는 학과 관리자 키만 봄
        keys = [k for k in keys if k["role"] == "department"]
    return keys


@router.delete("/keys/{key_id}", summary="키 폐기")
def revoke_key(key_id: int, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        k = db.get_admin_key(conn, key_id)
        if not k:
            raise HTTPException(404, "키를 찾을 수 없습니다")
        if k["role"] == "general" and admin.role != "super":
            raise HTTPException(403, "일반 관리자 키는 최종 관리자만 폐기할 수 있습니다")
        db.delete_admin_key(conn, key_id)
        conn.commit()
    finally:
        conn.close()
    return {"deleted": key_id}


# ── 입력 형식 ──

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=50, examples=["컴퓨터공학과"])
    college: str | None = Field(None, max_length=50, examples=["IT대학"])
    campus: str = Field(examples=["춘천"])

    @field_validator("campus")
    @classmethod
    def _campus(cls, v):
        if v not in CAMPUSES:
            raise ValueError(f"캠퍼스는 {CAMPUSES} 중 하나")
        return v


class FileRef(BaseModel):
    name: str
    url: str


class LinkRef(BaseModel):
    text: str
    url: str


class EventIn(BaseModel):
    department_id: int | None = Field(None, description="학과 관리자는 생략 가능 (자기 학과로 고정)")
    title: str = Field(min_length=1, max_length=200)
    content_text: str | None = Field(None, description="본문(일반 글). content_html을 안 주면 이걸로 HTML을 만듦")
    content_html: str | None = Field(None, description="본문 HTML (선택)")
    event_start: str | None = Field(None, examples=["2026-11-20T14:00"], description="YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM")
    event_end: str | None = Field(None, examples=["2026-11-20T17:00"])
    location: str | None = Field(None, max_length=100, examples=["공학관 101호"])
    deadline: str | None = Field(None, examples=["2026-11-18"], description="신청 마감일. 비우면 행사 종료일(없으면 시작일)")
    contact: str | None = Field(None, max_length=100)
    images: list[str] = Field(default_factory=list, description="사진 주소 (업로드 API 결과의 url)")
    attachments: list[FileRef] = Field(default_factory=list)
    links: list[LinkRef] = Field(default_factory=list)
    is_pinned: bool = False

    @field_validator("event_start", "event_end")
    @classmethod
    def _dt(cls, v):
        if v and not DATE_TIME.match(v):
            raise ValueError("YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM 형식")
        return v or None

    @field_validator("deadline")
    @classmethod
    def _d(cls, v):
        if v and not DATE_ONLY.match(v):
            raise ValueError("YYYY-MM-DD 형식")
        if v:
            date.fromisoformat(v)  # 2026-02-30 같은 없는 날짜 거르기
        return v or None

    def to_record(self) -> dict:
        ev = self.model_dump()
        text, body = ev.get("content_text") or "", ev.get("content_html") or ""
        if text and not body:  # 일반 글 → 줄바꿈 유지한 HTML
            ev["content_html"] = "".join(f"<p>{html.escape(line) or '&nbsp;'}</p>" for line in text.splitlines())
        elif body and not text:  # HTML만 왔으면 검색용 텍스트 생성
            ev["content_text"] = readable_text(body)
        return ev


# ── 학과 (최종·일반 관리자) ──

@router.post("/departments", summary="학과 추가")
def create_department(body: DepartmentIn, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        dept_id = db.save_department(conn, body.name, body.college, body.campus)
        conn.commit()
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "같은 캠퍼스에 같은 이름의 학과가 이미 있습니다")
        raise
    finally:
        conn.close()
    return {"id": dept_id, **body.model_dump()}


@router.put("/departments/{dept_id}", summary="학과 수정")
def update_department(dept_id: int, body: DepartmentIn, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        if not db.get_department(conn, dept_id):
            raise HTTPException(404, "학과를 찾을 수 없습니다")
        db.save_department(conn, body.name, body.college, body.campus, dept_id)
        conn.commit()
    finally:
        conn.close()
    return {"id": dept_id, **body.model_dump()}


@router.delete("/departments/{dept_id}", summary="학과 삭제 (행사·학과 관리자 키가 남아 있으면 거부)")
def remove_department(dept_id: int, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        if not db.get_department(conn, dept_id):
            raise HTTPException(404, "학과를 찾을 수 없습니다")
        if db.department_in_use(conn, dept_id):
            raise HTTPException(409, "이 학과의 행사나 학과 관리자 키가 남아 있어 삭제할 수 없습니다")
        db.delete_department(conn, dept_id)
        conn.commit()
    finally:
        conn.close()
    return {"deleted": dept_id}


# ── 학과 행사 ──

def _target_department(conn, admin: Admin, requested: int | None) -> dict:
    """학과 관리자는 자기 학과로 고정, 나머지는 요청한 학과."""
    dept_id = admin.department_id if admin.role == "department" else requested
    if admin.role == "department" and requested and requested != admin.department_id:
        raise HTTPException(403, "자기 학과 행사만 등록할 수 있습니다")
    dept = db.get_department(conn, dept_id) if dept_id else None
    if not dept:
        raise HTTPException(400, "학과를 선택하세요 (먼저 학과를 추가해야 합니다)")
    return dept


def _check_event_owner(conn, admin: Admin, post_id: int):
    if not db.exists(conn, ADMIN_BOARD_ID, post_id):
        raise HTTPException(404, "학과 행사를 찾을 수 없습니다")
    if admin.role == "department" and db.event_department(conn, post_id) != admin.department_id:
        raise HTTPException(403, "다른 학과의 행사는 수정·삭제할 수 없습니다")


@router.get("/events", summary="관리할 수 있는 학과 행사 목록 (학과 관리자는 자기 학과만)")
def list_events(admin: Admin = Depends(current_admin)):
    conn = db.connect()
    q, params = "SELECT * FROM notices WHERE board_id=?", [ADMIN_BOARD_ID]
    if admin.role == "department":
        q += " AND department_id=?"
        params.append(admin.department_id)
    rows = conn.execute(q + " ORDER BY post_id DESC", params).fetchall()
    depts = {d["id"]: d for d in db.list_departments(conn)}
    conn.close()
    return [db.row_to_dict(r, full=True, departments=depts) for r in rows]


@router.post("/events", summary="학과 행사 등록")
def create_event(body: EventIn, admin: Admin = Depends(current_admin)):
    conn = db.connect()
    try:
        post_id = db.save_event(conn, body.to_record(), _target_department(conn, admin, body.department_id))
        # 새 학과 행사도 맞는 사용자에게 알림 (태그 계산 → 알림 대기열)
        matching.queue_notifications(conn, [(ADMIN_BOARD_ID, post_id)])
        matching.tag_row(conn, ADMIN_BOARD_ID, post_id)
        conn.commit()
    finally:
        conn.close()
    return {"id": db.make_id(ADMIN_BOARD_ID, post_id)}


@router.put("/events/{post_id}", summary="학과 행사 수정 (post_id는 id '0-12'의 12)")
def update_event(post_id: int, body: EventIn, admin: Admin = Depends(current_admin)):
    conn = db.connect()
    try:
        _check_event_owner(conn, admin, post_id)
        db.save_event(conn, body.to_record(), _target_department(conn, admin, body.department_id), post_id)
        matching.tag_row(conn, ADMIN_BOARD_ID, post_id)  # 내용이 바뀌었으니 태그 다시 계산
        conn.commit()
    finally:
        conn.close()
    return {"id": db.make_id(ADMIN_BOARD_ID, post_id)}


@router.delete("/events/{post_id}", summary="학과 행사 삭제")
def remove_event(post_id: int, admin: Admin = Depends(current_admin)):
    conn = db.connect()
    try:
        _check_event_owner(conn, admin, post_id)
        db.delete_event(conn, post_id)
        conn.commit()
    finally:
        conn.close()
    return {"deleted": db.make_id(ADMIN_BOARD_ID, post_id)}


# ── 중요 표시·팝업 (게시글 상세 화면에서) ──

class FlagsIn(BaseModel):
    important: bool | None = Field(None, description="true면 같은 분류 목록 맨 위에 고정")
    popup: bool | None = Field(None, description="true면 사이트 접속 때 대표 이미지를 팝업으로 띄움")
    popup_image: str | None = Field(None, description="팝업 이미지 주소. 비우면 게시글의 첫 번째 사진")


@router.put("/notices/{notice_id}/flags", summary="게시글 중요 표시·팝업 켜고 끄기 (id 예: '721-2537')")
def set_notice_flags(notice_id: str, body: FlagsIn, admin: Admin = Depends(current_admin)):
    key = db.parse_id(notice_id)
    conn = db.connect()
    try:
        row = conn.execute("SELECT images, department_id FROM notices WHERE board_id=? AND post_id=?",
                           key or (None, None)).fetchone()
        if not row:
            raise HTTPException(404, "공지를 찾을 수 없습니다")
        # 학과 관리자는 자기 학과 행사에만 표시 가능, 최종·일반 관리자는 모든 게시글
        if admin.role == "department" and row["department_id"] != admin.department_id:
            raise HTTPException(403, "학과 관리자는 자기 학과 행사에만 표시할 수 있습니다")
        image = None
        if body.popup:
            images = json.loads(row["images"] or "[]")
            image = body.popup_image or (images[0] if images else None)
            if not image:
                raise HTTPException(400, "대표 이미지(사진)가 없는 게시글은 팝업으로 띄울 수 없습니다")
        db.set_flags(conn, *key, important=body.important, popup=body.popup, popup_image=image)
        conn.commit()
        flags = conn.execute("SELECT important, popup, popup_image FROM notice_view WHERE board_id=? AND post_id=?",
                             key).fetchone()
    finally:
        conn.close()
    return {"id": notice_id, "important": bool(flags["important"]), "popup": bool(flags["popup"]),
            "popup_image": flags["popup_image"]}


# ── 게시글 수정·삭제·복원 (관리자 사이트) ──
# 학과 행사는 /events 로 내용 전체를 고치고, 수집한 공고는 여기서 제목·분류·마감일만 덮어씀
# (원문은 학교·시청 사이트 것이라 본문은 고치지 않음. 다시 수집돼도 고친 값이 유지됨)

class NoticeEditIn(BaseModel):
    title: str | None = Field(None, max_length=200, description="비우면(null) 원래 제목으로")
    category: str | None = Field(None, description="비우면 원래 분류로")
    deadline: str | None = Field(None, description="YYYY-MM-DD. 비우면 원래(자동 추출) 마감일로")

    @field_validator("deadline")
    @classmethod
    def _d(cls, v):
        if v:
            if not DATE_ONLY.match(v):
                raise ValueError("YYYY-MM-DD 형식")
            date.fromisoformat(v)
        return v or None

    @field_validator("category")
    @classmethod
    def _c(cls, v):
        from .classifier import CATEGORIES
        if v and v not in CATEGORIES:
            raise ValueError(f"분류는 {CATEGORIES} 중 하나")
        return v or None


def _crawled_notice_or_error(conn, notice_id: str) -> tuple[int, int]:
    key = db.parse_id(notice_id)
    if not key or not db.exists(conn, *key):
        raise HTTPException(404, "공지를 찾을 수 없습니다")
    if key[0] == ADMIN_BOARD_ID:
        raise HTTPException(400, "학과 행사는 '학과 행사 수정'(/api/admin/events)으로 고쳐 주세요")
    return key


@router.put("/notices/{notice_id}", summary="수집한 공고의 제목·분류·마감일 수정 (최종·일반 관리자)")
def edit_notice(notice_id: str, body: NoticeEditIn, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        key = _crawled_notice_or_error(conn, notice_id)
        db.set_overrides(conn, *key, body.model_dump())
        conn.commit()
        row = conn.execute("SELECT * FROM notice_view WHERE board_id=? AND post_id=?", key).fetchone()
    finally:
        conn.close()
    return db.row_to_dict(row)


@router.delete("/notices/{notice_id}", summary="게시글 삭제 (수집 공고는 숨김 → 복원 가능, 학과 행사는 완전 삭제)")
def delete_notice(notice_id: str, admin: Admin = Depends(current_admin)):
    key = db.parse_id(notice_id)
    conn = db.connect()
    try:
        if not key or not db.exists(conn, *key):
            raise HTTPException(404, "공지를 찾을 수 없습니다")
        if key[0] == ADMIN_BOARD_ID:  # 학과 행사: 권한 확인 후 실제 삭제
            _check_event_owner(conn, admin, key[1])
            db.delete_event(conn, key[1])
            result = {"deleted": notice_id, "hidden": False}
        else:
            if admin.role not in ("super", "general"):
                raise HTTPException(403, "수집한 공고는 최종 또는 일반 관리자만 삭제할 수 있습니다")
            db.set_hidden(conn, *key, True)
            result = {"deleted": notice_id, "hidden": True}
        conn.commit()
    finally:
        conn.close()
    return result


@router.post("/notices/{notice_id}/restore", summary="숨긴(삭제한) 수집 공고 복원")
def restore_notice(notice_id: str, admin: Admin = Depends(manager)):
    conn = db.connect()
    try:
        key = _crawled_notice_or_error(conn, notice_id)
        db.set_hidden(conn, *key, False)
        conn.commit()
    finally:
        conn.close()
    return {"restored": notice_id}


# ── 사진·파일 업로드 (모든 관리자) ──

ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",  # 사진
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".txt",
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@router.post("/uploads", summary="사진·첨부파일 업로드 → 행사 등록 시 images/attachments에 url 사용")
async def upload(request: Request, file: UploadFile = File(...), admin: Admin = Depends(current_admin)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"허용되지 않는 파일 형식입니다: {ext or '(확장자 없음)'}")
    data = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"{MAX_UPLOAD_MB}MB 이하만 올릴 수 있습니다")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"  # 원래 이름은 경로 문제를 피하려고 저장 파일명에 쓰지 않음
    (UPLOAD_DIR / stored).write_bytes(data)
    return {
        "name": file.filename,
        "url": str(request.base_url).rstrip("/") + f"/uploads/{stored}",
        "is_image": ext in IMAGE_EXT,
        "size": len(data),
    }


# ── 수동 크롤링 (최종·일반 관리자) ──

@router.post("/crawl", summary="수동 크롤링 실행")
async def run_crawl(
    pages: int = Query(3, ge=1, le=300, description="출처마다 목록 몇 페이지까지 (1페이지 = 약 10~20개)"),
    refresh: bool = Query(False, description="이미 저장된 글도 다시 수집"),
    board: list[int] | None = Query(None, description=f"생략하면 전체. 가능: {CRAWLABLE_BOARDS}"),
    admin: Admin = Depends(manager),
):
    if board and any(b not in CRAWLABLE_BOARDS for b in board):
        raise HTTPException(400, f"게시판은 {CRAWLABLE_BOARDS} 중에서 고르세요")
    return await run_in_threadpool(crawl, pages, refresh, board)


# ── 관리자가 직접 보내는 알림 ──
# 앱은 5분마다(그리고 켤 때) /api/me/notifications 로 가져가서 휴대폰 알림으로 띄움

class BroadcastIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=60, description="알림 제목")
    body: str = Field("", max_length=300, description="알림 내용")
    target: Literal["all", "campus", "department"] = "all"
    campus: str | None = Field(None, description="target=campus 일 때")
    department_id: int | None = Field(None, description="target=department 일 때 (학과 관리자는 자기 학과로 고정)")
    user_type: Literal["student", "resident"] | None = Field(None, description="학생만·주민만 (비우면 모두)")
    notice_id: str | None = Field(None, description="알림을 누르면 열 공고 id (예: 721-2537)")


def _broadcast_dict(conn, r) -> dict:
    d = dict(r)
    if d["department_id"]:
        dep = conn.execute("SELECT name, campus FROM departments WHERE id=?", (d["department_id"],)).fetchone()
        d["department"] = f"{dep['name']} ({dep['campus']})" if dep else None
    d["read_count"] = conn.execute("SELECT COUNT(*) FROM broadcast_reads WHERE broadcast_id=? AND skipped=0", (d["id"],)).fetchone()[0]
    return d


@router.post("/broadcasts", summary="알림 보내기 (앱 사용자에게)")
def create_broadcast(body: BroadcastIn, admin: Admin = Depends(current_admin)):
    target, campus, dept_id = body.target, None, None
    if admin.role == "department":  # 학과 관리자는 자기 학과 학생에게만
        target, dept_id = "department", admin.department_id
    elif target == "campus":
        if body.campus not in CAMPUSES:
            raise HTTPException(422, f"캠퍼스를 골라 주세요 ({CAMPUSES})")
        campus = body.campus
    elif target == "department":
        dept_id = body.department_id
    conn = db.connect()
    try:
        if target == "department" and not conn.execute("SELECT 1 FROM departments WHERE id=?", (dept_id,)).fetchone():
            raise HTTPException(422, "학과를 골라 주세요")
        if body.notice_id:
            key = db.parse_id(body.notice_id)
            if not key or not conn.execute("SELECT 1 FROM notices WHERE board_id=? AND post_id=?", key).fetchone():
                raise HTTPException(422, "연결할 공고를 찾을 수 없습니다")
        cur = conn.execute(
            "INSERT INTO broadcasts (title, body, notice_id, target, campus, department_id, user_type, created_at, created_by)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (body.title.strip(), body.body.strip(), body.notice_id, target, campus, dept_id,
             "student" if target == "department" else body.user_type,
             datetime.now().isoformat(timespec="seconds"), admin.role))
        conn.commit()
        return _broadcast_dict(conn, conn.execute("SELECT * FROM broadcasts WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


@router.get("/broadcasts", summary="보낸 알림 목록")
def list_broadcasts(admin: Admin = Depends(current_admin)):
    conn = db.connect()
    try:
        if admin.role == "department":
            rows = conn.execute("SELECT * FROM broadcasts WHERE department_id=? ORDER BY id DESC LIMIT 100", (admin.department_id,))
        else:
            rows = conn.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT 100")
        return [_broadcast_dict(conn, r) for r in rows.fetchall()]
    finally:
        conn.close()


@router.delete("/broadcasts/{broadcast_id}", summary="알림 취소 (아직 못 받은 사용자에게는 안 감)")
def delete_broadcast(broadcast_id: int, admin: Admin = Depends(current_admin)):
    conn = db.connect()
    try:
        row = conn.execute("SELECT department_id FROM broadcasts WHERE id=?", (broadcast_id,)).fetchone()
        if not row:
            raise HTTPException(404, "알림이 없습니다")
        if admin.role == "department" and row["department_id"] != admin.department_id:
            raise HTTPException(403, "자기 학과 알림만 취소할 수 있습니다")
        conn.execute("DELETE FROM broadcasts WHERE id=?", (broadcast_id,))
        conn.execute("DELETE FROM broadcast_reads WHERE broadcast_id=?", (broadcast_id,))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": broadcast_id}
