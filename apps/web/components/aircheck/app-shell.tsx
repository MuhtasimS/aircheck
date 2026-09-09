import {
  Archive,
  ClipboardCheck,
  FileKey,
  Gauge,
  Plus,
  ShieldCheck,
} from 'lucide-react';

type NavKey = 'deliveries' | 'new' | 'control' | 'decision' | 'evidence';

export function AppShell({
  active,
  context,
  runId,
  children,
}: {
  active: NavKey;
  context: string;
  runId?: string;
  children: React.ReactNode;
}) {
  // Per-run surfaces resolve to the run in context when one is present, and to
  // the deliveries ledger (where a run is chosen) otherwise — never a dead link.
  const runBase = runId ? `/runs/${runId}` : '/';
  const navigation: { href: string; label: string; icon: typeof Archive; key: NavKey }[] = [
    { href: '/', label: 'Deliveries', icon: Archive, key: 'deliveries' },
    { href: '/new', label: 'New delivery', icon: Plus, key: 'new' },
    { href: runBase, label: 'Control room', icon: Gauge, key: 'control' },
    {
      href: runId ? `${runBase}/decision` : '/',
      label: 'Decision',
      icon: FileKey,
      key: 'decision',
    },
    {
      href: runId ? `${runBase}/evidence` : '/',
      label: 'Evidence',
      icon: ClipboardCheck,
      key: 'evidence',
    },
  ];

  return (
    <div className="app-shell">
      <aside className="nav-rail">
        <a href="/" className="brand" aria-label="AIRCheck deliveries">
          <span className="brand-mark">AC</span>
          <span className="brand-name">AIRCHECK</span>
        </a>

        <span className="nav-label">Operations</span>
        <nav className="nav-links" aria-label="Primary navigation">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <a
                key={item.key}
                href={item.href}
                className="nav-link"
                aria-current={active === item.key ? 'page' : undefined}
              >
                <Icon aria-hidden="true" />
                {item.label}
              </a>
            );
          })}
        </nav>

        <div className="nav-foot">
          <div className="system-line">
            <span className="system-dot" aria-hidden="true" />
            Deterministic core online
          </div>
          <div className="system-line" style={{ marginTop: 8 }}>
            <ShieldCheck aria-hidden="true" size={13} />
            Authority policy · v1
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <span className="breadcrumb">{context}</span>
          <span className="run-code">Synthetic workspace</span>
        </header>
        <main className="main-content">{children}</main>
      </div>

      <nav className="mobile-nav" aria-label="Mobile navigation">
        {navigation.map((item) => {
          const Icon = item.icon;
          return (
            <a
              key={item.key}
              href={item.href}
              aria-label={item.label}
              aria-current={active === item.key ? 'page' : undefined}
            >
              <Icon aria-hidden="true" />
            </a>
          );
        })}
      </nav>
    </div>
  );
}
