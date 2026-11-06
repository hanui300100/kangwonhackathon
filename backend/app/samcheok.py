"""삼척시청 공지사항(https://www.samcheok.go.kr/intro/00443/02073.web) 파싱.

시청 공지사항에는 행정 공지가 섞여 있어서, classifier.is_samcheok_event 로
시민·대학생 대상 공모·행사만 골라 저장한다. 캠퍼스는 '삼척'으로 고정.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import SAMCHEOK_BASE
from .parser import Detail, ListItem, fill_body, parse_date


def parse_total_pages(html: str) -> int:
    m = re.search(r"\(\s*<b[^>]*>\s*\d+\s*</b>\s*/\s*(\d+)\s*페이지", html)
    return int(m[1]) if m else 1


def parse_list(html: str) -> list[tuple[ListItem, str]]:
    """(목록 항목, 요약문) 목록. 요약문은 공모·행사 여부 판단에 씀."""
    soup = BeautifulSoup(html, "lxml")
    result = []
    for li in soup.select("div.bbs-skin-default li.li1, ul.lst1 > li.li1"):
        a = li.select_one("a.a1[href]")
        m = re.search(r"idx=(\d+)", a["href"]) if a else None
        title_el = li.select_one("strong.t1")
        if not m or not title_el:
            continue
        meta = [s.get_text(" ", strip=True) for s in li.select("span.t3")]
        views = next((int(re.sub(r"\D", "", t) or 0) for t in meta if "조회" in t), 0)
        summary = li.select_one("span.t2")
        result.append((
            ListItem(
                post_id=int(m[1]),
                title=title_el.get_text(" ", strip=True),
                campuses=["삼척"],
                author=meta[1] if len(meta) > 1 else "",
                posted_at=parse_date(meta[0]) if meta else None,
                views=views,
                is_pinned=False,
            ),
            summary.get_text(" ", strip=True) if summary else "",
        ))
    # 같은 글이 두 번 잡히지 않도록 (선택자 두 개가 같은 li를 가리킬 수 있음)
    seen, unique = set(), []
    for item, s in result:
        if item.post_id not in seen:
            seen.add(item.post_id)
            unique.append((item, s))
    return unique


def parse_detail(html: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    d = Detail()
    view = soup.select_one("div.bbs1view1") or soup

    for dt in view.select("div.info1 dt"):
        dd = dt.find_next_sibling("dd")
        if dd and "작성일" in dt.get_text():
            d.posted_at = parse_date(dd.get_text())

    for a in view.select("div.attach1 a.filename[href]"):
        name = re.sub(r"\(\s*[\d.]+\s*[KMG]?B\s*\)\s*$", "", a.get_text(" ", strip=True)).strip()
        d.attachments.append({"name": name or "첨부파일", "url": urljoin(SAMCHEOK_BASE, a["href"])})

    body = view.select_one("div.substance")
    if body:
        # 사진 넘김 버튼, 스크립트 등 화면 장식 제거
        for junk in body.select("script, style, .mControl, button"):
            junk.decompose()
        fill_body(body, SAMCHEOK_BASE, d)
        m = re.search(r"문\s*의\s*처?\s*[:：]\s*([^\n]+)", d.content_text)
        if m:
            d.contact = m[1].strip()
    return d
