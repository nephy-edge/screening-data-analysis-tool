import { useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { createPortal } from 'react-dom';

/** A "?" icon that shows `text` in a tooltip on hover or keyboard focus -
 * the same look as Metric's hint icon. */
export function HelpTip({ text }: { text: string }) {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);
  const timer = useRef<number>();
  const place = (el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: Math.min(rect.left, window.innerWidth - 240) });
  };
  // Hover waits briefly so sweeping across a row of icons doesn't flash tips; focus is instant.
  const hoverShow = (e: React.MouseEvent) => {
    const el = e.currentTarget as HTMLElement;
    timer.current = window.setTimeout(() => place(el), 150);
  };
  const focusShow = (e: React.FocusEvent) => place(e.currentTarget as HTMLElement);
  const hide = () => {
    window.clearTimeout(timer.current);
    setTip(null);
  };
  return (
    <>
      <span
        tabIndex={0}
        aria-label={text}
        onMouseEnter={hoverShow}
        onMouseLeave={hide}
        onFocus={focusShow}
        onBlur={hide}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
          width: 14, height: 14, marginLeft: 6, borderRadius: '50%', background: '#D8DEE5',
          color: '#16312E', fontSize: '.7rem', fontWeight: 700, cursor: 'help', verticalAlign: 'middle',
        }}
      >
        ?
      </span>
      {createPortal(
        <AnimatePresence>
          {tip ? (
            <motion.span
              role="tooltip"
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={{ duration: 0.12, ease: [0.23, 1, 0.32, 1] }}
              style={{
                transformOrigin: 'top left',
                position: 'fixed', top: tip.top, left: tip.left, width: 230, padding: '8px 10px',
                background: '#16312E', color: '#EDF3F1', fontSize: '.72rem', lineHeight: 1.4,
                borderRadius: 6, zIndex: 10000, fontWeight: 400, textAlign: 'left', textTransform: 'none',
                letterSpacing: 'normal', boxShadow: '0 2px 8px rgba(0,0,0,.2)', pointerEvents: 'none',
              }}
            >
              {text}
            </motion.span>
          ) : null}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
