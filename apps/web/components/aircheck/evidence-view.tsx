'use client';

import { useCallback } from 'react';
import {
  AlertTriangle,
  Check,
  Download,
  FileJson,
  FileText,
  Fingerprint,
  ListTree,
  LockKeyhole,
  type LucideIcon,
} from 'lucide-react';

import { SectionHeading } from '@/components/aircheck/section-heading';
import { StatusBadge } from '@/components/aircheck/status-badge';
import { ErrorState, LoadingState } from '@/components/aircheck/view-state';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { usePolledResource } from '@/hooks/use-polled-resource';
import {
  evidenceDownloadUrl,
  getEvidence,
  type EvidenceResponse,
} from '@/lib/api';
import type { ProductStatus } from '@/lib/types';

const TERMINAL = new Set(['DELIVERY_READY', 'BLOCKED', 'FAILED']);

// Stable predicate: poll evidence until the run reaches a terminal state.
function evidenceShouldPoll(ev: EvidenceResponse): boolean {
  return !TERMINAL.has(ev.run_state);
}

const ICONS: Record<string, LucideIcon> = {
  report: FileText,
  ledger: ListTree,
  lineage: ListTree,
  manifest: FileJson,
  checksums: Fingerprint,
  terminal: FileText,
  derivative: FileJson,
  generic: FileText,
};

/**
 * The readiness presentation is driven by the authoritative terminal verdict,
 * never by the run state alone. A DELIVERY_READY verdict is the only thing that
 * earns the green seal; a null/missing verdict — even if the run state reads
 * DELIVERY_READY — is surfaced as UNVERIFIED and is never green.
 */
function readiness(evidence: EvidenceResponse): {
  green: boolean;
  badge: ProductStatus;
  seal: 'ready' | 'blocked' | 'pending';
  headline: string;
} {
  const tv = evidence.terminal_verdict;
  if (tv && tv.outcome === 'DELIVERY_READY') {
    return {
      green: true,
      badge: 'DELIVERY READY',
      seal: 'ready',
      headline: evidence.requirements_verified,
    };
  }
  // A blocked run is terminal whether the block was computed as a verdict or
  // reached directly by a human denial (which carries no terminal verdict).
  if ((tv && tv.outcome === 'BLOCKED') || evidence.run_state === 'BLOCKED') {
    return {
      green: false,
      badge: 'BLOCKED',
      seal: 'blocked',
      headline: 'Delivery blocked — terminal blockers remain',
    };
  }
  if (evidence.run_state === 'FAILED') {
    return {
      green: false,
      badge: 'FAIL',
      seal: 'blocked',
      headline: 'Run failed before a verdict could be earned',
    };
  }
  if (evidence.run_state === 'DELIVERY_READY') {
    // Status claims ready but no authoritative terminal verdict exists.
    return {
      green: false,
      badge: 'UNVERIFIED',
      seal: 'pending',
      headline: 'No authoritative terminal verdict — not delivery ready',
    };
  }
  return {
    green: false,
    badge: 'INSPECTING',
    seal: 'pending',
    headline: 'Evidence is not final until the run reaches a terminal verdict',
  };
}

export function EvidenceView({ runId }: { runId: string }) {
  const fetcher = useCallback(() => getEvidence(runId), [runId]);
  const state = usePolledResource<EvidenceResponse>(fetcher, {
    intervalMs: 2500,
    shouldPoll: evidenceShouldPoll,
  });

  const evidence = state.data;

  if (evidence === null) {
    return (
      <>
        <SectionHeading eyebrow="Terminal evidence" title="Evidence" />
        {state.status === 'error' ? (
          <ErrorState
            title="Evidence unavailable"
            message={state.error ?? undefined}
          />
        ) : (
          <LoadingState
            label="Loading evidence"
            copy="Reading terminal verdict, artifacts, and the final requirement ledger."
          />
        )}
      </>
    );
  }

  const view = readiness(evidence);

  return (
    <>
      <SectionHeading
        eyebrow={`Terminal evidence / ${evidence.run_id}`}
        title={view.green ? 'Delivery ready' : 'Delivery evidence'}
        description={`${evidence.destination_profile} · ${evidence.program_title}.`}
      />

      <section
        className={`ready-banner${view.green ? '' : ' ready-banner-pending'}`}
        aria-labelledby="ready-heading"
      >
        <div className={`ready-seal${view.green ? '' : ' ready-seal-pending'}`}>
          {view.seal === 'ready' ? (
            <Check aria-hidden="true" />
          ) : view.seal === 'blocked' ? (
            <AlertTriangle aria-hidden="true" />
          ) : (
            <LockKeyhole aria-hidden="true" />
          )}
        </div>
        <div>
          <StatusBadge status={view.badge} />
          <h2 id="ready-heading">{view.headline}</h2>
          <p>
            {view.green && evidence.earned_at
              ? `Terminal state earned at ${evidence.earned_at}`
              : `Run state ${evidence.run_state}`}
            {evidence.package_hash
              ? ` · final package hash ${evidence.package_hash}`
              : ''}
          </p>
        </div>
        <dl className="ready-metrics">
          <div>
            <dt>Findings resolved</dt>
            <dd>{evidence.metrics.findings_resolved}</dd>
          </div>
          <div>
            <dt>Autonomous fixes</dt>
            <dd>{evidence.metrics.autonomous_fixes}</dd>
          </div>
          <div>
            <dt>Human-authorized</dt>
            <dd>{evidence.metrics.human_authorized}</dd>
          </div>
          <div>
            <dt>Open blockers</dt>
            <dd>{evidence.metrics.open_blockers}</dd>
          </div>
        </dl>
      </section>

      <section className="evidence-section" aria-labelledby="artifacts-heading">
        <div className="section-line-heading">
          <div>
            <span className="panel-index">01 / Artifacts</span>
            <h2 id="artifacts-heading">Delivery receipts</h2>
          </div>
          <span className="mono">
            {evidence.artifacts.length} file
            {evidence.artifacts.length === 1 ? '' : 's'} · synthetic workspace
          </span>
        </div>
        {evidence.artifacts.length === 0 ? (
          <p className="action-note">
            No evidence artifacts exist yet. Artifacts are written only after the
            run reaches a terminal verdict.
          </p>
        ) : (
          <div className="artifact-grid">
            {evidence.artifacts.map((artifact) => {
              const Icon = ICONS[artifact.icon_type] ?? FileText;
              return (
                <article className="artifact-card" key={artifact.filename}>
                  <Icon aria-hidden="true" />
                  <span className="eyebrow">{artifact.name}</span>
                  <h3 className="break-anywhere">{artifact.filename}</h3>
                  <p>{artifact.meta}</p>
                  <a
                    className="artifact-download"
                    href={evidenceDownloadUrl(evidence.run_id, artifact.filename)}
                    aria-label={`Download ${artifact.name}`}
                  >
                    <Download aria-hidden="true" /> Download
                  </a>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section className="evidence-section" aria-labelledby="verification-heading">
        <div className="section-line-heading">
          <div>
            <span className="panel-index">02 / Verification</span>
            <h2 id="verification-heading">Final requirement ledger</h2>
          </div>
          <span className="mono">{evidence.requirements_verified}</span>
        </div>
        <div className="data-panel">
          <Table className="air-table evidence-table">
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead>Observed final value</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {evidence.verification_rows.map((row) => (
                <TableRow key={row.requirement_id}>
                  <TableCell>{row.category}</TableCell>
                  <TableCell>
                    <span className="mono break-anywhere">{row.observed}</span>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={row.status} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
    </>
  );
}
