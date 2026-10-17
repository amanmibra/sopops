// The mark: a clipboard (the SOP) whose sheet is a server rack (what serves it).
export function Logo({ className = "size-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" fill="none">
      <rect x="5" y="5" width="22" height="25" rx="3.5" className="fill-primary" />
      <rect x="11" y="2.5" width="10" height="5.5" rx="1.6" className="fill-card stroke-primary" strokeWidth="1.6" />
      <rect x="8.5" y="11" width="15" height="5.5" rx="1.4" className="fill-card" />
      <rect x="8.5" y="19" width="15" height="5.5" rx="1.4" className="fill-card" />
      <circle cx="11.3" cy="13.75" r="1" className="fill-primary" />
      <circle cx="11.3" cy="21.75" r="1" className="fill-primary" />
      <path d="M14.5 13.75h6.5M14.5 21.75h6.5" className="stroke-primary" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
