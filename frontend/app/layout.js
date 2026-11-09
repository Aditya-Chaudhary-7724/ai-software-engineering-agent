import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import { connection } from 'next/server';

import './globals.css';

// next/font downloads these at BUILD time and serves them from this app's
// own origin — the browser never contacts Google.
const plexSans = IBM_Plex_Sans({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-plex-sans' });
const plexMono = IBM_Plex_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-plex-mono' });

export const metadata = {
  title: { default: 'Engineering Lab', template: '%s · Engineering Lab' },
  description: 'Software intelligence workspace for the AI Software Engineering Agent.',
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }) {
  // F8: the Content-Security-Policy nonce (proxy.js) is per request, so EVERY
  // page must be rendered per request. All pages already were (the DAL reads
  // cookies) except the built-in 404, which Next prerendered as static HTML
  // whose scripts carried no nonce and were blocked. connection() opts the
  // whole tree into dynamic rendering.
  await connection();
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
