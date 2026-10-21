import { useEffect, useState } from "react";
import { ApiError, getJson } from "./api";

export interface LoadState<T> {
  loading: boolean;
  data?: T;
  /** English message from the API, when the response included one. */
  error?: string;
  /** Stable error code. Client-only codes never leave the browser. */
  errorCode?: string;
}

export interface FailureInfo {
  message?: string;
  code: string;
}

export function describeFailure(reason: unknown): FailureInfo {
  if (reason instanceof ApiError) {
    return { message: reason.message, code: reason.code };
  }
  if (reason instanceof TypeError) {
    return { code: "network" };
  }
  return { code: "request_failed" };
}

export function useLoad<T>(path: string): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({ loading: true });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true });
    getJson<T>(path.split("?")[0] ?? path).then(
      (data) => {
        if (!cancelled) {
          setState({ loading: false, data });
        }
      },
      (reason: unknown) => {
        if (!cancelled) {
          const failure = describeFailure(reason);
          setState({ loading: false, error: failure.message, errorCode: failure.code });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [path]);

  return state;
}
