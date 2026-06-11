import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface EmptyStateProps {
  /** Stamped caption, e.g. "NO ACTIVE RUNS — FORGE IS COLD". */
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

/** Cold-forge treatment: dimmed spark, stamped caption. */
export function EmptyState({ title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 py-14 text-center', className)}>
      <span aria-hidden className="size-3 rotate-45 bg-muted opacity-40" />
      <div className="stamp text-2xs text-muted">{title}</div>
      {description && <p className="max-w-sm text-sm text-muted">{description}</p>}
      {action}
    </div>
  );
}
