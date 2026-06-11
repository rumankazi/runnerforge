'use client';

import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDownToLine } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/cn';
import type { LogEntry, Severity } from '@/lib/data/types';
import { Button } from './button';

const SEVERITY_TICK: Record<Severity, string> = {
  DEBUG: 'bg-iron-600',
  INFO: 'bg-iron-500',
  WARNING: 'bg-status-warning',
  ERROR: 'bg-status-failure',
  CRITICAL: 'bg-status-failure',
};

const SEVERITY_TEXT: Record<Severity, string> = {
  DEBUG: 'text-muted',
  INFO: 'text-secondary',
  WARNING: 'text-status-warning',
  ERROR: 'text-status-failure',
  CRITICAL: 'text-status-failure',
};

const HIDDEN_FIELDS = new Set(['severity', 'message', 'timestamp', 'logger']);

function entryFields(entry: LogEntry): [string, unknown][] {
  return Object.entries(entry).filter(([k, v]) => !HIDDEN_FIELDS.has(k) && v != null);
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return (
    d.toISOString().slice(11, 19) + '.' + d.getMilliseconds().toString().padStart(3, '0')
  );
}

/**
 * Virtualized, mono, severity-ticked log list. Click a row to expand the
 * full structured payload exactly as it exists in Cloud Logging.
 */
export function LogViewer({ entries, className }: { entries: LogEntry[]; className?: string }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [followTail, setFollowTail] = useState(true);

  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (i) => (expanded.has(i) ? 200 : 28),
    overscan: 12,
  });

  useEffect(() => {
    if (followTail && entries.length > 0) {
      virtualizer.scrollToIndex(entries.length - 1, { align: 'end' });
    }
  }, [entries.length, followTail, virtualizer]);

  const toggle = (index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
    virtualizer.measure();
  };

  return (
    <div className={cn('flex flex-col rounded-md bg-surface-inset machined', className)}>
      <div className="flex items-center justify-between border-b border-hairline px-3 py-1.5">
        <span className="stamp text-2xs text-muted">
          LOG ENTRIES <span className="font-mono">({entries.length})</span>
        </span>
        <Button
          variant={followTail ? 'secondary' : 'ghost'}
          size="sm"
          onClick={() => setFollowTail((f) => !f)}
          aria-pressed={followTail}
        >
          <ArrowDownToLine size={12} strokeWidth={1.75} absoluteStrokeWidth />
          Follow tail
        </Button>
      </div>
      <div ref={parentRef} className="h-80 overflow-auto font-mono text-xs">
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map((row) => {
            const entry = entries[row.index];
            const isOpen = expanded.has(row.index);
            return (
              <div
                key={row.key}
                data-index={row.index}
                ref={virtualizer.measureElement}
                className="absolute left-0 top-0 w-full"
                style={{ transform: `translateY(${row.start}px)` }}
              >
                <button
                  type="button"
                  onClick={() => toggle(row.index)}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-hover',
                    'focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus',
                  )}
                >
                  <span
                    aria-hidden
                    className={cn('h-3.5 w-0.5 shrink-0 rounded-full', SEVERITY_TICK[entry.severity])}
                  />
                  <span className="shrink-0 text-muted tnum">{formatTime(entry.timestamp)}</span>
                  <span className={cn('truncate', SEVERITY_TEXT[entry.severity])}>
                    {entry.message}
                  </span>
                  <span className="ml-auto hidden shrink-0 gap-2 text-2xs text-muted lg:flex">
                    {entryFields(entry)
                      .slice(0, 3)
                      .map(([k, v]) => (
                        <span key={k} className="tnum">
                          {k}={String(v)}
                        </span>
                      ))}
                  </span>
                </button>
                {isOpen && (
                  <pre className="overflow-x-auto border-y border-hairline bg-surface-page px-3 py-2 text-2xs leading-relaxed text-secondary">
                    {JSON.stringify(entry, null, 2)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
