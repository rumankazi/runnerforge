'use client';

import '@excalidraw/excalidraw/index.css';
import type {
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
} from '@excalidraw/excalidraw/types';
import { Maximize2, Minimize2 } from 'lucide-react';
import { useTheme } from 'next-themes';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const ExcalidrawCanvas = dynamic(async () => (await import('@excalidraw/excalidraw')).Excalidraw, {
  ssr: false,
});

interface ExcalidrawProps {
  data: ExcalidrawInitialDataState;
  height?: number | string;
}

export function Excalidraw({ data, height = 500 }: ExcalidrawProps) {
  const { resolvedTheme } = useTheme();
  const theme = resolvedTheme === 'dark' ? 'dark' : 'light';

  const containerRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<ExcalidrawImperativeAPI | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Transparent canvas so the wrapper's bg shows through — opaque inline,
  // translucent in fullscreen.
  const initialData = useMemo(
    () => ({
      ...data,
      appState: { ...(data.appState ?? {}), viewBackgroundColor: 'transparent' },
    }),
    [data],
  );

  const fitToContent = useCallback(() => {
    apiRef.current?.scrollToContent(undefined, {
      fitToViewport: true,
      viewportZoomFactor: 0.9,
      animate: false,
    });
  }, []);

  const onApiReady = useCallback(
    (api: ExcalidrawImperativeAPI) => {
      apiRef.current = api;
      requestAnimationFrame(fitToContent);
    },
    [fitToContent],
  );

  // Re-fit on any container size change — initial layout, fullscreen toggle,
  // window resize, late font/CSS load. Replaces the old onChange-once latch.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(fitToContent);
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [fitToContent]);

  useEffect(() => {
    if (!isFullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isFullscreen]);

  return (
    <div
      ref={containerRef}
      className={
        isFullscreen
          ? 'fixed inset-0 z-50 bg-background/80 backdrop-blur-md'
          : 'relative my-4 w-full rounded-lg bg-background'
      }
      style={isFullscreen ? undefined : { height }}
    >
      <button
        type="button"
        onClick={() => setIsFullscreen(v => !v)}
        aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        className="absolute right-2 top-2 z-10 rounded-md border border-border bg-background/80 p-1.5 backdrop-blur transition hover:bg-background"
      >
        {isFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
      </button>
      <ExcalidrawCanvas
        initialData={initialData}
        excalidrawAPI={onApiReady}
        viewModeEnabled
        zenModeEnabled
        theme={theme}
      />
    </div>
  );
}
