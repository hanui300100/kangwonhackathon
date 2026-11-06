// 백엔드(FastAPI)가 이 페이지를 같이 제공하므로 기본은 같은 주소(/api/...)를 사용합니다.
// 화면만 다른 서버에서 띄울 때는 주소 뒤에 ?api=http://localhost:8000 을 붙이면 그 백엔드로 보냅니다.
// 앱(Capacitor)에서는 config.js 의 window.MOABOM_API_BASE 에 적힌 서버 주소를 씁니다.
const apiParam = new URLSearchParams(location.search).get('api');
export const API_BASE = (apiParam || window.MOABOM_API_BASE || '').replace(/\/$/, '');

/** API 호출. 실패하면 서버가 보낸 이유(detail)를 담은 Error를 던짐 */
export async function api(path, { method = 'GET', key, data, form } = {}) {
  const headers = {};
  if (key) headers.Authorization = `Bearer ${key}`;
  let body;
  if (form) body = form; // 파일 업로드 (multipart)
  else if (data !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { method, headers, body });
  } catch {
    throw new Error('서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인해 주세요.');
  }
  let result = null;
  try { result = await response.json(); } catch { /* 빈 응답 */ }
  if (!response.ok) {
    const d = result?.detail;
    // 입력 형식 오류(422)는 detail이 목록으로 옴
    const msg = Array.isArray(d) ? d.map(x => x.msg).join(', ') : (d || `요청을 처리하지 못했습니다 (${response.status})`);
    throw new Error(msg);
  }
  return result;
}

/** 학교·시청 이미지/파일은 웹에서 바로 못 띄우는 경우가 있어 백엔드 중계 주소로 바꿈 */
export function proxied(url) {
  try {
    const host = new URL(url).hostname;
    // 백엔드 PROXY_HOSTS 와 같은 목록 (강원대 + 삼척·춘천·강릉·원주 시청)
    if (/(^|\.)(kangwon\.ac\.kr|samcheok\.go\.kr|chuncheon\.go\.kr|gn\.go\.kr|wonju\.go\.kr)$/.test(host)) {
      return `${API_BASE}/api/proxy?url=${encodeURIComponent(url)}`;
    }
  } catch { /* 상대 주소 등 */ }
  return url;
}
