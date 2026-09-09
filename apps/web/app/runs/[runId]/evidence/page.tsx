import { AppShell } from '@/components/aircheck/app-shell';
import { EvidenceView } from '@/components/aircheck/evidence-view';

export default async function EvidencePage({
  params,
}: PageProps<'/runs/[runId]/evidence'>) {
  const { runId } = await params;
  return (
    <AppShell active="evidence" context="Operations / Evidence" runId={runId}>
      <EvidenceView runId={runId} />
    </AppShell>
  );
}
