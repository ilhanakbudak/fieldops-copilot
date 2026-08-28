/**
 * Inline SVG rather than an icon package.
 *
 * Seven icons do not justify a dependency, a tree-shaking configuration and a
 * licence to check — and inlined, they cost no extra request and inherit
 * `currentColor`, so an active nav item colours its icon without a second rule.
 */
import type { SVGProps } from "react";

const base: SVGProps<SVGSVGElement> = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export function LibraryIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H9v16H5.5A1.5 1.5 0 0 1 4 18.5z" />
      <path d="M9 4h5.5A1.5 1.5 0 0 1 16 5.5v13a1.5 1.5 0 0 1-1.5 1.5H9" />
      <path d="m17.5 6.2 2.1 12.1" />
    </svg>
  );
}

export function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </svg>
  );
}

export function ChatIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M20 12a7.5 7.5 0 0 1-7.5 7.5H8L4 22v-4.2A7.5 7.5 0 0 1 12.5 4.5 7.5 7.5 0 0 1 20 12" />
    </svg>
  );
}

export function CustomersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.25" />
      <path d="M3.5 19.5a5.5 5.5 0 0 1 11 0" />
      <path d="M16 5.4a3.25 3.25 0 0 1 0 5.2M17.5 14.6a5.5 5.5 0 0 1 3 4.9" />
    </svg>
  );
}

export function InventoryIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z" />
      <path d="M3.5 7.5 12 12l8.5-4.5M12 12v9" />
    </svg>
  );
}

export function CallIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M6.5 3.5h2l1.5 4-2 1.4a11 11 0 0 0 5.1 5.1l1.4-2 4 1.5v2a2 2 0 0 1-2.2 2A15.5 15.5 0 0 1 4.5 5.7a2 2 0 0 1 2-2.2" />
    </svg>
  );
}

export function ShieldIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3.5 19 6v5.6c0 4-2.8 7.3-7 8.9-4.2-1.6-7-4.9-7-8.9V6z" />
      <path d="m9.2 12 2 2 3.6-3.8" />
    </svg>
  );
}

export function MenuIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="22" height="22" {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function CloseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function UploadIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="16" height="16" {...props}>
      <path d="M12 16V5m0 0L7.5 9.5M12 5l4.5 4.5" />
      <path d="M4.5 16.5v1.8a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-1.8" />
    </svg>
  );
}

export function SignOutIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="16" height="16" {...props}>
      <path d="M15 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-2" />
      <path d="M10 12h10m0 0-3-3m3 3-3 3" />
    </svg>
  );
}

export function PlusIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="16" height="16" {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function TrashIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="14" height="14" {...props}>
      <path d="M4 7h16M10 7V5h4v2M6 7l1 13h10l1-13" />
    </svg>
  );
}

export function PencilIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="14" height="14" {...props}>
      <path d="M4 20h4L20 8l-4-4L4 16z" />
    </svg>
  );
}

export function CopyIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} width="14" height="14" {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a1 1 0 0 1 1-1h9" />
    </svg>
  );
}

export function UsersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </svg>
  );
}
