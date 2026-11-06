from datetime import date, timedelta
from test_engagement import client
from app.main import period_start

def test_period_bounds():
    assert period_start(date(2024,3,31), '1m') == date(2024,2,29)
    assert period_start(date(2024,2,29), '1y') == date(2023,2,28)

def test_period_filter(client):
    from app import db
    c=db.connect(); today=date.today()
    for pid, d in [(1,today),(2,today-timedelta(days=3)),(3,today-timedelta(days=40))]:
        c.execute('UPDATE notices SET posted_at=? WHERE board_id=721 AND post_id=?',(d.isoformat(),pid))
    c.commit();c.close()
    def ids(period): return {p['id'] for p in client.get('/api/notices',params={'period':period}).json()['items']}
    assert ids('1d') == {'721-1'}
    assert ids('1w') == {'721-1','721-2'}
    assert ids('1y') == {'721-1','721-2','721-3'}
    assert client.get('/api/notices',params={'period':'bad'}).status_code == 422
