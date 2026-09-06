import { useCallback, useEffect, useRef, useState } from "react";
import type { SafeApiError } from "@/shared/api/slice3";

interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

export function useRecoverableCursorPage<T>(
  loadPage: (cursor?: string) => Promise<CursorPage<T>>,
  getSafeError: (error: unknown) => SafeApiError,
) {
  const requestSequence = useRef(0);
  const latestLoadPage = useRef(loadPage);
  latestLoadPage.current = loadPage;
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");
  const [canRecover, setCanRecover] = useState(false);

  const request = useCallback(
    async (targetCursor?: string) => {
      const requestId = ++requestSequence.current;
      const requestLoadPage = loadPage;
      const isCurrentRequest = () =>
        requestId === requestSequence.current && requestLoadPage === latestLoadPage.current;
      setCursor(targetCursor);
      setLoading(true);
      setFeedback("");
      setCanRecover(false);
      try {
        const page = await loadPage(targetCursor);
        if (!isCurrentRequest()) return;
        setItems(page.items);
        setNextCursor(page.next_cursor);
      } catch (error) {
        if (!isCurrentRequest()) return;
        const safe = getSafeError(error);
        setItems([]);
        setNextCursor(null);
        setFeedback(safe.message);
        setCanRecover(safe.status === 422 && targetCursor !== undefined);
      } finally {
        if (isCurrentRequest()) setLoading(false);
      }
    },
    [getSafeError, loadPage],
  );

  useEffect(() => {
    requestSequence.current += 1;
    setItems([]);
    setCursor(undefined);
    setHistory([]);
    setNextCursor(null);
    setFeedback("");
    setCanRecover(false);
    void request(undefined);
    return () => {
      requestSequence.current += 1;
    };
  }, [request]);

  const refresh = useCallback(() => request(cursor), [cursor, request]);

  const next = useCallback(() => {
    if (!nextCursor || loading) return;
    setHistory((current) => [...current, cursor]);
    void request(nextCursor);
  }, [cursor, loading, nextCursor, request]);

  const previous = useCallback(() => {
    if (!history.length || loading) return;
    const copy = [...history];
    const previousCursor = copy.pop();
    setHistory(copy);
    void request(previousCursor);
  }, [history, loading, request]);

  const recoverToFirstPage = useCallback(() => {
    if (!canRecover || loading) return;
    setItems([]);
    setHistory([]);
    setNextCursor(null);
    setFeedback("");
    setCanRecover(false);
    void request(undefined);
  }, [canRecover, loading, request]);

  return {
    items,
    nextCursor,
    loading,
    feedback,
    canRecover,
    canGoBack: history.length > 0,
    refresh,
    next,
    previous,
    recoverToFirstPage,
  };
}

export function CursorRecoveryAction({
  visible,
  loading,
  onRecover,
}: {
  visible: boolean;
  loading: boolean;
  onRecover: () => void;
}) {
  if (!visible) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
      <p className="text-sm text-amber-900">当前分页请求无法继续。可保留筛选条件并从首页重新查询。</p>
      <button
        className="inline-flex min-h-10 items-center justify-center rounded-lg border border-amber-300 bg-white px-4 text-sm font-semibold text-amber-900 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={loading}
        onClick={onRecover}
        type="button"
      >
        返回首页重新查询
      </button>
    </div>
  );
}
