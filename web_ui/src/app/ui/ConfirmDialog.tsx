import { useCallback, useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Button } from "./Button";
import { Disclosure } from "./Disclosure";
import { Icon } from "./icons/Icon";
import { formatCount } from "./format";
import "./confirm-dialog.css";

export type ConfirmOptions = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  billing?: { estimate: string | number };
  detail?: ReactNode;
};

type ConfirmRequest = {
  id: string;
  options: ConfirmOptions;
  trigger: HTMLElement | null;
  resolve: (accepted: boolean) => void;
};

let sequence = 0;
let requests: ConfirmRequest[] = [];
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function current() { return requests[0] ?? null; }
function publish() { for (const listener of listeners) listener(); }

function requestConfirm(options: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    requests = [...requests, {
      id: "gl-confirm-" + ++sequence,
      options,
      trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      resolve,
    }];
    publish();
  });
}

function settle(id: string, accepted: boolean) {
  if (requests[0]?.id !== id) return;
  const [head, ...rest] = requests;
  requests = rest;
  publish();
  head.resolve(accepted);
}

/** Use in any descendant of the app shell; mount one ConfirmDialog in the overlay provider. */
export function useConfirm() {
  return useCallback((options: ConfirmOptions) => requestConfirm(options), []);
}

export function ConfirmDialog() {
  const overlays = useOverlays();
  const request = useSyncExternalStore(subscribe, current, current);
  const opened = useRef<string | null>(null);
  const active = request ? overlays.isOpen(request.id) : false;

  useEffect(() => {
    if (!request) return;
    // Explicitly nested even when invoked over a root dialog (or a popover).
    overlays.openNested(request.id, "dialog", request.trigger);
  }, [request, overlays.openNested]);

  useEffect(() => {
    if (!request) { opened.current = null; return; }
    if (active) {
      opened.current = request.id;
    } else if (opened.current === request.id) {
      // Escape, scrim light-dismiss or a parent close are all cancellation.
      opened.current = null;
      settle(request.id, false);
    }
  }, [request, active]);

  if (!request) return null;
  const { title, body, confirmLabel, cancelLabel = "Cancel", destructive, billing, detail } = request.options;
  const finish = (accepted: boolean) => {
    overlays.closeLayer(request.id);
    settle(request.id, accepted);
  };
  return (
    <Dialog name={request.id} title={title} size="sm" className="gl-confirm-dialog"
      footer={<>
        <Button data-autofocus={destructive ? "" : undefined} onClick={() => finish(false)}>{cancelLabel}</Button>
        <Button variant="primary" destructive={destructive}
          data-autofocus={destructive ? undefined : ""} onClick={() => finish(true)}>{confirmLabel}</Button>
      </>}>
      <div className="gl-confirm-content">
        <div className="gl-confirm-body">{body}</div>
        {billing && <p className="gl-confirm-billing">
          <Icon name="status-info" size="sm" />
          Estimated cost: {typeof billing.estimate === "number" ? formatCount(billing.estimate) : billing.estimate}
        </p>}
        {detail && <Disclosure title="Details"><div className="gl-confirm-detail">{detail}</div></Disclosure>}
      </div>
    </Dialog>
  );
}
