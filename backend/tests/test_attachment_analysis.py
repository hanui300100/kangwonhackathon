import io, zipfile, json, time
from app import attachment_analysis as a, db

def test_hwpx_extract_and_url_validation():
    data=io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('Contents/section0.xml', '<p xmlns="h"><t>참가대상: 모든 학과</t></p>')
    assert '참가대상: 모든 학과' in a.extract_text('test.hwpx', data.getvalue())
    assert a.valid_url('https://www.kangwon.ac.kr/download')
    assert not a.valid_url('https://kangwon.ac.kr.evil.test/download')
    assert not a.valid_url('http://www.kangwon.ac.kr/download')

def test_background_cache_and_failed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'temp.db')
    monkeypatch.setattr(a, 'download', lambda url: b'test')
    monkeypatch.setattr(a, 'extract_text', lambda name, raw: '참가대상: 컴퓨터공학과')
    conn = db.connect()
    attachments=[{'name':'file.pdf','url':'https://www.kangwon.ac.kr/file'}]
    sig='abc'; a.run((719,3), sig, '', attachments)
    cached=conn.execute('SELECT result FROM attachment_analysis WHERE board_id=719 AND post_id=3').fetchone()
    assert json.loads(cached['result']) == {'kind':'specific','departments':['컴퓨터공학과']}
    monkeypatch.setattr(a, 'download', lambda url: (_ for _ in ()).throw(ValueError('network failed')))
    a.run((719,4), sig, '참가대상: 모든 학과', attachments)
    cached=conn.execute('SELECT result FROM attachment_analysis WHERE board_id=719 AND post_id=4').fetchone()
    assert json.loads(cached['result'])['kind'] == 'unknown'
    conn.close()
