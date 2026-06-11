import { Recycle } from 'lucide-react';
import type { SweepCompleted } from '@/lib/data/types';

/** Compact summary card for "Sweep completed" entries in the feed. */
export function SweepCard({ entry }: { entry: SweepCompleted }) {
  return (
    <div className="rounded-sm border border-hairline bg-surface-panel px-2.5 py-2">
      <div className="flex items-center gap-1.5 font-mono text-2xs text-secondary">
        <Recycle size={11} strokeWidth={1.75} absoluteStrokeWidth className="text-muted" />
        <span className="stamp">SWEEP</span>
        <span className="ml-auto text-disabled tnum">
          {new Date(entry.timestamp).toISOString().slice(11, 19)}
        </span>
      </div>
      <div className="mt-1.5 grid grid-cols-4 gap-1 font-mono text-2xs tnum">
        <span className="text-muted">
          chk <span className="text-primary">{entry.checked}</span>
        </span>
        <span className="text-muted">
          del{' '}
          <span className={entry.deleted > 0 ? 'text-status-warning' : 'text-primary'}>
            {entry.deleted}
          </span>
        </span>
        <span className="text-muted">
          skp <span className="text-primary">{entry.skipped}</span>
        </span>
        <span className="text-muted">
          err{' '}
          <span className={entry.error_count > 0 ? 'text-status-failure' : 'text-primary'}>
            {entry.error_count}
          </span>
        </span>
      </div>
    </div>
  );
}
