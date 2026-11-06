from datetime import date
from pathlib import Path

from app.classifier import classify
from app.deadline import extract_deadline
from app.parser import parse_detail, parse_list, parse_total_pages

FIX = Path(__file__).parent / "fixtures"


def test_parse_list():
    html = (FIX / "list.html").read_text(encoding="utf-8")
    items = parse_list(html)
    assert len(items) >= 10
    first = items[0]
    assert first.post_id == 2616
    assert first.campuses == ["춘천", "삼척"]
    assert first.posted_at == date(2026, 10, 29)
    assert first.is_pinned
    assert parse_total_pages(html) == 251


def test_parse_detail():
    d = parse_detail((FIX / "detail.html").read_text(encoding="utf-8"))
    assert "인하대학교" in d.content_text
    # 굵은 글씨(<strong>) 때문에 문장이 끊기지 않아야 함
    assert "2026학년도 겨울 계절수업 인하대학교 학점 교류 수학 안내문을" in d.content_text
    assert d.contact.startswith("033-250-6016")
    assert len(d.attachments) == 3
    assert d.attachments[0]["url"].startswith("https://www.kangwon.ac.kr/ko/cmmn/download.do")


def test_deadline_from_detail():
    d = parse_detail((FIX / "detail.html").read_text(encoding="utf-8"))
    r = extract_deadline(d.content_text, date(2026, 10, 29))
    assert r.deadline == date(2026, 11, 17) and not r.estimated


def test_deadline_short_range_and_fallback():
    r = extract_deadline("신청기간: 11. 3.(월) ~ 11. 14.(금)", date(2026, 10, 30))
    assert r.deadline == date(2026, 11, 14)
    r = extract_deadline("접수: 1월 5일까지", date(2026, 12, 20))
    assert r.deadline == date(2027, 1, 5)
    r = extract_deadline("날짜 없음", date(2026, 10, 1))
    assert r.estimated and r.deadline == date(2026, 10, 31)


def test_parse_list_without_author_column():
    # 행사안내(715)는 작성자 칸이 없음
    items = parse_list((FIX / "list_715.html").read_text(encoding="utf-8"))
    assert len(items) >= 10
    assert items[0].author == "" and items[0].posted_at is not None
    assert all(it.campuses for it in items)


def test_parse_list_all_campus_chip():
    # 장학공지(721)의 'ALL' 칩 → 4개 캠퍼스 모두
    items = parse_list((FIX / "list_721.html").read_text(encoding="utf-8"))
    first = next(it for it in items if it.post_id == 2537)
    assert first.campuses == ["춘천", "삼척", "강릉", "원주"]
    assert first.author == "강릉학생지원과"


def test_board_category_overrides_keywords():
    assert classify("주차장 공사 안내", "", "장학금") == "장학금"
    assert classify("창업 아이디어 공모전") == "공모"


def test_classify():
    assert classify("2027학년도 1학기 교내 장학생 선발 안내") == "장학금"
    assert classify("취업 특강 참가자 모집") == "행사"
    assert classify("겨울계절수업 학점교류 안내") == "학사"
    assert classify("주차장 공사 안내") == "기타"
