import type {
  JobConclusion,
  LogEntry,
  MachineType,
  VmOutcome,
} from './types';
import type { RunStatus } from '@/components/ui/status-chip';

/** Lifecycle stages reconstructed from the log stream for one job. */
export interface RunStage {
  key:
    | 'queued'
    | 'vm_submitted'
    | 'vm_created'
    | 'runner_picked_up'
    | 'in_progress'
    | 'finished';
  label: string;
  at: string;
}

/** Aggregate view of one workflow job, folded from its log entries. */
export interface JobRun {
  jobId: number;
  runId: number;
  runAttempt: number;
  repo: string;
  owner: string;
  status: RunStatus;
  conclusion?: JobConclusion;
  preempted: boolean;
  queuedAt: string;
  finishedAt?: string;
  runnerName?: string;
  vmName?: string;
  zone?: string;
  machineType?: MachineType;
  spot?: boolean;
  imageVersion?: string;
  vmOutcome?: VmOutcome;
  vmCreationMs?: number;
  timeToRunnerSeconds?: number;
  stages: RunStage[];
  entries: LogEntry[];
}

/** Folds a job's log entries (chronological) into a JobRun aggregate. */
export function foldRun(entries: LogEntry[]): JobRun | undefined {
  const first = entries.find((e) => e.job_id != null);
  if (!first) return undefined;

  const run: JobRun = {
    jobId: first.job_id!,
    runId: first.run_id ?? 0,
    runAttempt: first.run_attempt ?? 1,
    repo: first.repo ?? 'unknown/unknown',
    owner: first.owner ?? 'unknown',
    status: 'queued',
    preempted: false,
    queuedAt: entries[0].timestamp,
    stages: [],
    entries,
  };

  for (const entry of entries) {
    if (entry.runner_name) run.runnerName = entry.runner_name;

    switch (entry.message) {
      case 'Webhook event received': {
        const e = entry as Extract<LogEntry, { message: 'Webhook event received' }>;
        if (e.event_type === 'queued') {
          run.queuedAt = e.timestamp;
          run.stages.push({ key: 'queued', label: 'Queued', at: e.timestamp });
        } else if (e.event_type === 'in_progress') {
          run.status = 'running';
          run.stages.push({ key: 'in_progress', label: 'In progress', at: e.timestamp });
        } else if (e.event_type === 'completed') {
          run.finishedAt = e.timestamp;
          run.stages.push({ key: 'finished', label: 'Completed', at: e.timestamp });
        }
        break;
      }
      case 'Submitted VM creation': {
        const e = entry as Extract<LogEntry, { message: 'Submitted VM creation' }>;
        run.vmName = e.vm_name;
        run.zone = e.zone;
        run.machineType = e.machine_type;
        run.spot = e.spot;
        run.imageVersion = e.image_version;
        run.stages.push({ key: 'vm_submitted', label: 'VM submitted', at: e.timestamp });
        break;
      }
      case 'VM creation outcome': {
        const e = entry as Extract<LogEntry, { message: 'VM creation outcome' }>;
        run.vmOutcome = e.outcome;
        run.vmCreationMs = e.duration_ms;
        if (e.outcome === 'success') {
          run.stages.push({ key: 'vm_created', label: 'VM created', at: e.timestamp });
        } else {
          run.status = 'failure';
        }
        break;
      }
      case 'Time for runner to be picked up by github': {
        const e = entry as Extract<
          LogEntry,
          { message: 'Time for runner to be picked up by github' }
        >;
        run.timeToRunnerSeconds = e.time_to_runner_seconds;
        run.stages.push({ key: 'runner_picked_up', label: 'Runner picked up', at: e.timestamp });
        break;
      }
      case 'Job ended due to spot preemption': {
        run.preempted = true;
        break;
      }
    }
  }

  // Final status: conclusion comes from the completed webhook payload; the
  // mock generator stores it on the completed entry as `conclusion`.
  const completed = entries.find(
    (e) => e.message === 'Webhook event received' && (e as { event_type?: string }).event_type === 'completed',
  ) as (LogEntry & { conclusion?: JobConclusion }) | undefined;

  if (completed) {
    run.conclusion = completed.conclusion;
    if (run.preempted) run.status = 'preempted';
    else if (completed.conclusion === 'failure') run.status = 'failure';
    else if (completed.conclusion === 'cancelled') run.status = 'cancelled';
    else run.status = 'success';
  }

  return run;
}

/** Live duration of a run in seconds (against `now` for unfinished runs). */
export function runDurationSeconds(run: JobRun, now: number): number {
  const start = Date.parse(run.queuedAt);
  const end = run.finishedAt ? Date.parse(run.finishedAt) : now;
  return Math.max(0, (end - start) / 1000);
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s.toString().padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${(m % 60).toString().padStart(2, '0')}m`;
}
