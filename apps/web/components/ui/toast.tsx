'use client';

import { Toast as BaseToast } from '@base-ui-components/react/toast';
import { X } from 'lucide-react';
import { cn } from '@/lib/cn';

type ToastSeverity = 'info' | 'warning' | 'error' | 'success';

const SEVERITY_TICK: Record<ToastSeverity, string> = {
  info: 'before:bg-iron-500',
  warning: 'before:bg-status-warning',
  error: 'before:bg-status-failure',
  success: 'before:bg-status-success',
};

function ToastList() {
  const { toasts } = BaseToast.useToastManager();
  return toasts.map((toast) => {
    const severity = ((toast.data as { severity?: ToastSeverity } | undefined)?.severity ??
      'info') as ToastSeverity;
    return (
      <BaseToast.Root
        key={toast.id}
        toast={toast}
        className={cn(
          'relative w-80 rounded-md bg-surface-raised p-3 pl-4 machined shadow-overlay',
          'before:absolute before:inset-y-2 before:left-1.5 before:w-0.5 before:rounded-full',
          SEVERITY_TICK[severity],
          'data-[starting-style]:translate-y-2 data-[starting-style]:opacity-0',
          'data-[ending-style]:opacity-0',
          'transition-all duration-200 ease-[var(--ease-machine)] motion-reduce:transition-none',
        )}
      >
        <BaseToast.Title className="text-sm font-medium text-primary" />
        <BaseToast.Description className="mt-0.5 font-mono text-xs text-secondary" />
        <BaseToast.Close
          aria-label="Dismiss"
          className="absolute right-2 top-2 text-muted hover:text-primary"
        >
          <X size={12} strokeWidth={1.75} absoluteStrokeWidth />
        </BaseToast.Close>
      </BaseToast.Root>
    );
  });
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  return (
    <BaseToast.Provider>
      {children}
      <BaseToast.Portal>
        <BaseToast.Viewport className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
          <ToastList />
        </BaseToast.Viewport>
      </BaseToast.Portal>
    </BaseToast.Provider>
  );
}

export const useToast = BaseToast.useToastManager;
