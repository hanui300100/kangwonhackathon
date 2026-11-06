"""강원대 게시판 HTML 파싱 (네트워크 없이 HTML 문자열만 다룸)."""
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, parse_qs, urlparse

from bs4 import BeautifulSoup

from .config import BASE_URL, CAMPUSES


@dataclass
class ListItem:
    post_id: int
    title: str
    campuses: list[str]
    author: str
    posted_at: date | None
    views: int
    is_pinned: bool
    # 채용안내처럼 목록에 '접수기간'이 있는 게시판에서만 채워짐 → 본문 추측보다 우선
    period_end: date | None = None


@dataclass
class Detail:
    content_html: str = ""
    content_text: str = ""
    contact: str = ""
    posted_at: date | None = None  # 목록에 등록일이 없는 게시판(채용안내)용
    images: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)  # {name, url}
    links: list[dict] = field(default_factory=list)  # {text, url}


DATE_RE = re.compile(r"(\d{4})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})")


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def _to_int(text: str) -> int:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else 0


def parse_total_pages(html: str) -> int:
    m = re.search(r"페이지\s*\d+\s*/\s*([\d,]+)", html)
    return _to_int(m[1]) if m else 1


def _campuses(td) -> list[str]:
    if td is None:
        return list(CAMPUSES)
    names = [s.get_text(strip=True) for s in td.select("span.campus")]
    # 'ALL' 칩이거나 표시가 없으면 전 캠퍼스 대상 글로 본다
    return [c for c in CAMPUSES if c in names] or list(CAMPUSES)


def _period_end(text: str) -> date | None:
    """'2026.11.04 ~ 2026.11.15' → 2026-11-15. 비어 있으면(' ~ ') None."""
    dates = [d for d in (parse_date(m[0]) for m in DATE_RE.finditer(text or "")) if d]
    return dates[-1] if dates else None


def parse_list(html: str) -> list[ListItem]:
    """게시판마다 칸 구성이 달라서(작성자/접수기간 칸 유무 등) 머리글 이름으로 칸을 찾는다."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("div.table-list.pc table") or soup
    headers = [th.get_text(strip=True) for th in table.select("thead th")]

    def col(tds, name):
        i = headers.index(name) if name in headers else -1
        return tds[i] if 0 <= i < len(tds) else None

    def text(td):
        return td.get_text(" ", strip=True) if td is not None else ""

    items = []
    for tr in table.select("tbody tr"):
        a = tr.select_one("a.detail-button[data-sn]")
        tds = tr.find_all("td", recursive=False)
        if not a or len(tds) < len(headers):
            continue
        items.append(
            ListItem(
                post_id=int(a["data-sn"]),
                title=a.get_text(" ", strip=True),
                campuses=_campuses(col(tds, "캠퍼스")),
                author=text(col(tds, "작성자")),
                posted_at=parse_date(text(col(tds, "등록일시"))),
                views=_to_int(text(col(tds, "조회수"))),
                is_pinned=tr.select_one("span.noti") is not None,
                period_end=_period_end(text(col(tds, "접수기간"))),
            )
        )
    return items


_BLOCKS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote"]
# 한글 워드 문서에서 붙여넣은 글에 섞인 깨진 기호(사용자 정의 영역 문자, 폭 없는 공백)
_JUNK = re.compile("[-​-‏﻿]")


def readable_text(el) -> str:
    """문단/표 행이 끝날 때만 줄바꿈. (굵은 글씨 등 인라인 태그마다 줄이 끊기지 않게)"""
    soup = BeautifulSoup(str(el), "lxml")  # 원본 HTML은 건드리지 않도록 복사본 사용
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.append(" | ")  # 표 칸 구분
    for block in soup.find_all(_BLOCKS):
        block.append("\n")
    lines = []
    for line in _JUNK.sub("", soup.get_text("")).splitlines():
        line = re.sub("[ \t ]+", " ", line).strip().rstrip("|").strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def fill_body(el, base_url: str, d: Detail) -> None:
    """본문 요소에서 HTML/텍스트/사진/링크를 뽑아 Detail에 채움. (강원대·삼척시청 공용)"""
    # 상대경로를 절대경로로 바꿔서 앱/웹에서 그대로 쓸 수 있게 함
    for img in el.find_all("img"):
        src = img.get("src")
        if src:
            img["src"] = urljoin(base_url, src.strip())
            d.images.append(img["src"])
    for a in el.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "#", "mailto:")):
            continue
        a["href"] = urljoin(base_url, href)
        d.links.append({"text": a.get_text(" ", strip=True) or a["href"], "url": a["href"]})
    d.content_html = el.decode_contents().strip()
    d.content_text = readable_text(el)
    # 본문에 그냥 텍스트로 적힌 URL도 링크로 수집
    known = {l["url"] for l in d.links}
    for url in re.findall(r"https?://[^\s<>\"')]+", d.content_text):
        if url not in known:
            known.add(url)
            d.links.append({"text": url, "url": url})


def parse_detail(html: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    d = Detail()

    for p in soup.select("div.view-header-info p"):
        if "등록일" in p.get_text():
            d.posted_at = parse_date(p.get_text())

    for tr in soup.select("div.table-row tr"):
        th = tr.find("th")
        if th and "문의" in th.get_text():
            d.contact = tr.find("td").get_text(" ", strip=True)

    editor = soup.select_one("div.editor-wrap")
    if editor:
        fill_body(editor, BASE_URL, d)

    for item in soup.select("div.view-file-container .file-item"):
        a = item.select_one("a.view-file-link")
        url = item.get("data-atch-download-url") or (a.get("href") if a else None)
        if not url:
            continue
        name_el = item.select_one(".view-file-name")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            name = parse_qs(urlparse(url).query).get("fn", ["첨부파일"])[0]
        d.attachments.append({"name": name, "url": urljoin(BASE_URL, url)})

    return d
