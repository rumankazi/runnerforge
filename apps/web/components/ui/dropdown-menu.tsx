'use client';

import { Menu as BaseMenu } from '@base-ui-components/react/menu';
import type { ComponentProps } from 'react';

type WithClassName<T> = Omit<T, 'className'> & { className?: string };
import { cn } from '@/lib/cn';

export const DropdownMenu = BaseMenu.Root;
export const DropdownMenuTrigger = BaseMenu.Trigger;

export function DropdownMenuContent({
  className,
  children,
  ...props
}: WithClassName<ComponentProps<typeof BaseMenu.Popup>>) {
  return (
    <BaseMenu.Portal>
      <BaseMenu.Positioner sideOffset={4} className="z-50 outline-none">
        <BaseMenu.Popup
          className={cn(
            'min-w-44 rounded-lg border border-hairline bg-surface-raised py-1 shadow-overlay',
            className,
          )}
          {...props}
        >
          {children}
        </BaseMenu.Popup>
      </BaseMenu.Positioner>
    </BaseMenu.Portal>
  );
}

export function DropdownMenuItem({ className, ...props }: WithClassName<ComponentProps<typeof BaseMenu.Item>>) {
  return (
    <BaseMenu.Item
      className={cn(
        'flex cursor-default items-center gap-2 px-2.5 py-1.5 text-sm text-secondary select-none',
        'data-[highlighted]:bg-surface-hover data-[highlighted]:text-primary',
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuLabel({ className, ...props }: WithClassName<ComponentProps<typeof BaseMenu.GroupLabel>>) {
  return (
    <BaseMenu.GroupLabel
      className={cn('stamp px-2.5 pb-1 pt-2 text-2xs text-muted', className)}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({ className, ...props }: WithClassName<ComponentProps<typeof BaseMenu.Separator>>) {
  return <BaseMenu.Separator className={cn('my-1 h-px bg-hairline', className)} {...props} />;
}

export const DropdownMenuGroup = BaseMenu.Group;
