export interface TabItem {
  id: string;
  label: string;
}

interface TabsProps {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
}

export function Tabs({ items, value, onChange }: TabsProps) {
  return (
    <div className="sd-tabs" role="tablist">
      <div className="sd-tabs__bar">
        {items.map((item) => {
          const selected = item.id === value;
          return (
            <button
              key={item.id}
              role="tab"
              aria-selected={selected}
              className="sd-tabs__tab"
              onClick={() => onChange(item.id)}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
