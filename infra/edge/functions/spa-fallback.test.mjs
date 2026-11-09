import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SOURCE_PATH = join(dirname(fileURLToPath(import.meta.url)), 'spa-fallback.js');
const SOURCE = readFileSync(SOURCE_PATH, 'utf8');
const handler = new Function(SOURCE + '\nreturn handler;')();

function makeEvent(uri, querystring = {}) {
  return { request: { method: 'GET', uri, headers: {}, querystring } };
}

for (const uri of ['/', '/admin-users', '/admin-users/settings', '/devices/12345678', '/foo']) {
  test(`${uri} は /index.html へ書き換わる`, () => {
    assert.equal(handler(makeEvent(uri)).uri, '/index.html');
  });
}


for (const uri of [
  '/assets/app.js',
  '/favicon.svg',
  '/api/admin-users/health',
  '/api/base/me',
]) {
  test(`${uri} は書き換えない`, () => {
    assert.equal(handler(makeEvent(uri)).uri, uri);
  });
}

test('戻り値は event.request と同一オブジェクト（応答を作らずオリジンへ流す）', () => {
  for (const uri of ['/admin-users', '/devices/12345678', '/assets/app.js', '/api/base/me']) {
    const event = makeEvent(uri);
    assert.equal(handler(event), event.request);
  }
});
