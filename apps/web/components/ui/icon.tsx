import type { LucideIcon, LucideProps } from 'lucide-react';

interface IconProps extends LucideProps {
  icon: LucideIcon;
}

/** Lucide wrapper enforcing the brand stroke treatment. */
export function Icon({ icon: LucideGlyph, size = 16, strokeWidth = 1.75, ...props }: IconProps) {
  return (
    <LucideGlyph size={size} strokeWidth={strokeWidth} absoluteStrokeWidth {...props} />
  );
}
