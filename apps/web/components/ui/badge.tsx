import { cva, type VariantProps } from 'class-variance-authority';
import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const badgeVariants = cva(
  'inline-flex h-[18px] items-center rounded-xs border px-1.5 font-mono text-2xs tnum',
  {
    variants: {
      variant: {
        default: 'border-hairline bg-surface-inset text-secondary',
        spot: 'border-status-preempted-border bg-status-preempted-bg text-status-preempted',
        accent: 'border-transparent bg-ember-600/15 text-accent-text',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

/** Mono micro-badge for machine_type, spot, image_version, zone. */
export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
