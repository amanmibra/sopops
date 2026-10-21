// The mark: a check inside a loop, a call that passed and an agent that keeps improving.
export function Logo({ className = "size-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" fill="none">
      <path d="M27 16A11 11 0 1 1 23.8 8.2" className="stroke-primary" strokeWidth="3" strokeLinecap="round" />
      <path d="M18.8 8.2h5V3.2" className="stroke-primary" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11 16.5l3.5 3.5 6.5-7" className="stroke-primary" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
