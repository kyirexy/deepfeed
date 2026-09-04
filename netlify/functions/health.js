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

exports.handler = async function handler() {
  const started = Date.now();
  try {
    const url = `${ARCHIVE_URL}${ARCHIVE_URL.includes('?') ? '&' : '?'}t=${Date.now()}`;
    const upstream = await fetch(url, {
      headers: { Accept: 'application/json', 'User-Agent': 'TideNetlifyHealth/1.0' },
      signal: AbortSignal.timeout(10000),
      cache: 'no-store',
    });
    if (!upstream.ok) throw new Error(`Archive HTTP ${upstream.status}`);
    const payload = await upstream.json();
    if (payload.mock !== false) throw new Error('mock:false validation failed');
    const generated = payload.generatedAt ? Date.parse(payload.generatedAt) : NaN;
    const ageSeconds = Number.isFinite(generated) ? Math.max(0, Math.floor((Date.now() - generated) / 1000)) : null;
    return json(200, {
      ok: true,
      mock: false,
      archiveReachable: true,
      minuteArchive: true,
      generatedAt: payload.generatedAt || null,
      itemCount: Array.isArray(payload.items) ? payload.items.length : (payload.itemCount || 0),
      ageSeconds,
      elapsedMs: Date.now() - started,
      searchMode: process.env.SEARXNG_URL ? 'searxng+archive' : 'archive',
    });
  } catch (error) {
    return json(502, {
      ok: false,
      mock: false,
      archiveReachable: false,
      minuteArchive: true,
      error: error instanceof Error ? error.message : String(error),
      elapsedMs: Date.now() - started,
    });
  }
};
