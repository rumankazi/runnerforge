'use client';

import { useState } from 'react';
import { ChartFrame, ChartLegend } from '@/components/charts/chart-frame';
import { LineChart } from '@/components/charts/line';
import { StackedBarChart } from '@/components/charts/stacked-bar';
import { StepAreaChart } from '@/components/charts/step-area';
import { Tabs, TabsList, TabsTab } from '@/components/ui/tabs';
import { useMetrics } from '@/lib/data/use-live-data';

const OUTCOME_COLORS = [
  'var(--status-success)',
  'var(--status-failure)',
  'var(--status-cancelled)',
  'var(--status-preempted)',
];
const MACHINE_COLORS = [
  'var(--color-iron-500)',
  'var(--color-ember-700)',
  'var(--color-ember-500)',
  'var(--color-ember-300)',
];
const SPOT_COLORS = ['var(--status-preempted)', 'var(--color-iron-500)'];
const SWEEP_COLORS = ['var(--color-iron-500)', 'var(--status-warning)'];
const POLICY_COLORS = [
  'var(--status-warning)',
  'var(--status-failure)',
  'var(--status-preempted)',
];

export function Trends() {
  const [days, setDays] = useState(14);
  const metrics = useMetrics(days);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="stamp text-lg text-primary">TRENDS</h1>
        <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <TabsList className="border-b-0">
            <TabsTab value="14" className="font-mono text-xs">
              14d
            </TabsTab>
            <TabsTab value="30" className="font-mono text-xs">
              30d
            </TabsTab>
          </TabsList>
        </Tabs>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartFrame title="JOB VOLUME" metric="webhook_event_count">
          <StepAreaChart series={metrics.jobVolume[0]} color="var(--accent)" />
        </ChartFrame>

        <ChartFrame
          title="OUTCOMES"
          metric={['vm_creation_outcome_count', 'job_preempted_count']}
          legend={
            <ChartLegend
              items={[
                { label: 'forged', color: OUTCOME_COLORS[0] },
                { label: 'fractured', color: OUTCOME_COLORS[1] },
                { label: 'ash', color: OUTCOME_COLORS[2] },
                { label: 'quenched', color: OUTCOME_COLORS[3] },
              ]}
            />
          }
        >
          <StackedBarChart series={metrics.outcomes} colors={OUTCOME_COLORS} />
        </ChartFrame>

        <ChartFrame
          title="TIME TO RUNNER"
          metric="time_to_runner_seconds"
          legend={
            <ChartLegend
              items={[
                { label: 'p50', color: 'var(--accent)' },
                { label: 'p95', color: 'var(--status-warning)' },
              ]}
            />
          }
        >
          <LineChart
            series={metrics.timeToRunner}
            colors={['var(--accent)', 'var(--status-warning)']}
          />
        </ChartFrame>

        <ChartFrame
          title="MACHINE MIX"
          metric="vm_creation_count"
          legend={
            <ChartLegend
              items={['small', 'medium', 'large', 'xlarge'].map((label, i) => ({
                label,
                color: MACHINE_COLORS[i],
              }))}
            />
          }
        >
          <StackedBarChart series={metrics.machineMix} colors={MACHINE_COLORS} />
        </ChartFrame>

        <ChartFrame
          title="SPOT VS ON-DEMAND"
          metric="vm_creation_count"
          legend={
            <ChartLegend
              items={[
                { label: 'spot', color: SPOT_COLORS[0] },
                { label: 'on-demand', color: SPOT_COLORS[1] },
              ]}
            />
          }
        >
          <StackedBarChart series={metrics.spotShare} colors={SPOT_COLORS} />
        </ChartFrame>

        <ChartFrame
          title="SWEEP ACTIVITY"
          metric={['sweep_checked_count', 'sweep_deleted_count']}
          legend={
            <ChartLegend
              items={[
                { label: 'checked', color: SWEEP_COLORS[0] },
                { label: 'deleted', color: SWEEP_COLORS[1] },
              ]}
            />
          }
        >
          <StackedBarChart series={metrics.sweep} colors={SWEEP_COLORS} />
        </ChartFrame>

        <ChartFrame
          title="POLICY REJECTIONS"
          metric="machine_policy_reject_count"
          className="lg:col-span-2"
          legend={
            <ChartLegend
              items={[
                { label: 'unknown_token', color: POLICY_COLORS[0] },
                { label: 'conflicting_machines', color: POLICY_COLORS[1] },
                { label: 'invalid_machine_type', color: POLICY_COLORS[2] },
              ]}
            />
          }
        >
          <StackedBarChart series={metrics.policy} colors={POLICY_COLORS} height={140} />
        </ChartFrame>
      </div>
    </div>
  );
}
