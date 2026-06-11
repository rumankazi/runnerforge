'use client';

import { Tooltip as BaseTooltip } from '@base-ui-components/react/tooltip';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

export function Tooltip({
  content,
  children,
  mono,
}: {
  content: ReactNode;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={<span className="inline-flex" />}>{children}</BaseTooltip.Trigger>
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner sideOffset={6} className="z-50">
          <BaseTooltip.Popup
            className={cn(
              'max-w-72 rounded-sm border border-hairline bg-surface-raised px-2 py-1 text-xs text-secondary shadow-overlay',
              mono && 'font-mono text-2xs tnum',
            )}
          >
            {content}
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  );
}

export const TooltipProvider = BaseTooltip.Provider;
