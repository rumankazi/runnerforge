'use client';

import { curveMonotoneX } from '@visx/curve';
import { scaleLinear, scalePoint } from '@visx/scale';
import { LinePath } from '@visx/shape';
import type { MetricSeries } from '@/lib/data/types';
import { useMeasuredWidth } from './use-measure';
import { ChartAxes, chartDims } from './axes';

interface LineChartProps {
  series: MetricSeries[];
  colors: string[];
  height?: number;
}

/** Multi-series line chart (e.g. P50/P95 latency). */
export function LineChart({ series, colors, height = 180 }: LineChartProps) {
  const { ref, width } = useMeasuredWidth<HTMLDivElement>();
  const { innerWidth, innerHeight, margin } = chartDims(width, height);
  const days = series[0]?.points.map((p) => p.time) ?? [];

  const xScale = scalePoint({ domain: days, range: [0, innerWidth] });
  const maxY = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.value)));
  const yScale = scaleLinear({ domain: [0, maxY * 1.1], range: [innerHeight, 0] });

  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={series[0]?.metric}>
          <g transform={`translate(${margin.left},${margin.top})`}>
            <ChartAxes xScale={xScale} yScale={yScale} innerWidth={innerWidth} innerHeight={innerHeight} />
            {series.map((s, i) => (
              <LinePath
                key={JSON.stringify(s.labels)}
                data={s.points}
                x={(p) => xScale(p.time) ?? 0}
                y={(p) => yScale(p.value)}
                curve={curveMonotoneX}
                stroke={colors[i % colors.length]}
                strokeWidth={1.5}
                strokeDasharray={i > 0 ? '4 3' : undefined}
              />
            ))}
          </g>
        </svg>
      )}
    </div>
  );
}
