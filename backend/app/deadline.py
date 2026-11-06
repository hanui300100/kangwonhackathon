"""본문 텍스트에서 마감일 추출.

학교 게시판에는 '마감일' 항목이 없어서 본문의
"제출기한 : 2026. 11. 17.(화) 18:00까지", "신청기간: 11. 3.(월) ~ 11. 14.(금)" 같은
문장에서 날짜를 찾아 가장 늦은 날짜를 마감일로 본다.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .config import FALLBACK_ACTIVE_DAYS

# 마감과 직접 관련된 줄 (우선 사용)
STRONG = re.compile(r"마감|기한|까지|기간|접수|일시|일정|~|∼|-\s*\d")
FULL_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
# 연도 없는 날짜: "11. 17.(화)", "11월 17일"
SHORT_DATE = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:\.|월)\s*(\d{1,2})\s*(?:\.|일)?\s*\(\s*[월화수목금토일]")
SHORT_DATE_KR = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")


@dataclass
class DeadlineResult:
    deadline: date | None
    estimated: bool  # True면 본문에서 못 찾아 등록일 기준으로 추정한 값


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _dates_in(line: str, posted: date | None) -> list[date]:
    found: list[date] = []
    spans = []
    for m in FULL_DATE.finditer(line):
        dt = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if dt:
            found.append(dt)
            spans.append(m.span())
    base_year = (posted or date.today()).year
    for pattern in (SHORT_DATE, SHORT_DATE_KR):
        for m in pattern.finditer(line):
            if any(s <= m.start() < e for s, e in spans):
                continue  # 이미 연도 포함 날짜로 잡힌 부분
            dt = _safe_date(base_year, int(m[1]), int(m[2]))
            if dt and posted and dt < posted - timedelta(days=180):
                dt = _safe_date(base_year + 1, int(m[1]), int(m[2]))  # 연말 글의 내년 일정
            if dt:
                found.append(dt)
    return found


def extract_deadline(text: str, posted: date | None) -> DeadlineResult:
    strong, weak = [], []
    for line in (text or "").splitlines():
        dates = _dates_in(line, posted)
        (strong if STRONG.search(line) else weak).extend(dates)

    for candidates in (strong, weak):
        if posted:  # 등록일보다 한참 이전 날짜(예: 근거 공문 날짜)는 제외
            candidates = [d for d in candidates if d >= posted - timedelta(days=7)]
        # 1년 넘게 뒤 날짜(예: 졸업 예정 연도)는 마감일로 보기 어려움
        if posted:
            candidates = [d for d in candidates if d <= posted + timedelta(days=365)]
        if candidates:
            return DeadlineResult(max(candidates), False)

    if posted:
        return DeadlineResult(posted + timedelta(days=FALLBACK_ACTIVE_DAYS), True)
    return DeadlineResult(None, True)
