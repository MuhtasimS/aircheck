import { ArrowUpRight } from 'lucide-react';

import { AppShell } from '@/components/aircheck/app-shell';
import { DeliveriesView } from '@/components/aircheck/deliveries-view';
import { SectionHeading } from '@/components/aircheck/section-heading';

export default function DeliveriesPage() {
  return (
    <AppShell active="deliveries" context="Operations / Deliveries">
      <SectionHeading
        eyebrow="Delivery operations"
        title="Deliveries"
        description="Technical QC runs, human authority boundaries, and terminal evidence across destination profiles."
        action={
          <a className="primary-link" href="/new">
            New delivery <ArrowUpRight aria-hidden="true" size={15} />
          </a>
        }
      />

      <DeliveriesView />
    </AppShell>
  );
}
