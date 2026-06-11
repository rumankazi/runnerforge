'use client';

import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { useTickingNumber } from '@/lib/motion';

interface StatBlockProps {
  label: string;
  value: number;
  decimals?: number;
  /** Rendered after the value, e.g. "s" or "%". */
  unit?: string;
  /** Small line under the value, e.g. "98.2% success". */
  caption?: ReactNode;
  /** Tints the value with the live accent (for "molten" counts). */
  hot?: boolean;
  className?: string;
}

export function StatBlock({ label, value, decimals = 0, unit, caption, hot, className }: StatBlockProps) {
  const display = useTickingNumber(value, decimals);
  return (
    <div className={cn('flex flex-col gap-1 rounded-md bg-surface-panel p-4 machined', className)}>
      <div className="stamp text-2xs text-muted">{label}</div>
      <div
        className={cn(
          'font-mono text-metric font-medium tnum',
          hot ? 'text-accent-text' : 'text-primary',
        )}
      >
        {display}
        {unit && <span className="ml-1 text-lg text-muted">{unit}</span>}
      </div>
      {caption && <div className="font-mono text-2xs text-muted tnum">{caption}</div>}
    </div>
  );
}
