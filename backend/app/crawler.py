"""강원대 게시판·시청(삼척·춘천·강릉·원주) 공지에서 목록/상세를 받아와 DB에 저장."""
import logging
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from . import cities, db, matching, samcheok
from .classifier import classify, is_city_event
from .config import (BOARDS, CHUNCHEON_BBS_ID, CHUNCHEON_LIST_URL, CRAWLABLE_BOARDS, EGOV_BOARDS, MAX_RETRIES,
                     REQUEST_DELAY, SAMCHEOK_GCODE, SAMCHEOK_LIST_URL, USER_AGENT, detail_url, list_url)
from .deadline import DeadlineResult, extract_deadline
from . import parser

log = logging.getLogger(__name__)


@dataclass
class Source:
    """출처별 차이(주소, 파싱, 거르기)만 모아둔 것. 수집 흐름은 crawl_board 하나로 같다."""
    list_request: Callable[[int], tuple[str, dict]]  # 페이지 번호 → (url, params)
    detail_request: Callable[[int], tuple[str, dict]]  # 글 번호 → (url, params)
    parse_list: Callable[[str], list[tuple[parser.ListItem, str]]]  # → [(항목, 요약문)]
    parse_total_pages: Callable[[str], int]
    parse_detail: Callable[[str], parser.Detail]
    keep: Callable[[parser.ListItem, str], bool] = lambda item, summary: True


def _source(board_id: int) -> Source:
    board = BOARDS[board_id]
    city, campus = board.get("city"), board.get("campus")
    keep_event = lambda item, summary: is_city_event(item.title, summary)  # noqa: E731 시청은 공모·행사만
    if city == "samcheok":
        return Source(
            list_request=lambda page: (SAMCHEOK_LIST_URL, {"gcode": SAMCHEOK_GCODE, "cpage": page}),
            detail_request=lambda pid: (SAMCHEOK_LIST_URL, {"gcode": SAMCHEOK_GCODE, "idx": pid, "amode": "view"}),
            parse_list=samcheok.parse_list,
            parse_total_pages=samcheok.parse_total_pages,
            parse_detail=samcheok.parse_detail,
            keep=keep_event,
        )
    if city == "chuncheon":
        return Source(
            list_request=lambda page: (CHUNCHEON_LIST_URL, {"pageIndex": page}),
            detail_request=lambda pid: (CHUNCHEON_LIST_URL, {"bbsId": CHUNCHEON_BBS_ID, "nttId": pid, "flag": "view"}),
            parse_list=lambda html: cities.chuncheon_list(html, campus),
            parse_total_pages=cities.chuncheon_total_pages,
            parse_detail=cities.chuncheon_detail,
            keep=keep_event,
        )
    if city in EGOV_BOARDS:
        base, bbs_no, key = EGOV_BOARDS[city]
        return Source(
            list_request=lambda page: (f"{base}/www/selectBbsNttList.do", {"bbsNo": bbs_no, "key": key, "pageIndex": page}),
            detail_request=lambda pid: (f"{base}/www/selectBbsNttView.do", {"bbsNo": bbs_no, "key": key, "nttNo": pid}),
            parse_list=lambda html: cities.egov_list(html, campus),
            parse_total_pages=cities.egov_total_pages,
            parse_detail=lambda html: cities.egov_detail(html, base),
            keep=keep_event,
        )
    return Source(
        list_request=lambda page: (list_url(board_id), {"pageIndex": page}),
        detail_request=lambda pid: (detail_url(board_id), {"pstSn": pid}),
        parse_list=lambda html: [(it, "") for it in parser.parse_list(html)],
        parse_total_pages=parser.parse_total_pages,
        parse_detail=parser.parse_detail,
    )


def _get(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    """서버 연결이 자주 끊겨서 실패하면 점점 길게 쉬면서 재시도."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            res = client.get(url, params=params)
            res.raise_for_status()
            return res
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 * attempt
            log.warning("요청 실패(%s/%s) %s %s → %s초 후 재시도: %s", attempt, MAX_RETRIES, url, params, wait, e)
            time.sleep(wait)


def _deadline(item: parser.ListItem, detail: parser.Detail) -> DeadlineResult:
    if item.period_end:  # 채용안내의 '접수기간'처럼 게시판이 직접 알려주면 그대로 사용
        return DeadlineResult(item.period_end, False)
    return extract_deadline(detail.content_text, item.posted_at)


def crawl_board(client, conn, board_id: int, max_pages: int, refresh_existing: bool,
                new_keys: list | None = None) -> dict:
    """게시판 하나를 max_pages 페이지까지 훑고, 새 글(또는 refresh 시 전체)의 상세를 수집.

    고정 공지는 매 페이지마다 반복 노출되므로 글 번호 기준으로 중복 제거한다.
    """
    board = BOARDS[board_id]
    src = _source(board_id)
    stats = {"pages": 0, "seen": 0, "skipped": 0, "new": 0, "updated": 0, "errors": 0}
    seen: set[int] = set()
    page, total_pages = 1, max_pages

    while page <= min(max_pages, total_pages):
        try:
            res = _get(client, *src.list_request(page))
        except Exception:
            log.exception("[%s] 목록 %s페이지 수집 실패, 이 게시판은 여기서 중단", board["name"], page)
            stats["errors"] += 1
            break
        if page == 1:
            total_pages = src.parse_total_pages(res.text)
        stats["pages"] += 1

        for item, summary in src.parse_list(res.text):
            if item.post_id in seen:
                continue
            seen.add(item.post_id)
            stats["seen"] += 1
            if not src.keep(item, summary):
                stats["skipped"] += 1  # 삼척시청의 행정 공지 등
                continue

            exists = db.exists(conn, board_id, item.post_id)
            if exists and not refresh_existing:
                db.update_list_fields(conn, board_id, item)  # 조회수 등만 갱신
                continue
            try:
                time.sleep(REQUEST_DELAY)
                detail = src.parse_detail(_get(client, *src.detail_request(item.post_id)).text)
                item.posted_at = item.posted_at or detail.posted_at  # 채용안내는 목록에 등록일이 없음
                category = classify(item.title, detail.content_text, board["category"])
                db.upsert(conn, board_id, item, detail, category, _deadline(item, detail))
                stats["updated" if exists else "new"] += 1
                if not exists and new_keys is not None:
                    new_keys.append((board_id, item.post_id))  # 수집이 끝나면 적합도 판단·알림 대상
            except Exception:  # 한 글이 실패해도 전체 수집은 계속
                log.exception("[%s] 상세 수집 실패: %s", board["name"], item.post_id)
                stats["errors"] += 1

        conn.commit()  # 페이지마다 저장해서 중간에 끊겨도 수집분은 남김
        page += 1
        time.sleep(REQUEST_DELAY)
    return stats


def crawl(max_pages: int = 3, refresh_existing: bool = False, boards: list[int] | None = None) -> dict:
    """boards를 안 주면 크롤링 가능한 출처 전체를 수집. 결과는 출처 이름별 통계."""
    result = {}
    new_keys: list[tuple[int, int]] = []
    conn = db.connect()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for board_id in boards or CRAWLABLE_BOARDS:
            if board_id not in CRAWLABLE_BOARDS:
                raise ValueError(f"크롤링할 수 없는 게시판: {board_id}")
            log.info("== %s(%s) 수집 시작", BOARDS[board_id]["name"], board_id)
            result[BOARDS[board_id]["name"]] = crawl_board(client, conn, board_id, max_pages, refresh_existing, new_keys)
    # 새 공고가 있으면: 분류기 다시 학습 → 태그 계산 → 맞는 사용자에게 알림 대기열
    if new_keys:
        try:
            matching.retrain(conn)
            matching.tag_missing(conn)
            queued = matching.queue_notifications(conn, new_keys)
            conn.commit()
            log.info("적합도: 새 공고 %s건, 알림 %s건 대기", len(new_keys), queued)
        except Exception:  # 적합도 계산이 실패해도 수집 결과는 유지
            log.exception("적합도 계산 실패")
    conn.close()
    return result
