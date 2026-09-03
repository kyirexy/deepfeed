module.exports = async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'GET') return res.status(405).json({ ok: false, error: 'Method not allowed' });
  const source = process.env.LIVE_DATA_URL || 'https://raw.githubusercontent.com/kyirexy/deepfeed/main/data/live.json';
  try {
    const upstream = await fetch(`${source}${source.includes('?') ? '&' : '?'}t=${Date.now()}`, {
      headers: { Accept: 'application/json', 'User-Agent': 'TideLiveGateway/0.3' },
      signal: AbortSignal.timeout(12000),
      cache: 'no-store',
    });
    if (!upstream.ok) throw new Error(`Archive HTTP ${upstream.status}`);
    const payload = await upstream.json();
    if (payload.mock !== false) return res.status(409).json({ ok: false, error: 'mock:false validation failed' });
    return res.status(200).json(payload);
  } catch (error) {
    return res.status(502).json({
      ok: false,
      mock: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};
