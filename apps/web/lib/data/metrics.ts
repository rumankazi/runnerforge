import type { JobRun } from './derived';
import { mulberry32, intBetween } from './rng';
import type { MetricSeries } from './types';

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function lastNDays(days: number, now = Date.now()): string[] {
  const out: string[] = [];
  for (let i = days - 1; i >= 0; i--) {
    out.push(new Date(now - i * 86_400_000).toISOString().slice(0, 10));
  }
  return out;
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx];
}

function bucketByDay<T>(
  runs: JobRun[],
  days: string[],
  pick: (run: JobRun) => T | undefined,
): Map<string, Map<T, number>> {
  const buckets = new Map<string, Map<T, number>>(days.map((d) => [d, new Map()]));
  for (const run of runs) {
    const day = dayKey(run.queuedAt);
    const bucket = buckets.get(day);
    const key = pick(run);
    if (!bucket || key === undefined) continue;
    bucket.set(key, (bucket.get(key) ?? 0) + 1);
  }
  return buckets;
}

function toSeries<T extends string>(
  metric: MetricSeries['metric'],
  labelKey: string,
  buckets: Map<string, Map<T, number>>,
  labelValues: readonly T[],
  days: string[],
): MetricSeries[] {
  return labelValues.map((label) => ({
    metric,
    labels: { [labelKey]: label },
    points: days.map((day) => ({ time: day, value: buckets.get(day)?.get(label) ?? 0 })),
  }));
}

/**
 * Daily aggregations over folded runs, named after the log-based metrics in
 * infra/terraform/observability_metrics.tf. A real implementation queries
 * Cloud Monitoring instead — same series shapes.
 */
export function computeMetricSeries(runs: JobRun[], days: number, now = Date.now()) {
  const dayList = lastNDays(days, now);
  const inWindow = runs.filter((r) => dayList.includes(dayKey(r.queuedAt)));

  const eventBuckets = bucketByDay(inWindow, dayList, () => 'queued' as const);
  const outcomeBuckets = bucketByDay(inWindow, dayList, (r) =>
    r.preempted ? ('preempted' as const) : r.conclusion,
  );
  const machineBuckets = bucketByDay(inWindow, dayList, (r) => r.machineType);
  const spotBuckets = bucketByDay(inWindow, dayList, (r) =>
    r.spot ? ('spot' as const) : ('on_demand' as const),
  );

  const timeToRunner: MetricSeries[] = (['p50', 'p95'] as const).map((p) => ({
    metric: 'time_to_runner_seconds',
    labels: { percentile: p },
    points: dayList.map((day) => {
      const samples = inWindow
        .filter((r) => dayKey(r.queuedAt) === day && r.timeToRunnerSeconds != null)
        .map((r) => r.timeToRunnerSeconds!)
        .sort((a, b) => a - b);
      return { time: day, value: percentile(samples, p === 'p50' ? 50 : 95) };
    }),
  }));

  // Sweep + policy series have no per-run source; synthesize plausible,
  // deterministic counts (sweep fires every 10 minutes regardless of load).
  const synth = mulberry32(days * 7919);
  const sweep: MetricSeries[] = [
    {
      metric: 'sweep_checked_count',
      labels: {},
      points: dayList.map((day) => ({
        time: day,
        value: (eventBuckets.get(day)?.get('queued') ?? 0) + intBetween(synth, 0, 6),
      })),
    },
    {
      metric: 'sweep_deleted_count',
      labels: {},
      points: dayList.map((day) => ({ time: day, value: synth() < 0.3 ? intBetween(synth, 1, 3) : 0 })),
    },
  ];
  const policyReasons = ['unknown_token', 'conflicting_machines', 'invalid_machine_type'] as const;
  const policy: MetricSeries[] = policyReasons.map((reason) => ({
    metric: 'machine_policy_reject_count',
    labels: { reason },
    points: dayList.map((day) => ({ time: day, value: synth() < 0.18 ? intBetween(synth, 1, 4) : 0 })),
  }));

  return {
    days: dayList,
    jobVolume: toSeries('webhook_event_count', 'event_type', eventBuckets, ['queued'] as const, dayList),
    outcomes: toSeries(
      'vm_creation_outcome_count',
      'conclusion',
      outcomeBuckets,
      ['success', 'failure', 'cancelled', 'preempted'] as const,
      dayList,
    ),
    timeToRunner,
    machineMix: toSeries(
      'vm_creation_count',
      'machine_type',
      machineBuckets,
      ['n2-standard-2', 'n2-standard-4', 'n2-standard-8', 'n2-standard-16'] as const,
      dayList,
    ),
    spotShare: toSeries(
      'vm_creation_count',
      'spot',
      spotBuckets,
      ['spot', 'on_demand'] as const,
      dayList,
    ),
    sweep,
    policy,
  };
}

export type MetricsBundle = ReturnType<typeof computeMetricSeries>;
