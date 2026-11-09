// infra/edge/functions/spa-fallback.js（正本。変更時はテンプレートにも反映する）
// SPA のディープリンクを処理し、API とアセットはそのままオリジンへ渡す。
function handler(event) {
  var req = event.request;
  if (!req.uri.startsWith('/api/') && !req.uri.includes('.')) {
    req.uri = '/index.html';
  }
  return req;
}
