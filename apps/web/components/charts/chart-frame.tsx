import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';
import type { MetricName } from '@/lib/data/types';

interface ChartFrameProps {
  title: string;
  /** Real log-based metric name(s) backing this chart — the future data contract. */
  metric: MetricName | MetricName[];
  legend?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function ChartFrame({ title, metric, legend, className, children }: ChartFrameProps) {
  const metrics = Array.isArray(metric) ? metric : [metric];
  return (
    <figure className={cn('flex flex-col rounded-md bg-surface-panel machined', className)}>
      <figcaption className="flex items-center justify-between gap-2 border-b border-hairline px-4 py-2.5">
        <span className="stamp text-2xs text-muted">{title}</span>
        {legend}
      </figcaption>
      <div className="p-4">{children}</div>
      <div className="border-t border-hairline px-4 py-1.5 font-mono text-2xs text-muted">
        {metrics.map((m) => `logging.googleapis.com/user/${m}`).join(' · ')}
      </div>
    </figure>
  );
}

export function ChartLegend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5 font-mono text-2xs text-secondary">
          <span aria-hidden className="size-2" style={{ backgroundColor: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}
