import { AppShell } from '@/components/aircheck/app-shell';
import { NewDeliveryView } from '@/components/aircheck/new-delivery-view';
import { SectionHeading } from '@/components/aircheck/section-heading';

export default function NewDeliveryPage() {
  return (
    <AppShell active="new" context="Operations / New delivery">
      <SectionHeading
        eyebrow="Intake / 00"
        title="Start a delivery check"
        description="Pair one preserved media package with one human-readable destination specification. AIRCheck will derive the work from both."
      />

      <NewDeliveryView />
    </AppShell>
  );
}
