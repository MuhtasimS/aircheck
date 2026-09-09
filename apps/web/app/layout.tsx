import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'AIRCheck · Delivery control',
    template: '%s · AIRCheck',
  },
  description:
    'Autonomous technical QC, authority-aware remediation, and provable media-delivery readiness.',
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // Deployment-required only: the production API base is injected from the server
  // environment so the browser client (lib/api.ts `apiBase()`) targets the
  // deployed API instead of the local default. This is an invisible inline
  // script that runs before hydration; the frozen five-surface UI is unchanged.
  const apiBase = process.env.AIRCHECK_API_BASE ?? '';
  return (
    <html lang="en" className="dark">
      <body>
        {apiBase ? (
          <script
            dangerouslySetInnerHTML={{
              __html: `window.__AIRCHECK_API_BASE__=${JSON.stringify(apiBase)};`,
            }}
          />
        ) : null}
        {children}
      </body>
    </html>
  );
}
