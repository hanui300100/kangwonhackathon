"""키워드 규칙 기반 공고 분류. 규칙은 여기만 고치면 됨."""
from .config import BOARDS

# 위에서부터 우선순위. 제목에 나오면 가중치 3, 본문이면 1.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "봉사": ["봉사활동", "자원봉사", "봉사단", "봉사자", "봉사"],
    "장학금": ["장학", "학자금", "국가근로", "근로장학", "생활비 지원", "등록금 지원", "후원금"],
    "채용": ["채용", "직원 모집", "인턴", "기간제", "계약직", "공개모집 공고"],
    "공모": ["공모", "경진대회", "콘테스트", "아이디어 대회", "수기", "UCC"],
    "행사": [
        "행사", "축제", "특강", "설명회", "박람회", "대회", "캠프", "세미나",
        "워크숍", "워크샵", "페스티벌", "콘서트", "체험", "프로그램", "포럼", "토크", "전시",
        "탐방", "교육생 모집", "참가자 모집", "서포터즈", "멘토링",
    ],
    "학사": [
        "수강", "학점", "계절수업", "계절학기", "졸업", "휴학", "복학", "전과", "부전공",
        "복수전공", "부복수", "등록금", "성적", "학위", "교육과정", "수업", "시험", "학적",
        "재입학", "교직", "수료",
    ],
}
DEFAULT_CATEGORY = "기타"

# 탭에 보여줄 전체 분류 = 키워드 분류 + 출처 고정 분류(삼척시, 학과행사 등) + 기타
CATEGORIES = list(dict.fromkeys(
    [*CATEGORY_KEYWORDS, *(b["category"] for b in BOARDS.values() if b["category"]), DEFAULT_CATEGORY]
))


def classify(title: str, content: str = "", board_category: str | None = None) -> str:
    """게시판 자체가 분류를 정해주면(장학공지 등) 그대로 쓰고, 아니면 키워드로 판단."""
    if "봉사" in title:
        return "봉사"
    if board_category:
        return board_category
    best, best_score = DEFAULT_CATEGORY, 0
    body = (content or "")[:3000]
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(3 for k in keywords if k in title) + sum(1 for k in keywords if k in body)
        if score > best_score:  # 동점이면 위쪽(우선순위 높은) 분류 유지
            best, best_score = category, score
    return best


# ── 시청 공지(삼척·춘천·강릉·원주) 중 '시민·대학생 대상 공모·행사'만 고르는 규칙 ──
SAMCHEOK_INCLUDE = [
    "공모", "모집", "참가", "참여", "신청", "행사", "축제", "대회", "공연", "전시", "체험",
    "교육생", "강좌", "프로그램", "페스티벌", "설명회", "특강", "캠프", "콘테스트", "이벤트",
    "서포터즈", "기자단", "봉사", "청년", "대학생",
]
# 시민이 참여하는 행사가 아닌 행정 공지는 제외
SAMCHEOK_EXCLUDE = [
    "채용", "공무원", "기간제", "임기제", "입찰", "고시", "합격", "결과", "선정자", "인사발령",
    "면접", "시험", "계약", "보상", "공시송달", "열람", "변경 안내", "휴관", "중단", "단수",
    "업체", "우수기관", "사업자", "법인",  # 기업·기관 대상 공모
    "회의결과", "심의", "위원회", "입주자모집", "분양", "주유소", "가격 정보",  # 행정·생활 정보
    "공시지가", "이의신청", "과태료", "체납",
]
# 춘천시청처럼 시청이 글에 붙인 자체 분류가 있으면 이것도 참고 (예: 행사·축제)
CITY_CATEGORY_INCLUDE = ["행사", "축제", "교육", "공모", "문화", "체육", "청년"]


def is_city_event(title: str, summary: str = "") -> bool:
    """summary: 목록의 요약문(삼척) 또는 시청 자체 분류(춘천)"""
    if any(k in title for k in SAMCHEOK_EXCLUDE):
        return False
    return (any(k in title for k in SAMCHEOK_INCLUDE)
            or any(k in summary[:200] for k in ("공모", "모집기간", "참가"))
            or any(k in summary for k in CITY_CATEGORY_INCLUDE))


is_samcheok_event = is_city_event  # 예전 이름 (테스트 호환)
