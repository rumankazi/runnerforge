import { choice, intBetween, logNormal, mulberry32, weightedChoice, type Rng } from './rng';
import {
  SIZE_TABLE,
  type JobConclusion,
  type LogEntry,
  type MachineSize,
  type SweepCompleted,
  type SweepDecision,
} from './types';

export const MOCK_OWNER = 'rumankazi';

export const MOCK_REPOS = [
  'rumankazi/runnerforge',
  'rumankazi/api-service',
  'rumankazi/web-client',
  'rumankazi/ml-pipeline',
  'rumankazi/infra-modules',
] as const;

const WORKFLOWS = [
  { workflow: 'CI', job: 'test' },
  { workflow: 'CI', job: 'lint' },
  { workflow: 'CI', job: 'build' },
  { workflow: 'Release', job: 'publish-image' },
  { workflow: 'Nightly', job: 'integration-tests' },
  { workflow: 'Terraform', job: 'plan' },
] as const;

const IMAGE_VERSIONS = ['v1.8.2', 'v1.8.1'] as const;
const ZONE = 'europe-west4-a';
const LOGGER = 'runnerforge.handlers';

export interface RunScenario {
  jobId: number;
  runId: number;
  runAttempt: number;
  repo: string;
  workflow: string;
  jobName: string;
  spot: boolean;
  size: MachineSize;
  conclusion: JobConclusion;
  preempted: boolean;
  vmFailure: boolean;
  /** seconds */
  timeToRunner: number;
  /** seconds */
  jobDuration: number;
}

function hexId(rng: Rng, length: number): string {
  let out = '';
  for (let i = 0; i < length; i++) out += Math.floor(rng() * 16).toString(16);
  return out;
}

export function makeScenario(rng: Rng): RunScenario {
  const spot = rng() < 0.6;
  const preempted = spot && rng() < 0.05;
  const conclusion: JobConclusion = preempted
    ? choice(rng, ['failure', 'cancelled'] as const)
    : weightedChoice(rng, [
        ['success', 0.9],
        ['failure', 0.07],
        ['cancelled', 0.03],
      ] as const);
  const wf = choice(rng, WORKFLOWS);
  return {
    jobId: intBetween(rng, 51_000_000, 52_999_999),
    runId: intBetween(rng, 9_000_000_000, 9_999_999_999),
    runAttempt: rng() < 0.92 ? 1 : 2,
    repo: choice(rng, MOCK_REPOS),
    workflow: wf.workflow,
    jobName: wf.job,
    spot,
    size: weightedChoice(rng, [
      ['small', 0.25],
      ['medium', 0.45],
      ['large', 0.22],
      ['xlarge', 0.08],
    ] as const),
    conclusion,
    preempted,
    vmFailure: rng() < 0.015,
    timeToRunner: Math.max(36, logNormal(rng, 42, 0.3)),
    jobDuration: Math.max(45, logNormal(rng, 240, 0.7)),
  };
}

/**
 * Emits the chronological log entries for one job lifecycle, starting at
 * `queuedAtMs`. Shapes mirror the orchestrator's structured logs verbatim.
 */
export function generateRunEntries(rng: Rng, scenario: RunScenario, queuedAtMs: number): LogEntry[] {
  const s = scenario;
  const [owner] = s.repo.split('/');
  const machineType = SIZE_TABLE[s.size];
  const imageVersion = choice(rng, IMAGE_VERSIONS);
  const requestId = hexId(rng, 32);
  const vmName = `runnerforge-${s.jobId}-${hexId(rng, 6)}`;
  const installationId = 87_654_321;

  const ctx = {
    severity: 'INFO' as const,
    logger: LOGGER,
    request_id: requestId,
    repo: s.repo,
    owner,
    job_id: s.jobId,
    run_id: s.runId,
    run_attempt: s.runAttempt,
    installation_id: installationId,
  };

  const at = (offsetMs: number) => new Date(queuedAtMs + offsetMs).toISOString();
  const entries: LogEntry[] = [];

  entries.push({ ...ctx, message: 'Webhook event received', event_type: 'queued', timestamp: at(0) });

  entries.push({
    ...ctx,
    message: 'Submitted VM creation',
    timestamp: at(intBetween(rng, 600, 2200)),
    vm_name: vmName,
    zone: ZONE,
    operation_id: `operation-${hexId(rng, 12)}`,
    machine_type: machineType,
    spot: s.spot,
    image_version: imageVersion,
  });

  const creationMs = intBetween(rng, 18_000, 42_000);

  if (s.vmFailure) {
    entries.push({
      ...ctx,
      severity: 'ERROR',
      message: 'VM creation outcome',
      timestamp: at(2500 + creationMs),
      outcome: 'failure',
      op_name: `operation-${hexId(rng, 12)}`,
      zone: ZONE,
      duration_ms: creationMs,
      machine_type: machineType,
      spot: s.spot,
      image_version: imageVersion,
      error_code: 'ZONE_RESOURCE_POOL_EXHAUSTED',
      error_message: `The zone '${ZONE}' does not have enough resources available to fulfill the request.`,
    });
    return entries;
  }

  if (rng() < 0.08) {
    entries.push({
      ...ctx,
      severity: 'WARNING',
      message: 'Transient error polling operation, retrying',
      timestamp: at(2500 + Math.floor(creationMs / 2)),
      attempt: 1,
      error_message: '503 Service Unavailable',
    });
  }

  entries.push({
    ...ctx,
    message: 'VM creation outcome',
    timestamp: at(2500 + creationMs),
    outcome: 'success',
    op_name: `operation-${hexId(rng, 12)}`,
    zone: ZONE,
    duration_ms: creationMs,
    machine_type: machineType,
    spot: s.spot,
    image_version: imageVersion,
  });

  const runnerName = `${vmName}`;
  const pickedUpMs = Math.round(s.timeToRunner * 1000);

  entries.push({
    ...ctx,
    runner_name: runnerName,
    message: 'Time for runner to be picked up by github',
    timestamp: at(pickedUpMs),
    time_to_runner_seconds: Number(s.timeToRunner.toFixed(2)),
    labels: ['self-hosted', 'runnerforge', s.size, ...(s.spot ? ['spot'] : [])],
    machine_type: machineType,
    image_version: imageVersion,
  });

  entries.push({
    ...ctx,
    runner_name: runnerName,
    message: 'Webhook event received',
    event_type: 'in_progress',
    timestamp: at(pickedUpMs + intBetween(rng, 200, 900)),
  });

  const completedMs = pickedUpMs + Math.round(s.jobDuration * 1000);

  if (s.preempted) {
    entries.push({
      ...ctx,
      runner_name: runnerName,
      severity: 'WARNING',
      message: 'Job ended due to spot preemption',
      timestamp: at(completedMs + intBetween(rng, 1500, 4000)),
      vm_name: vmName,
      conclusion: s.conclusion,
    });
  }

  entries.push({
    ...ctx,
    runner_name: runnerName,
    message: 'Webhook event received',
    event_type: 'completed',
    conclusion: s.conclusion,
    timestamp: at(completedMs),
  });

  entries.push({
    ...ctx,
    message: 'Submitted VM deletion',
    timestamp: at(completedMs + intBetween(rng, 2000, 8000)),
    vm_name: vmName,
    zone: ZONE,
    operation_id: `operation-${hexId(rng, 12)}`,
    reason: 'webhook',
  });

  return entries;
}

/** Occasional standalone policy rejection (bad labels on a workflow). */
export function generatePolicyRejection(rng: Rng, atMs: number): LogEntry {
  const variant = choice(rng, [
    { reason: 'unknown_token', offending_tokens: ['gpu'] },
    { reason: 'conflicting_machines', offending_tokens: ['small', 'large'] },
    { reason: 'invalid_machine_type', offending_tokens: ['n9-mega-96'] },
  ] as const);
  return {
    severity: 'WARNING',
    logger: 'runnerforge.machine_policy',
    message: 'Machine policy rejected',
    timestamp: new Date(atMs).toISOString(),
    repo: choice(rng, MOCK_REPOS),
    owner: MOCK_OWNER,
    job_id: intBetween(rng, 51_000_000, 52_999_999),
    reason: variant.reason,
    offending_tokens: [...variant.offending_tokens],
  };
}

/** A sweep cycle: scan + decisions + completion summary. */
export function generateSweepEntries(rng: Rng, atMs: number, activeVms: number): LogEntry[] {
  const deleted = rng() < 0.12 ? 1 : 0;
  const entries: LogEntry[] = [];
  const base = { logger: 'runnerforge.sweep', severity: 'INFO' as const };

  entries.push({
    ...base,
    message: 'VM list scan',
    timestamp: new Date(atMs).toISOString(),
    runnerforge_vm_count: activeVms,
  });

  if (deleted) {
    const decision: SweepDecision = {
      ...base,
      severity: 'WARNING',
      message: 'Sweep decision',
      timestamp: new Date(atMs + 800).toISOString(),
      vm_name: `runnerforge-${intBetween(rng, 51_000_000, 52_999_999)}-${Math.floor(rng() * 0xffffff).toString(16)}`,
      age_minutes: intBetween(rng, 70, 340),
      decision: 'delete',
      reason: choice(rng, ['job_completed', 'hard_limit'] as const),
    };
    entries.push(decision);
  }

  const completed: SweepCompleted = {
    ...base,
    message: 'Sweep completed',
    timestamp: new Date(atMs + 1500).toISOString(),
    checked: activeVms,
    deleted,
    skipped: Math.max(0, activeVms - deleted),
    error_count: 0,
  };
  entries.push(completed);

  return entries;
}

/**
 * Historical batch: completed runs spread over the past `days`,
 * denser on weekdays and working hours. Returns entries grouped per run.
 */
export function generateHistory(seed: number, days: number, now = Date.now()): LogEntry[][] {
  const rng = mulberry32(seed);
  const runs: LogEntry[][] = [];
  for (let day = days - 1; day >= 0; day--) {
    const dayStart = now - day * 86_400_000;
    const weekday = new Date(dayStart).getUTCDay();
    const isWeekend = weekday === 0 || weekday === 6;
    const count = intBetween(rng, isWeekend ? 4 : 14, isWeekend ? 12 : 38);
    for (let i = 0; i < count; i++) {
      const hour = Math.min(23, Math.max(0, Math.round(13 + (rng() + rng() - 1) * 9)));
      const queuedAt =
        dayStart - 86_400_000 + hour * 3_600_000 + intBetween(rng, 0, 3_599_000);
      if (queuedAt > now - 600_000) continue; // leave the live window to the stream
      runs.push(generateRunEntries(rng, makeScenario(rng), queuedAt));
    }
  }
  return runs;
}
