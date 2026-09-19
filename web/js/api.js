/** Every call to the server, in one place. Errors always carry the server's own message. */

async function request(url, options) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
    throw new Error(`cannot reach the server (${cause.message}). Is it still running?`);
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  }
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.kind = payload.kind;
    error.status = response.status;
    throw error;
  }
  return payload;
}

const post = (url, body) => request(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  project: () => request('/api/project'),
  openProject: (root) => post('/api/project/open', { root }),
  documents: () => request('/api/documents'),
  document: (id) => request(`/api/document?id=${encodeURIComponent(id)}`),
  guide: () => request('/api/guide'),
  decide: (body) => post('/api/decide', body),
  setLayer: (layer) => post('/api/layer', { layer }),
  exportAll: (opts = {}) => post('/api/export', opts),
};
