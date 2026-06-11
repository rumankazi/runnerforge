import { forwardRef, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const baseStyles = [
  'w-full rounded-xs border border-hairline bg-surface-inset px-2.5 text-sm text-primary',
  'placeholder:text-muted transition-colors duration-150',
  'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus',
  'disabled:pointer-events-none disabled:opacity-45',
];

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Mono variant for IDs, filters, and other data inputs. */
  mono?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, mono, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(baseStyles, 'h-8', mono && 'font-mono text-xs tnum', className)}
      {...props}
    />
  ),
);
Input.displayName = 'Input';

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(baseStyles, 'min-h-20 py-2', className)} {...props} />
));
Textarea.displayName = 'Textarea';
