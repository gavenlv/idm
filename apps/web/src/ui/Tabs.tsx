import type { ReactNode } from "react";
import clsx from "clsx";

export interface TabItem<T extends string> {
  key: T;
  label: ReactNode;
  count?: number | string;
  color?: string;
}

export interface TabsProps<T extends string> {
  value: T;
  onChange: (key: T) => void;
  items: TabItem<T>[];
  className?: string;
}

export function Tabs<T extends string>({ value, onChange, items, className }: TabsProps<T>) {
  return (
    <div className={clsx("idm-tabs", className)}>
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          className={clsx("idm-tab", value === it.key && "idm-tab--active")}
          onClick={() => onChange(it.key)}
        >
          {it.label}
          {it.count !== undefined && <span className="idm-tab__count">{it.count}</span>}
        </button>
      ))}
    </div>
  );
}
