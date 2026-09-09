import { AppShell } from '@/components/aircheck/app-shell';
import { DecisionView } from '@/components/aircheck/decision-view';

export default async function DecisionPage({
  params,
}: PageProps<'/runs/[runId]/decision'>) {
  const { runId } = await params;
  return (
    <AppShell
      active="decision"
      context="Operations / Human decision"
      runId={runId}
    >
      <DecisionView runId={runId} />
    </AppShell>
  );
}
