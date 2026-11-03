// infra/edge/functions/spa-fallback.js（正本。直したらテンプレートの FunctionCode に貼り直す）
function handler(event) {
  var req = event.request;
  if (!req.uri.startsWith('/api/') && !req.uri.includes('.')) {
    req.uri = '/index.html';
  }
  return req;
}
