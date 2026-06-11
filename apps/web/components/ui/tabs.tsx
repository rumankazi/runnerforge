'use client';

import { Tabs as BaseTabs } from '@base-ui-components/react/tabs';
import type { ComponentProps } from 'react';

type WithClassName<T> = Omit<T, 'className'> & { className?: string };
import { cn } from '@/lib/cn';

export function Tabs({ className, ...props }: WithClassName<ComponentProps<typeof BaseTabs.Root>>) {
  return <BaseTabs.Root className={cn('flex flex-col gap-3', className)} {...props} />;
}

export function TabsList({ className, children, ...props }: WithClassName<ComponentProps<typeof BaseTabs.List>>) {
  return (
    <BaseTabs.List
      className={cn('relative flex gap-1 border-b border-hairline', className)}
      {...props}
    >
      {children}
      <BaseTabs.Indicator
        className={cn(
          'absolute bottom-0 left-0 h-[2px] bg-accent',
          'w-[var(--active-tab-width)] translate-x-[var(--active-tab-left)]',
          'transition-[width,translate] duration-200 ease-[var(--ease-machine)]',
          'motion-reduce:transition-none',
        )}
      />
    </BaseTabs.List>
  );
}

export function TabsTab({ className, ...props }: WithClassName<ComponentProps<typeof BaseTabs.Tab>>) {
  return (
    <BaseTabs.Tab
      className={cn(
        'h-8 px-3 text-sm text-secondary transition-colors duration-150 select-none',
        'hover:text-primary data-[selected]:text-primary',
        'focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus',
        className,
      )}
      {...props}
    />
  );
}

export function TabsPanel({ className, ...props }: WithClassName<ComponentProps<typeof BaseTabs.Panel>>) {
  return <BaseTabs.Panel className={cn('focus-visible:outline-none', className)} {...props} />;
}
