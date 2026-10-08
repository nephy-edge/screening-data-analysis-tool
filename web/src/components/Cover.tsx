interface CoverProps {
  title: string;
  subtitle?: string;
}

export function Cover({ title, subtitle }: CoverProps) {
  return (
    <div className="sd-cover">
      <h1 className="sd-cover__title">{title}</h1>
      {subtitle ? <div className="sd-cover__sub">{subtitle}</div> : null}
    </div>
  );
}
