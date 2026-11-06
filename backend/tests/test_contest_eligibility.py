from app.db import contest_eligibility

def test_department_target():
    assert contest_eligibility('참가대상: 모든 학과')['kind'] == 'all'
    assert contest_eligibility('참가대상: AI소프트웨어 학과, 기계공학과')['departments'] == ['AI소프트웨어 학과', '기계공학과']
    assert contest_eligibility('참가대상: AI소프트웨어학과')['departments'] == ['AI소프트웨어학과']

def test_no_unsupported_assumptions():
    for text in ['', '누구나 참가', '참가대상: 컴퓨터공학과 제외', '참가대상: 모든 학과 (1학년 제외)', '참가대상: 춘천캠퍼스 재학생', '참가대상: 모든 학과\n참여대상: 기계공학과']:
        assert contest_eligibility(text)['kind'] == 'unknown'
