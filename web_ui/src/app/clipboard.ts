import { toast } from "./ui/Toast";

/**
 * The one clipboard contract for Copy actions (#1147). Resolves true only
 * after a successful write. A missing Clipboard API or a refused write
 * always gets a visible, actionable toast, never a console-only result.
 *
 * `announce` adds a success toast, for menu commands that close and so
 * have no inline "Copied" state of their own. Icon buttons that already
 * flash a Copied state leave it off, so success is announced once.
 */
export async function copyText(text: string, what: string, options: { announce?: boolean } = {}): Promise<boolean> {
  const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
  if (typeof clipboard?.writeText !== "function") {
    toast.show({
      title: `Couldn't copy ${what}: clipboard access isn't available here. Select the text and press Ctrl+C instead.`,
      variant: "error",
    });
    return false;
  }
  try {
    await clipboard.writeText(text);
  } catch (error) {
    console.error(`[clipboard] Copy ${what} failed:`, error);
    toast.show({
      title: `Couldn't copy ${what}. Clipboard access was blocked. Try again, or select the text and press Ctrl+C.`,
      variant: "error",
    });
    return false;
  }
  if (options.announce) toast.show({ title: `Copied ${what}.`, variant: "success" });
  return true;
}
