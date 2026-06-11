'use client';

import { Select as BaseSelect } from '@base-ui-components/react/select';
import { Check, ChevronsUpDown } from 'lucide-react';
import { cn } from '@/lib/cn';

export interface SelectOption<T extends string = string> {
  value: T;
  label: string;
}

interface SelectProps<T extends string> {
  options: readonly SelectOption<T>[];
  value?: T;
  defaultValue?: T;
  onValueChange?: (value: T) => void;
  placeholder?: string;
  mono?: boolean;
  className?: string;
}

export function Select<T extends string>({
  options,
  value,
  defaultValue,
  onValueChange,
  placeholder = 'Select…',
  mono,
  className,
}: SelectProps<T>) {
  return (
    <BaseSelect.Root
      items={options.map((o) => ({ value: o.value, label: o.label }))}
      value={value}
      defaultValue={defaultValue}
      onValueChange={(v) => onValueChange?.(v as T)}
    >
      <BaseSelect.Trigger
        className={cn(
          'inline-flex h-8 min-w-36 items-center justify-between gap-2 rounded-xs border border-hairline',
          'bg-surface-inset px-2.5 text-sm text-primary transition-colors duration-150',
          'hover:bg-surface-hover data-[popup-open]:bg-surface-hover',
          'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus',
          mono && 'font-mono text-xs tnum',
          className,
        )}
      >
        <BaseSelect.Value>
          {(selected: T | null) =>
            selected != null ? (
              (options.find((o) => o.value === selected)?.label ?? selected)
            ) : (
              <span className="text-muted">{placeholder}</span>
            )
          }
        </BaseSelect.Value>
        <BaseSelect.Icon className="text-muted">
          <ChevronsUpDown size={13} strokeWidth={1.75} absoluteStrokeWidth />
        </BaseSelect.Icon>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner sideOffset={4} className="z-50 outline-none">
          <BaseSelect.Popup
            className={cn(
              'min-w-[var(--anchor-width)] rounded-lg border border-hairline bg-surface-raised py-1',
              'shadow-overlay',
              mono && 'font-mono text-xs',
            )}
          >
            {options.map((option) => (
              <BaseSelect.Item
                key={option.value}
                value={option.value}
                className={cn(
                  'flex cursor-default items-center justify-between gap-3 px-2.5 py-1.5 text-sm select-none',
                  'text-secondary data-[highlighted]:bg-surface-hover data-[highlighted]:text-primary',
                  mono && 'text-xs tnum',
                )}
              >
                <BaseSelect.ItemText>{option.label}</BaseSelect.ItemText>
                <BaseSelect.ItemIndicator className="text-accent-text">
                  <Check size={12} strokeWidth={2} absoluteStrokeWidth />
                </BaseSelect.ItemIndicator>
              </BaseSelect.Item>
            ))}
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  );
}
