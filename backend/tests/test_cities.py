"""춘천·강릉·원주 시청 파싱 (저장해 둔 실제 페이지 사용)."""
from datetime import date
from pathlib import Path

from app import cities
from app.classifier import is_city_event
from app.config import GANGNEUNG_BASE, WONJU_BASE, original_url

FIX = Path(__file__).parent / "fixtures"
read = lambda name: (FIX / name).read_text(encoding="utf-8")  # noqa: E731


def test_chuncheon_list_and_detail():
    rows = cities.chuncheon_list(read("chuncheon_list.html"))
    assert len(rows) >= 10
    item, city_cat = next((i, c) for i, c in rows if i.post_id == 526632)
    assert item.title == "2026년 춘천시 일자리박람회 참여기업 모집"
    assert item.campuses == ["춘천"] and item.posted_at == date(2026, 8, 5)
    assert item.is_pinned and city_cat == "행사·축제" and item.author == "일자리지원센터"
    assert cities.chuncheon_total_pages(read("chuncheon_list.html")) > 100

    d = cities.chuncheon_detail(read("chuncheon_detail.html"))
    assert d.posted_at == date(2026, 8, 5)
    assert len(d.attachments) == 2
    assert d.attachments[0]["url"].startswith("https://www.chuncheon.go.kr/_cmm/fms/FileDown.do?atchFileId=FILE_")
    assert d.attachments[0]["name"].endswith(".hwpx")
    assert "첨부파일" not in d.content_text[:20]  # 첨부 목록은 본문에서 빠짐


def test_gangneung_list_and_detail():
    rows = cities.egov_list(read("gangneung_list.html"), "강릉")
    assert len(rows) >= 10
    item = next(i for i, _ in rows if i.post_id == 240652)
    assert "그린바이오" in item.title and item.campuses == ["강릉"]
    assert item.posted_at == date(2026, 11, 3) and item.author == "기업지원과" and item.views == 176
    d = cities.egov_detail(read("gangneung_detail.html"), GANGNEUNG_BASE)
    assert d.posted_at == date(2026, 11, 3) and "그린바이오" in d.content_text
    assert d.attachments and d.attachments[0]["url"] == "https://www.gn.go.kr/www/downloadBbsFile.do?atchmnflNo=201296"
    assert d.attachments[0]["name"].endswith(".png")


def test_wonju_list_and_detail():
    rows = cities.egov_list(read("wonju_list.html"), "원주")
    assert len(rows) >= 10
    item = next(i for i, _ in rows if i.post_id == 495202)
    assert "꿈이룸" in item.title and item.posted_at == date(2026, 11, 5)
    assert item.author == "정보통신과" and item.views == 52
    d = cities.egov_detail(read("wonju_detail.html"), WONJU_BASE)
    assert d.posted_at == date(2026, 11, 5)
    assert d.attachments[0]["url"].startswith("https://www.wonju.go.kr/www/downloadBbsFile.do?key=211&atchmnflNo=")
    assert d.attachments[0]["name"].lower().endswith(".jpg")


def test_city_filter_and_urls():
    assert is_city_event("「꿈이룸 더하기+」인재양성 프로그램 최종 쇼케이스")
    assert is_city_event("일반 안내", "행사·축제")  # 춘천 자체 분류로 판단
    assert not is_city_event("2026년 제32회 원주시 건축위원회 회의결과")
    assert not is_city_event("최저가·알뜰 주유소 위치 및 가격 정보")
    assert original_url(9002, 526632).endswith("bbsId=BBSMSTR_000000000151&nttId=526632&flag=view")
    assert original_url(9003, 240652) == "https://www.gn.go.kr/www/selectBbsNttView.do?key=258&bbsNo=12&nttNo=240652"
    assert original_url(9004, 495202).startswith("https://www.wonju.go.kr/www/selectBbsNttView.do?key=211&bbsNo=1")
