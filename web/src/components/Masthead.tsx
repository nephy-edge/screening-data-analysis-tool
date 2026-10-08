interface MastheadProps {
  docType: string;
  date: string;
  brand?: string;
  onMenuClick?: () => void;
}

export function Masthead({ docType, date, brand = 'Lendable', onMenuClick }: MastheadProps) {
  return (
    <header className="sd-masthead">
      {onMenuClick ? (
        <button className="sd-masthead__menu" aria-label="Open menu" onClick={onMenuClick}>
          <span />
          <span />
          <span />
        </button>
      ) : null}
      <div className="sd-masthead__brand">
        <span className="sd-masthead__dot" />
        <span className="sd-masthead__name">{brand}</span>
      </div>
      <span className="sd-masthead__divider" />
      <span className="sd-masthead__doctype">{docType}</span>
      <span className="sd-masthead__spacer" />
      <div className="sd-masthead__meta">
        <span className="sd-masthead__date">{date}</span>
      </div>
    </header>
  );
}
