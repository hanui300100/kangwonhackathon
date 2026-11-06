"""춘천·강릉·원주 시청 공지 파싱. (삼척시청은 samcheok.py)

- 춘천시청 '시정소식': 목록이 <li> 형태, 글마다 자체 분류(예: 행사·축제)가 붙어 있음
- 강릉시청 '공지사항', 원주시청 '새소식': 같은 게시판 프로그램(selectBbsNttList.do)이라 파서 하나로 처리

시청 공지에는 행정 공지가 섞여 있어 classifier.is_city_event 로 공모·행사만 골라 저장한다.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import CHUNCHEON_BASE, CHUNCHEON_BBS_ID
from .parser import Detail, ListItem, fill_body, parse_date


def _max_page(html: str) -> int:
    pages = [int(n) for n in re.findall(r"pageIndex=(\d+)", html)]
    return max(pages) if pages else 1


# ───────────── 춘천시청 ─────────────

def chuncheon_list(html: str, campus: str = "춘천") -> list[tuple[ListItem, str]]:
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for a in soup.select("a.tit[href*='nttId=']"):
        m = re.search(r"nttId=(\d+)", a["href"])
        li = a.find_parent("li")
        if not m or not li:
            continue
        writers = [s.get_text(" ", strip=True) for s in li.select("span.writer")]
        day = li.select_one("span.day")
        city_category = writers[0] if len(writers) > 1 else ""  # 예: 행사·축제, 교육, 모집
        rows.append((
            ListItem(
                post_id=int(m[1]),
                title=a.get_text(" ", strip=True),
                campuses=[campus],
                author=writers[-1] if writers else "",
                posted_at=parse_date(day.get_text() if day else ""),
                views=0,
                is_pinned=li.select_one(".noti-icon") is not None,
            ),
            city_category,  # 거르기(공모·행사 여부) 판단에 사용
        ))
    return rows


def chuncheon_total_pages(html: str) -> int:
    return _max_page(html)


def chuncheon_detail(html: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    d = Detail()
    date = soup.select_one(".boardview-info .date")
    d.posted_at = parse_date(date.get_text() if date else "")
    # 첨부파일 목록(.boardview-file)은 본문 옆이나 안에 있을 수 있어 페이지 전체에서 찾고, 본문에서는 뺌
    files = soup.select_one(".boardview-file")
    if files:
        files.extract()
        for a in files.select("a.file-name"):
            # javascript:fn_egov_downFile('FILE_..','1','BBSMSTR_..','526632') → 실제 다운로드 주소
            m = re.search(r"fn_egov_downFile\('([^']+)','([^']+)','([^']*)','([^']*)'\)", a.get("href", ""))
            if m:
                url = (f"{CHUNCHEON_BASE}/_cmm/fms/FileDown.do?atchFileId={m[1]}&fileSn={m[2]}"
                       f"&bbsId={m[3] or CHUNCHEON_BBS_ID}&nttId={m[4]}")
                d.attachments.append({"name": a.get_text(" ", strip=True), "url": url})
    body = soup.select_one(".boardview-con")
    if body:
        for junk in body.select("script, style, button"):
            junk.decompose()
        fill_body(body, CHUNCHEON_BASE, d)
    m = re.search(r"문\s*의\s*처?\s*[:：]\s*([^\n]+)", d.content_text)
    if m:
        d.contact = m[1].strip()[:100]
    return d


# ───────────── 강릉시청 · 원주시청 (같은 게시판 프로그램) ─────────────

def egov_list(html: str, campus: str) -> list[tuple[ListItem, str]]:
    """칸 순서가 시청마다 달라서(조회수/작성일 위치) 내용 모양으로 찾는다."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.select("tbody tr"):
        a = tr.select_one("a[href*='nttNo=']")
        m = re.search(r"nttNo=(\d+)", a["href"]) if a else None
        if not m:
            continue
        tds = tr.find_all("td", recursive=False)
        texts = [td.get_text(" ", strip=True) for td in tds]
        subject_td = a.find_parent("td")
        idx = tds.index(subject_td) if subject_td in tds else 1
        date = next((parse_date(t) for t in texts if re.fullmatch(r"\d{4}[.\-]\d{1,2}[.\-]\d{1,2}", t)), None)
        views = next((int(t) for i, t in enumerate(texts) if i > idx + 1 and t.isdigit()), 0)
        rows.append((
            ListItem(
                post_id=int(m[1]),
                title=a.get_text(" ", strip=True),
                campuses=[campus],
                author=texts[idx + 1] if idx + 1 < len(texts) else "",
                posted_at=date,
                views=views,
                is_pinned=not texts[0].isdigit() if texts else False,  # 번호 대신 '공지' 표시
            ),
            "",
        ))
    return rows


def egov_total_pages(html: str) -> int:
    return _max_page(html)


def egov_detail(html: str, base: str) -> Detail:
    soup = BeautifulSoup(html, "lxml")
    d = Detail()
    for span in soup.select(".bbs_info span"):
        if "작성일" in span.get_text():
            d.posted_at = parse_date(span.get_text())
    body = soup.select_one("td.bbs_content, td.p-table__content")
    if body:
        for junk in body.select("script, style, button"):
            junk.decompose()
        fill_body(body, base, d)
    # 첨부파일: 강릉(.down_view) / 원주(.p-attach__link)
    for box in soup.select(".down_view"):
        a = box.select_one("a.file_down[href]")
        name = box.select_one("span")
        if a:
            d.attachments.append({"name": name.get_text(" ", strip=True) if name else "첨부파일",
                                  "url": urljoin(base + "/www/", a["href"])})
    for a in soup.select("a.p-attach__link[href]"):
        spans = a.select("span")
        name = spans[-1].get_text(" ", strip=True) if spans else a.get_text(" ", strip=True)
        d.attachments.append({"name": name, "url": urljoin(base, a["href"])})
    m = re.search(r"문\s*의\s*처?\s*[:：]\s*([^\n]+)", d.content_text)
    if m:
        d.contact = m[1].strip()[:100]
    return d
