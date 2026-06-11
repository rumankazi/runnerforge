'use client';

import { Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useSyncExternalStore } from 'react';
import { Button } from './button';

const noopSubscribe = () => () => {};

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // true after hydration only — avoids server/client icon mismatch
  const mounted = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );

  const isDark = resolvedTheme === 'dark';
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      {mounted ? (
        isDark ? (
          <Sun size={14} strokeWidth={1.75} absoluteStrokeWidth />
        ) : (
          <Moon size={14} strokeWidth={1.75} absoluteStrokeWidth />
        )
      ) : (
        <span className="size-3.5" />
      )}
    </Button>
  );
}
