import { AppShell } from '@/components/aircheck/app-shell';
import { ControlRoomView } from '@/components/aircheck/control-room-view';

export default async function ControlRoomPage({
  params,
}: PageProps<'/runs/[runId]'>) {
  const { runId } = await params;
  return (
    <AppShell active="control" context="Operations / Control room" runId={runId}>
      <ControlRoomView runId={runId} />
    </AppShell>
  );
}
