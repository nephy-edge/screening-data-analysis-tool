import type { ReactNode } from 'react';

type AlertTone = 'success' | 'info' | 'warning' | 'error';

const toneClass: Record<AlertTone, string> = {
  success: 'sd-alert sd-alert--success',
  info: 'sd-alert sd-alert--info',
  warning: 'sd-alert sd-alert--warning',
  error: 'sd-alert sd-alert--error',
};

export function Alert({ tone = 'info', children }: { tone?: AlertTone; children: ReactNode }) {
  return <div className={toneClass[tone]}>{children}</div>;
}
