import type { SelectHTMLAttributes } from 'react';

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`sd-select ${className ?? ''}`} {...rest}>
      {children}
    </select>
  );
}
