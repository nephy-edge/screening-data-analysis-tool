import { useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, type HTMLMotionProps } from 'framer-motion';

export interface MetricProps extends HTMLMotionProps<'div'> {
  label: string;
  value: string;
  hint?: string;
}

export function Metric({ label, value, hint, className, ...rest }: MetricProps) {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);

  const show = (e: React.SyntheticEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: rect.left });
  };
  const hide = () => setTip(null);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.26, ease: [0.23, 1, 0.32, 1] }}
      className={`sd-metric ${className ?? ''}`}
      {...rest}
    >
      <div className="sd-metric__label">
        <span style={{ display: 'inline-flex', alignItems: 'center' }}>
          {label}
          {hint ? (
            <span
              onMouseEnter={show}
              onMouseLeave={hide}
              onFocus={show}
              onBlur={hide}
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 14, height: 14, marginLeft: 6, borderRadius: '50%', background: '#D8DEE5',
                color: '#16312E', fontSize: '.7rem', fontWeight: 700, cursor: 'help',
              }}
            >
              ?
            </span>
          ) : null}
        </span>
        {tip && hint
          ? createPortal(
              <span
                role="tooltip"
                style={{
                  position: 'fixed',
                  top: tip.top,
                  left: tip.left,
                  width: 230,
                  padding: '8px 10px',
                  background: '#16312E',
                  color: '#EDF3F1',
                  fontSize: '.72rem',
                  lineHeight: 1.4,
                  borderRadius: 6,
                  zIndex: 10000,
                  fontWeight: 400,
                  textAlign: 'left',
                  boxShadow: '0 2px 8px rgba(0,0,0,.2)',
                  pointerEvents: 'none',
                }}
              >
                {hint}
              </span>,
              document.body,
            )
          : null}
      </div>
      <div className="sd-metric__value">{value}</div>
    </motion.div>
  );
}
