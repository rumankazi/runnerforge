'use client';

import { Pause, Play } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { HeatDot } from '@/components/ui/status-chip';
import { useDataSource, useStreamSnapshot } from '@/lib/data/use-live-data';

export function Topbar() {
  const pathname = usePathname();
  const source = useDataSource();
  const { paused } = useStreamSnapshot();

  const crumbs = pathname.split('/').filter(Boolean);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-hairline bg-surface-page/90 px-4 backdrop-blur-sm md:px-6">
      <nav aria-label="Breadcrumb" className="min-w-0 flex-1 truncate font-mono text-xs text-muted">
        {crumbs.map((part, i) => (
          <span key={i}>
            {i > 0 && <span className="mx-1.5 text-disabled">/</span>}
            <span className={i === crumbs.length - 1 ? 'text-primary' : undefined}>{part}</span>
          </span>
        ))}
      </nav>

      <span className="flex items-center gap-2 font-mono text-2xs uppercase tracking-wider text-secondary">
        <HeatDot status={paused ? 'cancelled' : 'running'} />
        {paused ? 'Stream paused' : 'Stream live'}
      </span>

      <Button
        variant="ghost"
        size="sm"
        aria-label={paused ? 'Resume stream' : 'Pause stream'}
        onClick={() => source.setPaused(!paused)}
      >
        {paused ? (
          <Play size={13} strokeWidth={1.75} absoluteStrokeWidth />
        ) : (
          <Pause size={13} strokeWidth={1.75} absoluteStrokeWidth />
        )}
      </Button>
    </header>
  );
}
