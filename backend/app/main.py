"""FastAPI 서버. 실행: uvicorn app.main:app --reload   (API 문서: http://localhost:8000/docs)"""
import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Literal
from urllib.parse import quote, unquote_plus, urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import json

from . import attachment_analysis, db, matching, refresh
from .admin import current_admin, router as admin_router
from .profiles import router as profiles_router
from .classifier import CATEGORIES
from .config import BOARDS, CAMPUSES, CITY_CATEGORY_BY_CAMPUS, UPLOAD_DIR, USER_AGENT, WEB_DIR

app = FastAPI(title="강원대 공지 모아보기 API", version="0.2.0")

# 화면을 다른 주소(포트)에서 띄워도 호출할 수 있도록 허용
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(admin_router)
app.include_router(profiles_router)
# 관리자가 올린 사진/파일 제공
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.on_event("startup")
def init_department_catalog():
    """학교 공식 학과 목록(departments.json)을 처음 한 번만 넣음. 기존 학과 id·관리자 수정은 유지"""
    conn = db.connect()
    try:
        db.seed_departments(conn)
    finally:
        conn.close()

Status = Literal["ongoing", "closed"]
Source = Literal["knu", "city", "admin"]


def _where(campus, category, status, q, board=None, department=None, source=None, hidden=False):
    # 관리자가 '삭제'(숨김)한 공고는 일반 목록에서 빠짐. hidden=True면 숨긴 공고만 (관리자 사이트용)
    clauses, params = ["hidden = ?"], [int(hidden)]
    if board is not None:
        clauses.append("board_id = ?")
        params.append(board)
    if source:
        ids = [b for b, v in BOARDS.items() if v["source"] == source]
        clauses.append(f"board_id IN ({','.join('?' * len(ids))})")
        params += ids
    if department is not None:
        clauses.append("department_id = ?")
        params.append(department)
    if campus:
        clauses.append("campuses LIKE ?")
        params.append(f"%,{campus},%")
    if category:
        clauses.append("category = ?")
        params.append(category)
    today = date.today().isoformat()
    if status == "ongoing":
        clauses.append("deadline >= ?")
        params.append(today)
    elif status == "closed":
        clauses.append("(deadline IS NULL OR deadline < ?)")
        params.append(today)
    if q:
        clauses.append("(title LIKE ? OR content_text LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _departments_by_id(conn) -> dict[int, dict]:
    return {d["id"]: d for d in db.list_departments(conn)}


@app.post("/api/refresh", summary="새 공고 확인 (사이트 접속·새로고침 버튼용)")
def refresh_notices():
    """백그라운드로 새 공고를 가져오기 시작하고 바로 응답한다.

    - started=true  → 이번 요청으로 시작함. /api/refresh/status 의 running이 false가 되면 목록을 다시 불러오면 됨
    - reason=running → 이미 누군가의 요청으로 진행 중
    - reason=recent  → 최근(기본 10분)에 이미 갱신함. 지금 목록이 최신
    """
    return refresh.request_refresh()


@app.get("/api/refresh/status", summary="새 공고 확인 진행 상태")
def refresh_status():
    return refresh.status()


@app.get("/api/meta", summary="탭 구성용 고정 값")
def meta():
    return {
        "campuses": CAMPUSES,
        "categories": CATEGORIES,
        "statuses": [{"key": "ongoing", "label": "진행중"}, {"key": "closed", "label": "마감"}],
        # 캠퍼스를 고르면 그 지역 시청 분류만 보여주기 위한 표 (예: 춘천 → 춘천시)
        "city_category_by_campus": CITY_CATEGORY_BY_CAMPUS,
        "sorts": [{"key": "newest", "label": "최신순"}, {"key": "views", "label": "조회순"}, {"key": "oldest", "label": "오래된 순"}],
        "boards": [{"id": k, "name": v["name"], "source": v["source"], "category": v["category"]}
                   for k, v in BOARDS.items()],
    }


@app.get("/api/summary", summary="캠퍼스 × 상태 × 분류별 개수 (탭 배지용)")
def summary():
    conn = db.connect()
    rows = conn.execute("SELECT campuses, category, deadline FROM notice_view WHERE hidden = 0").fetchall()
    conn.close()
    today = date.today().isoformat()
    result = {c: {"ongoing": {}, "closed": {}} for c in CAMPUSES}
    for r in rows:
        status = "ongoing" if r["deadline"] and r["deadline"] >= today else "closed"
        for campus in filter(None, r["campuses"].split(",")):
            if campus in result:
                bucket = result[campus][status]
                bucket[r["category"]] = bucket.get(r["category"], 0) + 1
    return result


@app.get("/api/departments", summary="학과 목록 (학과 행사 필터용)")
def departments(campus: str | None = Query(None, description="춘천/삼척/강릉/원주")):
    conn = db.connect()
    rows = db.list_departments(conn, campus)
    counts = dict(conn.execute(
        "SELECT department_id, COUNT(*) FROM notices WHERE department_id IS NOT NULL GROUP BY department_id"
    ).fetchall())
    conn.close()
    return [{**d, "event_count": counts.get(d["id"], 0)} for d in rows]


def period_start(today: date, period: str) -> date:
    if period == '1d': return today - timedelta(days=1)
    if period == '1w': return today - timedelta(days=7)
    months = 1 if period == '1m' else 12
    year, month_index = divmod(today.year * 12 + today.month - 1 - months, 12)
    month = month_index + 1
    return date(year, month, min(today.day, monthrange(year, month)[1]))


def _profile_or_none(conn, device_id: str | None) -> dict | None:
    if not device_id:
        return None
    row = conn.execute("SELECT profile FROM user_profiles WHERE device_id=?", (device_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _match_score(conn, profile: dict, r) -> int:
    """공고 하나의 적합도 (0~100). 태그가 아직 없으면 이때 계산해서 저장"""
    tag = conn.execute("SELECT tags FROM notice_tags WHERE board_id=? AND post_id=?", (r["board_id"], r["post_id"])).fetchone()
    tags = json.loads(tag[0]) if tag else matching.tag_row(conn, r["board_id"], r["post_id"])
    if not tag:
        conn.commit()
    return matching.score(profile, tags, r["title"], r["content_text"] or "")


@app.get("/api/notices", summary="공지 목록")
def list_notices(
    campus: str | None = Query(None, description="춘천/삼척/강릉/원주"),
    category: str | None = Query(None, description="/api/meta의 categories (장학금, 채용, 삼척시, 학과행사 ...)"),
    status: Status | None = None,
    q: str | None = Query(None, description="제목/본문 검색어"),
    board: int | None = Query(None, description="출처 번호 (/api/meta의 boards)"),
    source: Source | None = Query(None, description="knu=강원대, city=시청(삼척·춘천·강릉·원주), admin=학과 행사"),
    department: int | None = Query(None, description="학과 id (/api/departments)"),
    period: Literal["1d", "1w", "1m", "1y"] | None = Query(None, description="등록일 기준 최근 기간"),
    sort: Literal["newest", "oldest", "views"] = Query("newest", description="최신순 / 오래된 순 / 조회순"),
    flag: Literal["important", "popup"] | None = Query(None, description="중요 표시·팝업 걸린 공고만 (관리자 사이트 목록용)"),
    hidden: bool = Query(False, description="true면 관리자가 삭제(숨김)한 공고만. 관리자 키 필요"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    device_id: str | None = Query(None, max_length=64, description="앱 기기 ID. 주면 공고마다 내 적합도(match_score)를 같이 줌"),
    authorization: str = Header(""),
):
    if hidden:
        current_admin(authorization=authorization, x_admin_token="")  # 숨긴 공고는 관리자만
    where, params = _where(campus, category, status, q, board, department, source, hidden)
    if period:
        today = date.today()
        where += (" AND " if where else " WHERE ") + "posted_at >= ? AND posted_at <= ?"
        params.extend([period_start(today, period).isoformat(), today.isoformat()])
    if flag:
        where += (" AND " if where else " WHERE ") + f"{flag} = 1"
    conn = db.connect()
    total = conn.execute(f"SELECT COUNT(*) FROM notice_view{where}", params).fetchone()[0]
    order = {"newest": "posted_at DESC, post_id DESC", "oldest": "posted_at ASC, post_id ASC", "views": "site_views DESC, posted_at DESC, post_id DESC"}[sort]
    # 관리자가 '중요' 표시한 글은 고른 분류(태그) 목록의 맨 위에 고정
    rows = conn.execute(
        f"SELECT * FROM notice_view{where} ORDER BY important DESC, {order} LIMIT ? OFFSET ?",
        [*params, size, (page - 1) * size],
    ).fetchall()
    depts = _departments_by_id(conn)
    profile = _profile_or_none(conn, device_id)
    items = []
    for r in rows:
        item = db.row_to_dict(r, departments=depts)
        if profile:
            item["match_score"] = _match_score(conn, profile, r)
        if r["category"] == "공모":  # 본문에 없으면 첨부파일(hwp·pdf)에서 참가 대상 학과를 찾음 (뒤에서 처리)
            item["contest_eligibility"] = attachment_analysis.eligibility(r, conn)
        items.append(item)
    conn.close()
    return {"total": total, "page": page, "size": size, "items": items}


@app.get("/api/notices/by-ids", summary="여러 공고를 한 번에 (앱의 '좋아요' 목록 등)")
def notices_by_ids(ids: str = Query(..., description="쉼표로 구분한 공고 id, 예: 721-2537,9004-495202 (최대 200개)")):
    """요청한 순서대로 돌려줌. 삭제(숨김)됐거나 없는 공고는 빠지고 missing 에 id가 담김."""
    keys = [k for k in (db.parse_id(x.strip()) for x in ids.split(",")[:200]) if k]
    conn = db.connect()
    found = {}
    for b, p in keys:
        row = conn.execute("SELECT * FROM notice_view WHERE board_id=? AND post_id=? AND hidden=0", (b, p)).fetchone()
        if row:
            found[(b, p)] = row
    depts = _departments_by_id(conn)
    conn.close()
    return {
        "items": [db.row_to_dict(found[k], departments=depts) for k in keys if k in found],
        "missing": [db.make_id(*k) for k in keys if k not in found],
    }


@app.get("/api/notices/saved", summary="브라우저에 저장한 좋아요 공고 목록")
def saved_notices(ids: str = Query('', max_length=12000)):
    requested = list(dict.fromkeys(ids.split(','))) if ids else []
    if len(requested) > 500 or any(not re.fullmatch(r'\d{1,9}-\d{1,12}', value) for value in requested):
        raise HTTPException(400, "공고 ID를 확인하세요")
    if not requested: return {"items": []}
    conn = db.connect()
    try:
        placeholders = ','.join('?' for _ in requested)
        rows = conn.execute(f"SELECT * FROM notice_view WHERE hidden=0 AND (board_id || '-' || post_id) IN ({placeholders})", requested).fetchall()
        departments = _departments_by_id(conn)
        by_id = {db.make_id(r['board_id'], r['post_id']): db.row_to_dict(r, departments=departments) for r in rows}
        return {"items": [by_id[id] for id in requested if id in by_id]}
    finally:
        conn.close()


@app.get("/api/notices/{notice_id}", summary="공지 상세")
def get_notice(notice_id: str, authorization: str = Header("")):
    """notice_id는 목록의 id 값 그대로 (예: '721-2537', 학과 행사 '0-3'). 숨긴(삭제한) 공고는 관리자만 볼 수 있음."""
    key = db.parse_id(notice_id)
    if not key:
        raise HTTPException(400, "id 형식은 '게시판번호-글번호' 입니다")
    conn = db.connect()
    row = conn.execute("SELECT * FROM notice_view WHERE board_id=? AND post_id=?", key).fetchone()
    depts = _departments_by_id(conn)
    conn.close()
    if not row:
        raise HTTPException(404, "공지를 찾을 수 없습니다")
    if row["hidden"]:
        try:
            current_admin(authorization=authorization, x_admin_token="")
        except HTTPException:
            raise HTTPException(404, "공지를 찾을 수 없습니다")
    return db.row_to_dict(row, full=True, departments=depts)


def _existing_key(conn, notice_id: str) -> tuple[int, int]:
    key = db.parse_id(notice_id)
    if not key or not db.exists(conn, *key):
        conn.close()
        raise HTTPException(404, "공지를 찾을 수 없습니다")
    return key


@app.post("/api/notices/{notice_id}/view", summary="조회수 +1 (상세 화면을 열 때)")
def add_view(notice_id: str):
    """같은 사람이 여러 번 세지 않도록 화면 쪽에서 브라우저당 한 번만 호출한다."""
    conn = db.connect()
    key = _existing_key(conn, notice_id)
    db.add_view(conn, *key)
    conn.commit()
    stats = db.get_stats(conn, *key)
    conn.close()
    return stats


@app.post("/api/notices/{notice_id}/like", summary="좋아요")
def like(notice_id: str):
    conn = db.connect()
    key = _existing_key(conn, notice_id)
    db.change_likes(conn, *key, +1)
    conn.commit()
    stats = db.get_stats(conn, *key)
    conn.close()
    return stats


@app.delete("/api/notices/{notice_id}/like", summary="좋아요 취소")
def unlike(notice_id: str):
    conn = db.connect()
    key = _existing_key(conn, notice_id)
    db.change_likes(conn, *key, -1)
    conn.commit()
    stats = db.get_stats(conn, *key)
    conn.close()
    return stats


RANKINGS = {
    "views": ("site_views", "site_views > 0"),  # 지금 많이 보는 중
    "likes": ("likes", "likes > 0"),            # 좋아요 많은 공고
}


@app.get("/api/rankings", summary="배너용 순위 (조회수·좋아요·곧 마감)")
def rankings(limit: int = Query(5, ge=1, le=20)):
    """진행 중인 공고 기준. views=조회수 순, likes=좋아요 순, deadline=마감 가까운 순."""
    today = date.today().isoformat()
    conn = db.connect()
    depts = _departments_by_id(conn)
    result = {}
    for name, (col, cond) in RANKINGS.items():
        rows = conn.execute(
            f"SELECT * FROM notice_view WHERE hidden = 0 AND deadline >= ? AND {cond} ORDER BY {col} DESC, posted_at DESC LIMIT ?",
            (today, limit),
        ).fetchall()
        result[name] = [db.row_to_dict(r, departments=depts) for r in rows]
    rows = conn.execute(
        "SELECT * FROM notice_view WHERE hidden = 0 AND deadline >= ? AND deadline_estimated = 0 ORDER BY deadline ASC, site_views DESC LIMIT ?",
        (today, limit),
    ).fetchall()
    result["deadline"] = [db.row_to_dict(r, departments=depts) for r in rows]
    result["total_views"] = conn.execute("SELECT COALESCE(SUM(site_views), 0) FROM notice_stats").fetchone()[0]
    conn.close()
    return result


@app.get("/api/popups", summary="사이트 접속 때 띄울 팝업 목록")
def popups():
    """key가 바뀌면(관리자가 팝업을 다시 걸면) '앞으로 보지 않기'를 누른 사람에게도 다시 뜬다."""
    conn = db.connect()
    rows = db.active_popups(conn)
    conn.close()
    return [{
        "id": db.make_id(r["board_id"], r["post_id"]),
        "key": f"{db.make_id(r['board_id'], r['post_id'])}@{r['flag_updated_at']}",
        "title": r["title"],
        "image": r["popup_image"],
    } for r in rows]


PROXY_HOSTS = ("kangwon.ac.kr", "samcheok.go.kr", "chuncheon.go.kr", "gn.go.kr", "wonju.go.kr")


@app.get("/api/proxy", summary="학교·시청 이미지/첨부파일 중계 (Flutter 웹 CORS 대응)")
async def proxy(url: str):
    host = urlparse(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in PROXY_HOSTS):
        raise HTTPException(400, "강원대·삼척시청 도메인만 허용됩니다")
    # 삼척시청은 Referer가 없으면 이미지 대신 오류 페이지를 줌
    parsed = urlparse(url)
    headers = {"User-Agent": USER_AGENT, "Referer": f"{parsed.scheme}://{parsed.netloc}/"}
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True) as client:
        res = await client.get(url)
    if res.status_code != 200:
        raise HTTPException(502, f"원본 요청 실패 (원본 응답 {res.status_code})")
    out = {}
    if "cache-control" in res.headers:
        out["Cache-Control"] = res.headers["cache-control"]
    disposition = _safe_disposition(res)
    if disposition:
        out["Content-Disposition"] = disposition
    return Response(res.content, media_type=res.headers.get("content-type"), headers=out)


def _safe_disposition(res: httpx.Response) -> str | None:
    """원본의 한글 파일명 헤더를 표준 형식(filename*=UTF-8'')으로 다시 만듦.

    학교·시청 서버는 파일명을 UTF-8 그대로 헤더에 넣는데, 그대로 넘기면 서버 오류가 남.
    """
    raw = next((v for k, v in res.headers.raw if k.lower() == b"content-disposition"), None)
    if not raw:
        return None
    for enc in ("utf-8", "euc-kr", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", text, re.I)
    name = unquote_plus(m[1]) if m else "download"
    kind = "inline" if text.lower().startswith("inline") else "attachment"
    return f"{kind}; filename*=UTF-8''{quote(name)}"


# ── 웹 화면 (모아봄 통합/web) ──
# API 경로(/api, /uploads, /docs)를 모두 등록한 뒤 마지막에 붙여야 API가 가려지지 않음.
# 서버 하나로 http://localhost:8000 에서 화면과 API를 같이 제공한다.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
