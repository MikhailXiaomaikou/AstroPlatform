import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Rendered instead of children when the boundary catches an error. */
  fallback?: (error: Error, retry: () => void) => ReactNode;
  /** Optional label surfaced in the default fallback for debugging. */
  label?: string;
}

interface State {
  error: Error | null;
}

/**
 * H20: lightweight error boundary used to wrap <Suspense> around lazy-loaded
 * chunks (e.g. PlotBuilder).  Without it, a failed chunk fetch unwinds all
 * the way to App and blanks the page.  The fallback offers a retry button
 * that resets boundary state so a subsequent import() attempt can succeed.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Leave room for future telemetry; keep it logged for now.
    console.error("ErrorBoundary caught", this.props.label ?? "", error, info);
  }

  retry = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.retry);
      return (
        <div
          role="alert"
          style={{
            padding: 16,
            border: "1px solid #ddd",
            borderRadius: 8,
            background: "#fff8f8",
            margin: 12,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            Failed to load {this.props.label ?? "this section"}.
          </div>
          <div style={{ fontSize: 13, color: "#555", marginBottom: 10 }}>
            {error.message}
          </div>
          <button className="btn-secondary btn-small" onClick={this.retry}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
