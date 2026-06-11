/**
 * Cloud Logging entry shapes mirroring the orchestrator's structured logs.
 * Source of truth: apps/orchestrator/src/runnerforge/logger.py (JSON formatter:
 * severity/message/timestamp + extras merged top-level) and handlers.py /
 * sweep.py / machine_policy.py for the per-message payloads.
 */

export type Severity = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';

export type WorkflowJobAction = 'queued' | 'in_progress' | 'completed';

export type VmOutcome = 'success' | 'failure' | 'timeout';

export type JobConclusion = 'success' | 'failure' | 'cancelled';

/** Mirrors machine_policy.py SIZE_TABLE. */
export const SIZE_TABLE = {
  small: 'n2-standard-2',
  medium: 'n2-standard-4',
  large: 'n2-standard-8',
  xlarge: 'n2-standard-16',
} as const;

export type MachineSize = keyof typeof SIZE_TABLE;
export type MachineType = (typeof SIZE_TABLE)[MachineSize];

/** Context fields the orchestrator attaches via `extra` where known. */
export interface LogContext {
  request_id?: string;
  repo?: string;
  owner?: string;
  sender?: string;
  job_id?: number;
  run_id?: number;
  run_attempt?: number;
  runner_name?: string;
  installation_id?: number;
}

interface BaseEntry extends LogContext {
  severity: Severity;
  timestamp: string; // ISO 8601
  logger: string;
}

export interface WebhookEventReceived extends BaseEntry {
  message: 'Webhook event received';
  event_type: WorkflowJobAction;
}

export interface SubmittedVmCreation extends BaseEntry {
  message: 'Submitted VM creation';
  vm_name: string;
  zone: string;
  operation_id: string;
  machine_type: MachineType;
  spot: boolean;
  image_version: string;
}

export interface VmCreationOutcome extends BaseEntry {
  message: 'VM creation outcome';
  outcome: VmOutcome;
  op_name: string;
  zone: string;
  duration_ms: number;
  machine_type: MachineType;
  spot: boolean;
  image_version: string;
  error_code?: string;
  error_message?: string;
}

export interface SubmittedVmDeletion extends BaseEntry {
  message: 'Submitted VM deletion';
  vm_name: string;
  zone: string;
  operation_id: string;
  reason: 'webhook' | 'sweep';
}

export interface TimeToRunner extends BaseEntry {
  message: 'Time for runner to be picked up by github';
  time_to_runner_seconds: number;
  labels: string[];
  machine_type: MachineType;
  image_version: string;
}

export interface MachinePolicyRejected extends BaseEntry {
  message: 'Machine policy rejected';
  reason: 'unknown_token' | 'conflicting_machines' | 'invalid_machine_type';
  offending_tokens: string[];
}

export interface JobPreempted extends BaseEntry {
  message: 'Job ended due to spot preemption';
  vm_name: string;
  conclusion: JobConclusion;
}

export interface SweepCompleted extends BaseEntry {
  message: 'Sweep completed';
  checked: number;
  deleted: number;
  skipped: number;
  error_count: number;
}

export interface SweepDecision extends BaseEntry {
  message: 'Sweep decision';
  vm_name: string;
  age_minutes: number;
  decision: 'skip' | 'delete';
  reason: 'too_young' | 'hard_limit' | 'job_completed' | 'job_running';
}

export interface VmListScan extends BaseEntry {
  message: 'VM list scan';
  runnerforge_vm_count: number;
}

/** Free-form warnings/errors that surface verbatim in the UI. */
export interface GenericEntry extends BaseEntry {
  message: string;
  [key: string]: unknown;
}

export type LogEntry =
  | WebhookEventReceived
  | SubmittedVmCreation
  | VmCreationOutcome
  | SubmittedVmDeletion
  | TimeToRunner
  | MachinePolicyRejected
  | JobPreempted
  | SweepCompleted
  | SweepDecision
  | VmListScan
  | GenericEntry;

/** Names of the log-based metrics defined in infra/terraform/observability_metrics.tf. */
export type MetricName =
  | 'vm_creation_count'
  | 'vm_creation_outcome_count'
  | 'vm_deletion_count'
  | 'time_to_runner_seconds'
  | 'webhook_request_count'
  | 'webhook_rejection_count'
  | 'webhook_event_count'
  | 'runner_vm_count'
  | 'sweep_checked_count'
  | 'sweep_deleted_count'
  | 'sweep_error_count'
  | 'machine_policy_reject_count'
  | 'job_preempted_count'
  | 'vm_preemption_count';

export interface MetricPoint {
  /** Bucket start, ISO 8601. */
  time: string;
  value: number;
}

export interface MetricSeries {
  metric: MetricName;
  /** Label set this series is sliced by, e.g. { machine_type: 'n2-standard-4' }. */
  labels: Record<string, string>;
  points: MetricPoint[];
}
