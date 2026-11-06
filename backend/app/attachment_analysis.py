"""Bounded background extraction of publicly posted contest attachments."""
import hashlib, io, json, logging, re, struct, threading, time, zipfile, zlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
import olefile
from pypdf import PdfReader
from . import db

log = logging.getLogger(__name__)
MAX_BYTES = 8 * 1024 * 1024
MAX_UNPACKED = 20 * 1024 * 1024
ALLOWED = ('kangwon.ac.kr', 'samcheok.go.kr', 'chuncheon.go.kr', 'gn.go.kr', 'wonju.go.kr')
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='attachment-analysis')
lock = threading.Lock()
inflight = set()

def valid_url(url):
    try:
        p = urlparse(url)
        host = (p.hostname or '').lower()
        return p.scheme == 'https' and p.port in (None, 443) and not p.username and not p.password and any(host == x or host.endswith('.' + x) for x in ALLOWED)
    except ValueError:
        return False

def download(url):
    with httpx.Client(timeout=15, follow_redirects=False, headers={'User-Agent': 'KNU-Notice-Bot/1.0'}) as client:
        for _ in range(4):
            if not valid_url(url): raise ValueError('unsupported attachment URL')
            host = urlparse(url)
            with client.stream('GET', url, headers={'Referer': f'{host.scheme}://{host.netloc}/'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if not response.headers.get('location'): raise ValueError('redirect missing location')
                    url = urljoin(url, response.headers['location']); continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES: raise ValueError('attachment too large')
                    chunks.append(chunk)
                return b''.join(chunks)
        raise ValueError('too many redirects')

def extract_hwpx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = [i for i in archive.infolist() if re.fullmatch(r'Contents/section\d+\.xml', i.filename, re.I)]
        if not entries or sum(i.file_size for i in entries) > MAX_UNPACKED: raise ValueError('invalid HWPX')
        paragraphs = []
        for item in sorted(entries, key=lambda x: x.filename):
            xml = archive.read(item)
            if b'<!DOCTYPE' in xml.upper(): raise ValueError('unsupported XML')
            root = ElementTree.fromstring(xml)
            for paragraph in root.iter():
                if paragraph.tag.rsplit('}', 1)[-1] == 'p':
                    parts = [e.text or '' for e in paragraph.iter() if e.tag.rsplit('}', 1)[-1] == 't']
                    if parts: paragraphs.append(''.join(parts))
        return '\n'.join(paragraphs)

def extract_hwp(raw):
    with olefile.OleFileIO(io.BytesIO(raw)) as f:
        header = f.openstream('FileHeader').read(256)
        if len(header) < 40 or not header.startswith(b'HWP Document File'): raise ValueError('invalid HWP')
        flags = struct.unpack_from('<I', header, 36)[0]
        if flags & 2: raise ValueError('encrypted HWP')
        sections = sorted([path for path in f.listdir() if len(path)==2 and path[0]=='BodyText' and path[1].startswith('Section')])
        output, total = [], 0
        for path in sections:
            stream = f.openstream(path).read(MAX_BYTES + 1)
            if len(stream) > MAX_BYTES: raise ValueError('HWP too large')
            if flags & 1:
                dec = zlib.decompressobj(-15)
                stream = dec.decompress(stream, MAX_UNPACKED + 1)
                if dec.unconsumed_tail or len(stream) > MAX_UNPACKED: raise ValueError('HWP expanded too large')
            total += len(stream)
            if total > MAX_UNPACKED: raise ValueError('HWP expanded too large')
            pos = 0
            while pos + 4 <= len(stream):
                head = struct.unpack_from('<I', stream, pos)[0]; pos += 4
                tag, size = head & 0x3ff, head >> 20
                if size == 0xfff:
                    if pos + 4 > len(stream): raise ValueError('invalid HWP record')
                    size = struct.unpack_from('<I', stream, pos)[0]; pos += 4
                if pos + size > len(stream): raise ValueError('invalid HWP record')
                if tag == 67:
                    output.append(re.sub(r'[\x00-\x08\x0b-\x1f]', ' ', stream[pos:pos+size].decode('utf-16le', errors='ignore')))
                pos += size
        return '\n'.join(output)

def extract_text(name, raw):
    name = name.lower()
    if name.endswith('.pdf'):
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted or len(reader.pages) > 50: raise ValueError('encrypted or long PDF')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    elif name.endswith('.hwpx'): text = extract_hwpx(raw)
    elif name.endswith('.hwp'): text = extract_hwp(raw)
    elif name.endswith('.txt'):
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('cp949')
    else: raise ValueError('unsupported format')
    if not text.strip(): raise ValueError('no readable text')
    return text[:200000]

def fingerprint(row):
    value = (row['attachments'] or '[]') + '\n' + (row['content_text'] or '')
    return hashlib.sha256(value.encode()).hexdigest()

def run(key, signature, body, attachments):
    complete, texts = True, []
    try:
        for attachment in attachments[:5]:
            try: texts.append(extract_text(str(attachment.get('name', '')), download(attachment.get('url', ''))))
            except Exception: complete = False; log.info('첨부파일 분석 실패', exc_info=True)
        if len(attachments) > 5: complete = False
        results = [db.contest_eligibility(t) for t in [body, *texts]]
        definite = [r for r in results if r['kind'] != 'unknown']
        result = definite[0] if complete and definite and all(r == definite[0] for r in definite) else {'kind':'unknown','departments':[]}
        conn = db.connect()
        try:
            conn.execute('INSERT OR REPLACE INTO attachment_analysis(board_id,post_id,signature,result,checked_at) VALUES (?,?,?,?,?)', (*key, signature, json.dumps(result, ensure_ascii=False), int(time.time())))
            conn.commit()
        finally: conn.close()
    except Exception: log.exception('첨부파일 분석 실패')
    finally:
        with lock: inflight.discard((key, signature))

def eligibility(row, conn):
    body = row['content_text'] or ''
    try: attachments = json.loads(row['attachments'] or '[]')
    except (ValueError, TypeError): attachments = []
    if not attachments: return db.contest_eligibility(body)
    signature, key = fingerprint(row), (row['board_id'], row['post_id'])
    cache = conn.execute('SELECT result,checked_at FROM attachment_analysis WHERE board_id=? AND post_id=? AND signature=?', (*key,signature)).fetchone()
    if cache and time.time() - cache['checked_at'] < 86400:
        return json.loads(cache['result'])
    with lock:
        token = (key, signature)
        if token not in inflight and len(inflight) < 40:
            inflight.add(token)
            executor.submit(run, key, signature, body, attachments)
    return {'kind':'pending','departments':[]}
