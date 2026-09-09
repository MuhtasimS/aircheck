// Typed client for the AIRCheck application API (the M5 projection/command
// façade over the deterministic M4 runtime). Every shape here mirrors a
// Pydantic model in `apps/api/models.py`; the frontend consumes these
// projections verbatim and never manufactures domain truth from them.

import type { ProductStatus } from '@/lib/types';

// The browser talks directly to the FastAPI service. The origin defaults to the
// local development API and can be overridden at runtime (e.g. for a hosted
// demo) by setting `window.__AIRCHECK_API_BASE__` before the app loads.
export function apiBase(): string {
  if (typeof globalThis !== 'undefined') {
    const override = (globalThis as { __AIRCHECK_API_BASE__?: unknown })
      .__AIRCHECK_API_BASE__;
    if (typeof override === 'string' && override.length > 0) {
      return override.replace(/\/$/, '');
    }
  }
  return 'http://localhost:8000';
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${apiBase()}${path}`, {
      headers: { Accept: 'application/json' },
      ...init,
    });
  } catch {
    // Network/connection failure — the API is unreachable. Product-safe message.
    throw new ApiError(0, 'AIRCheck could not reach the delivery runtime.');
  }
  if (!resp.ok) {
    let detail = `Request failed (${resp.status}).`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === 'string' && body.detail.length > 0) {
        detail = body.detail;
      }
    } catch {
      // Non-JSON error body; keep the generic status message.
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

// ------------------------------------------------------------------ meta types

export interface StatusMeta {
  name: string;
  label: string;
  badge_variant: string;
  terminal: boolean;
  description: string;
}

export interface ProfileMeta {
  profile_id: string;
  profile_name: string;
  description: string;
  checks_count: number;
}

export interface MetaStatesResponse {
  run_statuses: StatusMeta[];
  product_statuses: StatusMeta[];
  terminal_outcomes: string[];
  profiles: ProfileMeta[];
}

// ------------------------------------------------------------- shared projections

export interface TerminalVerdict {
  outcome: string;
  cycle: number;
  reasons: string[];
  computed_at: string;
  originals_integrity_verified: boolean;
}

export interface DeliverySummary {
  run_id: string;
  program_id: string;
  program_title: string;
  destination_profile: string;
  profile_id: string;
  run_state: string;
  display_status: ProductStatus;
  finding_summary: string;
  verified: number;
  total: number;
  updated_at: string;
  terminal_verdict: TerminalVerdict | null;
}

export interface DeliveriesCounts {
  active_runs: number;
  decision_required: number;
  delivery_ready: number;
  unresolved_blockers: number;
}

export interface DeliveriesResponse {
  runs: DeliverySummary[];
  counts: DeliveriesCounts;
}

export interface Provenance {
  quote: string;
  document_title: string;
  line_start: number | null;
  line_end: number | null;
}

export interface RequirementView {
  id: string;
  category: string;
  description: string;
  observed: string;
  expected: string;
  status: ProductStatus;
  severity: string;
  predicate_outcome: string | null;
  source_reference: string;
  provenance: Provenance | null;
  disposition: string;
}

export interface TimelineEventView {
  id: string;
  seq: number;
  time: string;
  type: string;
  actor: string;
  summary: string;
  evidence: string | null;
}

export interface AssetView {
  id: string;
  filename: string;
  role: string;
  detail: string;
  provenance: string;
  status: ProductStatus;
  sha256: string | null;
}

export interface PendingDecisionView {
  decision_id: string;
  finding_id: string;
  option_id: string | null;
  status: string;
  resolved: boolean;
  question: string;
  consequences: string;
  tool: string | null;
  tier: number | null;
  policy_tier: string;
  asset_filename: string;
  source_asset_role: string;
  measured_value: string;
  required_value: string;
  delta: string | null;
  proposed_operation: string;
  original_preservation: string;
  requirement_id: string;
  requirement_text: string;
  source_reference: string;
  source_quote: string | null;
  produces_derivative: boolean;
  decided_by: string | null;
  decision_choice: string | null;
}

export interface RunMetrics {
  requirements_total: number;
  passing: number;
  remediated: number;
  pending: number;
  failed: number;
}

export interface RunDetailView {
  run_id: string;
  program_id: string;
  program_title: string;
  destination_profile: string;
  profile_id: string;
  run_state: string;
  display_status: ProductStatus;
  cycle: number;
  terminal_verdict: TerminalVerdict | null;
  pending_decision: PendingDecisionView | null;
  requirements: RequirementView[];
  events: TimelineEventView[];
  assets: AssetView[];
  metrics: RunMetrics;
  evidence_available: boolean;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  profile_id: string;
  profile_name: string;
  message: string;
}

export interface DecideResponse {
  run_id: string;
  decision_id: string;
  status: string;
  choice: string;
  next_status: string;
  message: string;
}

export interface EvidenceArtifactView {
  name: string;
  filename: string;
  size_bytes: number;
  meta: string;
  download_url: string;
  icon_type: string;
}

export interface EvidenceVerificationRow {
  requirement_id: string;
  category: string;
  observed: string;
  expected: string;
  status: ProductStatus;
}

export interface EvidenceMetrics {
  findings_resolved: number;
  autonomous_fixes: number;
  human_authorized: number;
  open_blockers: number;
}

export interface EvidenceResponse {
  run_id: string;
  program_title: string;
  destination_profile: string;
  run_state: string;
  terminal_verdict: TerminalVerdict | null;
  earned_at: string | null;
  package_hash: string | null;
  requirements_verified: string;
  metrics: EvidenceMetrics;
  artifacts: EvidenceArtifactView[];
  verification_rows: EvidenceVerificationRow[];
}

// ------------------------------------------------------------------- endpoints

export function getMetaStates(): Promise<MetaStatesResponse> {
  return request('/meta/states');
}

export function listRuns(): Promise<DeliveriesResponse> {
  return request('/runs');
}

export function getRun(runId: string): Promise<RunDetailView> {
  return request(`/runs/${encodeURIComponent(runId)}`);
}

export function getDecision(runId: string): Promise<PendingDecisionView> {
  return request(`/runs/${encodeURIComponent(runId)}/decision`);
}

export function submitDecision(
  runId: string,
  approved: boolean,
): Promise<DecideResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ approved }),
  });
}

export function getEvidence(runId: string): Promise<EvidenceResponse> {
  return request(`/runs/${encodeURIComponent(runId)}/evidence`);
}

export interface CreateRunBody {
  profile_id: string;
  package_type: 'hero' | 'clean';
}

export function createRun(body: CreateRunBody): Promise<CreateRunResponse> {
  return request('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
}

// Absolute URL for an evidence artifact download. The API serves it with a
// Content-Disposition attachment header, so a plain anchor performs a real,
// working download of an artifact that genuinely exists.
export function evidenceDownloadUrl(runId: string, filename: string): string {
  return `${apiBase()}/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(filename)}`;
}
