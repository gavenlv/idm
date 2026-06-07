import type { ReactNode } from "react";

export interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="idm-empty">
      {icon && <div className="idm-empty__icon">{icon}</div>}
      <div style={{ fontWeight: 600, color: "var(--idm-text)" }}>{title}</div>
      {description && <div>{description}</div>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  );
}
