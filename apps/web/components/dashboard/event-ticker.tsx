'use client';

import { motion } from 'motion/react';
import { cn } from '@/lib/cn';
import type { LogEntry, Severity, SweepCompleted } from '@/lib/data/types';
import { logEntryVariants } from '@/lib/motion';
import { SweepCard } from './sweep-card';

const SEVERITY_TEXT: Record<Severity, string> = {
  DEBUG: 'text-muted',
  INFO: 'text-secondary',
  WARNING: 'text-status-warning',
  ERROR: 'text-status-failure',
  CRITICAL: 'text-status-failure',
};

function entryKey(entry: LogEntry): string {
  return `${entry.timestamp}-${entry.message}-${entry.job_id ?? ''}`;
}

/** Raw orchestrator log feed, newest first. */
export function EventTicker({ entries, limit = 25 }: { entries: LogEntry[]; limit?: number }) {
  const visible = entries.slice(0, limit);
  return (
    <ol className="flex flex-col">
      {visible.map((entry) =>
        entry.message === 'Sweep completed' ? (
          <motion.li key={entryKey(entry)} {...logEntryVariants} className="py-1.5">
            <SweepCard entry={entry as SweepCompleted} />
          </motion.li>
        ) : (
          <motion.li
            key={entryKey(entry)}
            {...logEntryVariants}
            className="flex items-baseline gap-2 border-b border-hairline py-1.5 font-mono text-2xs last:border-b-0"
          >
            <span className="shrink-0 text-disabled tnum">
              {new Date(entry.timestamp).toISOString().slice(11, 19)}
            </span>
            <span className={cn('min-w-0 flex-1 truncate', SEVERITY_TEXT[entry.severity])}>
              {entry.message}
            </span>
          </motion.li>
        ),
      )}
    </ol>
  );
}
