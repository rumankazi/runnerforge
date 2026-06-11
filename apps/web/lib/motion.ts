'use client';

import { useReducedMotion } from 'motion/react';
import { animate, useMotionValue } from 'motion/react';
import { useEffect, useState } from 'react';

/** Decisive deceleration — matches --ease-machine in theme.css. */
export const easeMachine = [0.2, 0, 0, 1] as const;

/** Status chip transitions, row reorder. */
export const springStatus = {
  type: 'spring',
  stiffness: 480,
  damping: 34,
  mass: 0.6,
} as const;

/** Drawers, panel expand/collapse. */
export const springPanel = {
  type: 'spring',
  stiffness: 320,
  damping: 36,
} as const;

/** Log line entry: subtle rise, no exit animation. */
export const logEntryVariants = {
  initial: { opacity: 0, y: 4 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.15, ease: easeMachine } },
} as const;

/**
 * Animates a numeric value toward `target` (ticking metric).
 * Respects prefers-reduced-motion by snapping instantly.
 */
export function useTickingNumber(target: number, decimals = 0): string {
  const reduced = useReducedMotion();
  const mv = useMotionValue(target);
  const [animated, setAnimated] = useState(() => target.toFixed(decimals));

  useEffect(() => {
    if (reduced) return;
    const controls = animate(mv, target, {
      duration: 0.3,
      ease: easeMachine,
      onUpdate: (v) => setAnimated(v.toFixed(decimals)),
    });
    return () => controls.stop();
  }, [target, decimals, reduced, mv]);

  return reduced ? target.toFixed(decimals) : animated;
}
