'use client';

import { AnimatePresence, motion } from 'motion/react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/cn';
import { springStatus } from '@/lib/motion';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'success'
  | 'failure'
  | 'cancelled'
  | 'warning'
  | 'preempted';

export const STATUS_LABEL: Record<RunStatus, string> = {
  queued: 'Heating',
  running: 'Molten',
  success: 'Forged',
  failure: 'Fractured',
  cancelled: 'Ash',
  warning: 'Scorch',
  preempted: 'Quenched',
};

const STATUS_STYLES: Record<RunStatus, { fg: string; bg: string; dot: string }> = {
  queued: {
    fg: 'text-status-queued',
    bg: 'bg-status-queued-bg border-status-queued-border',
    dot: 'bg-status-queued heat-pulse-queued',
  },
  running: {
    fg: 'text-status-running',
    bg: 'bg-status-running-bg border-status-running-border',
    dot: 'bg-status-running heat-pulse-running',
  },
  success: {
    fg: 'text-status-success',
    bg: 'bg-status-success-bg border-status-success-border',
    dot: 'bg-status-success',
  },
  failure: {
    fg: 'text-status-failure',
    bg: 'bg-status-failure-bg border-status-failure-border',
    dot: 'bg-status-failure',
  },
  cancelled: {
    fg: 'text-status-cancelled',
    bg: 'bg-status-cancelled-bg border-status-cancelled-border',
    dot: 'bg-status-cancelled',
  },
  warning: {
    fg: 'text-status-warning',
    bg: 'bg-status-warning-bg border-status-warning-border',
    dot: 'bg-status-warning',
  },
  preempted: {
    fg: 'text-status-preempted',
    bg: 'bg-status-preempted-bg border-status-preempted-border',
    dot: 'bg-status-preempted',
  },
};

export function HeatDot({ status, className }: { status: RunStatus; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2 rounded-full shrink-0', STATUS_STYLES[status].dot, className)}
    />
  );
}

interface StatusChipProps {
  status: RunStatus;
  /** Optional trailing detail, e.g. a live duration. Rendered in mono. */
  detail?: string;
  /** dot-only renders just the heat dot with an accessible label. */
  size?: 'dot' | 'chip';
  className?: string;
}

export function StatusChip({ status, detail, size = 'chip', className }: StatusChipProps) {
  const styles = STATUS_STYLES[status];

  if (size === 'dot') {
    return (
      <span className={className} title={STATUS_LABEL[status]}>
        <HeatDot status={status} />
        <span className="sr-only">{STATUS_LABEL[status]}</span>
      </span>
    );
  }

  return (
    <span
      className={cn(
        'inline-flex h-[22px] items-center gap-1.5 rounded-xs border px-2',
        'font-mono text-2xs uppercase tracking-wider tnum',
        styles.bg,
        styles.fg,
        className,
      )}
    >
      <HeatDot status={status} />
      <span className="relative inline-grid overflow-hidden">
        <AnimatePresence mode="popLayout" initial={false}>
          <motion.span
            key={status}
            initial={{ y: 10, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -10, opacity: 0 }}
            transition={springStatus}
            className="col-start-1 row-start-1 inline-flex items-center gap-1"
          >
            {status === 'success' && <Check size={10} strokeWidth={2.5} absoluteStrokeWidth />}
            {STATUS_LABEL[status]}
          </motion.span>
        </AnimatePresence>
      </span>
      {detail && <span className="text-muted normal-case">{detail}</span>}
    </span>
  );
}
