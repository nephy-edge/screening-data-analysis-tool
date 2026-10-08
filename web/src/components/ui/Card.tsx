import type { HTMLAttributes } from 'react';

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`sd-card ${className ?? ''}`} {...rest} />;
}
