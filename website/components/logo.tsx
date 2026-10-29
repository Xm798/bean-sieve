import type { SVGProps } from 'react';

export function Logo(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path d="M2.5 7.5h19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path
        d="M4 7.5a8 8 0 0 0 16 0"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="9" cy="10.5" r="1" fill="currentColor" />
      <circle cx="12" cy="10.5" r="1" fill="currentColor" />
      <circle cx="15" cy="10.5" r="1" fill="currentColor" />
      <circle cx="10.5" cy="13" r="1" fill="currentColor" />
      <circle cx="13.5" cy="13" r="1" fill="currentColor" />
      <ellipse cx="12" cy="20.5" rx="2" ry="1.4" fill="currentColor" opacity="0.6" />
    </svg>
  );
}
