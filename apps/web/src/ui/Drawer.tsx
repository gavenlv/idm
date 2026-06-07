import type { ReactNode } from "react";
import { useEffect } from "react";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  width?: number;
  children: ReactNode;
}

export function Drawer({ open, onClose, title, width = 480, children }: DrawerProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    open && document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="idm-drawer__mask" onClick={onClose} />
      <aside className="idm-drawer" style={{ width }}>
        {title && <header className="idm-drawer__header">{title}</header>}
        <div className="idm-drawer__body">{children}</div>
      </aside>
    </>
  );
}
