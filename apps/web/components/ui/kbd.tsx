import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded-xs border border-hairline',
        'bg-surface-inset px-1 font-mono text-2xs text-secondary',
        className,
      )}
      {...props}
    />
  );
}
