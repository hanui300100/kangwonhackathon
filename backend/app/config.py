import os
from pathlib import Path

BASE_URL = "https://www.kangwon.ac.kr"
SAMCHEOK_BASE = "https://www.samcheok.go.kr"
SAMCHEOK_LIST_URL = f"{SAMCHEOK_BASE}/intro/00443/02073.web"  # 삼척시청 공지사항
SAMCHEOK_GCODE = "1002"

# 공지 출처. board_id가 곧 출처 구분값이다.
#   source="knu"    강원대 게시판 (board_id = 학교 게시판 번호)
#   source="city"   캠퍼스가 있는 시청 공지 중 시민·대학생 대상 공모·행사만 (campus 탭에서만 보임)
#   source="admin"  관리자가 직접 올리는 학과 행사 (크롤링 안 함)
# category가 있으면 그 출처 글은 모두 해당 분류로 고정, None이면 키워드로 분류.
# (메뉴에 750=장학공지, 720=학사공지도 있지만 721, 718과 같은 게시판이라 제외)
SAMCHEOK_BOARD_ID = 9001
CHUNCHEON_BOARD_ID = 9002
GANGNEUNG_BOARD_ID = 9003
WONJU_BOARD_ID = 9004
ADMIN_BOARD_ID = 0
BOARDS: dict[int, dict] = {
    718: {"name": "학사공지", "category": "학사", "source": "knu"},
    721: {"name": "장학공지", "category": "장학금", "source": "knu"},
    715: {"name": "행사안내", "category": "행사", "source": "knu"},
    719: {"name": "공모안내", "category": "공모", "source": "knu"},
    504: {"name": "일반공지", "category": None, "source": "knu"},
    716: {"name": "채용안내", "category": "채용", "source": "knu"},
    SAMCHEOK_BOARD_ID: {"name": "삼척시청", "category": "삼척시", "source": "city", "campus": "삼척", "city": "samcheok"},
    CHUNCHEON_BOARD_ID: {"name": "춘천시청", "category": "춘천시", "source": "city", "campus": "춘천", "city": "chuncheon"},
    GANGNEUNG_BOARD_ID: {"name": "강릉시청", "category": "강릉시", "source": "city", "campus": "강릉", "city": "gangneung"},
    WONJU_BOARD_ID: {"name": "원주시청", "category": "원주시", "source": "city", "campus": "원주", "city": "wonju"},
    ADMIN_BOARD_ID: {"name": "학과 행사", "category": "학과행사", "source": "admin"},
}
CRAWLABLE_BOARDS = [b for b, v in BOARDS.items() if v["source"] != "admin"]
# 캠퍼스 → 그 지역 시청 분류 (화면에서 캠퍼스를 고르면 해당 시청 탭만 보임)
CITY_CATEGORY_BY_CAMPUS = {v["campus"]: v["category"] for v in BOARDS.values() if v["source"] == "city"}

# 시청 게시판 주소 (춘천: 시정소식, 강릉: 공지사항, 원주: 새소식)
CHUNCHEON_BASE = "https://www.chuncheon.go.kr"
CHUNCHEON_LIST_URL = f"{CHUNCHEON_BASE}/cityhall/administrative-info/notice-info/municipal-news/"
CHUNCHEON_BBS_ID = "BBSMSTR_000000000151"
GANGNEUNG_BASE = "https://www.gn.go.kr"
WONJU_BASE = "https://www.wonju.go.kr"
# 강릉·원주는 같은 게시판 프로그램(selectBbsNttList.do)을 씀: (주소, bbsNo, key)
EGOV_BOARDS = {
    "gangneung": (GANGNEUNG_BASE, 12, 258),
    "wonju": (WONJU_BASE, 1, 211),
}


def list_url(board_id: int) -> str:
    return f"{BASE_URL}/ko/bbs/{board_id}/list.do"


def detail_url(board_id: int) -> str:
    return f"{BASE_URL}/ko/bbs/{board_id}/detail.do"


def original_url(board_id: int, post_id: int) -> str | None:
    """공지 원문 주소. 관리자가 올린 학과 행사는 원문이 없음."""
    board = BOARDS.get(board_id, {})
    if board.get("source") == "knu":
        return f"{detail_url(board_id)}?pstSn={post_id}"
    city = board.get("city")
    if city == "samcheok":
        return f"{SAMCHEOK_LIST_URL}?gcode={SAMCHEOK_GCODE}&idx={post_id}&amode=view"
    if city == "chuncheon":
        return f"{CHUNCHEON_LIST_URL}?bbsId={CHUNCHEON_BBS_ID}&nttId={post_id}&flag=view"
    if city in EGOV_BOARDS:
        base, bbs_no, key = EGOV_BOARDS[city]
        return f"{base}/www/selectBbsNttView.do?key={key}&bbsNo={bbs_no}&nttNo={post_id}"
    return None


# 배포 시 환경변수 DATA_DIR로 영구 저장소(예: Railway Volume /data)를 지정
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DB_PATH = DATA_DIR / "notices.db"
UPLOAD_DIR = DATA_DIR / "uploads"  # 관리자가 올린 사진/파일
WEB_DIR = Path(__file__).resolve().parents[2] / "web"  # 화면 (HTML/CSS/JS)

CAMPUSES = ["춘천", "삼척", "강릉", "원주"]

# 관리자 API 비밀번호. 서버 실행 전에 환경변수로 지정:  $env:ADMIN_TOKEN="원하는값"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
MAX_UPLOAD_MB = 20

# 학교 서버 부담을 줄이기 위한 요청 간 대기 시간(초)
REQUEST_DELAY = 0.5
# 학교 서버 연결이 자주 끊겨서 요청마다 재시도
MAX_RETRIES = 4
USER_AGENT = "KNU-Notice-Bot/1.0 (student hackathon project)"

# 사이트 접속/새로고침 때 자동 갱신: 출처마다 첫 몇 페이지만, 이 시간(분) 안에는 다시 안 함
REFRESH_PAGES = 1
REFRESH_COOLDOWN_MIN = 10

# 본문에서 마감일을 찾지 못했을 때, 등록일로부터 며칠까지 '진행중'으로 볼지
FALLBACK_ACTIVE_DAYS = 30
