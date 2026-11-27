import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

const NEAR_BOTTOM_PX = 24;

/**
 * Live activity logs follow new rows only while the reader is already at
 * the bottom (#1152). Scrolling up pauses following and counts unread rows;
 * jumpToLatest resumes. Nothing scrolls while `live` is false (a completed
 * run, or a closed log), and a log that becomes live again starts following.
 */
export function useTailFollow(listRef: RefObject<HTMLElement | null>, rowCount: number, live: boolean) {
  const [following, setFollowing] = useState(true);
  const [unread, setUnread] = useState(0);
  const [wasLive, setWasLive] = useState(live);
  if (live !== wasLive) {
    setWasLive(live);
    setFollowing(true);
    setUnread(0);
  }
  const seen = useRef({ rows: rowCount, live });

  const onScroll = useCallback(() => {
    const list = listRef.current;
    if (!list) return;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight <= NEAR_BOTTOM_PX;
    setFollowing(atBottom);
    if (atBottom) setUnread(0);
  }, [listRef]);

  // Scroll only when rows arrive or the log becomes live, never merely
  // because following resumed: jumpToLatest already moved the view.
  useLayoutEffect(() => {
    const previous = seen.current;
    seen.current = { rows: rowCount, live };
    if (!live) return;
    const added = rowCount - previous.rows;
    if (following) {
      if (added > 0 || !previous.live) listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
    } else if (added > 0) {
      setUnread((count) => count + added);
    }
  }, [rowCount, live, following, listRef]);

  const jumpToLatest = useCallback(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
    setFollowing(true);
    setUnread(0);
  }, [listRef]);

  return { following, unread, onScroll, jumpToLatest };
}
