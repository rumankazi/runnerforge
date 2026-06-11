'use client';

import { Flame, Hammer, Search, TriangleAlert } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Wordmark } from '@/components/brand/wordmark';
import { ChartFrame, ChartLegend } from '@/components/charts/chart-frame';
import { Sparkline } from '@/components/charts/sparkline';
import { StepAreaChart } from '@/components/charts/step-area';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/empty-state';
import { Icon } from '@/components/ui/icon';
import { Input, Textarea } from '@/components/ui/input';
import { Kbd } from '@/components/ui/kbd';
import { LogViewer } from '@/components/ui/log-viewer';
import { Panel } from '@/components/ui/panel';
import { Select } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { RowSkeleton, Skeleton, StatSkeleton } from '@/components/ui/skeleton';
import { StatBlock } from '@/components/ui/stat-block';
import { StatusChip, type RunStatus } from '@/components/ui/status-chip';
import { Tabs, TabsList, TabsPanel, TabsTab } from '@/components/ui/tabs';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { Tooltip, TooltipProvider } from '@/components/ui/tooltip';
import { generateRunEntries, makeScenario } from '@/lib/data/generators';
import { mulberry32 } from '@/lib/data/rng';
import type { MetricSeries } from '@/lib/data/types';

const IRON_RAMP = [
  ['iron-950', 'oklch(0.155 0.008 60)'],
  ['iron-900', 'oklch(0.20 0.009 60)'],
  ['iron-850', 'oklch(0.235 0.010 60)'],
  ['iron-800', 'oklch(0.27 0.010 60)'],
  ['iron-700', 'oklch(0.34 0.011 62)'],
  ['iron-600', 'oklch(0.43 0.012 64)'],
  ['iron-500', 'oklch(0.55 0.012 66)'],
  ['iron-400', 'oklch(0.65 0.012 70)'],
  ['iron-300', 'oklch(0.76 0.012 75)'],
  ['iron-200', 'oklch(0.85 0.010 80)'],
  ['iron-100', 'oklch(0.93 0.008 85)'],
  ['iron-50', 'oklch(0.965 0.006 85)'],
] as const;

const EMBER_RAMP = [
  ['ember-300', 'oklch(0.85 0.12 75)'],
  ['ember-400', 'oklch(0.78 0.16 60)'],
  ['ember-500', 'oklch(0.72 0.19 48)'],
  ['ember-600', 'oklch(0.63 0.19 42)'],
  ['ember-700', 'oklch(0.54 0.16 38)'],
] as const;

const ALL_STATUSES: RunStatus[] = [
  'queued',
  'running',
  'success',
  'failure',
  'cancelled',
  'warning',
  'preempted',
];

const HEAT_CYCLE: RunStatus[] = ['queued', 'running', 'success'];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="stamp border-b border-hairline pb-2 text-sm text-primary">{title}</h2>
      {children}
    </section>
  );
}

function Swatch({ name, value, bg }: { name: string; value: string; bg: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="h-12 rounded-sm machined" style={{ backgroundColor: bg }} />
      <div className="font-mono text-2xs text-secondary">{name}</div>
      <div className="font-mono text-2xs text-muted">{value}</div>
    </div>
  );
}

function HeatCycleDemo() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setIndex((i) => (i + 1) % HEAT_CYCLE.length), 2200);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex items-center gap-3">
      <StatusChip status={HEAT_CYCLE[index]} />
      <span className="font-mono text-2xs text-muted">auto-cycling: heating → molten → forged</span>
    </div>
  );
}

// Built once at module load — keeps render pure for the React Compiler.
const DEMO_ENTRIES = (() => {
  const rng = mulberry32(7);
  const scenario = makeScenario(rng);
  scenario.preempted = false;
  return generateRunEntries(rng, scenario, Date.now() - 3_600_000);
})();

const DEMO_SERIES: MetricSeries = {
  metric: 'webhook_event_count',
  labels: { event_type: 'queued' },
  points: Array.from({ length: 14 }, (_, i) => ({
    time: new Date(Date.now() - (13 - i) * 86_400_000).toISOString().slice(0, 10),
    value: [22, 31, 28, 35, 12, 8, 26, 33, 29, 38, 31, 14, 9, 27][i],
  })),
};

export function Styleguide() {
  return (
    <TooltipProvider>
      <main className="mx-auto flex max-w-4xl flex-col gap-10 p-6 pb-24 md:p-10">
        <header className="flex items-center justify-between gap-4">
          <div className="flex flex-col gap-2">
            <Wordmark />
            <h1 className="stamp text-xl text-primary">BRAND TOOLKIT</h1>
            <p className="max-w-xl text-sm text-secondary">
              Living reference for every token and component. Industrial forge: iron surfaces,
              one ember accent, mono for all data, machined edges, purposeful motion.
            </p>
          </div>
          <ThemeToggle />
        </header>

        <Section title="01 — COLOR / IRON RAMP">
          <div className="grid grid-cols-4 gap-3 md:grid-cols-6">
            {IRON_RAMP.map(([name, value]) => (
              <Swatch key={name} name={name} value={value} bg={`var(--color-${name})`} />
            ))}
          </div>
        </Section>

        <Section title="02 — COLOR / EMBER ACCENT">
          <div className="grid grid-cols-3 gap-3 md:grid-cols-5">
            {EMBER_RAMP.map(([name, value]) => (
              <Swatch key={name} name={name} value={value} bg={`var(--color-${name})`} />
            ))}
          </div>
          <p className="text-xs text-muted">
            Ember is reserved for live activity, primary actions, and focus rings. Nothing else
            gets to be orange.
          </p>
        </Section>

        <Section title="03 — STATUS SYSTEM (THE HEAT METAPHOR)">
          <div className="flex flex-wrap gap-2.5">
            {ALL_STATUSES.map((s) => (
              <StatusChip key={s} status={s} />
            ))}
          </div>
          <div className="flex flex-wrap gap-2.5">
            <StatusChip status="running" detail="2m 14s" />
            <StatusChip status="queued" size="dot" />
          </div>
          <HeatCycleDemo />
          <p className="text-xs text-muted">
            Success is “forged” steel blue, not green — cooled metal, always paired with a check
            glyph. Prefer green? One-line swap in{' '}
            <span className="font-mono">app/theme.css</span>:{' '}
            <span className="font-mono">--status-success: oklch(0.74 0.13 155)</span> (dark) /{' '}
            <span className="font-mono">oklch(0.52 0.12 155)</span> (light).
          </p>
        </Section>

        <Section title="04 — TYPOGRAPHY">
          <div className="flex flex-col gap-3">
            <div className="stamp text-2xl text-primary">STAMPED DISPLAY — ARCHIVO WDTH 75</div>
            <div className="text-xl text-primary">Archivo regular for UI headings</div>
            <div className="text-sm text-secondary">
              Body and UI copy at 13–14px. Sans never renders data.
            </div>
            <div className="font-mono text-xs text-secondary tnum">
              IBM Plex Mono for ALL data: job_id=51234189 · ttr=42.18s · n2-standard-4 ·
              europe-west4-a · 2026-06-11T14:02:11.482Z
            </div>
            <div className="font-mono text-metric text-primary tnum">1,284</div>
          </div>
        </Section>

        <Section title="05 — SURFACES & EDGES">
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-md bg-surface-page p-4 machined">
              <div className="font-mono text-2xs text-muted">surface-page + grain</div>
            </div>
            <div className="rounded-md bg-surface-panel p-4 machined">
              <div className="font-mono text-2xs text-muted">surface-panel, machined edge</div>
            </div>
            <div className="rounded-md bg-surface-raised p-4 shadow-overlay machined">
              <div className="font-mono text-2xs text-muted">surface-raised + shadow-overlay</div>
            </div>
          </div>
          <p className="text-xs text-muted">
            Depth comes from hairline borders and a 1px inset top highlight — the single drop
            shadow token is reserved for overlays. Radii: 1/2/4/6px. Nothing rounder.
          </p>
        </Section>

        <Section title="06 — BUTTONS">
          <div className="flex flex-wrap items-center gap-2.5">
            <Button variant="primary">Forge runner</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Delete VM</Button>
            <Button variant="primary" loading>
              Provisioning
            </Button>
            <Button variant="secondary" disabled>
              Disabled
            </Button>
            <Button variant="secondary" size="sm">
              Small
            </Button>
          </div>
        </Section>

        <Section title="07 — FORM CONTROLS">
          <div className="grid max-w-xl gap-3 md:grid-cols-2">
            <Input placeholder="Filter repositories…" />
            <Input mono placeholder="job_id" />
            <Select
              placeholder="Machine size"
              options={[
                { value: 'small', label: 'small · n2-standard-2' },
                { value: 'medium', label: 'medium · n2-standard-4' },
                { value: 'large', label: 'large · n2-standard-8' },
                { value: 'xlarge', label: 'xlarge · n2-standard-16' },
              ]}
              mono
            />
            <Textarea placeholder="Notes…" />
          </div>
        </Section>

        <Section title="08 — BADGES, KBD, TOOLTIP">
          <div className="flex flex-wrap items-center gap-2.5">
            <Badge>n2-standard-4</Badge>
            <Badge variant="spot">spot</Badge>
            <Badge variant="accent">v1.8.2</Badge>
            <Badge>europe-west4-a</Badge>
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
            <Tooltip content={<span>time_to_runner_seconds=42.18</span>} mono>
              <span className="cursor-help text-xs text-secondary underline decoration-dotted underline-offset-2">
                hover for mono tooltip
              </span>
            </Tooltip>
          </div>
        </Section>

        <Section title="09 — TABS">
          <Tabs defaultValue="live" className="max-w-md">
            <TabsList>
              <TabsTab value="live">Live</TabsTab>
              <TabsTab value="history">History</TabsTab>
              <TabsTab value="logs">Logs</TabsTab>
            </TabsList>
            <TabsPanel value="live" className="text-sm text-secondary">
              Animated underline indicator, spring-driven.
            </TabsPanel>
            <TabsPanel value="history" className="text-sm text-secondary">
              History panel.
            </TabsPanel>
            <TabsPanel value="logs" className="text-sm text-secondary">
              Logs panel.
            </TabsPanel>
          </Tabs>
        </Section>

        <Section title="10 — STATS & SPARKLINES">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <StatBlock label="MOLTEN NOW" value={4} hot />
            <StatBlock label="FORGED TODAY" value={128} caption="97.7% success" />
            <StatBlock label="TIME TO RUNNER P50" value={42.2} decimals={1} unit="s" />
          </div>
          <div className="flex items-center gap-3">
            <Sparkline values={[3, 5, 4, 8, 6, 9, 7, 11, 8, 12]} />
            <span className="font-mono text-2xs text-muted">sparkline · curveStepAfter</span>
          </div>
        </Section>

        <Section title="11 — CHARTS">
          <ChartFrame
            title="JOB VOLUME (DEMO)"
            metric="webhook_event_count"
            legend={<ChartLegend items={[{ label: 'queued', color: 'var(--accent)' }]} />}
          >
            <StepAreaChart series={DEMO_SERIES} color="var(--accent)" />
          </ChartFrame>
        </Section>

        <Section title="12 — LOG VIEWER (LOG-SHAPED MOCK DATA)">
          <LogViewer entries={DEMO_ENTRIES} />
          <p className="text-xs text-muted">
            Entries mirror the orchestrator&apos;s Cloud Logging shapes exactly — same field
            names the production data source will return. Click a row for the raw payload.
          </p>
        </Section>

        <Section title="13 — LOADING / EMPTY">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-md bg-surface-panel machined">
              <RowSkeleton />
              <RowSkeleton />
              <StatSkeleton />
              <div className="px-4 pb-4">
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
            <Panel>
              <EmptyState title="NO ACTIVE RUNS — FORGE IS COLD" description="Queue a workflow job to light it up." />
            </Panel>
          </div>
        </Section>

        <Section title="14 — ICONOGRAPHY">
          <div className="flex items-center gap-4 text-secondary">
            <Icon icon={Flame} />
            <Icon icon={Hammer} />
            <Icon icon={Search} />
            <Icon icon={TriangleAlert} />
            <span className="font-mono text-2xs text-muted">
              lucide · strokeWidth 1.75 · absoluteStrokeWidth · 16px
            </span>
          </div>
        </Section>

        <Section title="15 — MOTION">
          <ul className="flex flex-col gap-1.5 font-mono text-xs text-secondary">
            <li>durations: 150 / 200 / 300ms · ease-machine cubic-bezier(0.2, 0, 0, 1)</li>
            <li>springStatus: stiffness 480 · damping 34 · mass 0.6 (chips, row reorder)</li>
            <li>springPanel: stiffness 320 · damping 36 (drawers, expand)</li>
            <li>log lines: opacity 0→1, y 4→0, 150ms, no exit</li>
            <li>all pulses & ticks respect prefers-reduced-motion</li>
          </ul>
          <Separator />
          <p className="text-xs text-muted">
            Motion is physics, not decoration: status transitions, ticking numbers, streaming
            log lines. Everything else is near-instant.
          </p>
        </Section>
      </main>
    </TooltipProvider>
  );
}
