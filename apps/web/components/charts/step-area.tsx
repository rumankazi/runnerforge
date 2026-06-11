'use client';

import { curveStepAfter } from '@visx/curve';
import { scaleLinear, scalePoint } from '@visx/scale';
import { AreaClosed, LinePath } from '@visx/shape';
import type { MetricSeries } from '@/lib/data/types';
import { useMeasuredWidth } from './use-measure';
import { ChartAxes, chartDims } from './axes';

interface StepAreaChartProps {
  series: MetricSeries;
  color: string;
  height?: number;
}

/** Single-series step area — the telemetry look. */
export function StepAreaChart({ series, color, height = 180 }: StepAreaChartProps) {
  const { ref, width } = useMeasuredWidth<HTMLDivElement>();
  const points = series.points;
  const { innerWidth, innerHeight, margin } = chartDims(width, height);

  const xScale = scalePoint({ domain: points.map((p) => p.time), range: [0, innerWidth] });
  const maxY = Math.max(1, ...points.map((p) => p.value));
  const yScale = scaleLinear({ domain: [0, maxY * 1.1], range: [innerHeight, 0] });

  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={series.metric}>
          <g transform={`translate(${margin.left},${margin.top})`}>
            <ChartAxes xScale={xScale} yScale={yScale} innerWidth={innerWidth} innerHeight={innerHeight} />
            <AreaClosed
              data={points}
              x={(p) => xScale(p.time) ?? 0}
              y={(p) => yScale(p.value)}
              yScale={yScale}
              curve={curveStepAfter}
              fill={color}
              fillOpacity={0.12}
            />
            <LinePath
              data={points}
              x={(p) => xScale(p.time) ?? 0}
              y={(p) => yScale(p.value)}
              curve={curveStepAfter}
              stroke={color}
              strokeWidth={1.5}
            />
          </g>
        </svg>
      )}
    </div>
  );
}
