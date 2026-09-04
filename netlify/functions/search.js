const ARCHIVE_URL = process.env.LIVE_DATA_URL || 'https://raw.githubusercontent.com/kyirexy/deepfeed/live-data/live.json';

const PLATFORM_DOMAINS = {
  douyin: { label: '抖音', domains: ['douyin.com'] },
  xiaohongshu: { label: '小红书', domains: ['xiaohongshu.com'] },
  bilibili: { label: 'B站', domains: ['bilibili.com', 'b23.tv'] },
  taptap: { label: 'TapTap', domains: ['taptap.cn', 'taptap.com'] },
  wechat: { label: '微信公众号', domains: ['mp.weixin.qq.com'] },
  zhihu: { label: '知乎', domains: ['zhihu.com'] },
  tieba: { label: '贴吧', domains: ['tieba.baidu.com'] },
};

function json(statusCode, payload) {
  return { statusCode, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store, max-age=0', 'access-control-allow-origin': '*' }, body: JSON.stringify(payload) };
}
function hostOf(value) { try { return new URL(value).hostname.toLowerCase().replace(/^www\./, ''); } catch { return ''; } }
function inferPlatform(item) {
  const host = hostOf(item.url || item.sourceUrl || '');
  for (const [key, spec] of Object.entries(PLATFORM_DOMAINS)) if (spec.domains.some(d => host === d || host.endsWith(`.${d}`))) return { key, label: spec.label };
  const raw = String(item.platform || item.platformLabel || item.source || '').toLowerCase();
  for (const [key, spec] of Object.entries(PLATFORM_DOMAINS)) if (raw.includes(spec.label.toLowerCase()) || raw.includes(key)) return { key, label: spec.label };
  return { key: 'web', label: '网页' };
}
function withinRange(item, timeRange) {
  if (timeRange === 'all') return true;
  const days = timeRange === 'day' ? 1 : timeRange === 'month' ? 31 : timeRange === 'year' ? 366 : null;
  if (!days) return true;
  const candidate = item.publishedAt || item.firstSeenAt || item.discoveredAt || item.lastSeenAt;
  if (!candidate) return true;
  const stamp = Date.parse(candidate);
  return !Number.isFinite(stamp) || Date.now() - stamp <= days * 86400000;
}

exports.handler = async function handler(event) {
  if (event.httpMethod !== 'GET') return json(405, { ok: false, mock: false, error: 'Method not allowed' });
  const params = event.queryStringParameters || {};
  const q = String(params.q || '').trim();
  const platform = String(params.platform || 'all').trim();
  const timeRange = ['day', 'month', 'year', 'all'].includes(String(params.time_range || 'month')) ? String(params.time_range || 'month') : 'month';
  const limit = Math.max(1, Math.min(50, Number(params.limit || 20)));
  if (q.length < 2 || q.length > 120) return json(400, { ok: false, mock: false, error: 'q must contain 2-120 characters' });
  if (platform !== 'all' && !PLATFORM_DOMAINS[platform]) return json(400, { ok: false, mock: false, error: 'Unsupported platform' });
  const started = Date.now();
  try {
    const url = `${ARCHIVE_URL}${ARCHIVE_URL.includes('?') ? '&' : '?'}t=${Date.now()}`;
    const upstream = await fetch(url, { headers: { Accept: 'application/json', 'User-Agent': 'TideNetlifySearch/1.0' }, signal: AbortSignal.timeout(12000), cache: 'no-store' });
    if (!upstream.ok) throw new Error(`Archive HTTP ${upstream.status}`);
    const payload = await upstream.json();
    if (payload.mock !== false) throw new Error('mock:false validation failed');
    const tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    const results = [];
    for (const item of Array.isArray(payload.items) ? payload.items : []) {
      const inferred = inferPlatform(item);
      if (platform !== 'all' && inferred.key !== platform) continue;
      if (!withinRange(item, timeRange)) continue;
      const haystack = [item.title, item.description, item.snippet, item.summary, item.source, inferred.label].filter(Boolean).join(' ').toLowerCase();
      if (!tokens.every(t => haystack.includes(t))) continue;
      results.push({ title: item.title || '无标题', url: item.url || item.sourceUrl || '', snippet: item.description || item.snippet || item.summary || '', publishedAt: item.publishedAt || null, firstSeenAt: item.firstSeenAt || null, lastSeenAt: item.lastSeenAt || null, platform: inferred.key, platformLabel: inferred.label, accessLevel: item.accessLevel || item.dataLevel || '搜索发现', provider: item.provider || item.searchProvider || '分钟归档' });
      if (results.length >= limit) break;
    }
    return json(200, { ok: true, mock: false, provider: 'minute-archive', query: q, platform, timeRange, resultCount: results.length, archiveGeneratedAt: payload.generatedAt || null, elapsedMs: Date.now() - started, results });
  } catch (error) {
    return json(502, { ok: false, mock: false, error: error instanceof Error ? error.message : String(error), elapsedMs: Date.now() - started });
  }
};
