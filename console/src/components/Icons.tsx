// Inline SVG glyphs, 16px grid, 1.5px stroke. Drawn for Elix, no icon font.
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement> & { size?: number };

function S({ size = 16, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...rest}>
      {children}
    </svg>
  );
}

export const IconToday = (p: P) => (
  <S {...p}>
    <rect x="2.25" y="3" width="11.5" height="10.75" rx="1.5" />
    <path d="M2.25 6.25h11.5M5.5 1.75v2.5M10.5 1.75v2.5" />
    <rect x="5" y="8.5" width="2.5" height="2.5" rx="0.5" fill="currentColor" stroke="none" />
  </S>
);
export const IconInbox = (p: P) => (
  <S {...p}>
    <path d="M2 9.25 3.6 3.6A1.5 1.5 0 0 1 5 2.5h6a1.5 1.5 0 0 1 1.4 1.1L14 9.25V12a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12V9.25Z" />
    <path d="M2 9.25h3.25l.9 1.5h3.7l.9-1.5H14" />
  </S>
);
export const IconActivity = (p: P) => (
  <S {...p}>
    <path d="M2.5 4h11M2.5 8h11M2.5 12h7" />
  </S>
);
export const IconReconcile = (p: P) => (
  <S {...p}>
    <rect x="2" y="2.5" width="5" height="11" rx="1" />
    <rect x="9" y="2.5" width="5" height="11" rx="1" />
    <path d="M3.75 5.5h1.5M3.75 8h1.5M10.75 5.5h1.5M10.75 8h1.5" />
  </S>
);
export const IconLedger = (p: P) => (
  <S {...p}>
    <path d="M3 2.5h8.5A1.5 1.5 0 0 1 13 4v9.5H4.5A1.5 1.5 0 0 1 3 12V2.5Z" />
    <path d="M8 2.5v11M5 5.5h1.25M9.75 5.5h1.5M5 8h1.25M9.75 8h1.5" />
  </S>
);
export const IconPolicy = (p: P) => (
  <S {...p}>
    <path d="M8 1.75 13 3.5v4.25c0 3-2.2 5.25-5 6.5-2.8-1.25-5-3.5-5-6.5V3.5l5-1.75Z" />
    <path d="m5.75 8 1.6 1.6L10.5 6.4" />
  </S>
);
export const IconAgent = (p: P) => (
  <S {...p}>
    <rect x="5.5" y="1.75" width="5" height="12.5" rx="2.5" />
    <path d="M5.5 9h5" />
  </S>
);
export const IconAudit = (p: P) => (
  <S {...p}>
    <rect x="2" y="5.5" width="4.5" height="5" rx="2.25" />
    <rect x="9.5" y="5.5" width="4.5" height="5" rx="2.25" />
    <path d="M6.5 8h3" />
  </S>
);
export const IconProof = (p: P) => (
  <S {...p}>
    <path d="M2.5 13.5h11" />
    <path d="M4 13.5V9M7 13.5V5.5M10 13.5V7.5M13 13.5V3" />
  </S>
);
export const IconScenario = (p: P) => (
  <S {...p}>
    <path d="M5 3.25v9.5L12.5 8 5 3.25Z" />
  </S>
);
export const IconSearch = (p: P) => (
  <S {...p}>
    <circle cx="7" cy="7" r="4.25" />
    <path d="m10.25 10.25 3.25 3.25" />
  </S>
);
export const IconChevron = (p: P) => (
  <S {...p}>
    <path d="m4.5 6.25 3.5 3.5 3.5-3.5" />
  </S>
);
export const IconClose = (p: P) => (
  <S {...p}>
    <path d="m4 4 8 8M12 4l-8 8" />
  </S>
);
export const IconLock = (p: P) => (
  <S {...p}>
    <rect x="3.25" y="7" width="9.5" height="6.75" rx="1.25" />
    <path d="M5.25 7V5a2.75 2.75 0 0 1 5.5 0v2" />
  </S>
);
export const IconCheck = (p: P) => (
  <S {...p}>
    <path d="m3.25 8.25 3 3 6.5-6.5" />
  </S>
);
export const IconCopy = (p: P) => (
  <S {...p}>
    <rect x="5.25" y="5.25" width="8" height="8.5" rx="1.25" />
    <path d="M10.75 5.25V3.5a1.25 1.25 0 0 0-1.25-1.25h-5.5A1.25 1.25 0 0 0 2.75 3.5v6.25A1.25 1.25 0 0 0 4 11h1.25" />
  </S>
);
export const IconLink = (p: P) => (
  <S {...p}>
    <path d="M6.75 9.25a2.75 2.75 0 0 0 3.9 0l2-2a2.75 2.75 0 0 0-3.9-3.9l-.6.6" />
    <path d="M9.25 6.75a2.75 2.75 0 0 0-3.9 0l-2 2a2.75 2.75 0 0 0 3.9 3.9l.6-.6" />
  </S>
);
export const IconExpand = (p: P) => (
  <S {...p}>
    <path d="M9.5 2.5h4v4M13.5 2.5 9 7M6.5 13.5h-4v-4M2.5 13.5 7 9" />
  </S>
);
export const IconSun = (p: P) => (
  <S {...p}>
    <circle cx="8" cy="8" r="3" />
    <path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.05 1.05M11.55 11.55l1.05 1.05M3.4 12.6l1.05-1.05M11.55 4.45l1.05-1.05" />
  </S>
);
export const IconMoon = (p: P) => (
  <S {...p}>
    <path d="M13.25 9.6A5.5 5.5 0 0 1 6.4 2.75a5.5 5.5 0 1 0 6.85 6.85Z" />
  </S>
);
export const IconMonitor = (p: P) => (
  <S {...p}>
    <rect x="2" y="2.75" width="12" height="8.5" rx="1.25" />
    <path d="M6 13.75h4M8 11.25v2.5" />
  </S>
);
export const IconChain = (p: P) => (
  <S {...p}>
    <rect x="1.75" y="5.75" width="6" height="4.5" rx="2.25" />
    <rect x="8.25" y="5.75" width="6" height="4.5" rx="2.25" />
  </S>
);
export const IconSync = (p: P) => (
  <S {...p}>
    <path d="M13 6.5A5 5 0 0 0 3.6 4.9M3 9.5a5 5 0 0 0 9.4 1.6" />
    <path d="M3.25 2.5v2.75H6M12.75 13.5v-2.75H10" />
  </S>
);
export const IconQuestion = (p: P) => (
  <S {...p}>
    <path d="M6 6a2 2 0 1 1 2.75 1.85c-.45.2-.75.6-.75 1.1V9.5" />
    <circle cx="8" cy="12" r="0.4" fill="currentColor" />
  </S>
);
export const IconPlay = IconScenario;
export const IconMenu = (p: P) => (
  <S {...p}>
    <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
  </S>
);
export const IconKeyboard = (p: P) => (
  <S {...p}>
    <rect x="1.75" y="4" width="12.5" height="8" rx="1.25" />
    <path d="M4.5 6.75h.01M7 6.75h.01M9.5 6.75h.01M12 6.75h.01M5 9.5h6" />
  </S>
);
export const IconRefresh = IconSync;
export const IconWarn = (p: P) => (
  <S {...p}>
    <path d="M8 2 14.5 13.5h-13L8 2Z" />
    <path d="M8 6.5v3" />
    <circle cx="8" cy="11.5" r="0.4" fill="currentColor" />
  </S>
);

/** The Elix mark: an elixir pip, a capsule two thirds full. */
export function ElixMark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size * 0.56} height={size} viewBox="0 0 10 18" aria-hidden="true">
      <defs>
        <clipPath id="elix-mark-clip">
          <rect x="0.75" y="0.75" width="8.5" height="16.5" rx="4.25" />
        </clipPath>
      </defs>
      <rect x="0.75" y="0.75" width="8.5" height="16.5" rx="4.25" fill="var(--pip-soft)" stroke="var(--pip)" strokeWidth="1.5" />
      <rect x="0" y="6.5" width="10" height="12" fill="var(--pip)" clipPath="url(#elix-mark-clip)" />
    </svg>
  );
}
