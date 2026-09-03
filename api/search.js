const PLATFORM_DOMAINS = {
  douyin: { label: '抖音', site: 'site:douyin.com', domains: ['douyin.com'] },
  xiaohongshu: { label: '小红书', site: 'site:xiaohongshu.com', domains: ['xiaohongshu.com'] },
  bilibili: { label: 'B站', site: 'site:bilibili.com', domains: ['bilibili.com', 'b23.tv'] },
  taptap: { label: 'TapTap', site: 'site:taptap.cn', domains: ['taptap.cn', 'taptap.com'] },
  wechat: { label: '微信公众号', site: 'site:mp.weixin.qq.com', domains: ['mp.weixin.qq.com'] },
  zhihu: { label: '知乎', site: 'site:zhihu.com', domains: ['zhihu.com'] },
  tieba: { label: '贴吧', site: 'site:tieba.baidu.com', domains: ['tieba.baidu.com'] },
};

function hostOf(value) {
  try { return new URL(value).hostname.toLowerCase().replace(/^www\./, ''); }
  catch { return ''; }
}

function inferPlatform(url) {
  const host = hostOf(url);
  for (const [key, spec] of Object.entries(PLATFORM_DOMAINS)) {
    if (spec.domains.some((domain) => host === domain || host.endsWith(`.${domain}`))) {
      return { key, label: spec.label };
    }
  }
  return { key: 'web', label: '网页' };
}

module.exports = async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  if (req.method !== 'GET') return res.status(405).json({ ok: false, error: 'Method not allowed' });

  const searxngUrl = String(process.env.SEARXNG_URL || '').replace(/\/$/, '');
  if (!searxngUrl) {
    return res.status(503).json({
      ok: false,
      mock: false,
      error: 'SEARXNG_URL is not configured on the server',
    });
  }

  const rawQuery = String(req.query.q || '').trim();
  const platform = String(req.query.platform || 'all');
  const timeRange = ['day', 'month', 'year', 'all'].includes(String(req.query.time_range))
    ? String(req.query.time_range)
    : 'month';
  const limit = Math.max(1, Math.min(50, Number(req.query.limit || 20)));

  if (rawQuery.length < 2 || rawQuery.length > 120) {
    return res.status(400).json({ ok: false, error: 'q must contain 2-120 characters' });
  }
  if (platform !== 'all' && !PLATFORM_DOMAINS[platform]) {
    return res.status(400).json({ ok: false, error: 'Unsupported platform' });
  }

  const query = platform === 'all'
    ? rawQuery
    : `"${rawQuery}" ${PLATFORM_DOMAINS[platform].site}`;
  const params = new URLSearchParams({
    q: query,
    format: 'json',
    categories: 'general,news',
    language: 'zh-CN',
    safesearch: '0',
    pageno: '1',
  });
  if (timeRange !== 'all') params.set('time_range', timeRange);

  const started = Date.now();
  try {
    const upstream = await fetch(`${searxngUrl}/search?${params}`, {
      headers: { Accept: 'application/json', 'User-Agent': 'TideSearchGateway/0.3' },
      signal: AbortSignal.timeout(20000),
    });
    if (!upstream.ok) throw new Error(`SearXNG HTTP ${upstream.status}`);
    const payload = await upstream.json();
    const results = [];
    for (const row of (payload.results || []).slice(0, limit)) {
      if (!row.url) continue;
      const inferred = inferPlatform(row.url);
      if (platform !== 'all' && inferred.key !== platform) continue;
      results.push({
        title: row.title || '无标题',
        url: row.url,
        snippet: row.content || '',
        publishedAt: row.publishedDate || row.published_date || null,
        engine: row.engine || null,
        engines: Array.isArray(row.engines) ? row.engines : [],
        score: row.score ?? null,
        platform: inferred.key,
        platformLabel: inferred.label,
        accessLevel: '搜索发现',
      });
    }
    return res.status(200).json({
      ok: true,
      mock: false,
      provider: 'SearXNG',
      query,
      platform,
      timeRange,
      resultCount: results.length,
      elapsedMs: Date.now() - started,
      results,
    });
  } catch (error) {
    return res.status(502).json({
      ok: false,
      mock: false,
      error: error instanceof Error ? error.message : String(error),
      elapsedMs: Date.now() - started,
    });
  }
};
