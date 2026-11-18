import { useClerk, useUser } from "@clerk/react";
import { useEffect, useRef, useState } from "react";

export function UserMenu() {
  const { user } = useUser();
  const clerk = useClerk();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const name = user?.fullName || user?.primaryEmailAddress?.emailAddress || "Account";
  const initial = name.trim().charAt(0).toUpperCase() || "?";
  return (
    <div className="usermenu" ref={root}>
      <button type="button" className="chip avatar" aria-haspopup="menu" aria-expanded={open} aria-label="Account menu" onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true">{initial}</span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-id">
            <strong>{user?.fullName ?? "Signed in"}</strong>
            <span className="dim">{user?.primaryEmailAddress?.emailAddress}</span>
          </div>
          <button type="button" role="menuitem" className="menu-item" onClick={() => clerk.signOut({ redirectUrl: "/sign-in" })}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
