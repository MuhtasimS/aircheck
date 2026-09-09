'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, AudioLines, LockKeyhole, ShieldAlert } from 'lucide-react';

import { SectionHeading } from '@/components/aircheck/section-heading';
import { StatusBadge } from '@/components/aircheck/status-badge';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '@/components/aircheck/view-state';
import { Button } from '@/components/ui/button';
import {
  ApiError,
  getDecision,
  submitDecision,
  type PendingDecisionView,
} from '@/lib/api';
import { navigateTo } from '@/lib/navigate';

type Phase = 'loading' | 'ready' | 'none' | 'error';

export function DecisionView({ runId }: { runId: string }) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [decision, setDecision] = useState<PendingDecisionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<null | 'approve' | 'deny'>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    let active = true;
    mounted.current = true;

    const load = async () => {
      try {
        const data = await getDecision(runId);
        if (!active) return;
        setDecision(data);
        setPhase('ready');
      } catch (err) {
        if (!active) return;
        if (err instanceof ApiError && err.status === 404) {
          setPhase('none');
          return;
        }
        setError(err instanceof Error ? err.message : 'Unexpected error.');
        setPhase('error');
      }
    };

    void load();
    const id = setInterval(() => {
      // Keep the pending decision fresh until it is resolved (here or elsewhere).
      void load();
    }, 3500);

    return () => {
      active = false;
      mounted.current = false;
      clearInterval(id);
    };
  }, [runId]);

  const onDecide = useCallback(
    async (approved: boolean) => {
      setSubmitting(approved ? 'approve' : 'deny');
      setSubmitError(null);
      try {
        await submitDecision(runId, approved);
        if (!mounted.current) return;
        // The runtime — not the frontend — advances the run. Return to the
        // control room to watch re-verification / the terminal outcome.
        navigateTo(`/runs/${runId}`);
      } catch (err) {
        if (!mounted.current) return;
        setSubmitError(err instanceof Error ? err.message : 'Unexpected error.');
        setSubmitting(null);
      }
    },
    [runId],
  );

  const backLink = (
    <a className="back-link" href={`/runs/${runId}`}>
      <ArrowLeft aria-hidden="true" size={14} /> Back to control room
    </a>
  );

  if (phase === 'loading') {
    return (
      <>
        <SectionHeading
          eyebrow="Human authority"
          title="Human decision"
          action={backLink}
        />
        <LoadingState
          label="Loading decision"
          copy="Reading the pending authority request from durable run state."
        />
      </>
    );
  }

  if (phase === 'error') {
    return (
      <>
        <SectionHeading
          eyebrow="Human authority"
          title="Human decision"
          action={backLink}
        />
        <ErrorState title="Decision unavailable" message={error ?? undefined} />
      </>
    );
  }

  if (phase === 'none' || decision === null) {
    return (
      <>
        <SectionHeading
          eyebrow="Human authority"
          title="Human decision"
          action={backLink}
        />
        <EmptyState
          title="No decision is pending"
          copy="This run has not reached an authority boundary that requires a human decision. Everything AIRCheck can do autonomously is reflected in the control room."
        />
      </>
    );
  }

  const resolved = decision.resolved;

  return (
    <>
      <SectionHeading
        eyebrow={`Human authority / ${decision.policy_tier}`}
        title={
          resolved
            ? 'Authority decision recorded'
            : 'Content change requires approval'
        }
        description={
          resolved
            ? 'This authority request has been resolved. The runtime owns the outcome.'
            : 'One content-affecting operation is paused. Everything AIRCheck can safely do without you is already complete.'
        }
        action={backLink}
      />

      <div className="decision-layout">
        <section
          className="decision-card"
          aria-labelledby="decision-asset-heading"
        >
          <div className="decision-asset">
            <span className="decision-icon">
              <AudioLines aria-hidden="true" />
            </span>
            <div>
              <p className="eyebrow">Protected source asset</p>
              <h2 id="decision-asset-heading" className="break-anywhere">
                {decision.asset_filename}
              </h2>
              <p>{decision.source_asset_role}</p>
            </div>
            <span className="locked-label">
              <LockKeyhole aria-hidden="true" /> Original locked
            </span>
          </div>

          <div className="measurement-grid">
            <div className="measurement measurement-fail">
              <span className="meta-label">Measured</span>
              <strong>{decision.measured_value}</strong>
              <small>Inspected fact</small>
            </div>
            <div className="measurement">
              <span className="meta-label">Required</span>
              <strong>{decision.required_value}</strong>
              <small>Destination requirement</small>
            </div>
            {decision.delta ? (
              <div className="measurement-delta">
                <span>{decision.delta}</span>
                Distance from target
              </div>
            ) : null}
          </div>

          <div className="operation-copy">
            <span className="panel-index">Proposed operation</span>
            <h3>{decision.proposed_operation}</h3>
            <p>{decision.consequences}</p>
          </div>

          {resolved ? (
            <output className="decision-resolved">
              <StatusBadge
                status={
                  decision.decision_choice === 'APPROVED'
                    ? 'FIXED'
                    : 'BLOCKED'
                }
              />
              <p>
                Recorded as <strong>{decision.decision_choice}</strong>
                {decision.decided_by ? ` by ${decision.decided_by}` : ''}.
              </p>
            </output>
          ) : (
            <>
              <div className="decision-actions">
                <Button
                  type="button"
                  className="button-primary"
                  disabled={submitting !== null}
                  onClick={() => onDecide(true)}
                >
                  {submitting === 'approve'
                    ? 'Authorizing…'
                    : 'Create compliant derivative'}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="button-danger"
                  disabled={submitting !== null}
                  onClick={() => onDecide(false)}
                >
                  {submitting === 'deny' ? 'Recording…' : 'Leave delivery blocked'}
                </Button>
              </div>
              {submitError ? (
                <p className="decision-error" role="alert">
                  {submitError}
                </p>
              ) : null}
            </>
          )}

          <p className="original-promise">
            <LockKeyhole aria-hidden="true" /> The original master will remain
            unchanged.
          </p>
        </section>

        <aside className="authority-card">
          <ShieldAlert aria-hidden="true" />
          <p className="eyebrow">Why you’re being asked</p>
          <h2>AIRCheck has reached its authority boundary.</h2>
          <p>{decision.question}</p>
          <dl className="authority-ledger">
            <div>
              <dt>Policy</dt>
              <dd>{decision.policy_tier}</dd>
            </div>
            <div>
              <dt>Requested by</dt>
              <dd>AIRCheck</dd>
            </div>
            {decision.tool ? (
              <div>
                <dt>Operation</dt>
                <dd className="break-anywhere">{decision.tool}</dd>
              </div>
            ) : null}
            <div>
              <dt>Source</dt>
              <dd>{decision.source_reference}</dd>
            </div>
          </dl>
        </aside>
      </div>
    </>
  );
}
