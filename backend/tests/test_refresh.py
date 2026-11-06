"""자동 갱신: 한 번에 하나만, 최근에 했으면 건너뜀, 실패하면 다음 요청에 다시 시도."""
import threading
import time

import pytest

from app import refresh


@pytest.fixture(autouse=True)
def reset_state():
    refresh._state.update(running=False, started_at=None, finished_at=None, new_count=0, error=None)
    yield


def _wait_done(timeout=5):
    end = time.time() + timeout
    while refresh.status()["running"] and time.time() < end:
        time.sleep(0.02)


def test_runs_once_then_cooldown(monkeypatch):
    gate = threading.Event()
    calls = []

    def fake_crawl(max_pages, refresh_existing):
        calls.append(max_pages)
        gate.wait(2)
        return {"학사공지": {"new": 2}, "삼척시청": {"new": 1}}

    monkeypatch.setattr(refresh, "crawl", fake_crawl)
    first = refresh.request_refresh()
    assert first["started"] and first["running"]
    second = refresh.request_refresh()  # 진행 중에 또 누름
    assert not second["started"] and second["reason"] == "running"
    gate.set()
    _wait_done()
    s = refresh.status()
    assert s["new_count"] == 3 and s["last_refreshed_at"] and s["error"] is None
    third = refresh.request_refresh()  # 방금 끝났으니 건너뜀
    assert not third["started"] and third["reason"] == "recent"
    assert calls == [1]
    assert refresh.request_refresh(force=True)["started"]  # 관리자용 강제 실행
    _wait_done()


def test_failure_allows_retry(monkeypatch):
    monkeypatch.setattr(refresh, "crawl", lambda **kw: (_ for _ in ()).throw(RuntimeError("학교 서버 응답 없음")))
    refresh.request_refresh()
    _wait_done()
    assert "응답 없음" in refresh.status()["error"]
    # 실패했으면 10분 안이라도 다시 시도
    monkeypatch.setattr(refresh, "crawl", lambda **kw: {})
    assert refresh.request_refresh()["started"]
    _wait_done()
