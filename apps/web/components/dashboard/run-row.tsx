'use client';

import { motion } from 'motion/react';
import Link from 'next/link';
import { Badge } from '@/components/ui/badge';
import { StatusChip } from '@/components/ui/status-chip';
import { cn } from '@/lib/cn';
import { formatDuration, runDurationSeconds, type JobRun } from '@/lib/data/derived';
import { springStatus } from '@/lib/motion';

interface RunRowProps {
  run: JobRun;
  now: number;
}

export function RunRow({ run, now }: RunRowProps) {
  const finished = run.finishedAt != null;
  // Completed rows "cool" — dim after 30s out of the fire.
  const cooled = finished && now - Date.parse(run.finishedAt!) > 30_000;

  return (
    <motion.div layout transition={springStatus} initial={false} className="group">
      <Link
        href={`/dashboard/runs/${run.jobId}`}
        className={cn(
          'grid grid-cols-[110px_minmax(0,1.2fr)_minmax(0,1fr)_auto] items-center gap-3 border-b border-hairline px-4 py-2.5',
          'transition-[opacity,background-color] duration-300 hover:bg-surface-hover',
          'focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus',
          'md:grid-cols-[110px_minmax(0,1.2fr)_minmax(0,1fr)_auto_auto]',
          cooled && 'opacity-55',
        )}
      >
        <StatusChip status={run.status} />

        <span className="min-w-0">
          <span className="block truncate text-sm text-primary">{run.repo}</span>
          <span className="block truncate font-mono text-2xs text-muted tnum">
            run {run.runId} · attempt {run.runAttempt}
          </span>
        </span>

        <span className="hidden min-w-0 items-center gap-1.5 md:flex">
          {run.machineType && <Badge>{run.machineType}</Badge>}
          {run.spot && <Badge variant="spot">spot</Badge>}
        </span>

        <span className="hidden max-w-44 truncate font-mono text-2xs text-muted md:block">
          {run.runnerName ?? '—'}
        </span>

        <span className="justify-self-end text-right">
          <span className="block font-mono text-xs text-secondary tnum">
            {formatDuration(runDurationSeconds(run, now))}
          </span>
          {run.timeToRunnerSeconds != null && (
            <span className="block font-mono text-2xs text-muted tnum">
              ttr {run.timeToRunnerSeconds.toFixed(1)}s
            </span>
          )}
        </span>
      </Link>
    </motion.div>
  );
}
