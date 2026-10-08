import { motion, type HTMLMotionProps } from 'framer-motion';
import { forwardRef, type ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

export interface ButtonProps extends Omit<HTMLMotionProps<'button'>, 'children'> {
  variant?: Variant;
  loading?: boolean;
  children?: ReactNode;
}

const variantStyles: Record<Variant, string> = {
  primary: 'sd-btn--primary',
  secondary: 'sd-btn--secondary',
  ghost: 'sd-btn--ghost',
  danger: 'sd-btn--danger',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', loading, children, className, disabled, ...rest }, ref) => (
    <motion.button
      ref={ref}
      whileTap={disabled || loading ? undefined : { scale: 0.97 }}
      transition={{ duration: 0.16, ease: [0.23, 1, 0.32, 1] }}
      className={`sd-btn ${variantStyles[variant]} ${className ?? ''}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? (
        <span className="sd-btn__spinner" aria-hidden />
      ) : (
        <span className="sd-btn__content">{children}</span>
      )}
    </motion.button>
  ),
);
Button.displayName = 'Button';
