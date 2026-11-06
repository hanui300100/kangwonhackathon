import json
from collections import Counter
from app import db

def test_seed_preserves_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'seed.db')
    conn = db.connect()
    conn.execute("INSERT INTO departments(name,college,campus) VALUES ('컴퓨터공학과','기존 단과대학','춘천')")
    old_id = conn.execute('SELECT id FROM departments').fetchone()[0]
    db.seed_departments(conn)
    row = conn.execute("SELECT id,college,field FROM departments WHERE name='컴퓨터공학과' AND campus='춘천'").fetchone()
    assert row['id'] == old_id and row['college'] == '기존 단과대학' and row['field'] == '공대'
    counts = dict(conn.execute('SELECT campus,COUNT(*) FROM departments GROUP BY campus'))
    assert all(counts.get(c,0)>0 for c in ('춘천','삼척','강릉','원주'))
    count = conn.execute('SELECT count(*) FROM departments').fetchone()[0]
    db.seed_departments(conn)
    assert conn.execute('SELECT count(*) FROM departments').fetchone()[0] == count
    conn.close()

def test_catalog_unique():
    from pathlib import Path
    catalog = json.loads(Path(db.__file__).with_name('departments.json').read_text(encoding='utf-8'))
    assert len({(d['campus'],d['name']) for d in catalog}) == len(catalog)
    assert set(d['field'] for d in catalog) <= {'인문계','자연계','공대','예체능','기타·미분류'}
