import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  /** Stamped header label rendered above a hairline rule. */
  label?: ReactNode;
  /** Right-aligned content in the header row. */
  actions?: ReactNode;
}

export function Panel({ label, actions, className, children, ...props }: PanelProps) {
  return (
    <div
      className={cn('rounded-md bg-surface-panel machined', className)}
      {...props}
    >
      {(label || actions) && (
        <div className="flex items-center justify-between gap-2 border-b border-hairline px-4 py-2.5">
          {label && <div className="stamp text-2xs text-muted">{label}</div>}
          {actions}
        </div>
      )}
      <div className="p-4">{children}</div>
    </div>
  );
}
