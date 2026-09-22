import { motion, type HTMLMotionProps } from 'framer-motion';

export interface MetricProps extends HTMLMotionProps<'div'> {
  label: string;
  value: string;
  hint?: string;
}

export function Metric({ label, value, hint, className, ...rest }: MetricProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.26, ease: [0.23, 1, 0.32, 1] }}
      className={`sd-metric ${className ?? ''}`}
      {...rest}
    >
      <div className="sd-metric__label">{label}</div>
      <div className="sd-metric__value">{value}</div>
      {hint ? <div className="sd-metric__hint">{hint}</div> : null}
    </motion.div>
  );
}
