'use client';

import { curveStepAfter } from '@visx/curve';
import { scaleLinear } from '@visx/scale';
import { LinePath } from '@visx/shape';

interface SparklineProps {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}

export function Sparkline({
  values,
  color = 'var(--accent)',
  width = 80,
  height = 20,
}: SparklineProps) {
  if (values.length < 2) return null;
  const xScale = scaleLinear({ domain: [0, values.length - 1], range: [0, width] });
  const yScale = scaleLinear({
    domain: [0, Math.max(1, ...values)],
    range: [height - 2, 2],
  });
  return (
    <svg width={width} height={height} aria-hidden className="overflow-visible">
      <LinePath
        data={values}
        x={(_, i) => xScale(i)}
        y={(v) => yScale(v)}
        curve={curveStepAfter}
        stroke={color}
        strokeWidth={1.25}
      />
    </svg>
  );
}
