'use client';

import { useCallback } from 'react';
import {
  ArrowUpRight,
  Check,
  FileText,
  Link2,
  LockKeyhole,
  Radio,
  Wrench,
} from 'lucide-react';

import { SectionHeading } from '@/components/aircheck/section-heading';
import { StatusBadge } from '@/components/aircheck/status-badge';
import {
  ErrorState,
  LoadingState,
} from '@/components/aircheck/view-state';
import { usePolledResource } from '@/hooks/use-polled-resource';
import { getRun, type RunDetailView } from '@/lib/api';

const TERMINAL = new Set(['DELIVERY_READY', 'BLOCKED', 'FAILED']);

// Stable predicate: poll until the runtime reaches a terminal state.
function controlRoomShouldPoll(run: RunDetailView): boolean {
  return !TERMINAL.has(run.run_state);
}

function isDecisionEvent(type: string): boolean {
  return (
    type.includes('DECISION') ||
    type.includes('AUTHORIZATION') ||
    type.includes('AWAITING')
  );
}

function timelineLabel(runState: string): string {
  if (runState === 'AWAITING_HUMAN_DECISION') return 'Paused';
  if (TERMINAL.has(runState)) return 'Complete';
  return 'Running';
}

function RequirementsPanel({ run }: { run: RunDetailView }) {
  return (
    <section
      className="control-panel requirements-panel"
      aria-labelledby="requirements-heading"
    >
      <header className="control-panel-head">
        <div>
          <span className="panel-index">01 / Requirements</span>
          <h2 id="requirements-heading">Specification ledger</h2>
        </div>
        <span className="mono">{run.metrics.requirements_total} total</span>
      </header>
      <div className="requirement-list">
        {run.requirements.map((requirement) => (
          <article className="requirement-row" key={requirement.id}>
            <div className="requirement-topline">
              <span className="requirement-category">
                {requirement.category}
              </span>
              <StatusBadge status={requirement.status} />
            </div>
            <h3>{requirement.description}</h3>
            <p className="source-reference">{requirement.source_reference}</p>
            <dl className="comparison-list">
              <div>
                <dt>Observed</dt>
                <dd>{requirement.observed}</dd>
              </div>
              <div>
                <dt>Required</dt>
                <dd>{requirement.expected}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

function TimelinePanel({ run }: { run: RunDetailView }) {
  const hasPendingDecision = run.pending_decision !== null;
  return (
    <section
      className="control-panel timeline-panel"
      aria-labelledby="timeline-heading"
    >
      <header className="control-panel-head">
        <div>
          <span className="panel-index">02 / Execution</span>
          <h2 id="timeline-heading">Run timeline</h2>
        </div>
        <span className="live-label">
          <Radio aria-hidden="true" /> {timelineLabel(run.run_state)}
        </span>
      </header>
      <ol className="timeline">
        {run.events.map((event) => {
          const decisionNode = isDecisionEvent(event.type);
          return (
            <li className="timeline-event" key={event.id}>
              <div className="timeline-rail" aria-hidden="true">
                <span
                  className={
                    decisionNode
                      ? 'event-node event-node-decision'
                      : 'event-node'
                  }
                >
                  {decisionNode ? <LockKeyhole /> : <Check />}
                </span>
              </div>
              <div className="event-content">
                <div className="event-meta">
                  <time>{event.time}</time>
                  <span>{event.actor}</span>
                </div>
                <h3>{event.type}</h3>
                <p>{event.summary}</p>
                {event.evidence ? (
                  <span className="evidence-ref">
                    <Link2 aria-hidden="true" /> {event.evidence}
                  </span>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      {hasPendingDecision ? (
        <a
          className="decision-rail-link"
          href={`/runs/${run.run_id}/decision`}
        >
          Review protected content change
        </a>
      ) : null}
    </section>
  );
}

function PackagePanel({ run }: { run: RunDetailView }) {
  return (
    <section
      className="control-panel package-panel"
      aria-labelledby="package-heading"
    >
      <header className="control-panel-head">
        <div>
          <span className="panel-index">03 / Package</span>
          <h2 id="package-heading">Asset lineage</h2>
        </div>
        <span className="mono">
          {run.assets.length} asset{run.assets.length === 1 ? '' : 's'}
        </span>
      </header>
      <div className="asset-stack">
        {run.assets.map((asset) => (
          <article className="asset-card" key={asset.id}>
            <div className="asset-icon" aria-hidden="true">
              {asset.provenance.startsWith('ORIGINAL') ? (
                <FileText />
              ) : (
                <Wrench />
              )}
            </div>
            <div className="asset-main">
              <span
                className={`provenance-label ${asset.provenance.startsWith('ORIGINAL') ? 'original-label' : ''}`}
              >
                {asset.provenance}
              </span>
              <h3>{asset.filename}</h3>
              <p>{asset.role}</p>
              <p className="asset-detail">{asset.detail}</p>
              <StatusBadge status={asset.status} />
            </div>
          </article>
        ))}
      </div>
      <div className="preservation-note">
        <LockKeyhole aria-hidden="true" />
        <p>
          <strong>Original locked.</strong> All remediation targets delivery
          copies.
        </p>
      </div>
    </section>
  );
}

export function ControlRoomView({ runId }: { runId: string }) {
  const fetcher = useCallback(() => getRun(runId), [runId]);
  const state = usePolledResource<RunDetailView>(fetcher, {
    intervalMs: 2500,
    shouldPoll: controlRoomShouldPoll,
  });

  const run = state.data;

  if (run === null) {
    return (
      <>
        <SectionHeading
          eyebrow="Delivery control room"
          title="Delivery control room"
        />
        {state.status === 'error' ? (
          <ErrorState title="Run unavailable" message={state.error ?? undefined} />
        ) : (
          <LoadingState
            label="Loading run"
            copy="Reading deterministic run state, findings, and lineage."
          />
        )}
      </>
    );
  }

  return (
    <>
      <SectionHeading
        eyebrow={`${run.program_title} / ${run.run_id}`}
        title="Delivery control room"
        description={`${run.destination_profile} · run state ${run.run_state} · cycle ${run.cycle}.`}
        action={
          run.pending_decision !== null ? (
            <a
              className="primary-link decision-link"
              href={`/runs/${run.run_id}/decision`}
            >
              Resolve decision <ArrowUpRight aria-hidden="true" size={15} />
            </a>
          ) : run.evidence_available ? (
            <a className="primary-link" href={`/runs/${run.run_id}/evidence`}>
              View evidence <ArrowUpRight aria-hidden="true" size={15} />
            </a>
          ) : undefined
        }
      />

      <section className="run-status-strip" aria-label="Current run state">
        <div>
          <span className="meta-label">Run state</span>
          <strong>{run.run_state}</strong>
        </div>
        <StatusBadge status={run.display_status} />
        <dl>
          <div>
            <dt>Requirements</dt>
            <dd>{run.metrics.requirements_total}</dd>
          </div>
          <div>
            <dt>Passing</dt>
            <dd>{run.metrics.passing}</dd>
          </div>
          <div>
            <dt>Remediated</dt>
            <dd>{run.metrics.remediated}</dd>
          </div>
          <div>
            <dt>Pending</dt>
            <dd>{run.metrics.pending}</dd>
          </div>
        </dl>
      </section>

      <div className="control-grid">
        <RequirementsPanel run={run} />
        <TimelinePanel run={run} />
        <PackagePanel run={run} />
      </div>
    </>
  );
}
