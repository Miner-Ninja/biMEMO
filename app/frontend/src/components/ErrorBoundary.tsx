import { Component, type ReactNode, type ErrorInfo } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import i18n from "@/i18n";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Error boundary to catch React errors
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, State> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      return (
        <div className="flex min-h-[400px] flex-col items-center justify-center gap-4 p-8 text-center">
          <AlertTriangle className="h-12 w-12 text-destructive" />
          <div className="space-y-2">
            <h2 className="text-xl font-semibold">
              {i18n.t("layout.somethingWentWrong")}
            </h2>
            <p className="text-sm text-muted-foreground max-w-md">
              {this.state.error?.message || i18n.t("layout.unexpectedError")}
            </p>
          </div>
          <Button onClick={this.handleReset} variant="outline" className="gap-2">
            <RefreshCw className="h-4 w-4" />
            {i18n.t("layout.tryAgain")}
          </Button>
        </div>
      );
    }

    return this.props.children;
  }
}
