import type { HTMLAttributes, ReactNode } from 'react';

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right';
  mono?: boolean;
}

interface TableProps<T> extends HTMLAttributes<HTMLDivElement> {
  columns: TableColumn<T>[];
  rows: T[];
}

export function Table<T>({ columns, rows }: TableProps<T>) {
  return (
    <div className="sd-table">
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rIdx) => (
            <tr key={rIdx}>
              {columns.map((col) => (
                <td key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                  {col.render ? (
                    col.render(row)
                  ) : (
                    <span className={col.mono ? 'sd-mono' : undefined}>{String((row as Record<string, unknown>)[col.key] ?? '')}</span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
