import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { StatusBadge } from '@/components/aircheck/status-badge';
import { DeliveriesView } from '@/components/aircheck/deliveries-view';
import { NewDeliveryView } from '@/components/aircheck/new-delivery-view';
import { ControlRoomView } from '@/components/aircheck/control-room-view';
import { DecisionView } from '@/components/aircheck/decision-view';
import { EvidenceView } from '@/components/aircheck/evidence-view';

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }));
vi.mock('@/lib/navigate', () => ({ navigateTo }));

// ------------------------------------------------------------------ canned API

const META = {
  run_statuses: [],
  product_statuses: [],
  terminal_outcomes: ['DELIVERY_READY', 'BLOCKED'],
  profiles: [
    {
      profile_id: 'northstar_broadcast_master_v1',
      profile_name: 'Northstar Broadcast Master',
      description: '15 normalized checks · strict broadcast delivery',
      checks_count: 15,
    },
    {
      profile_id: 'northstar_digital_preview_v1',
      profile_name: 'Northstar Digital Preview',
      description: '13 normalized checks · digital streaming preview',
      checks_count: 13,
    },
  ],
};

const DELIVERIES = {
  runs: [
    {
      run_id: 'run_llk_broadcast_042',
      program_id: 'the_last_lightkeeper',
      program_title: 'The Last Lightkeeper',
      destination_profile: 'Northstar Broadcast Master',
      profile_id: 'northstar_broadcast_master_v1',
      run_state: 'AWAITING_HUMAN_DECISION',
      display_status: 'DECISION REQUIRED',
      finding_summary: '1 open decision',
      verified: 14,
      total: 15,
      updated_at: '10:38:00 UTC',
      terminal_verdict: null,
    },
    {
      run_id: 'run_llk_preview_018',
      program_id: 'the_last_lightkeeper',
      program_title: 'The Last Lightkeeper',
      destination_profile: 'Northstar Digital Preview',
      profile_id: 'northstar_digital_preview_v1',
      run_state: 'DELIVERY_READY',
      display_status: 'DELIVERY READY',
      finding_summary: 'All 13 checks verified',
      verified: 13,
      total: 13,
      updated_at: '10:38:46 UTC',
      terminal_verdict: {
        outcome: 'DELIVERY_READY',
        cycle: 1,
        reasons: [],
        computed_at: '2026-09-08T10:38:46+00:00',
        originals_integrity_verified: true,
      },
    },
  ],
  counts: {
    active_runs: 1,
    decision_required: 1,
    delivery_ready: 1,
    unresolved_blockers: 0,
  },
};

const BROADCAST_DETAIL = {
  run_id: 'run_llk_broadcast_042',
  program_id: 'the_last_lightkeeper',
  program_title: 'The Last Lightkeeper',
  destination_profile: 'Northstar Broadcast Master',
  profile_id: 'northstar_broadcast_master_v1',
  run_state: 'AWAITING_HUMAN_DECISION',
  display_status: 'DECISION REQUIRED',
  cycle: 1,
  terminal_verdict: null,
  pending_decision: {
    decision_id: 'decision_1',
    finding_id: 'finding_1',
    option_id: 'option_1',
    status: 'PENDING',
    resolved: false,
    question: 'Create the proposed content-affecting delivery derivative?',
    consequences:
      'The source master is preserved; AIRCheck renders a new derivative and re-runs the full QC plan.',
    tool: 'create_normalized_audio_derivative',
    tier: 2,
    policy_tier: 'Tier 2 · content-affecting',
    asset_filename: 'THE_LAST_LIGHTKEEPER_NSBM_v1.mov',
    source_asset_role: 'Program master',
    measured_value: '-19.05 LUFS',
    required_value: 'Requires between -26 and -22 LUFS inclusive',
    delta: '+2.95 LUFS above the -22 LUFS ceiling',
    proposed_operation: 'Create one authorized normalized-audio delivery derivative.',
    original_preservation: 'The original master will remain unchanged.',
    requirement_id: 'req_loudness',
    requirement_text: 'Integrated program loudness',
    source_reference: 'Northstar Broadcast Master § 2. Program audio',
    source_quote: 'Program loudness must fall between -26 and -22 LUFS.',
    produces_derivative: true,
    decided_by: null,
    decision_choice: null,
  },
  requirements: [
    {
      id: 'req_loudness',
      category: 'Audio',
      description: 'Integrated program loudness',
      observed: '-19.05 LUFS',
      expected: 'Requires between -26 and -22 LUFS inclusive',
      status: 'DECISION REQUIRED',
      severity: 'BLOCKING',
      predicate_outcome: 'FAIL',
      source_reference: 'Northstar Broadcast Master § 2. Program audio',
      provenance: null,
      disposition: 'HUMAN_DECISION',
    },
    {
      id: 'req_filename',
      category: 'Naming',
      description: 'Compliant delivery filename',
      observed: 'THE_LAST_LIGHTKEEPER_NSBM_v1.mov',
      expected: 'Requires filename match',
      status: 'FIXED',
      severity: 'BLOCKING',
      predicate_outcome: 'PASS',
      source_reference: 'Northstar Broadcast Master § 1',
      provenance: null,
      disposition: 'AUTONOMOUS',
    },
  ],
  events: [
    {
      id: 'evt_1',
      seq: 1,
      time: '10:38:00 UTC',
      type: 'RUN_CREATED',
      actor: 'runtime',
      summary: 'Delivery run created.',
      evidence: null,
    },
    {
      id: 'evt_2',
      seq: 2,
      time: '10:38:10 UTC',
      type: 'AWAITING_HUMAN_DECISION',
      actor: 'runtime',
      summary: 'Protected audio change paused for human authority.',
      evidence: null,
    },
  ],
  assets: [
    {
      id: 'asset_o1',
      filename: 'the-last-lightkeeper-delivery-v1.mov',
      role: 'Program master',
      detail: 'PROGRAM_MASTER · 512 KB',
      provenance: 'ORIGINAL · PRESERVED',
      status: 'PASS',
      sha256: 'abc123',
    },
    {
      id: 'asset_d1',
      filename: 'THE_LAST_LIGHTKEEPER_NSBM_v1.mov',
      role: 'Program master',
      detail: 'PROGRAM_MASTER · 512 KB',
      provenance: 'DELIVERY DERIVATIVE',
      status: 'FIXED',
      sha256: 'def456',
    },
  ],
  metrics: {
    requirements_total: 15,
    passing: 10,
    remediated: 4,
    pending: 1,
    failed: 0,
  },
  evidence_available: false,
};

const READY_EVIDENCE = {
  run_id: 'run_llk_preview_018',
  program_title: 'The Last Lightkeeper',
  destination_profile: 'Northstar Digital Preview',
  run_state: 'DELIVERY_READY',
  terminal_verdict: {
    outcome: 'DELIVERY_READY',
    cycle: 1,
    reasons: [],
    computed_at: '2026-09-08T10:38:46+00:00',
    originals_integrity_verified: true,
  },
  earned_at: '2026-09-08T10:38:46+00:00',
  package_hash: '38274ba58146dc1d',
  requirements_verified: '13 / 13 requirements verified',
  metrics: {
    findings_resolved: 1,
    autonomous_fixes: 1,
    human_authorized: 0,
    open_blockers: 0,
  },
  artifacts: [
    {
      name: 'Run ledger',
      filename: 'ledger.json',
      size_bytes: 7641,
      meta: '7641 bytes · SHA-256 abc123def456…',
      download_url: '/runs/run_llk_preview_018/evidence/ledger.json',
      icon_type: 'ledger',
    },
  ],
  verification_rows: [
    {
      requirement_id: 'req_loudness',
      category: 'Audio',
      observed: '-19.0 LUFS',
      expected: 'Requires between -21 and -17 LUFS inclusive',
      status: 'PASS',
    },
  ],
};

// Same run state, but the authoritative terminal verdict is missing. The
// readiness invariant requires this to never render green.
const UNVERIFIED_EVIDENCE = {
  ...READY_EVIDENCE,
  terminal_verdict: null,
  earned_at: null,
};

// A human denial routes the run to BLOCKED directly, carrying no terminal
// verdict object. Evidence must present it as blocked, never as "still running".
const BLOCKED_EVIDENCE = {
  ...READY_EVIDENCE,
  run_state: 'BLOCKED',
  terminal_verdict: null,
  earned_at: null,
  requirements_verified: '14 / 15 requirements verified',
  metrics: {
    findings_resolved: 4,
    autonomous_fixes: 4,
    human_authorized: 0,
    open_blockers: 1,
  },
};

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** Route mocked fetch by URL substring. */
function installFetch(routes: Array<[string, (url: string) => Response]>) {
  const fn = vi.fn((url: string, _init?: RequestInit) => {
    for (const [needle, make] of routes) {
      if (url.includes(needle)) return Promise.resolve(make(url));
    }
    return Promise.reject(new Error(`unrouted fetch: ${url}`));
  });
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

beforeEach(() => {
  navigateTo.mockClear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ------------------------------------------------------------- five surfaces

test('deliveries surface renders real run rows and live counts', async () => {
  installFetch([['/runs', () => jsonResponse(200, DELIVERIES)]]);
  render(<DeliveriesView />);

  expect(await screen.findByText('run_llk_broadcast_042')).toBeInTheDocument();
  expect(screen.getByText('run_llk_preview_018')).toBeInTheDocument();
  expect(screen.getByText('All 13 checks verified')).toBeInTheDocument();
  // Live count projection (decision_required = 1, zero-padded).
  const decisionRequired = screen
    .getByText('Decision required')
    .closest('.stat');
  expect(decisionRequired).toHaveTextContent('01');
});

test('deliveries no longer exposes the mock data-state switcher', async () => {
  installFetch([['/runs', () => jsonResponse(200, DELIVERIES)]]);
  render(<DeliveriesView />);
  await screen.findByText('run_llk_broadcast_042');

  for (const state of ['Loaded', 'Loading', 'Empty', 'Error']) {
    expect(screen.queryByRole('button', { name: state })).toBeNull();
  }
});

test('deliveries shows the real empty state when there are no runs', async () => {
  installFetch([
    [
      '/runs',
      () =>
        jsonResponse(200, {
          runs: [],
          counts: {
            active_runs: 0,
            decision_required: 0,
            delivery_ready: 0,
            unresolved_blockers: 0,
          },
        }),
    ],
  ]);
  render(<DeliveriesView />);
  expect(
    await screen.findByText('No deliveries in this view'),
  ).toBeInTheDocument();
});

test('deliveries shows a product-safe error state on API failure', async () => {
  installFetch([['/runs', () => jsonResponse(500, { detail: 'boom' })]]);
  render(<DeliveriesView />);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Run ledger unavailable');
});

test('deliveries surfaces a loading state before data resolves', () => {
  installFetch([['/runs', () => new Promise(() => {}) as unknown as Response]]);
  render(<DeliveriesView />);
  expect(screen.getByLabelText('Loading run ledger')).toBeInTheDocument();
});

test('new delivery surface renders and drives a real create command', async () => {
  const fetchFn = installFetch([
    ['/meta/states', () => jsonResponse(200, META)],
    [
      '/runs',
      () =>
        jsonResponse(201, {
          run_id: 'run_new_123',
          status: 'AWAITING_HUMAN_DECISION',
          profile_id: 'northstar_broadcast_master_v1',
          profile_name: 'Northstar Broadcast Master',
          message: 'ok',
        }),
    ],
  ]);
  render(<NewDeliveryView />);

  // Real /meta/states projection drives the receipt (15 checks, not a mock 12).
  expect(
    await screen.findByText(/15 normalized checks/i),
  ).toBeInTheDocument();

  fireEvent.click(
    screen.getByRole('button', { name: 'Start delivery check' }),
  );

  await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/runs/run_new_123'));
  const postCall = fetchFn.mock.calls.find(
    ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
  );
  expect(postCall).toBeTruthy();
});

test('control room renders real requirements, timeline, and lineage', async () => {
  installFetch([
    ['/runs/run_llk_broadcast_042', () => jsonResponse(200, BROADCAST_DETAIL)],
  ]);
  render(<ControlRoomView runId="run_llk_broadcast_042" />);

  expect(
    await screen.findByRole('heading', { name: /Delivery control room/i }),
  ).toBeInTheDocument();
  // Real metric projection: one requirement is pending a decision.
  const pending = screen.getByText('Pending').closest('div');
  expect(pending).toHaveTextContent('1');
  // Real lineage: preserved original and its derivative.
  expect(
    screen.getByText('the-last-lightkeeper-delivery-v1.mov'),
  ).toBeInTheDocument();
  // Real run state, shown in the status strip.
  const strip = screen.getByLabelText('Current run state');
  expect(
    within(strip).getByText('AWAITING_HUMAN_DECISION'),
  ).toBeInTheDocument();
});

test('decision surface shows real measured/required/delta and both authority choices', async () => {
  installFetch([
    [
      '/runs/run_llk_broadcast_042/decision',
      () => jsonResponse(200, BROADCAST_DETAIL.pending_decision),
    ],
  ]);
  render(<DecisionView runId="run_llk_broadcast_042" />);

  expect(
    await screen.findByText('THE_LAST_LIGHTKEEPER_NSBM_v1.mov'),
  ).toBeInTheDocument();
  expect(screen.getByText('-19.05 LUFS')).toBeInTheDocument();
  expect(
    screen.getByText('Requires between -26 and -22 LUFS inclusive'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('+2.95 LUFS above the -22 LUFS ceiling'),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Create compliant derivative' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Leave delivery blocked' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/original master will remain unchanged/i),
  ).toBeInTheDocument();
});

test('approving a decision submits the human choice and lets the runtime advance', async () => {
  // The GET returns the pending decision; the POST records the human choice.
  const fn = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return jsonResponse(200, {
        run_id: 'run_llk_broadcast_042',
        decision_id: 'decision_1',
        status: 'RESOLVED',
        choice: 'APPROVED',
        next_status: 'DELIVERY_READY',
        message: 'ok',
      });
    }
    return jsonResponse(200, BROADCAST_DETAIL.pending_decision);
  });
  global.fetch = fn as unknown as typeof fetch;

  render(<DecisionView runId="run_llk_broadcast_042" />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Create compliant derivative' }),
  );

  await waitFor(() =>
    expect(navigateTo).toHaveBeenCalledWith('/runs/run_llk_broadcast_042'),
  );
  const posted = fn.mock.calls.some(([, init]) => init?.method === 'POST');
  expect(posted).toBe(true);
});

test('decision surface offers no authority controls when nothing is pending', async () => {
  installFetch([
    [
      '/runs/run_x/decision',
      () => jsonResponse(404, { detail: 'Run run_x has no human-decision requirement.' }),
    ],
  ]);
  render(<DecisionView runId="run_x" />);

  expect(await screen.findByText('No decision is pending')).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Create compliant derivative' }),
  ).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Leave delivery blocked' }),
  ).toBeNull();
});

test('evidence surface renders the earned terminal verdict and a real download link', async () => {
  installFetch([
    [
      '/runs/run_llk_preview_018/evidence',
      () => jsonResponse(200, READY_EVIDENCE),
    ],
  ]);
  render(<EvidenceView runId="run_llk_preview_018" />);

  expect(
    await screen.findByRole('heading', { name: /Delivery ready/i }),
  ).toBeInTheDocument();
  expect(screen.getByText('DELIVERY READY')).toHaveAttribute(
    'data-status',
    'DELIVERY READY',
  );
  const download = screen.getByRole('link', { name: 'Download Run ledger' });
  expect(download).toHaveAttribute(
    'href',
    expect.stringContaining('/runs/run_llk_preview_018/evidence/ledger.json'),
  );
});

test('readiness invariant: a null terminal verdict is never rendered green', async () => {
  installFetch([
    [
      '/runs/run_llk_preview_018/evidence',
      () => jsonResponse(200, UNVERIFIED_EVIDENCE),
    ],
  ]);
  render(<EvidenceView runId="run_llk_preview_018" />);

  // Must NOT claim delivery ready.
  expect(await screen.findByText('UNVERIFIED')).toHaveAttribute(
    'data-status',
    'UNVERIFIED',
  );
  expect(
    screen.queryByRole('heading', { name: /^Delivery ready$/i }),
  ).toBeNull();
  expect(screen.queryByText('DELIVERY READY')).toBeNull();
});

test('evidence for a blocked run is presented as blocked, never as still running', async () => {
  installFetch([
    [
      '/runs/run_llk_broadcast_042/evidence',
      () => jsonResponse(200, BLOCKED_EVIDENCE),
    ],
  ]);
  render(<EvidenceView runId="run_llk_broadcast_042" />);

  expect(await screen.findByText('BLOCKED')).toHaveAttribute(
    'data-status',
    'BLOCKED',
  );
  expect(screen.queryByText('DELIVERY READY')).toBeNull();
  expect(screen.queryByText(/not final until/i)).toBeNull();
});

// -------------------------------------------------------------- polling lifecycle

test('control room polls while active and stops once the run is terminal', async () => {
  vi.useFakeTimers();
  let state = 'AWAITING_HUMAN_DECISION';
  const fn = vi.fn(async () =>
    jsonResponse(200, { ...BROADCAST_DETAIL, run_state: state }),
  );
  global.fetch = fn as unknown as typeof fetch;

  render(<ControlRoomView runId="run_llk_broadcast_042" />);
  // Initial fetch (flush the load microtask).
  await vi.advanceTimersByTimeAsync(0);
  expect(fn).toHaveBeenCalledTimes(1);

  // While active, the interval fetches again.
  await vi.advanceTimersByTimeAsync(2600);
  expect(fn.mock.calls.length).toBeGreaterThanOrEqual(2);

  // The run reaches a terminal verdict; the next poll observes it.
  state = 'DELIVERY_READY';
  await vi.advanceTimersByTimeAsync(2600);
  const countAfterTerminal = fn.mock.calls.length;

  // Polling must cease once the run is terminal.
  await vi.advanceTimersByTimeAsync(8000);
  expect(fn.mock.calls.length).toBe(countAfterTerminal);
});

// ---------------------------------------------------------------- status badges

describe('status vocabulary', () => {
  test.each([
    'PASS',
    'FAIL',
    'FIXED',
    'DECISION REQUIRED',
    'DELIVERY READY',
    'BLOCKED',
    'UNVERIFIED',
    'INSPECTING',
  ] as const)('renders the %s status', (status) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(status)).toHaveAttribute('data-status', status);
  });
});
