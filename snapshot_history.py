"""
snapshot_history.py — Rolling buffer of camera frames for before/after comparisons.

Saves frames on state changes so the user can see what changed.
"""

import logging
import os
import time
from collections import deque
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class SnapshotHistory:
    """
    Maintains a rolling buffer of recent camera frames and saves before/after
    pairs when a state change is detected.
    """

    def __init__(
        self,
        snapshot_dir: str = "snapshots",
        buffer_size: int = 5,
        max_pairs: int = 100,
        enabled: bool = True,
    ) -> None:
        """
        Initialise the snapshot history buffer.

        Args:
            snapshot_dir: Directory to save snapshot pairs to.
            buffer_size:  Number of recent frames to keep in memory.
            max_pairs:    Maximum number of before/after pairs to keep on disk.
            enabled:      If False, all operations are no-ops.
        """
        self.snapshot_dir = snapshot_dir
        self.buffer_size = buffer_size
        self.max_pairs = max_pairs
        self.enabled = enabled

        # Each entry is (timestamp_float, image_bytes)
        self._buffer: deque = deque(maxlen=buffer_size)

        if enabled:
            os.makedirs(snapshot_dir, exist_ok=True)
            logger.info(
                "SnapshotHistory initialised: dir=%s buffer=%d max_pairs=%d",
                snapshot_dir, buffer_size, max_pairs,
            )

    def add_frame(self, image_bytes: bytes) -> None:
        """
        Add a camera frame to the rolling buffer.

        Args:
            image_bytes: Raw JPEG image bytes.
        """
        if not self.enabled:
            return
        self._buffer.append((time.monotonic(), image_bytes))

    def get_latest_frame(self) -> Optional[bytes]:
        """Return the most recently buffered frame, or None if buffer is empty."""
        if not self._buffer:
            return None
        return self._buffer[-1][1]

    def get_before_after(self) -> Tuple[Optional[bytes], Optional[bytes]]:
        """
        Return the (before, after) image pair for a state change.

        'before' is the most recent frame in the buffer (captured just before
        the change was detected).  'after' is the same — typically the main
        loop will call this immediately after detecting a change, when the
        latest buffered frame IS the "after" frame.

        For a true before/after pair, call ``add_frame(after_image)`` with
        the post-change frame BEFORE calling this method, then pass the
        returned ``after`` bytes as the notification image.

        Returns:
            Tuple of (before_bytes, after_bytes).  Either may be None if the
            buffer has fewer than 2 frames.
        """
        if len(self._buffer) < 2:
            if len(self._buffer) == 1:
                frame = self._buffer[-1][1]
                return frame, frame
            return None, None

        before_bytes = self._buffer[-2][1]
        after_bytes = self._buffer[-1][1]
        return before_bytes, after_bytes

    def save_pair(self, before_bytes: Optional[bytes], after_bytes: Optional[bytes], label: str = "") -> None:
        """
        Save a before/after snapshot pair to disk and auto-rotate old pairs.

        Args:
            before_bytes: JPEG bytes for the "before" frame (may be None).
            after_bytes:  JPEG bytes for the "after" frame (may be None).
            label:        Optional label suffix for filenames (e.g. "FREE_to_OCCUPIED").
        """
        if not self.enabled:
            return

        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_label = label.replace(" ", "_").replace("/", "-") if label else ""
        prefix = f"{ts}_{safe_label}" if safe_label else ts

        if before_bytes:
            before_path = os.path.join(self.snapshot_dir, f"{prefix}_before.jpg")
            try:
                with open(before_path, "wb") as fh:
                    fh.write(before_bytes)
                logger.debug("Saved before snapshot: %s", before_path)
            except OSError as exc:
                logger.warning("Could not save before snapshot: %s", exc)

        if after_bytes:
            after_path = os.path.join(self.snapshot_dir, f"{prefix}_after.jpg")
            try:
                with open(after_path, "wb") as fh:
                    fh.write(after_bytes)
                logger.debug("Saved after snapshot: %s", after_path)
            except OSError as exc:
                logger.warning("Could not save after snapshot: %s", exc)

        self.cleanup_old(self.max_pairs)

    def cleanup_old(self, max_pairs: int = 100) -> int:
        """
        Delete oldest snapshot files, keeping at most *max_pairs* pairs.

        Args:
            max_pairs: Maximum number of before/after pairs to retain.

        Returns:
            Number of files deleted.
        """
        if not self.enabled:
            return 0

        try:
            files = sorted(
                (f for f in os.listdir(self.snapshot_dir) if f.endswith(".jpg")),
            )
        except OSError as exc:
            logger.warning("Could not list snapshot directory: %s", exc)
            return 0

        # Each "pair" is 2 files (before + after).  Max files = max_pairs * 2.
        max_files = max_pairs * 2
        deleted = 0
        while len(files) > max_files:
            oldest = files.pop(0)
            try:
                os.remove(os.path.join(self.snapshot_dir, oldest))
                deleted += 1
            except OSError as exc:
                logger.warning("Could not delete old snapshot %s: %s", oldest, exc)

        if deleted:
            logger.info("Cleaned up %d old snapshot files", deleted)

        return deleted

    def get_latest_snapshot_path(self) -> Optional[str]:
        """Return the path to the most recent snapshot file, or None."""
        if not self.enabled:
            return None
        try:
            files = sorted(
                f for f in os.listdir(self.snapshot_dir) if f.endswith(".jpg")
            )
            if not files:
                return None
            return os.path.join(self.snapshot_dir, files[-1])
        except OSError:
            return None
