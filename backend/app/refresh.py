"""사이트 접속·새로고침 때 백그라운드로 새 공고를 가져오는 기능.

- 요청은 바로 응답하고, 크롤링은 별도 스레드에서 돈다 (화면이 기다리지 않음)
- 한 번에 하나만 실행 (여러 사람이 동시에 눌러도 학교 서버에 한 번만 요청)
- 마지막 크롤링 후 REFRESH_COOLDOWN_MIN 분 안에는 다시 하지 않음
- 각 출처의 첫 페이지만 보고 새 글만 상세를 받음 → 보통 수십 초 안에 끝남
"""
import logging
import threading
from datetime import datetime, timedelta

from .config import REFRESH_COOLDOWN_MIN, REFRESH_PAGES
from .crawler import crawl

log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {
    "running": False,
    "started_at": None,   # datetime
    "finished_at": None,  # datetime
    "new_count": 0,       # 마지막 갱신에서 새로 들어온 공고 수
    "error": None,
}


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def status() -> dict:
    with _lock:
        s = dict(_state)
    next_allowed = s["finished_at"] + timedelta(minutes=REFRESH_COOLDOWN_MIN) if s["finished_at"] else None
    return {
        "running": s["running"],
        "started_at": _iso(s["started_at"]),
        "last_refreshed_at": _iso(s["finished_at"]),
        "new_count": s["new_count"],
        "error": s["error"],
        "next_refresh_after": _iso(next_allowed),
    }


def _run():
    new_count, error = 0, None
    try:
        result = crawl(max_pages=REFRESH_PAGES, refresh_existing=False)
        new_count = sum(s.get("new", 0) for s in result.values())
        log.info("자동 갱신 완료: 새 공고 %s건 %s", new_count, result)
    except Exception as e:  # 실패해도 다음 요청 때 다시 시도할 수 있게 상태만 기록
        log.exception("자동 갱신 실패")
        error = str(e)
    with _lock:
        _state.update(running=False, finished_at=datetime.now(), new_count=new_count, error=error)


def request_refresh(force: bool = False) -> dict:
    """갱신 요청. 이미 실행 중이거나 최근에 했으면 새로 시작하지 않음.

    반환 status에 started(이번 요청으로 시작했는지)와 reason을 붙여 돌려준다.
    """
    with _lock:
        if _state["running"]:
            started, reason = False, "running"
        elif (not force and _state["finished_at"] and not _state["error"]
              and datetime.now() - _state["finished_at"] < timedelta(minutes=REFRESH_COOLDOWN_MIN)):
            started, reason = False, "recent"
        else:
            _state.update(running=True, started_at=datetime.now(), error=None)
            threading.Thread(target=_run, name="auto-refresh", daemon=True).start()
            started, reason = True, "started"
    return {"started": started, "reason": reason, **status()}
