"""
tests/test_snapshot_history.py — Tests for snapshot_history.py
"""

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock

# Mock hardware dependencies
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest
from snapshot_history import SnapshotHistory


# ---------------------------------------------------------------------------
# SnapshotHistory
# ---------------------------------------------------------------------------

class TestSnapshotHistoryBuffer:
    def test_buffer_starts_empty(self):
        sh = SnapshotHistory(enabled=False)
        assert sh.get_latest_frame() is None

    def test_add_frame_disabled_no_op(self):
        sh = SnapshotHistory(enabled=False)
        sh.add_frame(b"\xff\xd8\xff")
        assert sh.get_latest_frame() is None

    def test_add_single_frame(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir="/tmp/test_snapshots_buf")
        sh.add_frame(b"\xff\xd8\xff")
        assert sh.get_latest_frame() == b"\xff\xd8\xff"

    def test_buffer_rolls_over(self):
        sh = SnapshotHistory(enabled=True, buffer_size=3, snapshot_dir="/tmp/test_snapshots_roll")
        frames = [bytes([i]) * 10 for i in range(5)]
        for f in frames:
            sh.add_frame(f)
        # Only last 3 should be in buffer
        assert len(sh._buffer) == 3
        assert sh.get_latest_frame() == frames[-1]

    def test_get_before_after_with_two_frames(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir="/tmp/test_snapshots_ba")
        sh.add_frame(b"before")
        sh.add_frame(b"after")
        before, after = sh.get_before_after()
        assert before == b"before"
        assert after == b"after"

    def test_get_before_after_with_one_frame(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir="/tmp/test_snapshots_one")
        sh.add_frame(b"only")
        before, after = sh.get_before_after()
        assert before == b"only"
        assert after == b"only"

    def test_get_before_after_empty_returns_none(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir="/tmp/test_snapshots_empty")
        before, after = sh.get_before_after()
        assert before is None
        assert after is None


class TestSnapshotHistoryDisk:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_save_pair_creates_files(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir, max_pairs=100)
        sh.save_pair(b"\xff\xd8\xff" + b"\x00" * 50, b"\xff\xd8\xff" + b"\x01" * 50, "FREE_to_OCCUPIED")
        files = os.listdir(self.tmpdir)
        assert len(files) == 2
        assert any("before" in f for f in files)
        assert any("after" in f for f in files)

    def test_save_pair_disabled_no_files(self):
        sh = SnapshotHistory(enabled=False, snapshot_dir=self.tmpdir)
        sh.save_pair(b"before", b"after", "test")
        assert os.listdir(self.tmpdir) == []

    def test_save_pair_only_before(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir)
        sh.save_pair(b"\xff\xd8\xff" + b"\x00" * 10, None, "test")
        files = os.listdir(self.tmpdir)
        assert len(files) == 1
        assert "before" in files[0]

    def test_cleanup_old_removes_excess(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir, max_pairs=2)
        # Create 6 fake jpg files (3 pairs)
        for i in range(6):
            with open(os.path.join(self.tmpdir, f"20260101_12000{i}_before.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
        deleted = sh.cleanup_old(max_pairs=2)
        remaining = os.listdir(self.tmpdir)
        assert deleted == 2  # 6 files - 4 max = 2 deleted
        assert len(remaining) == 4

    def test_cleanup_does_nothing_when_under_limit(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir, max_pairs=10)
        for i in range(4):
            with open(os.path.join(self.tmpdir, f"2026010{i}_before.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
        deleted = sh.cleanup_old(max_pairs=10)
        assert deleted == 0

    def test_get_latest_snapshot_path_returns_newest(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir)
        # Create files with sortable names
        files = ["20260101_before.jpg", "20260102_after.jpg", "20260103_after.jpg"]
        for fname in files:
            with open(os.path.join(self.tmpdir, fname), "wb") as f:
                f.write(b"\xff")
        path = sh.get_latest_snapshot_path()
        assert path is not None
        assert "20260103" in path

    def test_get_latest_snapshot_path_empty_dir(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir)
        assert sh.get_latest_snapshot_path() is None

    def test_get_latest_snapshot_path_disabled_returns_none(self):
        sh = SnapshotHistory(enabled=False, snapshot_dir=self.tmpdir)
        assert sh.get_latest_snapshot_path() is None

    def test_save_pair_auto_rotates(self):
        sh = SnapshotHistory(enabled=True, snapshot_dir=self.tmpdir, max_pairs=1)
        # Create 3 existing files
        for i in range(3):
            with open(os.path.join(self.tmpdir, f"2026000{i}_before.jpg"), "wb") as f:
                f.write(b"\xff")
        # Save a new pair (triggers cleanup)
        sh.save_pair(b"\xff\xd8\xff", b"\xff\xd8\xff", "test")
        files = os.listdir(self.tmpdir)
        # max_pairs=1 => max 2 files; we have 3 old + 2 new = 5, so 3 deleted
        assert len(files) <= 2
