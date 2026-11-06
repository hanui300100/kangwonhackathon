"""공고 ↔ 사용자 적합도 판단 (서버 자체 모델, 외부 AI 사용 안 함).

서버 부담을 줄이는 구조
  1) 공고를 수집할 때 '한 번만' 분석해서 작은 태그 묶음으로 저장 (notice_tags)
       - 대상: 학생 / 주민 (0~1 점수)
       - 지역: 공고의 캠퍼스(춘천·삼척·강릉·원주)
       - 관심 주제 10개 (0~1 점수)
       - 조건: 학년·성별·나이 (공고에 적혀 있을 때만)
  2) 사용자별 적합도는 이 태그와 프로필을 비교하는 계산만 함 (본문을 다시 읽지 않음)

'AI' 부분
  - 키워드 규칙 점수 + 나이브 베이즈 분류기(글자 2-gram)를 섞어 주제 점수를 냄
  - 분류기는 수집된 공고로 스스로 학습 (규칙으로 확실한 글을 정답 삼아 학습 → 키워드가 없는 글도 비슷한 표현으로 판단)
  - 학습·판단 모두 서버 안에서 몇 초 안에 끝남. 모델은 data/topic_model.json 에 저장
"""
import json
import math
import re
from collections import Counter
from datetime import datetime

from .config import BOARDS, CAMPUSES, DATA_DIR

MODEL_PATH = DATA_DIR / "topic_model.json"
TAG_VERSION = 1          # 태깅 규칙을 바꾸면 올려서 전체 다시 태깅
MATCH_THRESHOLD = 60     # 이 점수 이상이면 '나에게 맞는 공고' + 알림

# ── 관심 주제 (사용자가 고르는 것과 같은 목록, 최소한으로 유지) ──
TOPICS: dict[str, list[str]] = {
    "장학금": ["장학", "학자금", "등록금 지원", "생활비 지원", "근로장학", "국가장학", "장학생"],
    "행사·축제": ["행사", "축제", "페스티벌", "공연", "전시", "박람회", "콘서트", "체험", "개최"],
    "공모전": ["공모", "경진대회", "콘테스트", "아이디어", "수기", "UCC", "해커톤", "공모전"],
    "교육·강좌": ["교육", "강좌", "특강", "세미나", "워크숍", "아카데미", "캠프", "강의", "교실", "클래스", "양성과정"],
    "취업·채용": ["채용", "취업", "인턴", "일자리", "면접", "구직", "직무", "현장실습", "박람회 참여기업"],
    "학사": ["수강", "학점", "계절수업", "계절학기", "졸업", "휴학", "복학", "전과", "부전공", "복수전공", "성적", "학위"],
    "봉사·대외활동": ["봉사", "서포터즈", "기자단", "대외활동", "멘토링", "동아리", "홍보단"],
    "창업": ["창업", "스타트업", "사업화", "액셀러", "예비창업", "창업자"],
    "복지·지원": ["지원금", "복지", "바우처", "상품권", "캐시백", "긴급", "돌봄", "건강", "지원사업", "생활안정"],
    "문화·체육": ["스포츠", "체육", "걷기", "문화", "영화", "도서", "독서", "예술", "공연", "숲"],
}
TOPIC_NAMES = list(TOPICS)

STUDENT_WORDS = ["학생", "재학생", "대학생", "학부", "대학원", "수강", "학점", "신입생", "졸업", "학과", "휴학생"]
RESIDENT_WORDS = ["시민", "주민", "도민", "가족", "부모", "어르신", "청소년", "아동", "군민", "누구나", "일반인"]
REGIONS = CAMPUSES  # 주민 지역도 캠퍼스가 있는 4개 시로 한정 (그 외는 '기타')


# ───────────────────────── 나이브 베이즈 (글자 2-gram) ─────────────────────────

def _features(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return [text[i:i + 2] for i in range(len(text) - 1) if not text[i:i + 2].isspace()]


def _doc_features(title: str, body: str) -> Counter:
    c = Counter(_features(title) * 2)  # 제목은 두 배 가중
    c.update(_features((body or "")[:600]))
    return c


class TopicModel:
    """주제별 '해당/비해당' 2-class 다항 나이브 베이즈 (one-vs-rest)."""

    def __init__(self, data: dict | None = None):
        self.data = data or {}

    def trained(self) -> bool:
        return bool(self.data.get("topics"))

    @classmethod
    def train(cls, docs: list[tuple[str, str]]) -> "TopicModel":
        """docs: [(제목, 본문)]. 규칙 점수가 확실한 글을 정답(약한 지도학습)으로 사용."""
        feats = [_doc_features(t, b) for t, b in docs]
        rules = [rule_topic_scores(t, b) for t, b in docs]
        topics = {}
        for name in TOPIC_NAMES:
            pos, neg = Counter(), Counter()
            n_pos = n_neg = 0
            for f, r in zip(feats, rules):
                if r[name] >= 0.75:
                    pos.update(f); n_pos += 1
                elif r[name] == 0:
                    neg.update(f); n_neg += 1
            if n_pos < 2 or n_neg < 2:
                continue  # 학습 자료가 너무 적은 주제는 규칙만 사용
            vocab = set(pos) | set(neg)
            # 자주 나오는 특징만 저장해 모델을 작게 유지
            keep = {k for k, _ in (pos + neg).most_common(4000)}
            tp, tn = sum(pos.values()), sum(neg.values())
            v = len(vocab)
            topics[name] = {
                "prior": math.log(n_pos / (n_pos + n_neg)) - math.log(n_neg / (n_pos + n_neg)),
                "w": {k: round(math.log((pos[k] + 1) / (tp + v)) - math.log((neg[k] + 1) / (tn + v)), 4) for k in keep},
                "default": math.log(1 / (tp + v)) - math.log(1 / (tn + v)),
            }
        return cls({"topics": topics, "trained_at": datetime.now().isoformat(timespec="seconds"), "docs": len(docs)})

    def predict(self, title: str, body: str) -> dict[str, float]:
        f = _doc_features(title, body)
        out = {}
        for name, m in self.data.get("topics", {}).items():
            score = m["prior"] + sum(m["w"].get(k, 0.0) * n for k, n in f.items() if k in m["w"])
            score /= max(1.0, math.sqrt(sum(f.values())) / 3)  # 긴 글이 한쪽으로 쏠리지 않게 완화
            out[name] = 1 / (1 + math.exp(-max(-30, min(30, score))))
        return out

    def save(self):
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        MODEL_PATH.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "TopicModel":
        try:
            return cls(json.loads(MODEL_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()


_model: TopicModel | None = None


def model() -> TopicModel:
    global _model
    if _model is None:
        _model = TopicModel.load()
    return _model


# ───────────────────────── 공고 태깅 ─────────────────────────

def rule_topic_scores(title: str, body: str) -> dict[str, float]:
    body = (body or "")[:1500]
    out = {}
    for name, words in TOPICS.items():
        hits = sum(2 for w in words if w in title) + sum(1 for w in words if w in body)
        out[name] = min(1.0, hits / 4)
    return out


def _grades(text: str) -> list[int]:
    g = {int(x) for x in re.findall(r"([1-4])\s*학년", text)}
    if "신입생" in text:
        g.add(1)
    if "졸업예정" in text or "졸업 예정" in text:
        g.add(4)
    return sorted(g)


def _gender(text: str) -> str | None:
    if re.search(r"여성|여학생|여대생", text) and not re.search(r"남성|남학생", text):
        return "F"
    if re.search(r"남성|남학생", text) and not re.search(r"여성|여학생", text):
        return "M"
    return None


def _age_range(text: str) -> tuple[int | None, int | None]:
    m = re.search(r"만\s*(\d{1,2})\s*세\s*(?:이상)?\s*[~\-～]\s*(?:만\s*)?(\d{1,2})\s*세", text)
    if m:
        return int(m[1]), int(m[2])
    for words, rng in ((("청년",), (19, 39)), (("청소년", "중학생", "고등학생"), (13, 18)),
                       (("아동", "초등"), (7, 12)), (("중장년",), (40, 64)), (("어르신", "노인", "65세"), (65, 120))):
        if any(w in text for w in words):
            return rng
    return None, None


def tag_notice(board_id: int, title: str, body: str, campuses: list[str]) -> dict:
    """공고 하나를 분석해 태그 묶음을 만든다 (수집할 때 한 번만 호출)."""
    text = f"{title}\n{(body or '')[:1500]}"
    rules = rule_topic_scores(title, body)
    nb = model().predict(title, body) if model().trained() else {}
    topics = {}
    for name in TOPIC_NAMES:
        r, p = rules[name], nb.get(name)
        score = r if p is None else 0.65 * r + 0.35 * p  # 규칙 우선, 분류기로 보정
        if score >= 0.2:
            topics[name] = round(score, 2)

    source = BOARDS.get(board_id, {}).get("source")
    s_hits = sum(1 for w in STUDENT_WORDS if w in text)
    r_hits = sum(1 for w in RESIDENT_WORDS if w in text)
    # 출처별 기본값: 학교 공고는 학생 위주, 시청 공고는 주민 위주(대학생도 주민이라 절반)
    base_s, base_r = {"knu": (0.8, 0.2), "city": (0.5, 0.8), "admin": (0.9, 0.1)}.get(source, (0.5, 0.5))
    aud_student = min(1.0, base_s + 0.1 * s_hits)
    aud_resident = min(1.0, base_r + 0.1 * r_hits)
    age_min, age_max = _age_range(text)
    return {
        "topics": topics,
        "aud_student": round(aud_student, 2),
        "aud_resident": round(aud_resident, 2),
        "campuses": campuses,
        "grades": _grades(text),
        "gender": _gender(text),
        "age_min": age_min,
        "age_max": age_max,
    }


# ───────────────────────── 적합도 계산 ─────────────────────────

def score(profile: dict, tags: dict, title: str = "", text: str = "") -> int:
    """0~100점. profile: {user_type: student|resident, campus, region, department, grade, gender, age, interests[]}"""
    student = profile.get("user_type") == "student"
    area = profile.get("campus") if student else profile.get("region")
    campuses = tags.get("campuses") or CAMPUSES
    # 1) 지역: 다른 지역만 대상인 공고는 크게 감점
    region_fit = 1.0 if (len(campuses) >= 4 or not area or area in campuses) else 0.2
    # 2) 대상(학생/주민)
    aud = tags.get("aud_student" if student else "aud_resident", 0.5)
    # 3) 관심 주제: 고른 주제 중 가장 잘 맞는 점수
    interests = profile.get("interests") or []
    topic_scores = tags.get("topics", {})
    interest = max((topic_scores.get(t, 0.0) for t in interests), default=0.0) if interests else 0.5

    value = 100 * region_fit * (0.15 + 0.25 * aud + 0.6 * interest)
    # 4) 조건 (공고에 적혀 있을 때만 반영)
    grades = tags.get("grades") or []
    if student and grades and profile.get("grade"):
        value = value + 8 if int(profile["grade"]) in grades else value * 0.4
    if tags.get("gender") and profile.get("gender") in ("M", "F"):
        value = value + 5 if tags["gender"] == profile["gender"] else value * 0.2
    age = profile.get("age")
    if age and (tags.get("age_min") or tags.get("age_max")):
        lo, hi = tags.get("age_min") or 0, tags.get("age_max") or 200
        value = value + 5 if lo <= int(age) <= hi else value * 0.4
    dept = (profile.get("department") or "").strip()
    if student and len(dept) >= 2:
        core = dept.removesuffix("학과").removesuffix("학부").removesuffix("전공") or dept
        if core in title or core in (text or "")[:1500]:
            value += 20  # 내 학과 이름이 나오는 공고
    return max(0, min(100, round(value)))


# ───────────────────────── DB 연동 ─────────────────────────

def save_tags(conn, board_id: int, post_id: int, tags: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO notice_tags (board_id, post_id, tags, version, tagged_at) VALUES (?,?,?,?,?)",
        (board_id, post_id, json.dumps(tags, ensure_ascii=False), TAG_VERSION, datetime.now().isoformat(timespec="seconds")),
    )


def tag_row(conn, board_id: int, post_id: int) -> dict | None:
    row = conn.execute("SELECT title, content_text, campuses FROM notice_view WHERE board_id=? AND post_id=?",
                       (board_id, post_id)).fetchone()
    if not row:
        return None
    tags = tag_notice(board_id, row["title"], row["content_text"] or "", [c for c in row["campuses"].split(",") if c])
    save_tags(conn, board_id, post_id, tags)
    return tags


def retrain(conn) -> TopicModel:
    """수집된 공고 전체로 분류기를 다시 학습 (공고 수백 건 기준 1초 안팎)."""
    global _model
    docs = [(r[0], r[1] or "") for r in conn.execute("SELECT title, content_text FROM notices")]
    _model = TopicModel.train(docs)
    _model.save()
    return _model


def tag_missing(conn, limit: int = 2000) -> int:
    """태그가 없거나 옛 버전인 공고를 태깅. 반환: 태깅한 개수"""
    rows = conn.execute(
        """SELECT n.board_id, n.post_id FROM notices n
           LEFT JOIN notice_tags t ON t.board_id=n.board_id AND t.post_id=n.post_id
           WHERE t.tags IS NULL OR t.version < ? LIMIT ?""", (TAG_VERSION, limit)).fetchall()
    for r in rows:
        tag_row(conn, r[0], r[1])
    return len(rows)


def norm_dept(name: str | None) -> str:
    """학과 이름 비교용: 띄어쓰기·대소문자 무시 (예: 'AI소프트웨어 학과' = 'Ai소프트웨어학과')"""
    return "".join((name or "").split()).lower()


def is_my_department(profile: dict, dept: dict | None) -> bool:
    """사용자가 입력한 학과·캠퍼스가 행사를 올린 학과와 같은지"""
    return bool(dept and profile.get("user_type") == "student" and profile.get("campus") == dept["campus"]
                and norm_dept(profile.get("department")) and norm_dept(profile.get("department")) == norm_dept(dept["name"]))


def queue_notifications(conn, keys: list[tuple[int, int]]) -> int:
    """새 공고(keys)마다 모든 사용자 프로필과 비교해 기준 점수 이상이면 알림 대기열에 넣음."""
    if not keys:
        return 0
    profiles = [(r["device_id"], json.loads(r["profile"])) for r in conn.execute("SELECT device_id, profile FROM user_profiles")]
    if not profiles:
        return 0
    queued = 0
    now = datetime.now().isoformat(timespec="seconds")
    for b, p in keys:
        tags = tag_row(conn, b, p)
        row = conn.execute("SELECT title, content_text, hidden, department_id FROM notice_view WHERE board_id=? AND post_id=?", (b, p)).fetchone()
        if not tags or not row or row["hidden"]:
            continue
        dept = None
        if row["department_id"]:  # 학과 행사: 그 학과 학생에게는 적합도와 상관없이 알림
            d = conn.execute("SELECT name, campus FROM departments WHERE id=?", (row["department_id"],)).fetchone()
            dept = dict(d) if d else None
        for device_id, prof in profiles:
            s = score(prof, tags, row["title"], row["content_text"] or "")
            mine = is_my_department(prof, dept)
            if mine or s >= MATCH_THRESHOLD:
                conn.execute("INSERT OR IGNORE INTO notifications (device_id, board_id, post_id, score, created_at, reason)"
                             " VALUES (?,?,?,?,?,?)", (device_id, b, p, s, now, "department" if mine else "match"))
                queued += 1
    return queued
