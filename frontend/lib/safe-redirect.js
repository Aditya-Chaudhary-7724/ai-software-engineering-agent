/**
 * Validates a post-login `next` destination. Only same-site relative paths are
 * allowed, so `/login?next=...` can never become an open redirect
 * (`//evil.com`, `/\evil.com`, `https://evil.com`, `javascript:` ...).
 * Shared by the proxy (server) and the login form (client); holds no secrets.
 */
export function safeNextPath(value, fallback = '/') {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048) return fallback;
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return fallback;
  if (/[\u0000-\u001f\u007f]/.test(value)) return fallback;
  try {
    const parsed = new URL(value, 'http://placeholder.invalid');
    if (parsed.origin !== 'http://placeholder.invalid') return fallback;
    if (parsed.pathname === '/login' || parsed.pathname.startsWith('/api/')) return fallback;
    return parsed.pathname + parsed.search;
  } catch {
    return fallback;
  }
}
