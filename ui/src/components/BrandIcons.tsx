// Full-color brand marks. Dark-mark brands (GitHub, Zendesk) are rendered light
// so they stay visible on the dark theme.

export function SlackBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24">
      <path fill="#E01E5A" d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" />
      <path fill="#36C5F0" d="M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z" />
      <path fill="#2EB67D" d="M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z" />
      <path fill="#ECB22E" d="M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.528 2.528 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" />
    </svg>
  );
}

export function GmailBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24">
      <path fill="#4285F4" d="M1.636 20.727h3.819V11.454L0 7.364v11.727c0 .904.732 1.636 1.636 1.636z" />
      <path fill="#34A853" d="M18.545 20.727h3.819c.904 0 1.636-.732 1.636-1.636V7.364l-5.455 4.09z" />
      <path fill="#FBBC04" d="M18.545 4.364v7.09L24 7.365V5.182c0-2.023-2.309-3.178-3.927-1.964z" />
      <path fill="#EA4335" d="M5.455 11.455v-7.091L12 9.273l6.545-4.91v7.091L12 16.364z" />
      <path fill="#C5221F" d="M0 5.182v2.182l5.455 4.09V4.365L3.927 3.218C2.309 2.004 0 3.159 0 5.182z" />
    </svg>
  );
}

export function LinearBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 100 100" fill="#5E6AD2">
      <path d="M2.4 62.7 37.3 97.6c-1-.4-1.9-.8-2.8-1.3L2.4 62.7Zm-1.6-7.4L56.7 99.2a43.6 43.6 0 0 0 5.2-1.3L1.1 39.1c-.5 1.7-.9 3.4-1.2 5.2l1 11ZM1 47.3l57.6 51.5a41 41 0 0 0 6.2-3.7L4.2 35.5A49.5 49.5 0 0 0 1 47.3ZM7.3 30.3l60.1 53.7A36.4 36.4 0 0 0 74 79L12 24.3a46.8 46.8 0 0 0-4.7 6ZM16.5 20.4l67.1 59.9a33.2 33.2 0 0 0 4.5-6.7L23 14.8a43.3 43.3 0 0 0-6.5 5.6ZM28.4 11.6 89.3 66a30 30 0 0 0 2.5-5.6L35.2 8A40 40 0 0 0 28.4 11.6ZM40.5 6.3l50.1 44.7A27 27 0 0 0 91.5 39L52.6 4.3a37.6 37.6 0 0 0-12.1 2ZM63 3.6 89.5 27.3a25 25 0 0 0-8.2-14.2A25 25 0 0 0 67.1 3 38 38 0 0 0 63 3.6Z" />
    </svg>
  );
}

export function GitHubBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="#E8EDF4">
      <path d="M8 .2A8 8 0 005.5 15.8c.4.1.5-.2.5-.4v-1.4c-2.2.5-2.7-1-2.7-1a2.1 2.1 0 00-.9-1.2c-.7-.5.1-.5.1-.5a1.7 1.7 0 011.2.8 1.7 1.7 0 002.3.7 1.7 1.7 0 01.5-1.1c-1.8-.2-3.6-.9-3.6-4a3.1 3.1 0 01.8-2.2 2.9 2.9 0 01.1-2.1s.7-.2 2.2.8a7.6 7.6 0 014 0c1.5-1 2.2-.8 2.2-.8a2.9 2.9 0 01.1 2.1 3.1 3.1 0 01.8 2.2c0 3.1-1.9 3.8-3.6 4a1.9 1.9 0 01.5 1.5v2.2c0 .2.1.5.5.4A8 8 0 008 .2z" />
    </svg>
  );
}

export function DiscordBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="#5865F2">
      <path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z" />
    </svg>
  );
}

export function TeamsBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <circle cx="17.5" cy="6.5" r="2.6" fill="#7B83EB" />
      <path d="M14.6 10.2h6.9a1 1 0 011 1v4.8a4.6 4.6 0 01-4.6 4.6c-1.32 0-2.5-.55-3.34-1.44l.04-8.96z" fill="#7B83EB" />
      <rect x="1" y="5.8" width="13.2" height="13.2" rx="1.6" fill="#4B53BC" />
      <path d="M4.6 9.6h6M7.6 9.6v6.4" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

export function OutlookBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none">
      <path d="M13.8 4.8H22a1 1 0 011 1v12.4a1 1 0 01-1 1h-8.2V4.8z" fill="#1B9DE2" />
      <path d="M13.8 9.2l4.6 3.1L23 9.2" stroke="#fff" strokeWidth="1.3" />
      <rect x="1" y="4" width="12.8" height="16" rx="1.6" fill="#0078D4" />
      <ellipse cx="7.4" cy="12" rx="3.3" ry="3.9" stroke="#fff" strokeWidth="1.7" />
    </svg>
  );
}

export function JiraBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24">
      <path fill="#2684FF" d="M23.013 0H11.455a5.215 5.215 0 005.215 5.215h2.129v2.057A5.215 5.215 0 0024 12.483V1.005A1.001 1.001 0 0023.013 0z" />
      <path fill="#2684FF" d="M17.294 5.757H5.736a5.215 5.215 0 005.215 5.214h2.129v2.058a5.218 5.218 0 005.215 5.214V6.758a1.001 1.001 0 00-1.001-1.001z" opacity="0.8" />
      <path fill="#2684FF" d="M11.571 11.513H0a5.218 5.218 0 005.232 5.215h2.13v2.057A5.215 5.215 0 0012.575 24V12.518a1.005 1.005 0 00-1.004-1.005z" opacity="0.6" />
    </svg>
  );
}

export function ZendeskBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="#E8EDF4">
      <path d="M12.914 2.904V16.29L24 2.905H12.914zM0 2.906C0 5.966 2.483 8.45 5.543 8.45s5.542-2.484 5.543-5.544H0zm11.086 4.807L0 21.096h11.086V7.713zm7.37 7.84c-3.063 0-5.542 2.48-5.542 5.543H24c0-3.06-2.48-5.543-5.543-5.543z" />
    </svg>
  );
}

export function IntercomBrand({ className = "w-5 h-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="#3290E8">
      <path d="M21 0H3C1.343 0 0 1.343 0 3v18c0 1.658 1.343 3 3 3h18c1.658 0 3-1.342 3-3V3c0-1.657-1.342-3-3-3zm-5.801 4.399c0-.44.36-.8.802-.8.44 0 .8.36.8.8v10.688c0 .442-.36.801-.8.801-.443 0-.802-.359-.802-.801V4.399zM11.2 3.994c0-.44.357-.799.8-.799s.8.359.8.799v11.602c0 .44-.357.8-.8.8s-.8-.36-.8-.8V3.994zm-4 .405c0-.44.359-.8.799-.8.443 0 .802.36.802.8v10.688c0 .442-.36.801-.802.801-.44 0-.799-.359-.799-.801V4.399zM3.199 6c0-.442.36-.8.802-.8.44 0 .799.358.799.8v7.195c0 .441-.359.8-.799.8-.443 0-.802-.36-.802-.8V6zM20.52 18.202c-.123.105-3.086 2.593-8.52 2.593-5.433 0-8.397-2.486-8.521-2.593-.335-.288-.375-.792-.086-1.128.285-.334.79-.375 1.125-.09.047.041 2.693 2.211 7.481 2.211 4.848 0 7.456-2.186 7.479-2.207.334-.289.839-.25 1.128.086.289.336.25.84-.086 1.128zm.281-5.007c0 .441-.36.8-.801.8-.441 0-.801-.36-.801-.8V6c0-.442.361-.8.801-.8.441 0 .801.357.801.8v7.195z" />
    </svg>
  );
}

export function BrandIcon({ id, className = "w-5 h-5" }: { id: string; className?: string }) {
  switch (id) {
    case "slack": return <SlackBrand className={className} />;
    case "gmail": return <GmailBrand className={className} />;
    case "linear": return <LinearBrand className={className} />;
    case "github": return <GitHubBrand className={className} />;
    case "discord": return <DiscordBrand className={className} />;
    case "teams": return <TeamsBrand className={className} />;
    case "outlook": return <OutlookBrand className={className} />;
    case "jira": return <JiraBrand className={className} />;
    case "zendesk": return <ZendeskBrand className={className} />;
    case "intercom": return <IntercomBrand className={className} />;
    default:
      return (
        <span className={`${className} rounded bg-border text-dim inline-flex items-center justify-center font-bold uppercase`} style={{ fontSize: "9px" }}>
          {id.charAt(0)}
        </span>
      );
  }
}
