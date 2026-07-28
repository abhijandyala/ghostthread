export function DashboardIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="1" y="1" width="6" height="6" rx="1" />
      <rect x="9" y="1" width="6" height="4" rx="1" />
      <rect x="1" y="9" width="6" height="4" rx="1" />
      <rect x="9" y="7" width="6" height="6" rx="1" />
    </svg>
  );
}

export function InboxIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="1" y="3" width="14" height="10" rx="1.5" />
      <path d="M1 10h4l1 2h4l1-2h4" />
    </svg>
  );
}

export function MemoryIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="6.5" />
      <circle cx="8" cy="8" r="2" />
      <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" />
    </svg>
  );
}

export function SettingsIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="2.5" />
      <path d="M8 1v2M8 13v2M1 8h2M13 8h2M2.9 2.9l1.4 1.4M11.7 11.7l1.4 1.4M2.9 13.1l1.4-1.4M11.7 4.3l1.4-1.4" />
    </svg>
  );
}

export function ProjectIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 3.5A1.5 1.5 0 013.5 2h3l1.5 2h4.5A1.5 1.5 0 0114 5.5v7a1.5 1.5 0 01-1.5 1.5h-9A1.5 1.5 0 012 12.5v-9z" />
    </svg>
  );
}

export function SlackIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zm-1.27 0a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.163 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.163 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.163 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zm0-1.27a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.315A2.528 2.528 0 0 1 24 15.163a2.528 2.528 0 0 1-2.522 2.523h-6.315z" />
    </svg>
  );
}

export function GmailIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor">
      <path d="M1.5 4v8.5h3V7l3.5 3 3.5-3v5.5h3V4L8 9.5 1.5 4z" />
      <path d="M1.5 4L8 8.5 14.5 4v-.5A1.5 1.5 0 0013 2H3a1.5 1.5 0 00-1.5 1.5V4z" />
    </svg>
  );
}

export function LinearIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 100 100" fill="currentColor">
      <path d="M2.4 62.7 37.3 97.6c-1-.4-1.9-.8-2.8-1.3L2.4 62.7Zm-1.6-7.4L56.7 99.2a43.6 43.6 0 0 0 5.2-1.3L1.1 39.1c-.5 1.7-.9 3.4-1.2 5.2l1 11ZM1 47.3l57.6 51.5a41 41 0 0 0 6.2-3.7L4.2 35.5A49.5 49.5 0 0 0 1 47.3ZM7.3 30.3l60.1 53.7A36.4 36.4 0 0 0 74 79L12 24.3a46.8 46.8 0 0 0-4.7 6ZM16.5 20.4l67.1 59.9a33.2 33.2 0 0 0 4.5-6.7L23 14.8a43.3 43.3 0 0 0-6.5 5.6ZM28.4 11.6 89.3 66a30 30 0 0 0 2.5-5.6L35.2 8A40 40 0 0 0 28.4 11.6ZM40.5 6.3l50.1 44.7A27 27 0 0 0 91.5 39L52.6 4.3a37.6 37.6 0 0 0-12.1 2ZM63 3.6 89.5 27.3a25 25 0 0 0-8.2-14.2A25 25 0 0 0 67.1 3 38 38 0 0 0 63 3.6Z" />
    </svg>
  );
}

export function GitHubIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 .2A8 8 0 005.5 15.8c.4.1.5-.2.5-.4v-1.4c-2.2.5-2.7-1-2.7-1a2.1 2.1 0 00-.9-1.2c-.7-.5.1-.5.1-.5a1.7 1.7 0 011.2.8 1.7 1.7 0 002.3.7 1.7 1.7 0 01.5-1.1c-1.8-.2-3.6-.9-3.6-4a3.1 3.1 0 01.8-2.2 2.9 2.9 0 01.1-2.1s.7-.2 2.2.8a7.6 7.6 0 014 0c1.5-1 2.2-.8 2.2-.8a2.9 2.9 0 01.1 2.1 3.1 3.1 0 01.8 2.2c0 3.1-1.9 3.8-3.6 4a1.9 1.9 0 01.5 1.5v2.2c0 .2.1.5.5.4A8 8 0 008 .2z" />
    </svg>
  );
}

export function SearchIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" strokeLinecap="round" />
    </svg>
  );
}

export function PulseIcon({ className = "w-2 h-2", color = "#069494" }: { className?: string; color?: string }) {
  return (
    <span className={`relative inline-flex ${className}`}>
      <span className="absolute inline-flex h-full w-full rounded-full opacity-40" style={{ backgroundColor: color, animation: "ping 2s cubic-bezier(0,0,0.2,1) infinite" }} />
      <span className="relative inline-flex rounded-full h-full w-full" style={{ backgroundColor: color }} />
    </span>
  );
}

export function BellIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
      <path d="M8 2a4 4 0 00-4 4v2.5L2.5 11v1h11v-1L12 8.5V6a4 4 0 00-4-4z" />
      <path d="M6.5 14a1.5 1.5 0 003 0" strokeLinecap="round" />
    </svg>
  );
}

export function SignOutIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 14H3.5A1.5 1.5 0 012 12.5v-9A1.5 1.5 0 013.5 2H6" />
      <path d="M10.5 11.5L14 8l-3.5-3.5M14 8H6" />
    </svg>
  );
}

export function PlusIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

export function ConnectorIcon({ id, className = "w-4 h-4" }: { id: string; className?: string }) {
  switch (id) {
    case "slack": return <SlackIcon className={className} />;
    case "gmail": return <GmailIcon className={className} />;
    case "linear": return <LinearIcon className={className} />;
    case "github": return <GitHubIcon className={className} />;
    default:
      return (
        <span
          className={`${className} rounded bg-border text-dim inline-flex items-center justify-center font-bold uppercase`}
          style={{ fontSize: "8px" }}
        >
          {id.charAt(0)}
        </span>
      );
  }
}
