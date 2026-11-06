import { api, proxied } from './api.js';

// ───────────── 공통 도구 ─────────────
const $ = s => document.querySelector(s);
const el = (tag, text, className) => {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (className) e.className = className;
  return e;
};
const empty = (target, title, detail = '') => {
  target.replaceChildren();
  const box = el('div', null, 'empty');
  box.append(el('strong', title), el('span', detail));
  target.append(box);
};
const CAMPUSES = ['춘천', '삼척', '강릉', '원주'];
const STATUS = { '전체': null, '진행 중': 'ongoing', '마감': 'closed' };
const ADMIN_SITE = location.pathname.endsWith('/admin.html');
// 앱(Capacitor) 안에서 실행 중인지. 브라우저에서 앱 화면을 확인할 때는 주소 뒤에 ?app=1
const IN_APP = !!window.MOABOM_IN_APP || new URLSearchParams(location.search).has('app');
if (IN_APP) document.body.classList.add('in-app');
const CITY_URLS = {춘천:'https://www.chuncheon.go.kr/cityhall/', 삼척:'https://www.samcheok.go.kr/intro/00443/02073.web', 강릉:'https://www.gn.go.kr/', 원주:'https://www.wonju.go.kr/'};
const ROLE_NAMES = { super: '최종 관리자', general: '일반 관리자', department: '학과 관리자' };

const themeToggle = $('#theme-toggle');
function updateThemeButton() {
  const dark = document.documentElement.dataset.theme === 'dark';
  themeToggle.textContent = dark ? '☀ 라이트 모드' : '☾ 다크 모드';
  themeToggle.setAttribute('aria-pressed', String(dark));
  themeToggle.setAttribute('aria-label', dark ? '다크 모드 끄기' : '다크 모드 켜기');
}
themeToggle.addEventListener('click', () => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  try { localStorage.setItem('moabom.theme', dark ? 'dark' : 'light'); } catch (_) {}
  updateThemeButton();
});
updateThemeButton();

const fmtDate = s => (s ? s.slice(0, 10).replaceAll('-', '.') : '-');
/** '2026-11-20T14:00' ~ '2026-11-20T17:00' → '2026.11.20 14:00 ~ 17:00' */
function fmtEvent(start, end) {
  const one = s => s.replace('T', ' ').replaceAll('-', '.');
  if (!start) return '';
  if (!end) return one(start);
  const sameDay = start.slice(0, 10) === end.slice(0, 10) && end.length > 10;
  return `${one(start)} ~ ${sameDay ? end.slice(11) : one(end)}`;
}
const deptLabel = d => `${d.name} (${d.campus}${d.college ? ' · ' + d.college : ''})`;

/**
 * 브라우저 저장소 (DB 아님). 사생활 보호 모드 등에서 막혀 있어도 사이트는 정상 동작하도록 try/catch.
 *   local: 브라우저를 껐다 켜도 유지 (좋아요, 조회 기록, '앞으로 보지 않기' 팝업)
 *   session: 탭을 닫으면 사라짐 (관리자 로그인 키)
 */
const store = {
  get(kind, key, fallback) {
    try { const v = window[`${kind}Storage`].getItem(`moabom.${key}`); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(kind, key, value) {
    try { window[`${kind}Storage`].setItem(`moabom.${key}`, JSON.stringify(value)); } catch { /* 저장 불가 */ }
  },
  remove(kind, key) { try { window[`${kind}Storage`].removeItem(`moabom.${key}`); } catch { /* 무시 */ } },
};
/** 목록형 저장값 (최근 limit개만 유지해서 저장소가 무한히 커지지 않게) */
const storedSet = (key, limit = 500) => ({
  has: id => store.get('local', key, []).includes(id),
  add: id => { const a = store.get('local', key, []).filter(x => x !== id); a.push(id); store.set('local', key, a.slice(-limit)); },
  delete: id => store.set('local', key, store.get('local', key, []).filter(x => x !== id)),
});
const liked = storedSet('liked');            // 내가 좋아요 누른 공고
const viewed = storedSet('viewed', 1000);    // 조회수를 이미 올린 공고 (브라우저당 한 번만 셈)
const hiddenPopups = storedSet('hiddenPopups', 100); // '앞으로 보지 않기' 누른 팝업

// ───────────── 상태 ─────────────
const state = {
  campus: '전체', category: '전체', status: '진행 중', q: '',
  items: [], total: 0, page: 1, loading: false, req: 0, syncing: false,
  key: null, admin: null, depts: [], edit: null, files: [], uploading: 0,
};
// 캠퍼스별 지역 시청 분류: 캠퍼스를 고르면 그 지역 시청 탭만 보이고, '전체'면 모두 보임
const CITY_CATEGORY = { 춘천: '춘천시', 삼척: '삼척시', 강릉: '강릉시', 원주: '원주시' };
const CITY_CATEGORIES = Object.values(CITY_CATEGORY);
const groups = {
  campus: ['전체', ...CAMPUSES],
  category: ['전체', '장학금', '행사', '봉사', '공모', '학사', '채용', ...CITY_CATEGORIES, '학과행사', '기타'],
  status: Object.keys(STATUS),
};
const visibleCategory = c => !CITY_CATEGORIES.includes(c) || state.campus === '전체' || CITY_CATEGORY[state.campus] === c;
const labels = { campus: '캠퍼스', category: '분류', status: '진행 상태' };

// ───────────── 화면 이동 (#home, #department, #admin, #notice/아이디) ─────────────
let listScroll = 0;
let routedOnce = false;
function route() {
  const [page, arg] = location.hash.slice(1).split('/');
  let view = ['home', 'department', 'admin', 'notice', 'mine', 'liked', 'settings'].includes(page) ? page : (ADMIN_SITE ? 'admin' : 'home');
  if (!ADMIN_SITE && view === 'admin') view = 'home';
  if (ADMIN_SITE && !state.admin) view = 'admin';
  if (!IN_APP && ['mine', 'liked', 'settings'].includes(view)) view = 'home';
  // 앱: 최초 실행(사용자 정보 없음)이면 입력 화면부터 / 설정은 입력 화면을 수정 모드로
  if (IN_APP && (!myProfile() || view === 'settings')) {
    if (view === 'settings' || !onboardOpen) openOnboarding(view === 'settings');
    view = 'onboard';
  } else {
    onboardOpen = false;
  }
  document.querySelectorAll('.view').forEach(x => (x.hidden = x.id !== view));
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${view}`));
  document.querySelectorAll('.tabbar a').forEach(a => a.classList.toggle('active', a.dataset.tab === (view === 'onboard' ? 'settings' : view)));
  if (view === 'mine') loadMine();
  if (view === 'liked') loadLiked();
  // 관리자 패널로 돌아오면 목록(중요·팝업·삭제한 공고 등)을 새로 불러옴 — 상세에서 바꾼 내용 반영
  if (view === 'admin' && state.admin && routedOnce) loadAdmin();
  routedOnce = true;
  if (view === 'department' && !$('#my-likes-panel').hidden) loadLikedPosts();
  if (view === 'home' && state.items.length) renderNotices();
  if (view === 'notice' && arg) {
    showDetail(decodeURIComponent(arg));
    window.scrollTo(0, 0);
  } else if (view === 'home') {
    window.scrollTo(0, listScroll); // 상세에서 돌아오면 보던 위치로
  } else {
    window.scrollTo(0, 0);
  }
}
window.addEventListener('hashchange', route);

// ───────────── 전체 공고 ─────────────
function renderFilters() {
  const root = $('#filters');
  root.replaceChildren();
  for (const [group, allValues] of Object.entries(groups)) {
    const values = group === 'category' ? allValues.filter(visibleCategory) : allValues;
    const wrap = el('div', null, 'filter'), chips = el('div', null, 'chips');
    for (const value of values) {
      const b = el('button', value, 'chip' + (state[group] === value ? ' active' : ''));
      b.type = 'button';
      b.setAttribute('aria-pressed', state[group] === value);
      b.addEventListener('click', () => {
        state[group] = value;
        // 다른 캠퍼스로 바꿨는데 이전 지역의 시청 탭이 골라져 있으면 '전체'로
        if (group === 'campus' && !visibleCategory(state.category)) state.category = '전체';
        renderFilters(); renderCity(); loadNotices(true);
      });
      chips.append(b);
    }
    wrap.append(el('strong', labels[group]), chips);
    root.append(wrap);
  }
}

/** 공고 카드 (전체 공고와 내 학과 소식에서 같이 씀). 누르면 상세 화면 */
function noticeCard(n) {
  const isLiked = liked.has(n.id);
  const a = el('a', null, 'card card-link' + (n.important ? ' important' : '') + (isLiked ? ' liked-card' : ''));
  a.href = `#notice/${encodeURIComponent(n.id)}`;
  a.addEventListener('click', () => { if (!location.hash.startsWith('#notice')) listScroll = window.scrollY; });
  const body = el('div', null, 'body'), tags = el('div', null, 'tags');
  if (n.important) tags.append(el('span', '📌 중요', 'tag pin'));
  tags.append(el('span', n.category, 'tag strong'));
  if (isLiked) tags.append(el('span', '♥ 내가 좋아요', 'tag liked-tag'));
  if (n.campuses.length >= 4) tags.append(el('span', '전 캠퍼스', 'tag'));
  else n.campuses.forEach(c => tags.append(el('span', c, 'tag')));
  const meta = n.department
    ? [n.department.name, fmtEvent(n.event_start, n.event_end), n.location].filter(Boolean)
    : [n.board_name, `등록 ${fmtDate(n.posted_at)}`, n.has_attachments ? '첨부파일' : ''].filter(Boolean);
  const metaEl = el('div', meta.join('  ·  '), 'meta');
  if (n.site_views || n.likes) metaEl.append(el('span', `  👁 ${n.site_views || 0}  ♥ ${n.likes || 0}`, 'stats'));
  if (n.match_score != null) tags.append(el('span', `적합도 ${n.match_score}`, 'tag match'));
  body.append(tags, el('h3', n.title));
  // 앱: 사진이 있으면 왼쪽에 사진, 오른쪽에 제목과 (더 작은 글씨의) 내용
  if (IN_APP && n.summary) body.append(el('p', n.summary, 'summary'));
  body.append(metaEl);
  if (IN_APP && n.thumbnail) {
    const img = el('img', null, 'thumb');
    img.src = proxied(n.thumbnail); img.alt = ''; img.loading = 'lazy';
    img.addEventListener('error', () => img.remove());
    a.classList.add('with-thumb');
    a.append(img);
  }
  a.append(body, deadlineBadge(n));
  return a;
}

function deadlineBadge(n) {
  const b = el('div', null, 'badge');
  if (n.status !== 'ongoing' || !n.deadline) {
    b.classList.add('closed');
    b.textContent = '마감';
    return b;
  }
  const [y, m, d] = n.deadline.split('-').map(Number);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(y, m - 1, d) - today) / 86400000);
  b.textContent = days <= 0 ? 'D-DAY' : `D-${days}`;
  if (days <= 3) b.classList.add('urgent');
  b.append(el('small', n.deadline_estimated ? '추정' : `~${m}/${d}`));
  return b;
}

async function loadNotices(reset = false) {
  const req = ++state.req;
  const page = reset ? 1 : state.page + 1;
  if (reset) { state.items = []; state.total = 0; empty($('#notices'), '공고를 불러오는 중이에요'); }
  state.loading = true;
  $('#more').disabled = true;
  const p = new URLSearchParams({ page, size: 20 });
  if (state.campus !== '전체') p.set('campus', state.campus);
  if (state.category !== '전체') p.set('category', state.category);
  if (STATUS[state.status]) p.set('status', STATUS[state.status]);
  if ($('#upload-period').value) p.set('period', $('#upload-period').value);
  if (state.q) p.set('q', state.q);
  p.set('sort', $('#sort-order').value);
  // 앱: 내 정보로 공고마다 적합도를 같이 받음
  if (IN_APP && myProfile()?.device_id) p.set('device_id', myProfile().device_id);
  try {
    const data = await api(`/api/notices?${p}`);
    if (req !== state.req) return; // 필터를 빠르게 바꾼 경우 늦게 온 옛 응답은 버림
    state.items.push(...data.items);
    state.total = data.total;
    state.page = page;
    renderNotices();
  } catch (e) {
    if (req !== state.req) return;
    if (!state.items.length) { $('#count').textContent = ''; empty($('#notices'), '공고를 불러오지 못했어요', e.message); }
  } finally {
    if (req === state.req) { state.loading = false; $('#more').disabled = false; }
  }
}

function renderNotices() {
  const s = state.status === '전체' ? '' : `${state.status} `;
  $('#list-title').textContent = `${s}${state.category === '전체' ? '공고' : state.category + ' 공고'}`;
  $('#count').textContent = `${state.total}건`;
  $('#more-wrap').hidden = state.items.length >= state.total;
  $('#more').textContent = `더 보기 (${state.items.length}/${state.total})`;
  if (!state.items.length) return empty($('#notices'), '표시할 공고가 없어요', '다른 조건으로 검색하거나 잠시 후 다시 확인해 주세요.');
  $('#notices').replaceChildren(...state.items.map(noticeCard));
}

// ───────────── 좌측 배너 (조회수·좋아요·마감 순위) ─────────────
function rankList(target, items, number, emptyText) {
  if (!items.length) { target.replaceChildren(el('li', emptyText, 'none')); return; }
  target.replaceChildren(...items.map((n, i) => {
    const li = el('li'), a = el('a');
    a.href = `#notice/${encodeURIComponent(n.id)}`;
    a.title = n.title;
    a.append(el('span', number(n), 'n'), el('span', String(i + 1), 'ic'), el('span', n.title, 't'));
    li.append(a);
    return li;
  }));
}
function dday(deadline) {
  const [y, m, d] = deadline.split('-').map(Number);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(y, m - 1, d) - today) / 86400000);
  return days <= 0 ? 'D-DAY' : `D-${days}`;
}
async function loadBanner() {
  try {
    const r = await api('/api/rankings?limit=5');
    $('#side-live').textContent = r.total_views ? `누적 조회 ${r.total_views.toLocaleString()}회` : '';
    rankList($('#rank-views'), r.views, n => `${n.site_views}회`, '아직 조회 기록이 없어요');
    rankList($('#rank-likes'), r.likes, n => `♥ ${n.likes}`, '아직 좋아요가 없어요');
    rankList($('#rank-deadline'), r.deadline, n => dday(n.deadline), '마감 예정 공고가 없어요');
  } catch {
    ['#rank-views', '#rank-likes', '#rank-deadline'].forEach(s => $(s).replaceChildren(el('li', '불러오지 못했어요', 'none')));
  }
}

/** 새로고침: 저장된 목록을 바로 다시 보여주고, 뒤에서 학교·시청 사이트의 새 공고를 확인 (서버가 10분에 한 번만 실제로 가져옴) */
async function refreshAll() {
  loadNotices(true);
  loadBanner();
  if (state.syncing) return;
  state.syncing = true;
  $('#sync-status').hidden = false;
  $('#refresh').disabled = true;
  try {
    let s = await api('/api/refresh', { method: 'POST' });
    const waited = s.running;
    for (let i = 0; i < 60 && s.running; i++) { // 3초마다 진행 상태 확인 (최대 약 3분)
      await new Promise(r => setTimeout(r, 3000));
      s = await api('/api/refresh/status');
    }
    if (waited && !s.running) {
      await loadNotices(true);
      if (IN_APP) checkNotifications(); // 새 공고 중 나에게 맞는 게 있으면 알림
      loadBanner();
      if (s.new_count > 0) toast(`새 공고 ${s.new_count}건을 가져왔어요`);
    }
  } catch { /* 새 공고 확인이 실패해도 저장된 목록은 볼 수 있음 */ }
  finally {
    state.syncing = false;
    $('#sync-status').hidden = true;
    $('#refresh').disabled = false;
  }
}

function toast(message) {
  const t = el('div', message, 'toast');
  Object.assign(t.style, { position: 'fixed', left: '50%', bottom: '24px', transform: 'translateX(-50%)', background: '#172439', color: '#fff', padding: '12px 18px', borderRadius: '10px', zIndex: 10, fontSize: '14px' });
  document.body.append(t);
  setTimeout(() => t.remove(), 3500);
}

let searchTimer;
$('#search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); loadNotices(true); }, 350);
});
$('#sort-order').addEventListener('change', () => loadNotices(true));
$('#upload-period').addEventListener('change', () => loadNotices(true));
$('#refresh').addEventListener('click', refreshAll);
$('#more').addEventListener('click', () => loadNotices());
// ───────────── 상세 ─────────────
// 사이트 안에서 들어왔으면 이전 화면으로, 링크로 바로 들어왔으면 전체 공고로
let navigatedInside = false;
window.addEventListener('hashchange', () => { navigatedInside = true; }, { once: true });
$('#back').addEventListener('click', () => {
  if (navigatedInside) history.back(); else location.hash = '#home';
});

/**
 * 학교·시청 본문 HTML은 다른 사람이 쓴 내용이라 그대로 넣지 않고 안전한 태그만 남김.
 * (script, iframe, onclick 같은 속성 등 제거 / 이미지는 중계 주소로 / 링크는 새 창)
 */
const DROP = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'LINK', 'META', 'SVG', 'MATH', 'NOSCRIPT', 'TEMPLATE', 'FRAME', 'FRAMESET', 'BASE']);
const KEEP = new Set(['P', 'BR', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'SPAN', 'DIV', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TD', 'TH', 'CAPTION', 'COLGROUP', 'COL', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'A', 'IMG', 'BLOCKQUOTE', 'HR', 'FONT', 'SUB', 'SUP', 'PRE', 'CODE', 'CENTER', 'SMALL', 'BIG', 'MARK', 'FIGURE', 'FIGCAPTION', 'DL', 'DT', 'DD']);
const ATTRS = new Set(['href', 'src', 'alt', 'title', 'colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'style', 'color', 'size', 'face']);
const safeUrl = (v, extra = []) => { try { const u = new URL(v, location.href); return ['http:', 'https:', ...extra].includes(u.protocol) ? u.href : null; } catch { return null; } };

function sanitize(html) {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  const clean = node => {
    for (const child of [...node.children]) {
      if (DROP.has(child.tagName)) { child.remove(); continue; }
      clean(child);
      if (!KEEP.has(child.tagName)) { child.replaceWith(...child.childNodes); continue; } // 태그만 벗기고 내용은 유지
      for (const attr of [...child.attributes]) {
        const name = attr.name.toLowerCase();
        if (!ATTRS.has(name)) child.removeAttribute(attr.name);
        else if (name === 'style' && /url\(|expression|position\s*:/i.test(attr.value)) child.removeAttribute('style');
      }
      if (child.tagName === 'A') {
        const href = safeUrl(child.getAttribute('href') || '', ['mailto:', 'tel:']);
        if (href) { child.href = href; child.target = '_blank'; child.rel = 'noopener noreferrer'; } else child.removeAttribute('href');
      }
      if (child.tagName === 'IMG') {
        const src = safeUrl(child.getAttribute('src') || '');
        if (src) { child.dataset.orig = src; child.src = proxied(src); child.loading = 'lazy'; child.removeAttribute('width'); child.removeAttribute('height'); } else child.remove();
      }
    }
  };
  clean(doc.body);
  // DOMParser 문서의 이미지는 옮겨도 로딩이 안 될 때가 있어 현재 문서로 복사해서 사용
  const frag = document.createDocumentFragment();
  frag.append(...document.importNode(doc.body, true).childNodes);
  return frag;
}

async function showDetail(id) {
  const root = $('#notice-detail');
  empty(root, '공고를 불러오는 중이에요');
  let n;
  // 관리자 사이트에서는 키를 같이 보내서 숨긴(삭제한) 공고도 열 수 있게
  try { n = await api(`/api/notices/${encodeURIComponent(id)}`, { key: state.key || undefined }); }
  catch (e) { return empty(root, '공고를 불러오지 못했어요', e.message); }
  if (!location.hash.startsWith(`#notice/${encodeURIComponent(id)}`)) return;

  // 조회수: 이 브라우저에서 처음 연 공고만 +1
  if (!viewed.has(n.id)) {
    viewed.add(n.id);
    api(`/api/notices/${encodeURIComponent(n.id)}/view`, { method: 'POST' })
      .then(s => { n.site_views = s.site_views; const v = $('#detail-views'); if (v) v.textContent = `조회 ${s.site_views}`; loadBanner(); })
      .catch(() => viewed.delete(n.id));
  }

  const head = el('div', null, 'detail-head'), tags = el('div', null, 'tags');
  if (n.important) tags.append(el('span', '📌 중요', 'tag pin')); // 관리자 중요 표시 (목록과 똑같이)
  tags.append(el('span', n.category, 'tag strong'));
  (n.campuses.length >= 4 ? ['전 캠퍼스'] : n.campuses).forEach(c => tags.append(el('span', c, 'tag')));
  head.append(tags, el('h1', n.title));

  // 기본 정보
  const info = el('dl', null, 'info');
  const rows = [
    ['게시판', n.board_name],
    ['캠퍼스', n.campuses.length >= 4 ? '전 캠퍼스' : n.campuses.join(', ')],
    n.department && ['학과', n.department.name],
    n.event_start && ['일시', fmtEvent(n.event_start, n.event_end)],
    n.location && ['장소', n.location],
    !n.department && n.author && ['작성자', n.author],
    ['등록일', fmtDate(n.posted_at)],
    ['마감일', n.deadline ? fmtDate(n.deadline) + (n.deadline_estimated ? '  (본문에 기한이 없어 추정)' : '') : '-'],
    n.contact && ['문의', n.contact],
  ].filter(Boolean);
  for (const [k, v] of rows) info.append(el('dt', k), el('dd', v));
  const infoPanel = el('div', null, 'panel');
  infoPanel.append(info);

  // 본문 (원문 서식 유지)
  const content = el('div', null, 'panel content');
  if (n.content_html) content.append(sanitize(n.content_html));
  else content.textContent = n.content_text || '본문이 없습니다. 첨부파일이나 원문을 확인해 주세요.';
  // 본문 HTML 안에 없는 사진(관리자가 올린 사진 등)은 아래에 추가
  // (주소 표기가 조금씩 달라서 정규화한 원본 주소로 비교)
  const shown = new Set([...content.querySelectorAll('img')].map(i => i.dataset.orig));
  for (const src of n.images || []) {
    if (!safeUrl(src) || shown.has(safeUrl(src))) continue;
    const img = el('img'); img.src = proxied(src); img.alt = '첨부 사진'; img.loading = 'lazy';
    content.append(img);
  }

  const parts = [head, detailActions(n)];
  const tools = adminTools(n);
  if (tools) parts.push(tools);
  parts.push(infoPanel, el('h2', '내용', 'sub'), content);
  const fileList = (title, items, cls) => {
    const ul = el('ul', null, cls);
    for (const it of items) {
      const href = safeUrl(it.url);
      if (!href) continue;
      const a = el('a', it.name || it.text || it.url);
      a.href = cls.includes('links') ? href : proxied(href); // 시청 첨부는 중계를 거쳐야 받아짐
      a.target = '_blank'; a.rel = 'noopener noreferrer';
      const li = el('li'); li.append(a); ul.append(li);
    }
    const panel = el('div', null, 'panel'); panel.append(ul);
    parts.push(el('h2', `${title} ${items.length}`, 'sub'), panel);
  };
  if (n.attachments?.length) fileList('첨부파일', n.attachments, 'files');
  if (n.links?.length) fileList('관련 링크', n.links, 'files links');
  if (n.url) {
    const a = el('a', n.source === 'city' ? `${n.board_name} 원문에서 보기 ↗` : '학교 원문에서 보기 ↗', 'button');
    a.href = n.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.style.display = 'inline-block'; a.style.marginTop = '18px';
    parts.push(a);
  }
  root.replaceChildren(...parts);
}

/** 상세 화면 상단: 좋아요 버튼 + 조회수 */
function detailActions(n) {
  const box = el('div', null, 'detail-actions');
  const btn = el('button', null, 'like');
  btn.type = 'button';
  const paint = () => {
    const on = liked.has(n.id);
    btn.classList.toggle('on', on);
    btn.textContent = `${on ? '♥' : '♡'} 좋아요 ${n.likes || 0}`;
    btn.setAttribute('aria-pressed', on);
  };
  btn.addEventListener('click', async () => {
    const on = liked.has(n.id);
    btn.disabled = true;
    try {
      const s = await api(`/api/notices/${encodeURIComponent(n.id)}/like`, { method: on ? 'DELETE' : 'POST' });
      n.likes = s.likes;
      on ? liked.delete(n.id) : liked.add(n.id);
      paint();
      renderNotices();
      loadBanner();
    } catch (err) { toast(err.message); }
    finally { btn.disabled = false; }
  });
  paint();
  const views = el('span', `조회 ${n.site_views || 0}`, 'viewcount');
  views.id = 'detail-views';
  box.append(btn, views);
  return box;
}

/** 관리자로 로그인한 경우에만: 중요 표시·팝업 버튼 */
function adminTools(n) {
  if (!state.key || !state.admin) return null;
  // 학과 관리자는 자기 학과 행사에만 표시 가능 (서버에서도 한 번 더 검사)
  if (state.admin.role === 'department' && n.department?.id !== state.admin.department?.id) return null;
  const box = el('div', null, 'admin-tools');
  box.append(el('strong', '관리자'));
  const pin = el('button', null, 'button secondary'), pop = el('button', null, 'button secondary');
  pin.type = pop.type = 'button';
  const paint = () => {
    pin.textContent = n.important ? '📌 중요 해제' : '📌 중요 표시';
    pin.classList.toggle('on', !!n.important);
    pop.textContent = n.popup ? '팝업 내리기' : '팝업으로 띄우기';
    pop.classList.toggle('on', !!n.popup);
    pop.title = n.images?.length ? '이 게시글의 첫 번째 사진이 접속 팝업으로 떠요' : '사진이 없는 게시글은 팝업으로 띄울 수 없어요';
  };
  const save = async (data, done) => {
    try {
      const r = await api(`/api/admin/notices/${encodeURIComponent(n.id)}/flags`, { method: 'PUT', key: state.key, data });
      Object.assign(n, { important: r.important, popup: r.popup, popup_image: r.popup_image });
      paint();
      toast(done(r));
      loadNotices(true); // 목록 순서(중요 고정) 갱신
    } catch (err) { toast(err.message); }
  };
  pin.addEventListener('click', () => save({ important: !n.important }, r => (r.important ? '같은 분류 목록 맨 위에 고정했어요' : '중요 표시를 해제했어요')));
  pop.addEventListener('click', () => save({ popup: !n.popup }, r => (r.popup ? '사이트 접속 때 팝업으로 떠요' : '팝업을 내렸어요')));
  paint();
  if (n.hidden) {
    // 삭제(숨김)된 공고: 복원만 가능
    box.replaceChildren(el('strong', '삭제된 공고 (사이트에 안 보임)'));
    if (isManager()) box.append(actionButton('복원', () => restoreNotice(n)));
    return box;
  }
  box.append(pin, pop);
  if (ADMIN_SITE) box.append(actionButton('🔔 이 공고로 알림', () => { location.hash = '#admin'; setTimeout(() => openCast(n.id), 0); }));
  // 수정: 학과 행사는 내용 전체, 수집한 공고는 제목·분류·마감일만 (본문은 원문 사이트 것)
  const isEvent = n.source === 'admin';
  if (isEvent || isManager()) {
    box.append(actionButton('✏️ 수정', () => (isEvent ? openPost(n) : openNoticeEdit(n))));
    box.append(actionButton('🗑 삭제', () => deleteNotice(n)));
  }
  return box;
}
function actionButton(label, onClick) {
  const b = el('button', label, 'button secondary');
  b.type = 'button';
  b.addEventListener('click', onClick);
  return b;
}

/** 게시글 삭제: 수집 공고는 숨김(복원 가능), 학과 행사는 완전 삭제 */
async function deleteNotice(n) {
  const msg = n.source === 'admin'
    ? `“${n.title}” 학과 행사를 삭제할까요? 되돌릴 수 없습니다.`
    : `“${n.title}” 공고를 사이트에서 삭제할까요?\n(숨김 처리되며, 관리자 패널의 '삭제한 공고'에서 복원할 수 있어요)`;
  if (!confirm(msg)) return;
  try {
    await api(`/api/admin/notices/${encodeURIComponent(n.id)}`, { method: 'DELETE', key: state.key });
    toast('삭제했어요');
    loadNotices(true); loadBanner();
    if (state.admin) loadAdmin();
    location.hash = '#admin';
  } catch (err) { toast(err.message); }
}
async function restoreNotice(n) {
  try {
    await api(`/api/admin/notices/${encodeURIComponent(n.id)}/restore`, { method: 'POST', key: state.key });
    toast('복원했어요');
    loadNotices(true); loadBanner(); loadAdmin();
    showDetail(n.id);
  } catch (err) { toast(err.message); }
}

/** 수집한 공고 수정 (제목·분류·마감일). 비우면 원래 값으로 돌아감 */
function openNoticeEdit(n) {
  const dlg = $('#notice-edit-dialog');
  if (!dlg) return;
  state.editNotice = n;
  $('#edit-title').value = n.title;
  $('#edit-category').replaceChildren(...groups.category.filter(c => c !== '전체').map(c => new Option(c, c, false, c === n.category)));
  $('#edit-deadline').value = n.deadline || '';
  $('#edit-error').textContent = '';
  dlg.showModal();
}
$('#notice-edit-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const n = state.editNotice;
  try {
    await api(`/api/admin/notices/${encodeURIComponent(n.id)}`, { method: 'PUT', key: state.key, data: {
      title: $('#edit-title').value.trim() || null,
      category: $('#edit-category').value || null,
      deadline: $('#edit-deadline').value || null,
    } });
    $('#notice-edit-dialog').close();
    toast('수정했어요');
    loadNotices(true); loadBanner();
    showDetail(n.id);
  } catch (err) { $('#edit-error').textContent = err.message; }
});
$('#edit-reset')?.addEventListener('click', async () => {
  const n = state.editNotice;
  if (!confirm('관리자가 고친 제목·분류·마감일을 지우고 원래 값으로 되돌릴까요?')) return;
  try {
    await api(`/api/admin/notices/${encodeURIComponent(n.id)}`, { method: 'PUT', key: state.key, data: { title: null, category: null, deadline: null } });
    $('#notice-edit-dialog').close();
    toast('원래 값으로 되돌렸어요');
    loadNotices(true);
    showDetail(n.id);
  } catch (err) { $('#edit-error').textContent = err.message; }
});

// ───────────── 접속 팝업 ─────────────
// '닫기'는 이번 방문에서만 닫고, '앞으로 보지 않기'는 브라우저에 기억해서 다시 띄우지 않음
let popupQueue = [];
async function showPopups() {
  try {
    const list = await api('/api/popups');
    popupQueue = list.filter(p => !hiddenPopups.has(p.key));
    nextPopup();
  } catch { /* 팝업은 못 띄워도 사이트 이용엔 지장 없음 */ }
}
function nextPopup() {
  const p = popupQueue.shift();
  const dlg = $('#popup-dialog');
  if (!p) { if (dlg.open) dlg.close(); return; }
  $('#popup-image').src = proxied(p.image);
  $('#popup-image').alt = p.title;
  $('#popup-title').textContent = p.title;
  $('#popup-link').href = `#notice/${encodeURIComponent(p.id)}`;
  dlg.dataset.key = p.key;
  if (!dlg.open) dlg.showModal();
}
$('#popup-close').addEventListener('click', nextPopup);
$('#popup-never').addEventListener('click', () => { hiddenPopups.add($('#popup-dialog').dataset.key); nextPopup(); });
$('#popup-link').addEventListener('click', () => { popupQueue = []; $('#popup-dialog').close(); });

let likedRequest = 0;
async function loadLikedPosts() {
  const root = $('#liked-posts');
  const request = ++likedRequest;
  const raw = store.get('local', 'liked', []);
  const ids = Array.isArray(raw) ? raw.filter(id => typeof id === 'string' && /^\d{1,9}-\d{1,12}$/.test(id)).slice(-500).reverse() : [];
  if (!ids.length) return empty(root, '좋아요한 공고가 없습니다', '공고 상세 화면에서 좋아요를 눌러 보세요.');
  empty(root, '좋아요한 공고를 불러오는 중이에요');
  try {
    // by-ids: 이전 서버와 새 서버 모두에 있는 주소 (앱 하단 '좋아요' 탭과 같은 방식)
    const data = await api(`/api/notices/by-ids?${new URLSearchParams({ids: ids.join(',')})}`);
    if (request !== likedRequest) return;
    if (!data.items.length) return empty(root, '표시할 수 있는 공고가 없습니다', '삭제되었거나 숨겨진 공고일 수 있습니다.');
    root.replaceChildren(...data.items.map(noticeCard));
    if (data.items.length < ids.length) root.append(el('p', '일부 공고는 삭제되었거나 숨겨져 표시되지 않습니다.', 'meta'));
  } catch (err) { if (request === likedRequest) empty(root, '좋아요 목록을 불러오지 못했어요', err.message); }
}
function setMyTab(showLikes) {
  $('#my-department-panel').hidden = showLikes; $('#my-likes-panel').hidden = !showLikes;
  for (const [id, active] of [['#my-department-tab', !showLikes], ['#my-likes-tab', showLikes]]) {
    $(id).classList.toggle('active', active); $(id).setAttribute('aria-selected', String(active));
  }
  if (showLikes) loadLikedPosts();
}
$('#my-department-tab').addEventListener('click', () => setMyTab(false));
$('#my-likes-tab').addEventListener('click', () => setMyTab(true));
window.addEventListener('storage', e => { if (e.key === 'moabom.liked') { renderNotices(); if (!$('#my-likes-panel').hidden) loadLikedPosts(); } });

// ───────────── 내 학과 소식 ─────────────
let departmentChoices = [];
let departmentRequest = 0;
function filterDepartments() {
  const campus = $('#department-campus').value, field = $('#department-field').value;
  const select = $('#department-select');
  select.replaceChildren(new Option('학과를 선택하세요', ''));
  select.disabled = !campus || !field;
  $('#department-posts').replaceChildren();
  if (campus && field) {
    // 서버에 계열 정보가 없으면(이전 서버) 캠퍼스만으로 거름
    const hasField = departmentChoices.some(d => d.field);
    const matching = departmentChoices.filter(d => d.campus === campus && (!hasField || (d.field || '기타·미분류') === field));
    matching.forEach(d => select.add(new Option(`${deptLabel(d)} · 행사 ${d.event_count}`, d.id)));
    if (!matching.length) empty($('#department-posts'), '해당 계열에 등록된 학과가 없습니다.');
  }
}
$('#department-campus').addEventListener('change', e => { $('#department-field').value=''; $('#department-field').disabled=!e.target.value; filterDepartments(); });
$('#department-field').addEventListener('change', filterDepartments);
$('#department-enabled').addEventListener('change', async e => {
  const request = ++departmentRequest;
  $('#department-options').hidden = !e.target.checked;
  $('#department-campus').value=''; $('#department-field').value=''; $('#department-field').disabled=true;
  filterDepartments();
  if (!e.target.checked) return;
  try {
    const choices = await api('/api/departments');
    if (request !== departmentRequest) return;
    departmentChoices = choices; filterDepartments();
  } catch (err) {
    if (request === departmentRequest) empty($('#department-posts'), '학과 목록을 불러오지 못했어요', err.message);
  }
});
$('#department-select').addEventListener('change', async e => {
  const root = $('#department-posts'), selected = e.target.value;
  if (!selected) { root.replaceChildren(); return; }
  empty(root, '학과 소식을 불러오는 중이에요');
  try {
    const data = await api(`/api/notices?department=${selected}&size=50`);
    if ($('#department-select').value !== selected || !$('#department-enabled').checked) return;
    if (!data.items.length) return empty(root, '등록된 학과 소식이 없습니다', '학과 사무실에서 소식을 올리면 여기에 보여요.');
    root.replaceChildren(...data.items.map(noticeCard));
  } catch (err) { empty(root, '학과 소식을 불러오지 못했어요', err.message); }
});

// ───────────── 관리자 패널 ─────────────
const isManager = () => ['super', 'general'].includes(state.admin?.role);
const adminApi = (path, opts = {}) => api(path, { ...opts, key: state.key });

$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  $('#login-error').textContent = '';
  const key = $('#admin-key').value.trim();
  try {
    applyAdmin(key, await api('/api/admin/me', { key }));
    $('#admin-key').value = '';
  } catch (err) { $('#login-error').textContent = err.message; }
});

/** 로그인 상태 적용. 키는 이 탭에서만 기억 (탭을 닫으면 로그아웃) */
function applyAdmin(key, info) {
  state.key = key;
  state.admin = info;
  store.set('session', 'adminKey', key);
  $('#login-panel').hidden = true;
  $('#admin-panel').hidden = false;
  const d = info.department;
  $('#admin-role').textContent = `관리 권한: ${ROLE_NAMES[info.role]}${d ? ' · ' + deptLabel(d) : ''}`;
  document.querySelectorAll('.manager-only').forEach(x => (x.hidden = !isManager()));
  loadAdmin();
  route(); // 보고 있던 상세 화면에 관리자 버튼 표시
}
async function restoreAdmin() {
  const key = store.get('session', 'adminKey', null);
  if (!key) return;
  try { applyAdmin(key, await api('/api/admin/me', { key })); }
  catch { store.remove('session', 'adminKey'); } // 폐기된 키 등
}
$('#logout').addEventListener('click', () => {
  state.key = null; state.admin = null;
  store.remove('session', 'adminKey');
  $('#admin-panel').hidden = true; $('#login-panel').hidden = false;
  ['#admin-posts', '#admin-departments', '#admin-keys'].forEach(s => $(s).replaceChildren());
});

async function loadAdmin() {
  try { state.depts = await api('/api/departments'); } catch { state.depts = []; }
  loadEvents();
  loadFlagged('important'); loadFlagged('popup');
  if (isManager()) loadHidden();
  if (isManager()) { renderDepartments(); loadKeys(); }
  loadCasts();
}

// ── 관리자 알림 보내기 ──
const CAST_TARGET = { all: '전체', campus: '캠퍼스', department: '학과' };
async function loadCasts() {
  const root = $('#admin-casts');
  if (!root) return;
  try {
    const list = await adminApi('/api/admin/broadcasts');
    $('#cast-count').textContent = list.length ? `${list.length}건` : '';
    if (!list.length) return empty(root, '보낸 알림이 없습니다', "'🔔 알림 보내기'로 앱 사용자에게 알림을 보낼 수 있어요.");
    root.replaceChildren(...list.map(b => row(
      b.title,
      [b.target === 'campus' ? `${b.campus} 캠퍼스` : b.target === 'department' ? (b.department || '학과') : '전체',
       b.user_type ? { student: '학생', resident: '주민' }[b.user_type] : null,
       `받은 사람 ${b.read_count}명`, fmtDate(b.created_at)].filter(Boolean).join('  ·  '),
      [...(b.notice_id ? [['공고 보기', () => { location.hash = `#notice/${encodeURIComponent(b.notice_id)}`; }]] : []),
       ['취소', async () => {
         if (!confirm(`“${b.title}” 알림을 취소할까요? 아직 받지 않은 사용자에게는 가지 않아요.`)) return;
         try { await adminApi(`/api/admin/broadcasts/${b.id}`, { method: 'DELETE' }); loadCasts(); } catch (err) { toast(err.message); }
       }]],
    )));
  } catch (err) { empty(root, '알림 목록을 불러오지 못했어요', err.message); }
}
function openCast(noticeId = '') {
  $('#cast-form').reset();
  $('#cast-error').textContent = '';
  $('#cast-notice').value = noticeId;
  const deptAdmin = state.admin.role === 'department';
  $('#cast-target-group').hidden = deptAdmin;
  $('#cast-dept-note').hidden = !deptAdmin;
  $('#cast-dept').replaceChildren(...state.depts.map(d => new Option(deptLabel(d), d.id)));
  paintCastTarget();
  $('#cast-dialog').showModal();
}
function paintCastTarget() {
  const t = $('#cast-target').value;
  $('#cast-campus-group').hidden = t !== 'campus';
  $('#cast-dept-group').hidden = t !== 'department';
  $('#cast-type-group').hidden = t === 'department';
}
$('#send-cast')?.addEventListener('click', () => openCast());
$('#cast-target')?.addEventListener('change', paintCastTarget);
$('#cast-form')?.addEventListener('submit', async e => {
  e.preventDefault();
  const target = $('#cast-target').value;
  try {
    await adminApi('/api/admin/broadcasts', { method: 'POST', data: {
      title: $('#cast-title').value.trim(), body: $('#cast-body').value.trim(), target,
      campus: target === 'campus' ? $('#cast-campus').value : null,
      department_id: target === 'department' ? Number($('#cast-dept').value) || null : null,
      user_type: target === 'department' ? null : ($('#cast-type').value || null),
      notice_id: $('#cast-notice').value.trim() || null,
    } });
    $('#cast-dialog').close();
    toast('알림을 보냈어요. 앱이 다음에 확인할 때 휴대폰에 떠요.');
    loadCasts();
  } catch (err) { $('#cast-error').textContent = err.message; }
});

function row(title, meta, buttons) {
  const card = el('div', null, 'card'), r = el('div', null, 'row'), info = el('div'), actions = el('div', null, 'actions');
  info.append(el('h3', title), el('span', meta, 'meta'));
  for (const [label, fn] of buttons) {
    const b = el('button', label, 'button secondary'); b.type = 'button'; b.addEventListener('click', fn); actions.append(b);
  }
  r.append(info, actions); card.append(r);
  return card;
}

/** 관리자가 삭제(숨김)한 수집 공고 목록 — 복원 버튼 포함 */
async function loadHidden() {
  const root = $('#admin-hidden');
  if (!root) return;
  try {
    const data = await api('/api/notices?hidden=true&size=100', { key: state.key });
    $('#hidden-count').textContent = data.total ? `${data.total}건` : '';
    if (!data.items.length) return empty(root, '삭제한 공고가 없습니다');
    root.replaceChildren(...data.items.map(n => row(
      n.title,
      [n.board_name, `등록 ${fmtDate(n.posted_at)}`].join('  ·  '),
      [['보기', () => { location.hash = `#notice/${encodeURIComponent(n.id)}`; }], ['복원', () => restoreNotice(n)]],
    )));
  } catch (err) { empty(root, '삭제한 공고를 불러오지 못했어요', err.message); }
}

async function loadFlagged(flag) {
  const root = $(flag === 'important' ? '#admin-important' : '#admin-popup');
  empty(root, '공고를 불러오는 중이에요');
  try {
    const params = new URLSearchParams({flag, size: '100'});
    if (state.admin.role === 'department') params.set('department', state.admin.department.id);
    const data = await api(`/api/notices?${params}`);
    if (!data.items.length) return empty(root, flag === 'important' ? '중요 표시된 공고가 없습니다.' : '팝업 표시된 공고가 없습니다.');
    root.replaceChildren(...data.items.map(n => {
      const card = noticeCard(n);
      return card;
    }));
    if (data.total > data.items.length) root.append(el('p', `처음 ${data.items.length}건을 표시합니다.`, 'meta'));
  } catch (err) { empty(root, '공고를 불러오지 못했어요', err.message); }
}

async function loadEvents() {
  const root = $('#admin-posts');
  empty(root, '학과 행사를 불러오는 중이에요');
  try {
    const events = await adminApi('/api/admin/events');
    if (!events.length) return empty(root, '등록된 학과 행사가 없습니다', '[+ 학과 행사 작성]으로 올려 보세요.');
    root.replaceChildren(...events.map(ev => row(
      ev.title,
      [ev.department?.name, fmtEvent(ev.event_start, ev.event_end), ev.status === 'ongoing' ? '진행 중' : '마감'].filter(Boolean).join('  ·  '),
      [['수정', () => openPost(ev)], ['삭제', () => deleteEvent(ev)]],
    )));
  } catch (err) { empty(root, '학과 행사를 불러오지 못했어요', err.message); }
}

async function deleteEvent(ev) {
  if (!confirm(`“${ev.title}” 행사를 삭제할까요?`)) return;
  try { await adminApi(`/api/admin/events/${ev.id.split('-')[1]}`, { method: 'DELETE' }); loadAdmin(); }
  catch (err) { alert(err.message); }
}

function renderDepartments() {
  const root = $('#admin-departments');
  $('#dept-count').textContent = `${state.depts.length}개`;
  if (!state.depts.length) return empty(root, '등록된 학과가 없습니다', '[학과 추가]로 먼저 학과를 만들어 주세요.');
  root.replaceChildren(...state.depts.map(d => row(deptLabel(d), `행사 ${d.event_count}건`, [['삭제', async () => {
    if (!confirm(`${d.name}을(를) 삭제할까요?`)) return;
    try { await adminApi(`/api/admin/departments/${d.id}`, { method: 'DELETE' }); loadAdmin(); }
    catch (err) { alert(err.message); }
  }]])));
}

async function loadKeys() {
  const root = $('#admin-keys');
  try {
    const keys = await adminApi('/api/admin/keys');
    if (!keys.length) return empty(root, '발급된 키가 없습니다');
    const name = id => state.depts.find(d => d.id === id)?.name || '';
    root.replaceChildren(...keys.map(k => row(
      k.role === 'general' ? '일반 관리자' : `학과 관리자 · ${name(k.department_id)}`,
      [k.label, `발급 ${fmtDate(k.created_at)}`].filter(Boolean).join('  ·  '),
      [['폐기', async () => {
        if (!confirm('이 키로는 더 이상 로그인할 수 없게 됩니다. 폐기할까요?')) return;
        try { await adminApi(`/api/admin/keys/${k.id}`, { method: 'DELETE' }); loadAdmin(); }
        catch (err) { alert(err.message); }
      }]],
    )));
  } catch (err) { empty(root, '키 목록을 불러오지 못했어요', err.message); }
}

// 학과 행사 작성/수정
function fillDeptSelect(select, selectedId) {
  const own = state.admin.role === 'department' ? state.admin.department : null;
  const list = own ? [own] : state.depts;
  select.replaceChildren(...list.map(d => new Option(deptLabel(d), d.id, false, d.id === (own?.id ?? selectedId))));
  select.disabled = !!own;
}
function renderFiles() {
  const ul = $('#post-file-list');
  ul.replaceChildren(...state.files.map((f, i) => {
    const li = el('li'), x = el('button', '삭제');
    x.type = 'button';
    x.addEventListener('click', () => { state.files.splice(i, 1); renderFiles(); });
    li.append(el('span', `${f.is_image ? '🖼' : '📎'} ${f.name}`), x);
    return li;
  }));
  if (state.uploading) ul.append(el('li', `올리는 중… (${state.uploading})`));
  $('#post-save').disabled = state.uploading > 0;
}
function openPost(ev = null) {
  if (isManager() && !state.depts.length) return alert('먼저 [학과 추가]로 학과를 만들어 주세요.');
  state.edit = ev;
  $('#post-dialog-title').textContent = ev ? '학과 행사 수정' : '학과 행사 작성';
  fillDeptSelect($('#post-department'), ev?.department?.id);
  const dt = v => (!v ? '' : v.length === 10 ? `${v}T00:00` : v.slice(0, 16));
  $('#post-title').value = ev?.title || '';
  $('#post-body').value = ev?.content_text || '';
  $('#post-start').value = dt(ev?.event_start);
  $('#post-end').value = dt(ev?.event_end);
  $('#post-location').value = ev?.location || '';
  $('#post-deadline').value = ev?.deadline || '';
  $('#post-contact').value = ev?.contact || '';
  $('#post-link').value = ev?.links?.[0]?.url || '';
  state.files = [
    ...(ev?.images || []).map(url => ({ name: decodeURIComponent(url.split('/').pop()), url, is_image: true })),
    ...(ev?.attachments || []).map(a => ({ name: a.name, url: a.url, is_image: false })),
  ];
  $('#post-files').value = '';
  renderFiles();
  $('#post-error').textContent = '';
  $('#post-dialog').showModal();
}
$('#post-files').addEventListener('change', async e => {
  for (const file of e.target.files) {
    state.uploading++; renderFiles();
    const form = new FormData(); form.append('file', file);
    try { state.files.push(await adminApi('/api/admin/uploads', { method: 'POST', form })); }
    catch (err) { $('#post-error').textContent = `${file.name}: ${err.message}`; }
    finally { state.uploading--; renderFiles(); }
  }
  e.target.value = '';
});
$('#add-post').addEventListener('click', () => openPost());
$('#post-form').addEventListener('submit', async e => {
  e.preventDefault();
  const v = s => $(s).value.trim() || null;
  const link = v('#post-link');
  const data = {
    department_id: Number($('#post-department').value) || null,
    title: $('#post-title').value.trim(),
    content_text: $('#post-body').value,
    event_start: v('#post-start'), event_end: v('#post-end'),
    location: v('#post-location'), deadline: v('#post-deadline'), contact: v('#post-contact'),
    links: link ? [{ text: '신청·안내 링크', url: link }] : [],
    images: state.files.filter(f => f.is_image).map(f => f.url),
    attachments: state.files.filter(f => !f.is_image).map(f => ({ name: f.name, url: f.url })),
  };
  try {
    const pid = state.edit?.id.split('-')[1];
    await adminApi(pid ? `/api/admin/events/${pid}` : '/api/admin/events', { method: pid ? 'PUT' : 'POST', data });
    $('#post-dialog').close();
    toast(pid ? '수정했어요' : '학과 행사를 등록했어요');
    loadAdmin();
  } catch (err) { $('#post-error').textContent = err.message; }
});

// 학과 추가
$('#add-department').addEventListener('click', () => { $('#dept-form').reset(); $('#dept-error').textContent = ''; $('#dept-dialog').showModal(); });
$('#dept-form').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    await adminApi('/api/admin/departments', { method: 'POST', data: { name: $('#dept-name').value.trim(), college: $('#dept-college').value.trim() || null, campus: $('#dept-campus').value } });
    $('#dept-dialog').close();
    loadAdmin();
  } catch (err) { $('#dept-error').textContent = err.message; }
});

// 관리자 키 발급
$('#new-key').addEventListener('click', () => {
  if (!state.depts.length && state.admin.role !== 'super') return alert('학과 관리자 키를 만들려면 먼저 학과를 추가해 주세요.');
  $('#key-form').reset();
  $('#key-error').textContent = '';
  $('#key-role').querySelector('[value=general]').hidden = state.admin.role !== 'super';
  $('#key-department').replaceChildren(...state.depts.map(d => new Option(deptLabel(d), d.id)));
  $('#key-department-group').hidden = false;
  $('#key-dialog').showModal();
});
$('#key-role').addEventListener('change', e => ($('#key-department-group').hidden = e.target.value !== 'department'));
$('#key-form').addEventListener('submit', async e => {
  e.preventDefault();
  const role = $('#key-role').value;
  try {
    const data = await adminApi('/api/admin/keys', { method: 'POST', data: {
      role, department_id: role === 'department' ? Number($('#key-department').value) || null : null, label: $('#key-label').value.trim() || null,
    } });
    $('#key-dialog').close();
    $('#issued-key').textContent = data.key;
    $('#issued-dialog').showModal();
    loadKeys();
  } catch (err) { $('#key-error').textContent = err.message; }
});
$('#copy-key').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#issued-key').textContent); toast('복사했어요'); } catch { /* 직접 선택해서 복사 */ }
});
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => document.getElementById(b.dataset.close).close()));

// ───────────── 시작 ─────────────
/** 캠퍼스를 고르면 분류 아래에 '○○시청 공모·행사는 [○○시] 탭' 안내와 시청 사이트 링크 */
function renderCity() {
  const root = $('#city-link');
  root.replaceChildren();
  root.hidden = state.campus === '전체';
  if (root.hidden) return;
  root.append(el('span', `${state.campus}시청의 시민·대학생 대상 공모·행사는 '${CITY_CATEGORY[state.campus]}' 탭에서 볼 수 있어요 · `));
  const a = el('a', '시청 공식 사이트 ↗');
  a.href = CITY_URLS[state.campus]; a.target = '_blank'; a.rel = 'noopener noreferrer';
  root.append(a);
}
// ═════════════════════════ 앱 전용 기능 ═════════════════════════
// 사용자 정보(최초 실행·설정) / 나에게 맞는 공고 / 휴대폰 알림
// 사용자 정보는 이 기기(앱 저장소)와 서버(적합도 계산용)에 저장. 이름·연락처는 받지 않음.

let onboardOpen = false;
const myProfile = () => store.get('local', 'profile', null);   // {device_id, profile}
let profileOptions = {
  campuses: CAMPUSES, regions: [...CAMPUSES, '기타'], grades: [1, 2, 3, 4, 5, 6],
  genders: [{ key: 'M', label: '남성' }, { key: 'F', label: '여성' }, { key: 'N', label: '밝히지 않음' }],
  topics: ['장학금', '행사·축제', '공모전', '교육·강좌', '취업·채용', '학사', '봉사·대외활동', '창업', '복지·지원', '문화·체육'],
};
let obDepts = null; // 사용자 정보 입력 화면의 학과 목록 (한 번만 불러옴)
const ob = { step: 'type', history: [], data: {}, edit: false, error: '' };

function openOnboarding(edit) {
  onboardOpen = true;
  const saved = myProfile();
  ob.edit = edit && !!saved;
  ob.data = saved ? { ...saved.profile, role: 'user' } : { role: 'user', interests: [] };
  ob.step = 'type'; // 앱에는 관리자 기능 없음 (관리는 관리자 사이트에서)
  ob.history = [];
  ob.error = '';
  renderOnboarding();
}

/** 단계 이동. 돌아가기를 위해 지나온 단계를 기억 */
function goStep(step) { ob.history.push(ob.step); ob.step = step; ob.error = ''; renderOnboarding(); }
function goBack() {
  if (ob.history.length) { ob.step = ob.history.pop(); ob.error = ''; renderOnboarding(); return; }
  if (ob.edit) { onboardOpen = false; location.hash = '#home'; } // 설정에서 들어왔으면 취소하고 돌아감
}

function choiceRow(options, selected, onPick, multi = false) {
  const wrap = el('div', null, 'chips ob-chips');
  for (const o of options) {
    const key = typeof o === 'object' ? o.key : o, label = typeof o === 'object' ? o.label : String(o);
    const on = multi ? (selected || []).includes(key) : selected === key;
    const b = el('button', label, 'chip' + (on ? ' active' : ''));
    b.type = 'button';
    b.setAttribute('aria-pressed', on);
    b.addEventListener('click', () => onPick(key));
    wrap.append(b);
  }
  return wrap;
}
function bigChoice(icon, title, desc, onPick, selected) {
  const b = el('button', null, 'ob-big' + (selected ? ' active' : ''));
  b.type = 'button';
  b.append(el('span', icon, 'ob-icon'), el('strong', title), el('small', desc));
  b.addEventListener('click', onPick);
  return b;
}
function field(label, node) { const w = el('div', null, 'ob-field'); w.append(el('label', label), node); return w; }

function renderOnboarding() {
  const root = $('#onboard-body'), d = ob.data;
  root.replaceChildren();
  const steps = ['type', d.user_type === 'resident' ? 'resident' : 'student', 'interests'];
  const idx = Math.max(0, steps.indexOf(ob.step));
  root.append(el('p', `${ob.edit ? '사용자 정보 수정' : '처음 오셨네요! 맞춤 공고를 위해 알려주세요'} · ${idx + 1}/${steps.length}`, 'ob-progress'));
  const body = el('div', null, 'ob-step');
  let next = null; // 다음 버튼 동작 (없으면 버튼 숨김)

  if (ob.step === 'type') {
    body.append(el('h2', '어떤 분이신가요?'));
    body.append(
      bigChoice('🎓', '학생', '강원대 재학생', () => { d.user_type = 'student'; goStep('student'); }, d.user_type === 'student'),
      bigChoice('🏘', '주민', '지역 주민 (시청 공모·행사 위주)', () => { d.user_type = 'resident'; goStep('resident'); }, d.user_type === 'resident'),
    );
  } else if (ob.step === 'student') {
    body.append(el('h2', '학생 정보'));
    // 캠퍼스 → 계열 → 학과 순서로 고름 ('내 소식'의 학과 선택과 같은 학교 공식 학과 목록)
    body.append(field('어느 캠퍼스 학생인가요?', choiceRow(profileOptions.campuses, d.campus, v => {
      if (d.campus !== v) { d.field = null; d.department = ''; }
      d.campus = v; renderOnboarding();
    })));
    if (!obDepts) {
      body.append(el('p', '학과 목록을 불러오는 중이에요…', 'meta'));
      api('/api/departments').then(ds => { obDepts = ds; renderOnboarding(); }).catch(() => { obDepts = []; renderOnboarding(); });
    } else if (d.campus) {
      const inCampus = obDepts.filter(x => x.campus === d.campus);
      if (!inCampus.length) {
        // 서버에 학과 목록이 없으면 직접 입력
        const dept = el('input'); dept.placeholder = '예: 컴퓨터공학과'; dept.value = d.department || ''; dept.maxLength = 50;
        dept.addEventListener('input', () => (d.department = dept.value));
        body.append(field('학과', dept));
      } else {
        const fieldOf = x => x.field || '기타·미분류';
        // 저장된 학과로 수정 화면을 열면 계열을 자동으로 맞춤
        if (!d.field && d.department) d.field = fieldOf(inCampus.find(x => x.name === d.department) || {}) || null;
        const fields = ['인문계', '자연계', '공대', '예체능', '기타·미분류'].filter(f => inCampus.some(x => fieldOf(x) === f));
        body.append(field('계열', choiceRow(fields, d.field, v => {
          if (d.field !== v) d.department = '';
          d.field = v; renderOnboarding();
        })));
        if (d.field) {
          const sel = el('select');
          sel.add(new Option('학과를 선택하세요', ''));
          inCampus.filter(x => fieldOf(x) === d.field).forEach(x => sel.add(new Option(x.college ? `${x.name} (${x.college})` : x.name, x.name, false, x.name === d.department)));
          sel.addEventListener('change', () => (d.department = sel.value));
          body.append(field('학과', sel));
        }
      }
    }
    body.append(field('몇 학년인가요?', choiceRow(profileOptions.grades.map(g => ({ key: g, label: `${g}학년` })), d.grade, v => { d.grade = v; renderOnboarding(); })));
    next = () => (d.campus ? goStep('interests') : (ob.error = '캠퍼스를 골라 주세요', renderOnboarding()));
  } else if (ob.step === 'resident') {
    body.append(el('h2', '주민 정보'));
    body.append(field('어느 지역 주민인가요?', choiceRow(profileOptions.regions, d.region, v => { d.region = v; renderOnboarding(); })));
    body.append(field('성별', choiceRow(profileOptions.genders, d.gender, v => { d.gender = v; renderOnboarding(); })));
    const age = el('input'); age.type = 'number'; age.min = 1; age.max = 120; age.inputMode = 'numeric'; age.placeholder = '예: 27';
    age.value = d.age || '';
    age.addEventListener('input', () => (d.age = age.value ? Number(age.value) : null));
    body.append(field('나이', age));
    next = () => (d.region ? goStep('interests') : (ob.error = '지역을 골라 주세요', renderOnboarding()));
  } else if (ob.step === 'interests') {
    body.append(el('h2', '관심 있는 주제를 골라 주세요'), el('p', '여러 개 고를 수 있어요. 고른 주제에 맞는 공고를 알려드려요.', 'meta'));
    body.append(choiceRow(profileOptions.topics, d.interests, v => {
      d.interests = d.interests.includes(v) ? d.interests.filter(x => x !== v) : [...d.interests, v];
      renderOnboarding();
    }, true));
    next = saveProfile;
  }
  root.append(body);
  if (ob.error) root.append(el('p', ob.error, 'error'));

  const actions = el('div', null, 'ob-actions');
  if (ob.history.length || ob.edit) {
    const back = el('button', '← 돌아가기', 'button secondary'); back.type = 'button';
    back.addEventListener('click', goBack);
    actions.append(back);
  }
  if (next) {
    const nb = el('button', ob.step === 'interests' ? '저장하고 시작하기' : '다음', 'button'); nb.type = 'button';
    nb.addEventListener('click', next);
    actions.append(nb);
  }
  root.append(actions);
}

async function saveProfile() {
  const d = ob.data, saved = myProfile();
  const body = { device_id: saved?.device_id, role: 'user',
    user_type: d.user_type, interests: d.interests || [] };
  if (d.user_type === 'student') Object.assign(body, { campus: d.campus, department: d.department || '', grade: d.grade || null });
  else Object.assign(body, { region: d.region, gender: d.gender || null, age: d.age || null });
  try {
    const r = await api('/api/profiles', { method: 'POST', data: body });
    store.set('local', 'profile', { device_id: r.device_id, profile: r.profile });
    onboardOpen = false;
    toast(ob.edit ? '사용자 정보를 저장했어요' : '준비됐어요! 나에게 맞는 공고를 보여드릴게요');
    location.hash = '#mine';
    if (!ob.edit) setupNotifications();
  } catch (err) { ob.error = err.message; renderOnboarding(); }
}

// ── 나에게 맞는 공고 ──
async function loadMine() {
  const root = $('#mine-list'), saved = myProfile();
  if (!saved) return;
  store.set('local', 'mineBadge', 0); paintBadge();
  const p = saved.profile;
  const who = p.user_type === 'student'
    ? `${p.campus}캠퍼스${p.department ? ' ' + p.department : ''}${p.grade ? ' ' + p.grade + '학년' : ''} 학생`
    : `${p.region} 주민`;
  $('#mine-desc').textContent = `${who} · 관심: ${(p.interests || []).join(', ') || '없음'} — 적합도가 높은, 지금 신청할 수 있는 공고예요.`;
  empty(root, '맞춤 공고를 고르는 중이에요');
  try {
    const data = await api(`/api/me/matches?device_id=${encodeURIComponent(saved.device_id)}`);
    if (!data.items.length) return empty(root, '아직 딱 맞는 공고가 없어요', '관심 주제를 더 고르면 더 많이 찾아드려요 (⚙ 설정)');
    root.replaceChildren(...data.items.map(noticeCard));
  } catch (err) {
    if (/프로필이 없습니다/.test(err.message)) { store.remove('local', 'profile'); route(); return; } // 서버에서 지워진 경우 다시 입력
    empty(root, '맞춤 공고를 불러오지 못했어요', err.message);
  }
}

// ── 좋아요한 공고 (좋아요 목록은 이 휴대폰에 저장, 내용은 서버에서 한 번에 가져옴) ──
async function loadLiked() {
  const root = $('#liked-list');
  const ids = store.get('local', 'liked', []).slice().reverse(); // 최근에 누른 것부터
  if (!ids.length) return empty(root, '아직 좋아요한 공고가 없어요', '공고를 열어 ♡ 좋아요를 누르면 여기에 모여요.');
  empty(root, '좋아요한 공고를 불러오는 중이에요');
  try {
    const data = await api(`/api/notices/by-ids?ids=${encodeURIComponent(ids.join(','))}`);
    // 관리자가 삭제했거나 사라진 공고는 목록에서도 정리
    data.missing.forEach(id => liked.delete(id));
    if (!data.items.length) return empty(root, '좋아요한 공고가 모두 삭제됐어요', '');
    root.replaceChildren(...data.items.map(noticeCard));
  } catch (err) { empty(root, '좋아요한 공고를 불러오지 못했어요', err.message); }
}

// ── 휴대폰 알림: 서버가 미리 골라 둔 '나에게 맞는 새 공고'를 가져와서 알림으로 띄움 ──
const LocalNotifications = window.Capacitor?.Plugins?.LocalNotifications;
function paintBadge() {
  const n = store.get('local', 'mineBadge', 0), b = $('#mine-badge');
  if (!b) return;
  b.hidden = !n; b.textContent = n > 9 ? '9+' : String(n);
}
async function checkNotifications() {
  const saved = myProfile();
  if (!saved) return;
  try {
    const { items } = await api(`/api/me/notifications?device_id=${encodeURIComponent(saved.device_id)}`);
    if (!items.length) return;
    // kind: admin(관리자 알림) / department(내 학과 새 소식) / match(맞춤 공고)
    const matches = items.filter(it => it.kind !== 'admin').length;
    if (matches) { store.set('local', 'mineBadge', store.get('local', 'mineBadge', 0) + matches); paintBadge(); }
    const noteTitle = it => it.kind === 'admin' ? `📢 ${it.title}` : it.kind === 'department' ? '🎓 내 학과 새 소식' : `나에게 맞는 새 공고 (적합도 ${it.score})`;
    const noteBody = it => it.kind === 'admin' ? (it.body || '강대 모음 알림') : it.title;
    if (LocalNotifications) {
      try {
        // '알람 및 리마인더' 권한이 꺼져 있으면 안드로이드에서 schedule 응답이 오지 않을 수 있어 3초만 기다림
        const scheduled = LocalNotifications.schedule({ notifications: items.slice(0, 5).map((it, i) => ({
          id: (Math.floor(Date.now() / 1000) % 1000000) * 10 + i, // 정수 범위 안의 고유 번호
          title: noteTitle(it),
          body: noteBody(it),
          schedule: { at: new Date(Date.now() + 1000), allowWhileIdle: true }, // 1초 뒤 표시
          extra: { id: it.id || null },
        })) });
        await Promise.race([scheduled, new Promise((_, no) => setTimeout(() => no(new Error('시간 초과')), 3000))]);
      } catch {
        toast(`${noteTitle(items[0])}: ${noteBody(items[0])}${items.length > 1 ? ` 외 ${items.length - 1}건` : ''}`); // 휴대폰 알림이 안 되면 앱 안에서라도
      }
    } else {
      toast(`${noteTitle(items[0])}: ${noteBody(items[0])}${items.length > 1 ? ` 외 ${items.length - 1}건` : ''}`);
    }
    if (location.hash === '#mine') loadMine();
  } catch (err) { console.error('알림 확인 실패', err?.message); }
}
let notifTimer = null;
async function setupNotifications() {
  if (!IN_APP || !myProfile()) return;
  if (LocalNotifications && !setupNotifications.done) {
    setupNotifications.done = true;
    try { await LocalNotifications.requestPermissions(); } catch { /* 거부해도 앱 안 배지로는 보임 */ }
    // 안드로이드 14+: 알림을 정확한 시간에 띄우려면 '알람 및 리마인더' 허용이 필요. 꺼져 있으면 한 번만 안내
    try {
      const exact = await LocalNotifications.checkExactNotificationSetting?.();
      if (exact && exact.exact_alarm !== 'granted' && !store.get('local', 'askedExactAlarm', false)) {
        store.set('local', 'askedExactAlarm', true);
        if (confirm('나에게 맞는 새 공고를 휴대폰 알림으로 받으려면 "알람 및 리마인더"를 허용해 주세요.\n설정 화면으로 이동할까요?')) {
          await LocalNotifications.changeExactNotificationSetting();
        }
      }
    } catch { /* 지원하지 않는 기기는 그냥 진행 */ }
    LocalNotifications.addListener('localNotificationActionPerformed', e => {
      const id = e.notification?.extra?.id;
      if (id) location.hash = `#notice/${encodeURIComponent(id)}`;
    });
  }
  checkNotifications();
  clearInterval(notifTimer);
  notifTimer = setInterval(checkNotifications, 5 * 60 * 1000); // 앱이 켜져 있는 동안 5분마다
}
if (IN_APP) {
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') checkNotifications(); });
  api('/api/profile-options').then(o => (profileOptions = { ...profileOptions, ...o })).catch(() => {});
  paintBadge();
}

// 앱에는 관리자 사이트가 없으므로 하단 링크 숨김
if (window.MOABOM_IN_APP) document.querySelectorAll(".admin-link").forEach(a => a.closest("p")?.remove());
renderFilters();
renderCity();
route();
refreshAll(); // 사이트에 들어오면 목록·배너를 바로 보여주고 새 공고 확인도 시작
if (ADMIN_SITE) restoreAdmin(); // 이 탭에서 관리자로 로그인해 둔 경우
if (IN_APP) { store.remove('local', 'adminKey'); setupNotifications(); } // 앱: 예전에 저장한 관리자 키는 지움, 맞춤 공고 알림
showPopups();   // 관리자가 걸어 둔 팝업 ('앞으로 보지 않기' 한 것은 제외)
