'use client';

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { MockDataSource, type DataSource } from './source';
import type { StreamSnapshot } from './stream';

const EMPTY_SNAPSHOT: StreamSnapshot = { runs: [], feed: [], paused: false };

const DataSourceContext = createContext<DataSource | null>(null);

/**
 * Owns the simulated stream for the dashboard shell. The stream only starts
 * client-side (useEffect) — the server/first paint renders the static
 * snapshot, avoiding hydration mismatches on ticking values.
 */
export function MockStreamProvider({ children }: { children: ReactNode }) {
  const [source] = useState(() => new MockDataSource());

  useEffect(() => {
    source.start();
    return () => source.stop();
  }, [source]);

  return <DataSourceContext.Provider value={source}>{children}</DataSourceContext.Provider>;
}

export function useDataSource(): DataSource {
  const source = useContext(DataSourceContext);
  if (!source) throw new Error('useDataSource requires a MockStreamProvider');
  return source;
}

export function useStreamSnapshot(): StreamSnapshot {
  const source = useDataSource();
  return useSyncExternalStore(source.subscribe, source.getSnapshot, () => EMPTY_SNAPSHOT);
}

/** Re-renders every `intervalMs` for live ages/durations; pauses with the stream. */
export function useNow(intervalMs = 1000): number {
  const { paused } = useStreamSnapshot();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (paused) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, paused]);
  return now;
}

/** Metrics bundle for the trends page, recomputed when the stream changes. */
export function useMetrics(days: number) {
  const source = useDataSource();
  // subscribing makes this hook re-run when the stream emits; the source
  // memoizes the bundle per snapshot internally.
  useStreamSnapshot();
  return source.getMetricSeries(days);
}
