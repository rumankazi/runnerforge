'use client';

import type { ReactNode } from 'react';
import { Sidebar } from '@/components/dashboard/sidebar';
import { Topbar } from '@/components/dashboard/topbar';
import { ToastProvider } from '@/components/ui/toast';
import { TooltipProvider } from '@/components/ui/tooltip';
import { MockStreamProvider } from '@/lib/data/use-live-data';

export default function ForgeLayout({ children }: { children: ReactNode }) {
  return (
    <MockStreamProvider>
      <TooltipProvider>
        <ToastProvider>
          {/* single ambient ember glow, dark theme only */}
          <div
            aria-hidden
            className="pointer-events-none fixed inset-0 z-0 hidden dark:block"
            style={{
              background:
                'radial-gradient(600px 400px at 0% 0%, oklch(0.72 0.19 48 / var(--glow-opacity)), transparent 70%)',
            }}
          />
          <div className="relative z-10 flex min-h-screen w-full">
            <Sidebar />
            <div className="flex min-w-0 flex-1 flex-col">
              <Topbar />
              <main className="flex-1 p-4 md:p-6">{children}</main>
            </div>
          </div>
        </ToastProvider>
      </TooltipProvider>
    </MockStreamProvider>
  );
}
