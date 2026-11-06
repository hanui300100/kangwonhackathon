import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .config import ADMIN_BOARD_ID, BOARDS, DB_PATH, original_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    board_id           INTEGER NOT NULL,      -- 출처 (718=학사공지, 9001=삼척시청, 0=학과 행사 ...)
    post_id            INTEGER NOT NULL,      -- 출처 안의 글 번호. 출처마다 따로 매겨짐
    title              TEXT NOT NULL,
    campuses           TEXT NOT NULL,         -- ',춘천,삼척,' 형식 (LIKE 검색용)
    author             TEXT,
    posted_at          TEXT,                  -- YYYY-MM-DD
    views              INTEGER DEFAULT 0,
    is_pinned          INTEGER DEFAULT 0,
    category           TEXT,
    deadline           TEXT,                  -- YYYY-MM-DD
    deadline_estimated INTEGER DEFAULT 1,
    content_html       TEXT,
    content_text       TEXT,
    contact            TEXT,
    images             TEXT DEFAULT '[]',
    attachments        TEXT DEFAULT '[]',
    links              TEXT DEFAULT '[]',
    crawled_at         TEXT,
    -- 아래는 관리자가 올리는 학과 행사용 (크롤링한 글은 비어 있음)
    department_id      INTEGER REFERENCES departments(id),
    event_start        TEXT,                  -- 행사 시작 (YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM)
    event_end          TEXT,
    location           TEXT,
    PRIMARY KEY (board_id, post_id)
);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_notices_deadline ON notices(deadline);

CREATE TABLE IF NOT EXISTS attachment_analysis (
    board_id INTEGER NOT NULL, post_id INTEGER NOT NULL, signature TEXT NOT NULL,
    result TEXT NOT NULL, checked_at INTEGER NOT NULL, PRIMARY KEY(board_id, post_id)
);

CREATE TABLE IF NOT EXISTS departments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,                   -- 학과명 (예: 컴퓨터공학과)
    college  TEXT,                            -- 단과대학 (예: IT대학)
    campus   TEXT NOT NULL,                   -- 춘천/삼척/강릉/원주
    UNIQUE (name, campus)
);

-- 발급된 관리자 키. 키 원문은 저장하지 않고 SHA-256 값만 저장 (발급할 때 한 번만 보여줌)
-- 최종 관리자(super)는 여기 없이 환경변수 ADMIN_TOKEN 으로 로그인
CREATE TABLE IF NOT EXISTS admin_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash      TEXT NOT NULL UNIQUE,
    role          TEXT NOT NULL CHECK (role IN ('general', 'department')),
    department_id INTEGER REFERENCES departments(id),  -- 학과 관리자만
    label         TEXT,                                -- 메모 (예: 담당자 이름)
    created_at    TEXT NOT NULL
);

-- 우리 사이트에서의 조회수·좋아요 (학교 사이트 조회수 notices.views 와 별개)
-- 크롤러가 공고를 다시 저장해도 지워지지 않도록 따로 둠
CREATE TABLE IF NOT EXISTS notice_stats (
    board_id    INTEGER NOT NULL,
    post_id     INTEGER NOT NULL,
    site_views  INTEGER NOT NULL DEFAULT 0,
    likes       INTEGER NOT NULL DEFAULT 0 CHECK (likes >= 0),
    PRIMARY KEY (board_id, post_id)
);

-- 관리자 표시·수정: 크롤러가 공고를 다시 받아도 유지되도록 공고 표와 따로 저장
--   important: 목록 최상단 고정 / popup: 접속 시 대표 이미지 팝업
--   hidden: 관리자가 '삭제'한 수집 공고 (다시 수집돼도 안 보이게 숨김, 복원 가능)
--   *_override: 관리자가 고친 제목·분류·마감일 (원래 값 대신 보임)
CREATE TABLE IF NOT EXISTS notice_flags (
    board_id           INTEGER NOT NULL,
    post_id            INTEGER NOT NULL,
    important          INTEGER NOT NULL DEFAULT 0,
    popup              INTEGER NOT NULL DEFAULT 0,
    popup_image        TEXT,               -- 팝업에 띄울 대표 이미지 주소
    updated_at         TEXT,               -- 팝업을 다시 걸면 바뀜 → '앞으로 보지 않기'를 누른 사람에게도 새로 뜸
    hidden             INTEGER NOT NULL DEFAULT 0,
    title_override     TEXT,
    category_override  TEXT,
    deadline_override  TEXT,
    PRIMARY KEY (board_id, post_id)
);

-- 적합도용 공고 태그 (수집할 때 한 번만 계산, matching.py)
CREATE TABLE IF NOT EXISTS notice_tags (
    board_id   INTEGER NOT NULL,
    post_id    INTEGER NOT NULL,
    tags       TEXT NOT NULL,          -- {topics, aud_student, aud_resident, campuses, grades, gender, age_min, age_max}
    version    INTEGER NOT NULL,
    tagged_at  TEXT,
    PRIMARY KEY (board_id, post_id)
);

-- 앱 사용자 프로필 (로그인 없음: 앱이 처음 실행될 때 만든 기기 ID로 구분)
CREATE TABLE IF NOT EXISTS user_profiles (
    device_id   TEXT PRIMARY KEY,
    profile     TEXT NOT NULL,         -- {role, user_type, campus, region, department, grade, gender, age, interests}
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 보낼 알림 대기열: 새 공고가 기준 점수 이상 맞는 사용자에게 쌓이고, 앱이 가져가면 delivered=1
CREATE TABLE IF NOT EXISTS notifications (
    device_id   TEXT NOT NULL,
    board_id    INTEGER NOT NULL,
    post_id     INTEGER NOT NULL,
    score       INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    delivered   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, board_id, post_id)
);

-- 관리자가 직접 보내는 알림 (대상: 전체 / 캠퍼스 / 학과 / 학생·주민)
CREATE TABLE IF NOT EXISTS broadcasts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',
    notice_id     TEXT,                  -- 알림을 누르면 열 공고 (선택)
    target        TEXT NOT NULL,         -- all / campus / department
    campus        TEXT,
    department_id INTEGER,
    user_type     TEXT,                  -- student / resident / NULL(모두)
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL          -- 보낸 관리자 등급
);
-- 어떤 기기가 어떤 관리자 알림을 이미 받았는지
CREATE TABLE IF NOT EXISTS broadcast_reads (
    device_id    TEXT NOT NULL,
    broadcast_id INTEGER NOT NULL,
    skipped      INTEGER NOT NULL DEFAULT 0,   -- 1: 가입 전에 보낸 알림이라 안 보내고 넘김
    PRIMARY KEY (device_id, broadcast_id)
);
"""

# 공고 + 조회수·좋아요 + 관리자 표시·수정을 한 번에 보는 뷰 (목록·상세 조회는 이걸 사용)
# 관리자가 고친 값이 있으면 그 값을, 없으면 원래 값을 보여줌
VIEW_SQL = """
CREATE VIEW notice_view AS
SELECT n.board_id, n.post_id,
       COALESCE(f.title_override, n.title)          AS title,
       n.campuses, n.author, n.posted_at, n.views, n.is_pinned,
       CASE WHEN f.category_override IS NOT NULL THEN f.category_override WHEN instr(COALESCE(f.title_override, n.title), '봉사') > 0 THEN '봉사' ELSE n.category END AS category,
       COALESCE(f.deadline_override, n.deadline)    AS deadline,
       CASE WHEN f.deadline_override IS NOT NULL THEN 0 ELSE n.deadline_estimated END AS deadline_estimated,
       n.content_html, n.content_text, n.contact, n.images, n.attachments, n.links, n.crawled_at,
       n.department_id, n.event_start, n.event_end, n.location,
       COALESCE(s.site_views, 0) AS site_views,
       COALESCE(s.likes, 0)      AS likes,
       COALESCE(f.important, 0)  AS important,
       COALESCE(f.popup, 0)      AS popup,
       f.popup_image             AS popup_image,
       f.updated_at              AS flag_updated_at,
       COALESCE(f.hidden, 0)     AS hidden,
       (f.title_override IS NOT NULL OR f.category_override IS NOT NULL OR f.deadline_override IS NOT NULL) AS edited
FROM notices n
LEFT JOIN notice_stats s ON s.board_id = n.board_id AND s.post_id = n.post_id
LEFT JOIN notice_flags f ON f.board_id = n.board_id AND f.post_id = n.post_id
"""

# 예전 DB에 없던 칼럼은 지우지 않고 추가만 함 (수집한 데이터 유지)
_ADDED_COLUMNS = {
    "department_id": "INTEGER", "event_start": "TEXT", "event_end": "TEXT", "location": "TEXT",
}
_ADDED_FLAG_COLUMNS = {
    "hidden": "INTEGER NOT NULL DEFAULT 0", "title_override": "TEXT",
    "category_override": "TEXT", "deadline_override": "TEXT",
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notices)")]
    if cols and "board_id" not in cols:
        conn.execute("DROP TABLE notices")  # 게시판 1개 시절의 옛 구조 → 새로 만듦 (재크롤링 필요)
        cols = []
    for name, typ in _ADDED_COLUMNS.items():
        if cols and name not in cols:
            conn.execute(f"ALTER TABLE notices ADD COLUMN {name} {typ}")
    flag_cols = [r[1] for r in conn.execute("PRAGMA table_info(notice_flags)")]
    for name, typ in _ADDED_FLAG_COLUMNS.items():
        if flag_cols and name not in flag_cols:
            conn.execute(f"ALTER TABLE notice_flags ADD COLUMN {name} {typ}")
    conn.executescript(SCHEMA)
    # 알림 이유: match(적합도) / department(내 학과 소식)
    if "reason" not in [r[1] for r in conn.execute("PRAGMA table_info(notifications)")]:
        conn.execute("ALTER TABLE notifications ADD COLUMN reason TEXT NOT NULL DEFAULT 'match'")
    dept_cols = [r[1] for r in conn.execute("PRAGMA table_info(departments)")]
    if "field" not in dept_cols:
        conn.execute("ALTER TABLE departments ADD COLUMN field TEXT NOT NULL DEFAULT '기타·미분류'")
    # 뷰는 구조가 바뀌었을 때만 다시 만듦 (예전 뷰에는 hidden 칸이 없음)
    view = conn.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='notice_view'").fetchone()
    if not view or "edited" not in view[0] or "instr(COALESCE(f.title_override, n.title), '봉사')" not in view[0]:
        conn.executescript(f"DROP VIEW IF EXISTS notice_view; {VIEW_SQL};")
    return conn


def seed_departments(conn):
    """Add the catalog once, preserving existing department IDs and administrator changes."""
    conn.execute("CREATE TABLE IF NOT EXISTS catalog_migrations (name TEXT PRIMARY KEY)")
    if conn.execute("SELECT 1 FROM catalog_migrations WHERE name=?", ('departments-2026-v1',)).fetchone():
        return
    entries = json.loads((Path(__file__).with_name('departments.json')).read_text(encoding='utf-8'))
    for entry in entries:
        conn.execute("INSERT OR IGNORE INTO departments(name,college,campus,field) VALUES (?,?,?,?)", (entry['name'],entry['college'],entry['campus'],entry['field']))
        conn.execute("UPDATE departments SET field=? WHERE name=? AND campus=? AND field='기타·미분류'", (entry['field'],entry['name'],entry['campus']))
    conn.execute("INSERT INTO catalog_migrations(name) VALUES (?)", ('departments-2026-v1',))
    conn.commit()


def make_id(board_id: int, post_id: int) -> str:
    """API에서 쓰는 공지 ID. 예: '721-2537', 학과 행사는 '0-12'"""
    return f"{board_id}-{post_id}"


def parse_id(notice_id: str) -> tuple[int, int] | None:
    try:
        b, p = notice_id.split("-")
        return int(b), int(p)
    except ValueError:
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _campus_str(campuses: list[str]) -> str:
    return "," + ",".join(campuses) + ","


# ── 크롤링한 공지 ──

def exists(conn, board_id: int, post_id: int) -> bool:
    q = "SELECT 1 FROM notices WHERE board_id=? AND post_id=?"
    return conn.execute(q, (board_id, post_id)).fetchone() is not None


def update_list_fields(conn, board_id: int, item) -> None:
    conn.execute(
        "UPDATE notices SET views=?, is_pinned=?, title=? WHERE board_id=? AND post_id=?",
        (item.views, int(item.is_pinned), item.title, board_id, item.post_id),
    )


def upsert(conn, board_id: int, item, detail, category: str, deadline) -> None:
    conn.execute(
        """
        INSERT INTO notices (board_id, post_id, title, campuses, author, posted_at, views, is_pinned,
            category, deadline, deadline_estimated, content_html, content_text, contact,
            images, attachments, links, crawled_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(board_id, post_id) DO UPDATE SET
            title=excluded.title, campuses=excluded.campuses, author=excluded.author,
            posted_at=excluded.posted_at, views=excluded.views, is_pinned=excluded.is_pinned,
            category=excluded.category, deadline=excluded.deadline,
            deadline_estimated=excluded.deadline_estimated, content_html=excluded.content_html,
            content_text=excluded.content_text, contact=excluded.contact, images=excluded.images,
            attachments=excluded.attachments, links=excluded.links, crawled_at=excluded.crawled_at
        """,
        (
            board_id, item.post_id, item.title, _campus_str(item.campuses), item.author,
            _iso(item.posted_at), item.views, int(item.is_pinned), category,
            _iso(deadline.deadline), int(deadline.estimated),
            detail.content_html, detail.content_text, detail.contact,
            json.dumps(detail.images, ensure_ascii=False),
            json.dumps(detail.attachments, ensure_ascii=False),
            json.dumps(detail.links, ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


# ── 학과 ──

def list_departments(conn, campus: str | None = None) -> list[dict]:
    q, params = "SELECT * FROM departments", []
    if campus:
        q += " WHERE campus=?"
        params.append(campus)
    return [dict(r) for r in conn.execute(q + " ORDER BY campus, college, name", params)]


def get_department(conn, dept_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM departments WHERE id=?", (dept_id,)).fetchone()
    return dict(row) if row else None


def save_department(conn, name: str, college: str | None, campus: str, dept_id: int | None = None) -> int:
    if dept_id is None:
        cur = conn.execute("INSERT INTO departments (name, college, campus) VALUES (?,?,?)", (name, college, campus))
        return cur.lastrowid
    conn.execute("UPDATE departments SET name=?, college=?, campus=? WHERE id=?", (name, college, campus, dept_id))
    # 학과 캠퍼스가 바뀌면 그 학과 행사의 캠퍼스도 맞춤
    conn.execute("UPDATE notices SET campuses=? WHERE department_id=?", (_campus_str([campus]), dept_id))
    return dept_id


def department_in_use(conn, dept_id: int) -> bool:
    """행사나 학과 관리자 키가 남아 있으면 True (학과 삭제 불가)"""
    q = """SELECT 1 FROM notices WHERE department_id=?
           UNION SELECT 1 FROM admin_keys WHERE department_id=? LIMIT 1"""
    return conn.execute(q, (dept_id, dept_id)).fetchone() is not None


# ── 관리자 키 ──

def create_admin_key(conn, key_hash: str, role: str, department_id: int | None, label: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO admin_keys (key_hash, role, department_id, label, created_at) VALUES (?,?,?,?,?)",
        (key_hash, role, department_id, label, datetime.now().isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def find_admin_key(conn, key_hash: str) -> dict | None:
    row = conn.execute("SELECT * FROM admin_keys WHERE key_hash=?", (key_hash,)).fetchone()
    return dict(row) if row else None


def list_admin_keys(conn) -> list[dict]:
    rows = conn.execute("SELECT id, role, department_id, label, created_at FROM admin_keys ORDER BY id DESC")
    return [dict(r) for r in rows]


def get_admin_key(conn, key_id: int) -> dict | None:
    row = conn.execute("SELECT id, role, department_id, label, created_at FROM admin_keys WHERE id=?",
                       (key_id,)).fetchone()
    return dict(row) if row else None


def delete_admin_key(conn, key_id: int) -> None:
    conn.execute("DELETE FROM admin_keys WHERE id=?", (key_id,))


def delete_department(conn, dept_id: int) -> None:
    conn.execute("DELETE FROM departments WHERE id=?", (dept_id,))


# ── 학과 행사 (관리자 등록) ──

def save_event(conn, ev: dict, dept: dict, post_id: int | None = None) -> int:
    """ev: 관리자가 보낸 값(dict). post_id가 없으면 새로 만듦. 반환: post_id"""
    if post_id is None:
        row = conn.execute("SELECT COALESCE(MAX(post_id), 0) + 1 FROM notices WHERE board_id=?",
                           (ADMIN_BOARD_ID,)).fetchone()
        post_id = row[0]
        posted_at = date.today().isoformat()
    else:
        posted_at = conn.execute("SELECT posted_at FROM notices WHERE board_id=? AND post_id=?",
                                 (ADMIN_BOARD_ID, post_id)).fetchone()[0]
    # 마감일을 안 적으면 행사 종료일(없으면 시작일)을 마감일로 사용
    deadline = ev.get("deadline") or (ev.get("event_end") or ev.get("event_start") or "")[:10] or None
    conn.execute(
        """
        INSERT OR REPLACE INTO notices (board_id, post_id, title, campuses, author, posted_at, views,
            is_pinned, category, deadline, deadline_estimated, content_html, content_text, contact,
            images, attachments, links, crawled_at, department_id, event_start, event_end, location)
        VALUES (?,?,?,?,?,?,0,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ADMIN_BOARD_ID, post_id, ev["title"], _campus_str([dept["campus"]]), dept["name"], posted_at,
            int(ev.get("is_pinned", False)), BOARDS[ADMIN_BOARD_ID]["category"], deadline,
            ev.get("content_html") or "", ev.get("content_text") or "", ev.get("contact") or "",
            json.dumps(ev.get("images") or [], ensure_ascii=False),
            json.dumps(ev.get("attachments") or [], ensure_ascii=False),
            json.dumps(ev.get("links") or [], ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
            dept["id"], ev.get("event_start"), ev.get("event_end"), ev.get("location"),
        ),
    )
    return post_id


def event_department(conn, post_id: int) -> int | None:
    row = conn.execute("SELECT department_id FROM notices WHERE board_id=? AND post_id=?",
                       (ADMIN_BOARD_ID, post_id)).fetchone()
    return row[0] if row else None


def delete_event(conn, post_id: int) -> bool:
    cur = conn.execute("DELETE FROM notices WHERE board_id=? AND post_id=?", (ADMIN_BOARD_ID, post_id))
    for table in ("notice_stats", "notice_flags"):  # 지운 행사의 조회수·표시도 정리
        conn.execute(f"DELETE FROM {table} WHERE board_id=? AND post_id=?", (ADMIN_BOARD_ID, post_id))
    return cur.rowcount > 0


# ── 조회수·좋아요 ──

def add_view(conn, board_id: int, post_id: int) -> None:
    conn.execute(
        """INSERT INTO notice_stats (board_id, post_id, site_views) VALUES (?,?,1)
           ON CONFLICT(board_id, post_id) DO UPDATE SET site_views = site_views + 1""",
        (board_id, post_id),
    )


def change_likes(conn, board_id: int, post_id: int, delta: int) -> None:
    """delta=+1 좋아요, -1 취소. 0 밑으로는 안 내려감"""
    conn.execute(
        """INSERT INTO notice_stats (board_id, post_id, likes) VALUES (?,?,MAX(?,0))
           ON CONFLICT(board_id, post_id) DO UPDATE SET likes = MAX(likes + ?, 0)""",
        (board_id, post_id, delta, delta),
    )


def get_stats(conn, board_id: int, post_id: int) -> dict:
    row = conn.execute("SELECT site_views, likes FROM notice_stats WHERE board_id=? AND post_id=?",
                       (board_id, post_id)).fetchone()
    return {"site_views": row[0], "likes": row[1]} if row else {"site_views": 0, "likes": 0}


# ── 중요 표시·팝업 ──

def set_flags(conn, board_id: int, post_id: int, important: bool | None = None,
              popup: bool | None = None, popup_image: str | None = None) -> None:
    """None인 값은 그대로 둠. 팝업을 켜면 updated_at이 바뀌어 '앞으로 보지 않기'한 사람에게도 새로 뜸"""
    conn.execute("INSERT OR IGNORE INTO notice_flags (board_id, post_id) VALUES (?,?)", (board_id, post_id))
    if important is not None:
        conn.execute("UPDATE notice_flags SET important=? WHERE board_id=? AND post_id=?",
                     (int(important), board_id, post_id))
    if popup is not None:
        conn.execute(
            "UPDATE notice_flags SET popup=?, popup_image=?, updated_at=? WHERE board_id=? AND post_id=?",
            (int(popup), popup_image if popup else None, datetime.now().isoformat(timespec="seconds"),
             board_id, post_id),
        )


def active_popups(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM notice_view WHERE popup=1 AND hidden=0 AND popup_image IS NOT NULL ORDER BY flag_updated_at DESC"
    ).fetchall()


# ── 관리자 수정·숨김 (수집한 공고) ──

def set_hidden(conn, board_id: int, post_id: int, hidden: bool) -> None:
    """수집 공고 '삭제' = 숨김. 다시 수집돼도 계속 숨겨지고, 복원하면 다시 보임. 숨기면 팝업도 내림"""
    conn.execute("INSERT OR IGNORE INTO notice_flags (board_id, post_id) VALUES (?,?)", (board_id, post_id))
    conn.execute("UPDATE notice_flags SET hidden=? WHERE board_id=? AND post_id=?", (int(hidden), board_id, post_id))
    if hidden:
        conn.execute("UPDATE notice_flags SET popup=0, important=0 WHERE board_id=? AND post_id=?", (board_id, post_id))


def set_overrides(conn, board_id: int, post_id: int, fields: dict) -> None:
    """fields: {'title': ..., 'category': ..., 'deadline': ...}. 값이 None이면 원래 값으로 되돌림"""
    conn.execute("INSERT OR IGNORE INTO notice_flags (board_id, post_id) VALUES (?,?)", (board_id, post_id))
    for name in ("title", "category", "deadline"):
        if name in fields:
            conn.execute(f"UPDATE notice_flags SET {name}_override=? WHERE board_id=? AND post_id=?",
                         (fields[name] or None, board_id, post_id))


# ── 응답 변환 ──

def contest_eligibility(content: str | None) -> dict:
    """Extract only explicitly stated, unambiguous department eligibility from the detail body."""
    unknown = {"kind": "unknown", "departments": []}
    if not content:
        return unknown
    found = []
    for line in content.splitlines():
        m = re.search(r'(?:참가|참여|신청|지원|공모)\s*(?:대상|자격)\s*[:：]\s*(.+)', line)
        if m:
            found.append(m.group(1).strip())
    if len(found) != 1:
        return unknown
    target = found[0].strip().rstrip('.。')
    if re.search(r'제외|불가|특정|관련|해당|별도|첨부|붙임|참조|전국|제한', target):
        return unknown
    if re.fullmatch(r'(?:모든|전체|전)\s*학과(?:\s*(?:재학생|학생))?', target):
        return {"kind": "all", "departments": []}
    # Do not claim a complete list if other qualifiers or exclusions are present.
    name = r'[가-힣A-Za-z0-9·ㆍ\s]{2,45}?(?:학과|학부|전공)'
    if not re.fullmatch(rf'{name}(?:\s*[,，、/·]\s*{name})*(?:\s*재학생)?', target):
        return unknown
    parts = re.split(r'\s*[,，、/]\s*', re.sub(r'\s*재학생$', '', target))
    if any(not re.fullmatch(name, part.strip()) for part in parts):
        return unknown
    return {"kind": "specific", "departments": [part.strip() for part in parts]}


def row_to_dict(row: sqlite3.Row, full: bool = False, today: date | None = None,
                departments: dict[int, dict] | None = None) -> dict:
    today = today or date.today()
    deadline = row["deadline"]
    board_id = row["board_id"]
    dept = (departments or {}).get(row["department_id"]) if row["department_id"] else None
    data = {
        "id": make_id(board_id, row["post_id"]),
        "board_id": board_id,
        "board_name": BOARDS.get(board_id, {}).get("name", str(board_id)),
        "source": BOARDS.get(board_id, {}).get("source", "knu"),  # knu / city / admin
        "title": row["title"],
        "campuses": [c for c in row["campuses"].split(",") if c],
        "author": row["author"],
        "posted_at": row["posted_at"],
        "views": row["views"],
        "is_pinned": bool(row["is_pinned"]),
        "category": row["category"],
        "contest_eligibility": contest_eligibility(row["content_text"]) if row["category"] == "공모" else None,
        "deadline": deadline,
        "deadline_estimated": bool(row["deadline_estimated"]),
        "status": "ongoing" if deadline and deadline >= today.isoformat() else "closed",
        "has_attachments": row["attachments"] not in (None, "", "[]"),
        "url": original_url(board_id, row["post_id"]),  # 학과 행사는 null
        "department": dept,  # {id, name, college, campus} 또는 null
        "event_start": row["event_start"],
        "event_end": row["event_end"],
        "location": row["location"],
        # 앱 목록 카드용: 첫 번째 사진, 본문 앞부분 요약
        "thumbnail": (json.loads(row["images"] or "[]") or [None])[0],
        "summary": " ".join((row["content_text"] or "").split())[:120],
    }
    keys = row.keys()
    if "site_views" in keys:  # notice_view 로 읽은 경우
        data.update(
            site_views=row["site_views"],  # 우리 사이트 조회수
            likes=row["likes"],
            important=bool(row["important"]),  # 중요 표시 (목록 최상단 고정)
            popup=bool(row["popup"]),
            popup_image=row["popup_image"],
        )
    if "hidden" in keys:
        data.update(hidden=bool(row["hidden"]), edited=bool(row["edited"]))  # 숨김(삭제)·관리자 수정 여부
    if full:
        data.update(
            content_html=row["content_html"],
            content_text=row["content_text"],
            contact=row["contact"],
            images=json.loads(row["images"] or "[]"),
            attachments=json.loads(row["attachments"] or "[]"),
            links=json.loads(row["links"] or "[]"),
            crawled_at=row["crawled_at"],
        )
    return data
