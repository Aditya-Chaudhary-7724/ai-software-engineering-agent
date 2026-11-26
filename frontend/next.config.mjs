/**
 * Baseline security headers for EVERY response, including static assets the
 * proxy does not see. F8: pages and API routes additionally get a per-request,
 * nonce-based script-src Content-Security-Policy (and HSTS over HTTPS) from
 * proxy.js / lib/security-headers.js. Browsers enforce every CSP header they
 * receive, so this baseline only ever adds restrictions.
 */
const securityHeaders = [
  { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'" },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'same-origin' },
  { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=()' },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Don't let `next dev` write AGENTS.md (managed AI-agent instructions)
  // into the project when it detects a coding agent.
  agentRules: false,
  // Container builds only (frontend/Dockerfile sets NEXT_OUTPUT_STANDALONE=1):
  // a self-contained server with just the traced dependencies. Local
  // `npm start` (next start) does not support standalone output, so it stays off.
  output: process.env.NEXT_OUTPUT_STANDALONE === '1' ? 'standalone' : undefined,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};

export default nextConfig;
