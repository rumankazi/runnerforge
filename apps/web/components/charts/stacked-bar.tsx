'use client';

import { scaleBand, scaleLinear } from '@visx/scale';
import type { MetricSeries } from '@/lib/data/types';
import { useMeasuredWidth } from './use-measure';
import { chartDims } from './axes';
import { GridRows } from '@visx/grid';

interface StackedBarChartProps {
  series: MetricSeries[];
  colors: string[];
  height?: number;
}

/** Stacked bars per day, one segment per labeled series. */
export function StackedBarChart({ series, colors, height = 180 }: StackedBarChartProps) {
  const { ref, width } = useMeasuredWidth<HTMLDivElement>();
  const { innerWidth, innerHeight, margin } = chartDims(width, height);
  const days = series[0]?.points.map((p) => p.time) ?? [];

  const totals = days.map((_, i) => series.reduce((sum, s) => sum + (s.points[i]?.value ?? 0), 0));
  const maxY = Math.max(1, ...totals);

  const xScale = scaleBand({ domain: days, range: [0, innerWidth], padding: 0.35 });
  const yScale = scaleLinear({ domain: [0, maxY * 1.1], range: [innerHeight, 0] });
  const yTicks = yScale.ticks(4);
  const labelStep = Math.max(1, Math.ceil(days.length / 6));

  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={series[0]?.metric}>
          <g transform={`translate(${margin.left},${margin.top})`}>
            <GridRows scale={yScale} width={innerWidth} numTicks={4} stroke="var(--border-hairline)" />
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
            {days.map((day, dayIdx) => {
              let yCursor = innerHeight;
              return (
                <g key={day}>
                  {series.map((s, i) => {
                    const value = s.points[dayIdx]?.value ?? 0;
                    const barHeight = innerHeight - yScale(value);
                    yCursor -= barHeight;
                    return (
                      <rect
                        key={JSON.stringify(s.labels)}
                        x={xScale(day)}
                        y={yCursor}
                        width={xScale.bandwidth()}
                        height={barHeight}
                        fill={colors[i % colors.length]}
                        fillOpacity={0.85}
                      />
                    );
                  })}
                  {dayIdx % labelStep === 0 && (
                    <text
                      x={(xScale(day) ?? 0) + xScale.bandwidth() / 2}
                      y={innerHeight + 16}
                      textAnchor="middle"
                      className="fill-[var(--text-muted)] font-mono text-[10px]"
                    >
                      {day.slice(5)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      )}
    </div>
  );
}
