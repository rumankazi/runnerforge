'use client';

import { cva, type VariantProps } from 'class-variance-authority';
import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const buttonVariants = cva(
  [
    'inline-flex items-center justify-center gap-1.5 rounded-sm font-medium',
    'transition-colors duration-150 select-none',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
    'disabled:pointer-events-none disabled:opacity-45',
  ],
  {
    variants: {
      variant: {
        primary: [
          'bg-ember-600 text-iron-50 hover:bg-ember-500 active:bg-ember-700',
          'shadow-[inset_0_1px_0_oklch(0.85_0.12_75_/_35%)]',
        ],
        secondary: [
          'bg-surface-panel text-primary machined',
          'hover:bg-surface-hover active:bg-surface-inset',
        ],
        ghost: ['text-secondary hover:bg-surface-hover hover:text-primary'],
        danger: [
          'bg-status-failure-bg text-status-failure border border-status-failure-border',
          'hover:brightness-110',
        ],
      },
      size: {
        sm: 'h-7 px-2.5 text-xs',
        md: 'h-8 px-3 text-sm',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="inline-block size-3 animate-spin border border-current border-t-transparent rounded-full motion-reduce:animate-none"
        />
      )}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
