from test_engagement import client

def test_saved_notices(client):
    r=client.get('/api/notices/saved', params={'ids':'721-2,721-1,721-999'})
    assert r.status_code == 200
    assert [n['id'] for n in r.json()['items']] == ['721-2','721-1']
    assert client.get('/api/notices/saved',params={'ids':'bad'}).status_code == 400
    assert client.get('/api/notices/saved').json() == {'items': []}
