import { cn } from '@/lib/cn';

export function Spark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2 rotate-45 bg-accent', className)}
    />
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-baseline gap-2', className)}>
      <Spark className="self-center" />
      <span className="stamp text-sm tracking-[0.04em] text-primary">
        RUNNER
        <span className="text-accent-text">FORGE</span>
      </span>
    </span>
  );
}
