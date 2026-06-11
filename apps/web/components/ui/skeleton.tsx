import { cn } from '@/lib/cn';

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        'rounded-xs bg-surface-hover',
        'animate-pulse motion-reduce:animate-none',
        className,
      )}
    />
  );
}

/** Skeleton matching the runs-table row layout. */
export function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <Skeleton className="size-2 rounded-full" />
      <Skeleton className="h-3.5 w-44" />
      <Skeleton className="h-3.5 w-28" />
      <Skeleton className="ml-auto h-3.5 w-16" />
    </div>
  );
}

/** Skeleton matching the stat-block layout. */
export function StatSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-4">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-8 w-24" />
    </div>
  );
}
