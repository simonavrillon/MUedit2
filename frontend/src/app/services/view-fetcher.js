/**
 * Keeps a canvas's window in step with what it should show while the user
 * pans and zooms: at most one request is in flight, and while it is, only the
 * latest window asked for waits; the ones asked for in between are skipped.
 */

/**
 * @template P, V
 * @param {(params: P) => Promise<V>} fetchView
 * @param {(view: V) => void} onView Called with each window that arrives.
 * @param {(err: unknown) => void} [onError]
 */
export function createViewFetcher(fetchView, onView, onError) {
  /** @type {string | null} */
  let inFlight = null;
  /** @type {{ key: string, params: P } | null} */
  let next = null;

  async function drain() {
    while (next) {
      const { key, params } = next;
      next = null;
      inFlight = key;
      try {
        onView(await fetchView(params));
      } catch (err) {
        onError?.(err);
      } finally {
        inFlight = null;
      }
    }
  }

  return {
    /**
     * Ask for the window `key`; nothing is sent when it is already on its way.
     *
     * @param {string} key
     * @param {P} params
     */
    want(key, params) {
      if (key === inFlight) {
        next = null;
        return;
      }
      const idle = inFlight === null && next === null;
      next = { key, params };
      if (idle) void drain();
    },
  };
}
