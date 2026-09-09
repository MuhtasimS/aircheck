import { AlertTriangle, Inbox } from 'lucide-react';

export function EmptyState({
  title = 'No deliveries in this view',
  copy = 'Start a delivery check to create the first auditable run.',
}: {
  title?: string;
  copy?: string;
} = {}) {
  return (
    <output className="state-panel">
      <div className="state-panel-inner">
        <span className="state-symbol">
          <Inbox aria-hidden="true" />
        </span>
        <h2 className="state-title">{title}</h2>
        <p className="state-copy">{copy}</p>
      </div>
    </output>
  );
}

export function LoadingState({
  label = 'Loading run ledger',
  copy = 'Reading deterministic run state and evidence references.',
}: {
  label?: string;
  copy?: string;
} = {}) {
  return (
    <output className="state-panel" aria-label={label} aria-busy="true">
      <div className="state-panel-inner">
        <p className="eyebrow" style={{ marginBottom: 24 }}>
          {label}
        </p>
        <div className="loading-lines" aria-hidden="true">
          <span className="loading-line" />
          <span className="loading-line" />
          <span className="loading-line" />
        </div>
        <p className="state-copy">{copy}</p>
      </div>
    </output>
  );
}

export function ErrorState({
  title = 'Run ledger unavailable',
  message = 'AIRCheck could not read delivery state. No run data was changed. Retry when the application API is available.',
}: {
  title?: string;
  message?: string;
} = {}) {
  return (
    <div className="state-panel" role="alert">
      <div className="state-panel-inner">
        <span className="state-symbol">
          <AlertTriangle aria-hidden="true" />
        </span>
        <h2 className="state-title">{title}</h2>
        <p className="state-copy">{message}</p>
      </div>
    </div>
  );
}
