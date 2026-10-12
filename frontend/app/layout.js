import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';

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

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
