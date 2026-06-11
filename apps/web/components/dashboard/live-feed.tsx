'use client';

import { useMemo, useState } from 'react';
import { EmptyState } from '@/components/ui/empty-state';
import { StatBlock } from '@/components/ui/stat-block';
import { STATUS_LABEL, type RunStatus } from '@/components/ui/status-chip';
import { Panel } from '@/components/ui/panel';
import { cn } from '@/lib/cn';
import { useNow, useStreamSnapshot } from '@/lib/data/use-live-data';
import { EventTicker } from './event-ticker';
import { RunRow } from './run-row';

const FILTERS: (RunStatus | 'all')[] = ['all', 'queued', 'running', 'success', 'failure', 'preempted'];

export function LiveFeed() {
  const { runs, feed } = useStreamSnapshot();
  const now = useNow();
  const [filter, setFilter] = useState<RunStatus | 'all'>('all');

  const stats = useMemo(() => {
    const today = new Date(now).toISOString().slice(0, 10);
    const todayRuns = runs.filter((r) => r.queuedAt.slice(0, 10) === today && r.finishedAt);
    const succeeded = todayRuns.filter((r) => r.status === 'success').length;
    const ttrs = runs
      .filter((r) => r.timeToRunnerSeconds != null)
      .map((r) => r.timeToRunnerSeconds!)
      .sort((a, b) => a - b);
    return {
      molten: runs.filter((r) => r.status === 'running').length,
      heating: runs.filter((r) => r.status === 'queued').length,
      forgedToday: todayRuns.length,
      successRate: todayRuns.length ? (succeeded / todayRuns.length) * 100 : 0,
      ttrP50: ttrs.length ? ttrs[Math.floor(ttrs.length / 2)] : 0,
    };
  }, [runs, now]);

  const visibleRuns = useMemo(() => {
    const filtered = filter === 'all' ? runs : runs.filter((r) => r.status === filter);
    return filtered.slice(0, 40);
  }, [runs, filter]);

  return (
    <div className="flex flex-col gap-4 xl:grid xl:grid-cols-[minmax(0,1fr)_300px] xl:items-start">
      <div className="flex min-w-0 flex-col gap-4">
        <h1 className="stamp text-lg text-primary">LIVE RUNS</h1>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatBlock label="MOLTEN NOW" value={stats.molten} hot />
          <StatBlock label="HEATING" value={stats.heating} />
          <StatBlock
            label="FORGED TODAY"
            value={stats.forgedToday}
            caption={`${stats.successRate.toFixed(1)}% success`}
          />
          <StatBlock label="TIME TO RUNNER P50" value={stats.ttrP50} decimals={1} unit="s" />
        </div>

        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={cn(
                'h-6 rounded-xs border px-2 font-mono text-2xs uppercase tracking-wider transition-colors duration-150',
                'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus',
                filter === f
                  ? 'border-strong bg-surface-hover text-primary'
                  : 'border-hairline text-muted hover:text-secondary',
              )}
            >
              {f === 'all' ? 'All' : STATUS_LABEL[f]}
            </button>
          ))}
        </div>

        <div className="rounded-md bg-surface-panel machined">
          <div className="grid grid-cols-[110px_minmax(0,1.2fr)_minmax(0,1fr)_auto] gap-3 border-b border-hairline px-4 py-2 md:grid-cols-[110px_minmax(0,1.2fr)_minmax(0,1fr)_auto_auto]">
            <span className="stamp text-2xs text-muted">STATUS</span>
            <span className="stamp text-2xs text-muted">REPOSITORY</span>
            <span className="stamp hidden text-2xs text-muted md:block">MACHINE</span>
            <span className="stamp hidden text-2xs text-muted md:block">RUNNER</span>
            <span className="stamp justify-self-end text-2xs text-muted">DURATION</span>
          </div>
          {visibleRuns.length === 0 ? (
            <EmptyState title="NO ACTIVE RUNS — FORGE IS COLD" />
          ) : (
            visibleRuns.map((run) => <RunRow key={run.jobId} run={run} now={now} />)
          )}
        </div>
      </div>

      <Panel label="EVENT FEED" className="hidden xl:block">
        <EventTicker entries={feed} />
      </Panel>
    </div>
  );
}
