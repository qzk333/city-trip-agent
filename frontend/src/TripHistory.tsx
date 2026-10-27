import { useEffect, useRef, useState } from "react";
import { History, LoaderCircle, Search, Trash2, X } from "lucide-react";
import { api } from "./api";

interface Summary {
  id: string;
  city: string;
  start_date: string;
  end_date: string;
  days: number;
  current_version: number;
  confirmed_at: string;
  activity_count: number;
}
interface HistoryPage {
  items: Summary[];
  total: number;
}
const pageSize = 8;

export default function TripHistory({
  onClose,
  onOpen,
  onDeleted,
}: {
  onClose: () => void;
  onOpen: (id: string) => Promise<void>;
  onDeleted: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState<HistoryPage>({ items: [], total: 0 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const mutation = useRef(false);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLElement>("button")?.focus();
    function keyboard(event: KeyboardEvent) {
      if (event.key === "Escape" && !mutation.current) close.current();
      if (event.key !== "Tab") return;
      const elements = Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), [tabindex='0']",
        ) || [],
      );
      if (!elements.length) {
        event.preventDefault();
        dialog.current?.focus();
        return;
      }
      const first = elements[0],
        last = elements[elements.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          !dialog.current?.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          !dialog.current?.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", keyboard);
    return () => {
      document.removeEventListener("keydown", keyboard);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setDeleting(null);
    void api<HistoryPage>(
      `/trips?q=${encodeURIComponent(filter)}&offset=${offset}&limit=${pageSize}`,
      undefined,
      undefined,
      { signal: controller.signal },
    )
      .then((result) => {
        if (offset > 0 && result.items.length === 0) {
          setOffset(Math.max(0, offset - pageSize));
          return;
        }
        setPage(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "历史行程加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [filter, offset, revision]);

  async function open(id: string) {
    setBusy(true);
    mutation.current = true;
    setError("");
    try {
      await onOpen(id);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "打开失败，请刷新列表重试");
    } finally {
      setBusy(false);
      mutation.current = false;
    }
  }
  async function remove(item: Summary) {
    setBusy(true);
    mutation.current = true;
    setError("");
    try {
      await api(
        `/trips/${item.id}`,
        { base_version: item.current_version },
        undefined,
        { method: "DELETE" },
      );
      onDeleted(item.id);
      setDeleting(null);
      setRevision((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
      mutation.current = false;
    }
  }

  return (
    <div
      className="modal-backdrop"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <section
        ref={dialog}
        tabIndex={-1}
        className="history-modal trip-history"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trip-history-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title">
          <h2 id="trip-history-title">
            <History size={20} /> 历史行程
          </h2>
          <button aria-label="关闭历史行程" disabled={busy} onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p>只展示你已确认保存的行程，未保存的草案不会出现在这里。</p>
        <form
          className="history-search"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setFilter(query.trim());
            setRevision((v) => v + 1);
          }}
        >
          <input
            aria-label="按城市查找历史行程"
            placeholder="输入城市，例如长沙"
            value={query}
            maxLength={100}
            disabled={busy}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="outline-button" disabled={busy || loading}>
            <Search size={15} />
            查找
          </button>
        </form>
        {error && (
          <div className="history-error" role="alert">
            {error}
            <button
              disabled={busy || loading}
              onClick={() => setRevision((v) => v + 1)}
            >
              刷新列表
            </button>
          </div>
        )}
        {loading ? (
          <p role="status">
            <LoaderCircle className="spin" size={16} /> 正在读取历史行程…
          </p>
        ) : (
          <>
            {!page.items.length && !error && (
              <p className="history-empty">
                {filter
                  ? "没有找到这个城市的已保存行程。"
                  : "还没有已保存的行程。确认一份行程后，就能在这里找到它。"}
              </p>
            )}
            <div className="history-list">
              {page.items.map((item) => (
                <article
                  className="trip-history-item"
                  data-trip-id={item.id}
                  key={item.id}
                >
                  <div>
                    <h3>
                      {item.city} · {item.days} 天
                    </h3>
                    <p>
                      {item.start_date} 至 {item.end_date}
                    </p>
                    <small>
                      {item.activity_count} 项活动 · v{item.current_version} ·{" "}
                      {new Date(item.confirmed_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                      })}{" "}
                      保存
                    </small>
                  </div>
                  <div className="history-item-actions">
                    <button
                      className="outline-button"
                      disabled={busy}
                      onClick={() => void open(item.id)}
                    >
                      打开行程
                    </button>
                    <button
                      className="text-button"
                      aria-label={`删除${item.city}行程`}
                      disabled={busy}
                      onClick={() => setDeleting(item.id)}
                    >
                      <Trash2 size={15} />
                      删除
                    </button>
                  </div>
                  {deleting === item.id && (
                    <div
                      className="history-delete-confirm"
                      role="group"
                      aria-label="确认删除行程"
                    >
                      <p>
                        将永久删除这份行程的全部版本、任务与预览记录，无法撤销。
                      </p>
                      <button
                        className="outline-button"
                        disabled={busy}
                        onClick={() => setDeleting(null)}
                      >
                        保留行程
                      </button>
                      <button
                        className="text-button danger"
                        disabled={busy}
                        onClick={() => void remove(item)}
                      >
                        {busy ? "正在删除…" : "确认永久删除"}
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
            <div className="history-pagination">
              <span>共 {page.total} 份已保存行程</span>
              <button
                disabled={busy || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                上一页
              </button>
              <button
                disabled={busy || offset + pageSize >= page.total}
                onClick={() => setOffset(offset + pageSize)}
              >
                下一页
              </button>
            </div>
          </>
        )}
        <small className="history-storage-note">
          删除后数据库空间可供后续行程复用；独立备份和共享候选目录会保留。
        </small>
      </section>
    </div>
  );
}
