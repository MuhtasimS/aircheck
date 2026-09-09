'use client';

import { useCallback } from 'react';

import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '@/components/aircheck/view-state';
import { StatusBadge } from '@/components/aircheck/status-badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { usePolledResource } from '@/hooks/use-polled-resource';
import { listRuns, type DeliveriesResponse } from '@/lib/api';

function pad2(value: number): string {
  return value.toString().padStart(2, '0');
}

function SummaryStrip({
  counts,
}: {
  counts: DeliveriesResponse['counts'] | null;
}) {
  return (
    <section className="summary-strip" aria-label="Delivery summary">
      <div className="stat">
        <span className="stat-label">Active runs</span>
        <span className="stat-value">{counts ? pad2(counts.active_runs) : '—'}</span>
      </div>
      <div className="stat">
        <span className="stat-label">Decision required</span>
        <span className="stat-value" style={{ color: 'var(--decision)' }}>
          {counts ? pad2(counts.decision_required) : '—'}
        </span>
      </div>
      <div className="stat">
        <span className="stat-label">Delivery ready</span>
        <span className="stat-value" style={{ color: 'var(--pass)' }}>
          {counts ? pad2(counts.delivery_ready) : '—'}
        </span>
      </div>
      <div className="stat">
        <span className="stat-label">Unresolved blockers</span>
        <span className="stat-value" style={{ color: 'var(--fail)' }}>
          {counts ? pad2(counts.unresolved_blockers) : '—'}
        </span>
      </div>
    </section>
  );
}

export function DeliveriesView() {
  const fetcher = useCallback(() => listRuns(), []);
  const state = usePolledResource<DeliveriesResponse>(fetcher, {
    intervalMs: 4000,
  });

  const data = state.data;

  return (
    <>
      <SummaryStrip counts={data?.counts ?? null} />

      <section className="data-panel" aria-labelledby="delivery-ledger-heading">
        <div className="panel-toolbar">
          <p id="delivery-ledger-heading" className="table-kicker">
            Run ledger · latest first
          </p>
        </div>

        {state.status === 'loading' && data === null ? <LoadingState /> : null}
        {state.status === 'error' && data === null ? (
          <ErrorState message={state.error ?? undefined} />
        ) : null}
        {data !== null && data.runs.length === 0 ? <EmptyState /> : null}

        {data !== null && data.runs.length > 0 ? (
          <Table className="air-table">
            <TableHeader>
              <TableRow>
                <TableHead>Program / run</TableHead>
                <TableHead>Destination profile</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Verification</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead>
                  <span className="sr-only">Open</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.runs.map((delivery) => (
                <TableRow key={delivery.run_id}>
                  <TableCell>
                    <div className="program-cell">
                      <span className="program-title">
                        {delivery.program_title}
                      </span>
                      <span className="program-id">{delivery.run_id}</span>
                    </div>
                  </TableCell>
                  <TableCell>{delivery.destination_profile}</TableCell>
                  <TableCell>
                    <StatusBadge status={delivery.display_status} />
                    <div className="mono" style={{ marginTop: 8 }}>
                      {delivery.finding_summary}
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="mono">
                      {delivery.verified} / {delivery.total} verified
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="mono">{delivery.updated_at}</span>
                  </TableCell>
                  <TableCell>
                    <a className="row-link" href={`/runs/${delivery.run_id}`}>
                      Open run
                    </a>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </section>
    </>
  );
}
