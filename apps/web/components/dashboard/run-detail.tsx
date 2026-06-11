'use client';

import { Copy } from 'lucide-react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { EmptyState } from '@/components/ui/empty-state';
import { LifecycleTimeline } from '@/components/ui/lifecycle-timeline';
import { LogViewer } from '@/components/ui/log-viewer';
import { Panel } from '@/components/ui/panel';
import { StatusChip } from '@/components/ui/status-chip';
import { useToast } from '@/components/ui/toast';
import { useStreamSnapshot } from '@/lib/data/use-live-data';
import type { JobPreempted } from '@/lib/data/types';

function IdField({ label, value }: { label: string; value: string | number }) {
  const toast = useToast();
  return (
    <span className="flex items-center gap-1.5 font-mono text-2xs text-muted tnum">
      {label} <span className="text-secondary">{value}</span>
      <button
        type="button"
        aria-label={`Copy ${label}`}
        className="text-disabled hover:text-primary"
        onClick={() => {
          navigator.clipboard.writeText(String(value));
          toast.add({ title: 'Copied', description: `${label} ${value}` });
        }}
      >
        <Copy size={11} strokeWidth={1.75} absoluteStrokeWidth />
      </button>
    </span>
  );
}

export function RunDetail({ jobId }: { jobId: number }) {
  const { runs } = useStreamSnapshot();
  const run = runs.find((r) => r.jobId === jobId);

  if (!run) {
    return (
      <EmptyState
        title="RUN NOT FOUND"
        description="This job is not in the current log window."
        action={
          <Link href="/dashboard" className="text-xs text-accent-text underline underline-offset-2">
            Back to live runs
          </Link>
        }
      />
    );
  }

  const preemption = run.entries.find(
    (e) => e.message === 'Job ended due to spot preemption',
  ) as JobPreempted | undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="min-w-0 truncate text-xl font-medium text-primary">{run.repo}</h1>
        <StatusChip status={run.status} />
      </div>

      <div className="flex flex-wrap gap-4">
        <IdField label="job_id" value={run.jobId} />
        <IdField label="run_id" value={run.runId} />
        <IdField label="run_attempt" value={run.runAttempt} />
        {run.runnerName && <IdField label="runner" value={run.runnerName} />}
      </div>

      {preemption && (
        <div className="rounded-md border border-status-preempted-border bg-status-preempted-bg p-4">
          <div className="stamp text-2xs text-status-preempted">QUENCHED — SPOT PREEMPTION</div>
          <p className="mt-1.5 font-mono text-xs text-secondary">
            “{preemption.message}” · vm_name={preemption.vm_name} · conclusion=
            {preemption.conclusion}
          </p>
          <p className="mt-1 text-xs text-muted">
            GCE reclaimed this Spot VM mid-job. Re-run the job, or drop the{' '}
            <span className="font-mono">spot</span> label for on-demand capacity.
          </p>
        </div>
      )}

      <Panel label="LIFECYCLE">
        <LifecycleTimeline run={run} />
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Panel label="RUNNER VM" className="self-start">
          <dl className="flex flex-col gap-2.5 font-mono text-xs tnum">
            {(
              [
                ['vm_name', run.vmName],
                ['zone', run.zone],
                ['image_version', run.imageVersion],
                ['vm_creation', run.vmCreationMs != null ? `${run.vmCreationMs} ms` : undefined],
                [
                  'time_to_runner',
                  run.timeToRunnerSeconds != null
                    ? `${run.timeToRunnerSeconds.toFixed(2)} s`
                    : undefined,
                ],
              ] as const
            ).map(
              ([key, value]) =>
                value != null && (
                  <div key={key} className="flex justify-between gap-3">
                    <dt className="text-muted">{key}</dt>
                    <dd className="truncate text-secondary">{value}</dd>
                  </div>
                ),
            )}
            <div className="flex justify-between gap-3">
              <dt className="text-muted">machine</dt>
              <dd className="flex gap-1.5">
                {run.machineType && <Badge>{run.machineType}</Badge>}
                {run.spot && <Badge variant="spot">spot</Badge>}
              </dd>
            </div>
          </dl>
        </Panel>

        <LogViewer entries={run.entries} />
      </div>
    </div>
  );
}
