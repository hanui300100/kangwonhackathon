// ../web 화면을 앱 폴더(www)로 복사하고, 앱이 접속할 백엔드 주소를 config.js 에 적는다.
// 화면은 항상 ../web 에서만 고치면 되고, 앱을 빌드하기 전에 이 스크립트가 최신 화면을 가져옴.
//
// 백엔드 주소 정하는 법 (위에서부터 우선):
//   1) 명령에 직접:      npm run sync-web -- http://192.168.0.10:8000
//   2) 환경변수:         $env:MOABOM_API="https://내서버주소"; npm run sync-web
//   3) app/api-url.txt 파일에 한 줄로 적어 두기
//
// 휴대폰에서는 localhost 가 휴대폰 자신을 가리키므로, PC 서버라면 PC의 내부 IP(같은 와이파이),
// 배포한 서버라면 그 https 주소를 써야 함.
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const appDir = join(here, '..');
const webDir = join(appDir, '..', 'web');
const outDir = join(appDir, 'www');

const fromFile = existsSync(join(appDir, 'api-url.txt')) ? readFileSync(join(appDir, 'api-url.txt'), 'utf8').trim() : '';
const apiUrl = (process.argv[2] || process.env.MOABOM_API || fromFile || '').replace(/\/$/, '');

if (!apiUrl) {
  console.error('백엔드 주소가 없습니다. 예) npm run sync-web -- http://192.168.0.10:8000  (자세한 방법은 scripts/sync-web.mjs 맨 위 설명)');
  process.exit(1);
}
if (!/^https?:\/\//.test(apiUrl)) {
  console.error(`백엔드 주소는 http:// 또는 https:// 로 시작해야 합니다: ${apiUrl}`);
  process.exit(1);
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });
// 앱에는 일반 사이트만 넣음 (관리자 사이트는 웹에서 사용)
for (const file of ['index.html', 'app.js', 'api.js', 'style.css']) {
  cpSync(join(webDir, file), join(outDir, file));
}
writeFileSync(
  join(outDir, 'config.js'),
  `// sync-web.mjs 가 자동으로 만든 파일 (직접 고치지 마세요)\nwindow.MOABOM_API_BASE = ${JSON.stringify(apiUrl)};\nwindow.MOABOM_IN_APP = true;\n`,
);
console.log(`화면 복사 완료 → app/www (백엔드: ${apiUrl})`);
