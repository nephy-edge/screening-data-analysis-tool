import { useState } from 'react';
import { createPortal } from 'react-dom';

/** A "?" icon that shows `text` in a tooltip on hover or keyboard focus -
 * the same look as Metric's hint icon. */
export function HelpTip({ text }: { text: string }) {
  const [tip, setTip] = useState<{ top: number; left: number } | null>(null);
  const show = (e: React.SyntheticEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setTip({ top: rect.bottom + 6, left: Math.min(rect.left, window.innerWidth - 270) });
  };
  const hide = () => setTip(null);
  return (
    <>
      <span
        tabIndex={0}
        aria-label={text}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
          width: 14, height: 14, marginLeft: 6, borderRadius: '50%', background: '#D8DEE5',
          color: '#16312E', fontSize: '.7rem', fontWeight: 700, cursor: 'help', verticalAlign: 'middle',
        }}
      >
        ?
      </span>
      {tip
        ? createPortal(
            <span
              role="tooltip"
              style={{
                position: 'fixed', top: tip.top, left: tip.left, width: 260, padding: '8px 10px',
                background: '#16312E', color: '#EDF3F1', fontSize: '.72rem', lineHeight: 1.4,
                borderRadius: 6, zIndex: 10000, fontWeight: 400, textAlign: 'left', textTransform: 'none',
                letterSpacing: 'normal', boxShadow: '0 2px 8px rgba(0,0,0,.2)', pointerEvents: 'none',
              }}
            >
              {text}
            </span>,
            document.body,
          )
        : null}
    </>
  );
}
