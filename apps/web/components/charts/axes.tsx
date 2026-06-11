'use client';

import { GridRows } from '@visx/grid';
import type { ScaleLinear, ScalePoint } from 'd3-scale';

export function chartDims(width: number, height: number) {
  const margin = { top: 8, right: 8, bottom: 22, left: 34 };
  return {
    margin,
    innerWidth: Math.max(0, width - margin.left - margin.right),
    innerHeight: Math.max(0, height - margin.top - margin.bottom),
  };
}

interface ChartAxesProps {
  xScale: ScalePoint<string>;
  yScale: ScaleLinear<number, number>;
  innerWidth: number;
  innerHeight: number;
}

/** Hairline grid + mono tick labels; no axis lines, no chart borders. */
export function ChartAxes({ xScale, yScale, innerWidth, innerHeight }: ChartAxesProps) {
  const xTicks = xScale.domain();
  const step = Math.max(1, Math.ceil(xTicks.length / 6));
  const yTicks = yScale.ticks(4);

  return (
    <g aria-hidden>
      <GridRows
        scale={yScale}
        width={innerWidth}
        numTicks={4}
        stroke="var(--border-hairline)"
      />
      {yTicks.map((t) => (
        <text
          key={t}
          x={-8}
          y={yScale(t)}
          dy="0.32em"
          textAnchor="end"
          className="fill-[var(--text-muted)] font-mono text-[10px]"
        >
          {t}
        </text>
      ))}
      {xTicks
        .filter((_, i) => i % step === 0)
        .map((t) => (
          <text
            key={t}
            x={xScale(t) ?? 0}
            y={innerHeight + 16}
            textAnchor="middle"
            className="fill-[var(--text-muted)] font-mono text-[10px]"
          >
            {t.slice(5)}
          </text>
        ))}
    </g>
  );
}
