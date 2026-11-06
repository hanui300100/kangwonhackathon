from app.classifier import classify
from test_engagement import client, S

def test_volunteer_priority():
    assert classify('자원봉사활동 참가자 모집', '', '공모') == '봉사'
    assert classify('일반 행사', '', '행사') == '행사'

def test_existing_and_override(client):
    from app import db
    conn=db.connect();conn.execute("UPDATE notices SET title='자원봉사 활동 모집' WHERE board_id=721 AND post_id=1");conn.commit();conn.close()
    all_items=client.get('/api/notices').json()['items']
    assert next(n for n in all_items if n['id']=='721-1')['category']=='봉사'
    assert [n['id'] for n in client.get('/api/notices',params={'category':'봉사'}).json()['items']] == ['721-1']
    assert '721-1' not in [n['id'] for n in client.get('/api/notices',params={'category':'장학금'}).json()['items']]
    assert client.put('/api/admin/notices/721-1',headers=S,json={'category':'행사'}).status_code == 200
    assert '721-1' in [n['id'] for n in client.get('/api/notices',params={'category':'행사'}).json()['items']]
