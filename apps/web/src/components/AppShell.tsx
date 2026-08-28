"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Permission } from "@fieldops/shared";
import { useSession } from "@/lib/session";
import { Button } from "@/components/ui";
import {
  CallIcon,
  ChatIcon,
  CloseIcon,
  CustomersIcon,
  InventoryIcon,
  LibraryIcon,
  MenuIcon,
  SearchIcon,
  ShieldIcon,
  SignOutIcon,
} from "@/components/icons";
import styles from "./AppShell.module.css";

type NavItem = {
  href: string;
  label: string;
  icon: (props: { className?: string }) => ReactNode;
  permission?: Permission;
  soon?: boolean;
};

const NAVIGATION: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Knowledge",
    items: [
      { href: "/knowledge", label: "Documents", icon: LibraryIcon },
      { href: "/search", label: "Retrieval", icon: SearchIcon },
      { href: "/chat", label: "Company AI", icon: ChatIcon },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/customers", label: "Customers", icon: CustomersIcon, soon: true },
      { href: "/inventory", label: "Inventory", icon: InventoryIcon, soon: true },
      { href: "/call", label: "Live call", icon: CallIcon, permission: "calls:assist", soon: true },
    ],
  },
  {
    label: "Administration",
    items: [{ href: "/audit", label: "Audit log", icon: ShieldIcon, permission: "audit:read", soon: true }],
  },
];

function initials(name: string): string {
  return name
    .split(" ")
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function AppShell({ children }: { children: ReactNode }) {
  const { session, signOut, can } = useSession();
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);

  // Navigating should dismiss the drawer, or a tap on a link leaves the reader
  // looking at the menu they just used.
  useEffect(() => setDrawerOpen(false), [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKey);
    // Move focus into the drawer so a keyboard user is not left tabbing through
    // the page behind it.
    closeButton.current?.focus();
    // The page behind must not scroll under an overlay.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  const current = NAVIGATION.flatMap((group) => group.items).find(
    (item) => item.href === pathname,
  );

  return (
    <div className={styles.shell}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      {drawerOpen && (
        <button
          className={styles.scrim}
          onClick={() => setDrawerOpen(false)}
          aria-label="Close navigation"
          tabIndex={-1}
        />
      )}

      <aside
        className={`${styles.sidebar} ${drawerOpen ? styles.sidebarOpen : ""}`}
        aria-label="Main"
      >
        <div className={styles.brand}>
          <span className={styles.mark} aria-hidden>
            FO
          </span>
          <span className={styles.brandName}>FieldOps</span>
          <button
            ref={closeButton}
            className={styles.menuButton}
            style={{ marginLeft: "auto" }}
            onClick={() => setDrawerOpen(false)}
            aria-label="Close navigation"
            hidden={!drawerOpen}
          >
            <CloseIcon width={20} height={20} />
          </button>
        </div>

        <nav className={styles.nav}>
          {NAVIGATION.map((group) => {
            const visible = group.items.filter(
              (item) => !item.permission || can(item.permission),
            );
            if (visible.length === 0) return null;

            return (
              <div className={styles.navGroup} key={group.label}>
                <p className={styles.navLabel}>{group.label}</p>
                {visible.map((item) => {
                  const active = pathname === item.href;
                  const Icon = item.icon;
                  const className = [
                    styles.navItem,
                    active && styles.navItemActive,
                    item.soon && styles.navItemDisabled,
                  ]
                    .filter(Boolean)
                    .join(" ");

                  // Unbuilt sections are shown but inert. Hiding them would
                  // make the product look smaller than it is; letting them
                  // navigate to nothing would be worse.
                  if (item.soon) {
                    return (
                      <span className={className} key={item.href} title={`${item.label} — not built yet`}>
                        <Icon className={styles.navIcon} />
                        <span className={styles.navText}>{item.label}</span>
                        <span className={styles.navSoon}>Soon</span>
                      </span>
                    );
                  }

                  return (
                    <Link
                      className={className}
                      key={item.href}
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                    >
                      <Icon className={styles.navIcon} />
                      {/* The label is a span so the icon rail can hide it.
                          `title` keeps it discoverable when it is hidden. */}
                      <span className={styles.navText}>{item.label}</span>
                      <span className="visually-hidden">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className={styles.account}>
          <span className={styles.avatar} aria-hidden>
            {initials(session?.user.fullName ?? "")}
          </span>
          <span className={styles.accountText}>
            <span className={styles.accountName}>{session?.user.fullName}</span>
            <span className={styles.accountRole}>{session?.user.role}</span>
          </span>
          <Button variant="ghost" size="small" onClick={signOut} aria-label="Sign out">
            <SignOutIcon />
            <span className={styles.navText}>Sign out</span>
          </Button>
        </div>
      </aside>

      <div>
        <header className={styles.header}>
          <button
            className={styles.menuButton}
            onClick={() => setDrawerOpen(true)}
            aria-label="Open navigation"
            aria-expanded={drawerOpen}
          >
            <MenuIcon />
          </button>
          <span className={styles.headerTitle}>{current?.label ?? "FieldOps Copilot"}</span>
        </header>

        <main className={styles.main} id="main">
          <div className={styles.page}>{children}</div>
        </main>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className={styles.pageHeader}>
      <div>
        <h1 className={styles.pageTitle}>{title}</h1>
        {subtitle && <p className={styles.pageSubtitle}>{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}
