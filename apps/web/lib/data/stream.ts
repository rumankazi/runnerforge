import { foldRun, type JobRun } from './derived';
import {
  generateHistory,
  generatePolicyRejection,
  generateRunEntries,
  generateSweepEntries,
  makeScenario,
} from './generators';
import { intBetween, mulberry32, type Rng } from './rng';
import type { LogEntry } from './types';

export interface StreamSnapshot {
  /** All runs, newest queued first. */
  runs: JobRun[];
  /** Raw event feed, newest first (capped). */
  feed: LogEntry[];
  paused: boolean;
}

const FEED_CAP = 120;

/**
 * Simulated live backend: seeds N days of history, then keeps spawning jobs
 * that walk queued → in_progress → completed at demo pace (lifecycle
 * compressed to seconds), interleaved with sweeps and policy rejections.
 *
 * Stands in for Cloud Logging tailing; consumed via subscribe/getSnapshot
 * (useSyncExternalStore-compatible).
 */
export class MockStream {
  private rng: Rng;
  private entriesByJob = new Map<number, LogEntry[]>();
  private extraFeed: LogEntry[] = [];
  private listeners = new Set<() => void>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private snapshot: StreamSnapshot = { runs: [], feed: [], paused: false };
  private dirty = true;
  private running = false;
  private paused = false;

  constructor(seed = 20260611, historyDays = 30) {
    this.rng = mulberry32(seed);
    for (const entries of generateHistory(seed ^ 0x5eed, historyDays)) {
      const jobId = entries.find((e) => e.job_id != null)?.job_id;
      if (jobId != null) this.entriesByJob.set(jobId, entries);
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    // A few runs already mid-flight so the first paint is alive.
    for (let i = 0; i < 3; i++) this.spawnRun(intBetween(this.rng, 2_000, 25_000));
    this.scheduleSpawner();
    this.scheduleSweep();
    this.emit();
  }

  stop() {
    this.running = false;
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
  }

  setPaused(paused: boolean) {
    this.paused = paused;
    this.dirty = true;
    this.emit();
  }

  subscribe = (cb: () => void) => {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  };

  getSnapshot = (): StreamSnapshot => {
    if (this.dirty) {
      const runs: JobRun[] = [];
      for (const entries of this.entriesByJob.values()) {
        const run = foldRun(entries);
        if (run) runs.push(run);
      }
      runs.sort((a, b) => Date.parse(b.queuedAt) - Date.parse(a.queuedAt));
      const feed = [...this.extraFeed];
      for (const entries of this.entriesByJob.values()) feed.push(...entries);
      feed.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
      this.snapshot = { runs, feed: feed.slice(0, FEED_CAP), paused: this.paused };
      this.dirty = false;
    }
    return this.snapshot;
  };

  private emit() {
    this.dirty = true;
    for (const cb of this.listeners) cb();
  }

  private schedule(fn: () => void, delayMs: number) {
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (!this.running) return;
      fn();
    }, delayMs);
    this.timers.add(timer);
  }

  private scheduleSpawner() {
    this.schedule(() => {
      if (!this.paused) this.spawnRun(0);
      if (!this.paused && this.rng() < 0.12) {
        this.extraFeed.unshift(generatePolicyRejection(this.rng, Date.now()));
        this.emit();
      }
      this.scheduleSpawner();
    }, intBetween(this.rng, 6_000, 16_000));
  }

  private scheduleSweep() {
    this.schedule(() => {
      if (!this.paused) {
        const active = this.getSnapshot().runs.filter(
          (r) => r.status === 'queued' || r.status === 'running',
        ).length;
        this.extraFeed.unshift(...generateSweepEntries(this.rng, Date.now(), active).reverse());
        this.emit();
      }
      this.scheduleSweep();
    }, 45_000);
  }

  /** Spawns one run and reveals its entries as their timestamps pass. */
  private spawnRun(startedAgoMs: number) {
    const scenario = makeScenario(this.rng);
    // Demo pace: compress the lifecycle so transitions are watchable.
    scenario.timeToRunner = intBetween(this.rng, 8, 18);
    scenario.jobDuration = intBetween(this.rng, 20, 75);

    const queuedAt = Date.now() - startedAgoMs;
    const entries = generateRunEntries(this.rng, scenario, queuedAt);
    const revealed: LogEntry[] = [];
    this.entriesByJob.set(scenario.jobId, revealed);

    for (const entry of entries) {
      const delay = Date.parse(entry.timestamp) - Date.now();
      if (delay <= 0) {
        revealed.push(entry);
      } else {
        this.schedule(() => {
          revealed.push(entry);
          this.emit();
        }, delay);
      }
    }
    this.emit();
  }
}
