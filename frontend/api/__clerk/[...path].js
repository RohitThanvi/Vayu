// api/__clerk/[...path].js
//
// Vercel serverless function that proxies Clerk's Frontend API through
// our own vayu-geop.vercel.app domain, per Clerk's proxy requirements:
// https://clerk.com/docs/advanced-usage/using-proxies
//
// A plain vercel.json rewrite can't do this because Clerk requires two
// extra headers (Clerk-Proxy-Url, Clerk-Secret-Key) attached server-side
// on every forwarded request — the secret key must never reach the
// browser, so this has to be a function, not a static rewrite.
//
// Routed here via the vercel.json rewrite: /__clerk/:path* -> /api/__clerk/:path*

export const config = {
  api: {
    bodyParser: false, // pass the raw request body straight through untouched
  },
};

export default async function handler(req, res) {
  const { path } = req.query;
  const pathStr = Array.isArray(path) ? path.join('/') : (path || '');

  const queryString = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const targetUrl = `https://frontend-api.clerk.dev/${pathStr}${queryString}`;

  // Forward incoming headers, but strip hop-by-hop / host-specific ones
  // that shouldn't be replayed to the upstream host.
  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (typeof value !== 'string') continue;
    if (['host', 'connection', 'content-length'].includes(key.toLowerCase())) continue;
    headers.set(key, value);
  }

  headers.set('Clerk-Proxy-Url', 'https://vayu-geop.vercel.app/__clerk');
  headers.set('Clerk-Secret-Key', process.env.CLERK_SECRET_KEY || '');
  headers.set(
    'X-Forwarded-For',
    (req.headers['x-forwarded-for'] || req.socket?.remoteAddress || '').toString()
  );

  const hasBody = !['GET', 'HEAD'].includes(req.method);

  let upstreamResponse;
  try {
    upstreamResponse = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: hasBody ? req : undefined,
      duplex: hasBody ? 'half' : undefined,
    });
  } catch (err) {
    console.error('Clerk proxy fetch failed:', err);
    res.status(502).json({ error: 'clerk_proxy_upstream_error' });
    return;
  }

  res.status(upstreamResponse.status);
  upstreamResponse.headers.forEach((value, key) => {
    // content-encoding/transfer-encoding can cause double-decoding issues
    // when re-served through Vercel — let Vercel manage those itself.
    if (['content-encoding', 'transfer-encoding'].includes(key.toLowerCase())) return;
    res.setHeader(key, value);
  });

  const buffer = Buffer.from(await upstreamResponse.arrayBuffer());
  res.send(buffer);
}