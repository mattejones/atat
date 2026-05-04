export default function Footer() {
  return (
    <footer className="border-t border-bg-border bg-bg-surface mt-auto">
      <div className="max-w-6xl mx-auto px-6 h-11 flex items-center justify-between">
        <p className="text-xs text-text-muted">
          ATAT — Application Tracking &amp; Automation Tool
        </p>
        <a
          href="https://mej.xyz"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-text-muted hover:text-accent transition-colors"
        >
          mej.xyz ↗
        </a>
      </div>
    </footer>
  );
}
