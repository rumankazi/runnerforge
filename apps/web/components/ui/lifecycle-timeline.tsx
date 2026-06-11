import { cn } from '@/lib/cn';
import { formatDuration, type JobRun, type RunStage } from '@/lib/data/derived';

/** Heat phase for each lifecycle stage marker. */
const STAGE_COLOR: Record<RunStage['key'], string> = {
  queued: 'bg-status-queued',
  vm_submitted: 'bg-status-queued',
  vm_created: 'bg-status-queued',
  runner_picked_up: 'bg-status-running',
  in_progress: 'bg-status-running',
  finished: 'bg-status-success',
};

function stageColor(run: JobRun, stage: RunStage): string {
  if (stage.key !== 'finished') return STAGE_COLOR[stage.key];
  if (run.status === 'failure') return 'bg-status-failure';
  if (run.status === 'preempted') return 'bg-status-preempted';
  if (run.status === 'cancelled') return 'bg-status-cancelled';
  return 'bg-status-success';
}

/**
 * Job lifecycle reconstructed from log timestamps: horizontal on desktop,
 * vertical on small screens. Inter-stage durations rendered in mono.
 */
export function LifecycleTimeline({ run, className }: { run: JobRun; className?: string }) {
  const stages = run.stages;
  if (stages.length === 0) return null;

  return (
    <ol className={cn('flex flex-col gap-0 md:flex-row md:items-start md:gap-0', className)}>
      {stages.map((stage, i) => {
        const next = stages[i + 1];
        const gapSeconds = next
          ? (Date.parse(next.at) - Date.parse(stage.at)) / 1000
          : undefined;
        return (
          <li key={stage.key + stage.at} className="flex md:flex-1 md:flex-col">
            {/* marker + connector */}
            <div className="flex flex-col items-center md:w-full md:flex-row">
              <span
                aria-hidden
                className={cn('size-2.5 shrink-0 rotate-45', stageColor(run, stage))}
              />
              {next && (
                <span aria-hidden className="min-h-8 w-px flex-1 bg-strong md:h-px md:min-h-0 md:w-auto" />
              )}
            </div>
            {/* labels */}
            <div className="-mt-1 mb-3 ml-3 flex flex-col gap-0.5 md:ml-0 md:mt-2 md:mb-0 md:pr-4">
              <span className="text-xs font-medium text-primary">{stage.label}</span>
              <span className="font-mono text-2xs text-muted tnum">
                {new Date(stage.at).toISOString().slice(11, 19)}
                {stage.key === 'runner_picked_up' && run.timeToRunnerSeconds != null && (
                  <> · ttr {run.timeToRunnerSeconds.toFixed(1)}s</>
                )}
              </span>
              {gapSeconds != null && gapSeconds > 0.5 && (
                <span className="font-mono text-2xs text-muted tnum">
                  +{formatDuration(gapSeconds)}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
