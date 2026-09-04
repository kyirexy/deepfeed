const ARCHIVE_URL = process.env.LIVE_DATA_URL || 'https://raw.githubusercontent.com/kyirexy/deepfeed/live-data/live.json';

function json(statusCode, payload) {
  return {
    statusCode,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store, max-age=0',
      'access-control-allow-origin': '*',
    },
    body: JSON.stringify(payload),
  };
}

exports.handler = async function handler(event) {
  if (event.httpMethod !== 'GET') return json(405, { ok: false, mock: false, error: 'Method not allowed' });
  try {
    const url = `${ARCHIVE_URL}${ARCHIVE_URL.includes('?') ? '&' : '?'}t=${Date.now()}`;
    const upstream = await fetch(url, {
      headers: { Accept: 'application/json', 'User-Agent': 'TideNetlifyLive/1.0' },
      signal: AbortSignal.timeout(12000),
      cache: 'no-store',
    });
    if (!upstream.ok) throw new Error(`Archive HTTP ${upstream.status}`);
    const payload = await upstream.json();
    if (payload.mock !== false) return json(409, { ok: false, mock: false, error: 'mock:false validation failed' });
    return json(200, payload);
  } catch (error) {
    return json(502, { ok: false, mock: false, error: error instanceof Error ? error.message : String(error) });
  }
};
