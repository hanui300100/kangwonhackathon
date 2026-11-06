"""채용안내 · 삼척시청 파싱 테스트 (저장해 둔 실제 페이지 사용)."""
from datetime import date
from pathlib import Path

from app import samcheok
from app.classifier import is_samcheok_event
from app.parser import parse_list

FIX = Path(__file__).parent / "fixtures"


def test_recruit_list_period_column():
    # 채용안내(716): 등록일 칸 없음, 접수기간 칸에서 마감일
    items = parse_list((FIX / "list_716.html").read_text(encoding="utf-8"))
    assert len(items) >= 10
    assert all(it.posted_at is None for it in items)
    with_period = [it for it in items if it.period_end]
    assert with_period, "접수기간이 적힌 글이 하나는 있어야 함"
    assert all(isinstance(it.period_end, date) for it in with_period)
    # ' ~ '처럼 비어 있는 접수기간은 None
    assert any(it.period_end is None for it in items)


def test_samcheok_list():
    html = (FIX / "samcheok_list.html").read_text(encoding="utf-8")
    rows = samcheok.parse_list(html)
    assert len(rows) >= 10
    first, summary = rows[0]
    assert first.post_id == 144004
    assert "풋볼페스티벌" in first.title
    assert first.campuses == ["삼척"]
    assert first.posted_at == date(2026, 10, 21)
    assert first.author == "기획예산실"
    assert "모집기간" in summary
    assert samcheok.parse_total_pages(html) == 10


def test_samcheok_detail():
    d = samcheok.parse_detail((FIX / "samcheok_detail.html").read_text(encoding="utf-8"))
    assert d.posted_at == date(2026, 10, 21)
    assert "모집기간" in d.content_text
    assert d.images and d.images[0].startswith("https://www.samcheok.go.kr/board/image.do")
    assert d.attachments and d.attachments[0]["url"].startswith("https://www.samcheok.go.kr/board/download.do")
    assert not d.attachments[0]["name"].endswith("MB)")
    assert "033-570-6847" in d.contact
    assert "이전 보기" not in d.content_html  # 사진 넘김 버튼 제거


def test_samcheok_filter():
    assert is_samcheok_event("『삼척 풋볼페스티벌 시즌2』 특별 프로그램 신청 안내")
    assert is_samcheok_event("2026 삼척시 청년 아이디어 공모전")
    assert not is_samcheok_event("삼척시 기간제근로자 채용 공고")
    assert not is_samcheok_event("2026년 공모전 수상자 선정 결과 발표")
    assert not is_samcheok_event("도로 굴착 공사에 따른 교통 통제 안내")
    assert not is_samcheok_event("자동차 판금·도장 분야 외국인력 고용희망 업체 모집 및 선정 공고 안내")
