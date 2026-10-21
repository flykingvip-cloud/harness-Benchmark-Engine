import { useEffect, useState } from "react";
import { getJson, routes, type ResultDetail, type ResultIndex } from "./api";
import { defaultResultId, resultDetailPath, type ListedResult } from "./results";
import { describeFailure, useLoad } from "./useLoad";

interface ResultList {
  results: ResultIndex[];
}

export function useResultDetail(resultId: string | null) {
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [detailError, setDetailError] = useState<{ message?: string; code: string } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    if (!resultId) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    getJson<ResultDetail>(resultDetailPath(resultId)).then(
      (data) => {
        if (!cancelled) {
          setDetail(data);
          setDetailLoading(false);
        }
      },
      (reason: unknown) => {
        if (!cancelled) {
          const failure = describeFailure(reason);
          setDetail(null);
          setDetailError(failure.code === "request_failed" && !failure.message ? { code: "result_read_failed" } : failure);
          setDetailLoading(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [resultId]);

  return { detail, detailError, detailLoading };
}

export function useFirstResult() {
  const index = useLoad<ResultList>(routes.results);
  const results: ListedResult[] = index.data?.results ?? [];
  const { detail, detailError, detailLoading } = useResultDetail(defaultResultId(results));
  return {
    detail,
    loading: index.loading || detailLoading,
    error: index.error,
    errorCode: index.errorCode,
    detailError,
    empty: Boolean(index.data && results.length === 0),
  };
}
