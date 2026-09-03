module.exports = async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  const base = String(process.env.SEARXNG_URL || '').replace(/\/$/, '');
  if (!base) return res.status(503).json({ ok: false, mock: false, searxngConfigured: false });
  const started = Date.now();
  try {
    const upstream = await fetch(`${base}/`, { signal: AbortSignal.timeout(7000) });
    return res.status(upstream.ok ? 200 : 502).json({
      ok: upstream.ok,
      mock: false,
      searxngConfigured: true,
      status: upstream.status,
      elapsedMs: Date.now() - started,
    });
  } catch (error) {
    return res.status(502).json({
      ok: false,
      mock: false,
      searxngConfigured: true,
      error: error instanceof Error ? error.message : String(error),
      elapsedMs: Date.now() - started,
    });
  }
};
