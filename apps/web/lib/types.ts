// Product status vocabulary rendered by the StatusBadge. These labels are the
// exact strings the backend emits as `display_status` (run/delivery level) and
// as requirement/asset `status`. The frontend never computes them — it only
// renders the value the deterministic runtime projected.

export type ProductStatus =
  | 'PASS'
  | 'FAIL'
  | 'FIXED'
  | 'DECISION REQUIRED'
  | 'DELIVERY READY'
  | 'BLOCKED'
  | 'UNVERIFIED'
  | 'INSPECTING';
