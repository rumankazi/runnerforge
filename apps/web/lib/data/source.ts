import type { JobRun } from './derived';
import { computeMetricSeries, type MetricsBundle } from './metrics';
import { MockStream, type StreamSnapshot } from './stream';
import type { LogEntry } from './types';

/**
 * The API seam. The dashboard only talks to this interface; swapping the
 * mock for real GCP data means implementing it against Cloud Logging
 * (entries.list / tail) and Cloud Monitoring (timeSeries.list) — see the
 * CloudLoggingDataSource stub below.
 */
export interface DataSource {
  listRuns(): JobRun[];
  getRun(jobId: number): JobRun | undefined;
  getRunLogs(jobId: number): LogEntry[];
  getEventFeed(): LogEntry[];
  getMetricSeries(days: number): MetricsBundle;
  /** useSyncExternalStore-compatible. */
  subscribe(cb: () => void): () => void;
  getSnapshot(): StreamSnapshot;
  setPaused(paused: boolean): void;
  start(): void;
  stop(): void;
}

export class MockDataSource implements DataSource {
  private stream: MockStream;
  private metricsCache = new Map<number, { version: StreamSnapshot; bundle: MetricsBundle }>();

  constructor(seed?: number, historyDays?: number) {
    this.stream = new MockStream(seed, historyDays);
  }

  start() {
    this.stream.start();
  }

  stop() {
    this.stream.stop();
  }

  setPaused(paused: boolean) {
    this.stream.setPaused(paused);
  }

  subscribe = (cb: () => void) => this.stream.subscribe(cb);
  getSnapshot = () => this.stream.getSnapshot();

  listRuns() {
    return this.getSnapshot().runs;
  }

  getRun(jobId: number) {
    return this.getSnapshot().runs.find((r) => r.jobId === jobId);
  }

  getRunLogs(jobId: number) {
    return this.getRun(jobId)?.entries ?? [];
  }

  getEventFeed() {
    return this.getSnapshot().feed;
  }

  getMetricSeries(days: number) {
    const snapshot = this.getSnapshot();
    const cached = this.metricsCache.get(days);
    if (cached && cached.version === snapshot) return cached.bundle;
    const bundle = computeMetricSeries(snapshot.runs, days);
    this.metricsCache.set(days, { version: snapshot, bundle });
    return bundle;
  }
}

/**
 * Future production implementation (Phase 4+): queries Cloud Logging for
 * entries with jsonPayload filters and Cloud Monitoring for the log-based
 * metrics, via an authenticated backend route. Intentionally unimplemented.
 */
export type CloudLoggingDataSource = DataSource;
