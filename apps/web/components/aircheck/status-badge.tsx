import type { ProductStatus } from '@/lib/types';

const statusClass: Record<ProductStatus, string> = {
  PASS: 'status-pass',
  FAIL: 'status-fail',
  FIXED: 'status-fixed',
  'DECISION REQUIRED': 'status-decision-required',
  'DELIVERY READY': 'status-delivery-ready',
  BLOCKED: 'status-blocked',
  UNVERIFIED: 'status-unverified',
  INSPECTING: 'status-inspecting',
};

export function StatusBadge({ status }: { status: ProductStatus }) {
  return (
    <span
      className={`status-badge ${statusClass[status]}`}
      data-status={status}
    >
      {status}
    </span>
  );
}
