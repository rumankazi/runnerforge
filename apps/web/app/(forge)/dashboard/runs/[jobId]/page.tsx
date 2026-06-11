import { RunDetail } from '@/components/dashboard/run-detail';

export const metadata = { title: 'Run detail — RunnerForge' };

export default async function RunDetailPage({ params }: PageProps<'/dashboard/runs/[jobId]'>) {
  const { jobId } = await params;
  return <RunDetail jobId={Number(jobId)} />;
}
