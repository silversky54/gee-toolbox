"""Tests for gee_toolbox.batch.tasks.exports (ExportTask and ExportTaskList)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from ee.ee_exception import EEException

from gee_toolbox.batch.tasks import exports
from gee_toolbox.batch.tasks.exports import (
    EXPORT_TARGET_MAP,
    EXPORT_TARGETS,
    ExportTask,
    ExportTaskList,
    dict_to_export_task,
    export_task_list_to_dict,
    export_task_to_dict,
    load_export_task_list,
    save_export_task_list,
)


def _make_ee_task(mocker, *, task_id=None, state="UNSUBMITTED"):
    """Build a lightweight mock of ee.batch.Task."""
    task = mocker.MagicMock()
    task.id = task_id
    task.state = state
    task.start = mocker.MagicMock(
        side_effect=lambda: (
            setattr(task, "state", "READY")
            or setattr(task, "id", task_id or "started-task-id")
        )
    )
    task.status = mocker.MagicMock(return_value={"state": state})
    task.cancel = mocker.MagicMock()
    return task


def _make_export_task(**kwargs) -> ExportTask:
    defaults = {
        "type": "image",
        "name": "export_a",
        "target": "assets",
        "path": "users/test/export_a",
    }
    defaults.update(kwargs)
    return ExportTask(**defaults)  # type: ignore


# ---------------------------------------------------------------------------
# Helpers / validation
# ---------------------------------------------------------------------------


class TestValidateTaskStatus:
    def test_accepts_known_status(self):
        assert exports._validate_task_status("ready") == "READY"
        assert exports._validate_task_status("COMPLETED") == "COMPLETED"

    def test_rejects_unknown_status(self):
        with pytest.raises(ValueError, match="Invalid task status"):
            exports._validate_task_status("not_a_real_status")


class TestExportTargetMap:
    def test_aliases_normalize_to_canonical_targets(self):
        assert EXPORT_TARGET_MAP["gee"] == "assets"
        assert EXPORT_TARGET_MAP["gdrive"] == "drive"
        assert EXPORT_TARGET_MAP["gcs"] == "storage"

    def test_export_targets_lists_all_aliases(self):
        assert set(EXPORT_TARGETS) == set(EXPORT_TARGET_MAP.keys())


# ---------------------------------------------------------------------------
# ExportTask
# ---------------------------------------------------------------------------


class TestExportTaskInit:
    def test_creates_without_task(self):
        task = _make_export_task(id="local-1")
        assert task.id == "local-1"
        assert task.type == "image"
        assert task.target == "assets"
        assert task.path == Path("users/test/export_a")
        assert task.task is None
        assert task.task_id is None
        assert task.task_status == "NO_TASK"
        assert task.status == "NO_TASK"

    def test_normalizes_target_aliases(self):
        assert _make_export_task(target="gee").target == "assets"
        assert _make_export_task(target="google-drive").target == "drive"
        assert _make_export_task(target="storage").target == "storage"

    def test_rejects_invalid_type(self):
        with pytest.raises(ValueError, match="invalid type"):
            _make_export_task(type="feature")

    def test_rejects_invalid_target(self):
        with pytest.raises(ValueError, match="invalid target"):
            _make_export_task(target="s3")

    def test_task_id_must_match_task(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="abc", state="READY")
        with pytest.raises(ValueError, match="Task ID mismatch"):
            _make_export_task(task=ee_task, task_id="xyz", task_status="READY")

    def test_cannot_clear_task_id_when_task_has_id(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="abc", state="READY")
        with pytest.raises(ValueError, match="Can't set task_id to 'None'"):
            _make_export_task(task=ee_task, task_id=None, task_status="READY")

    def test_uses_status_from_task_when_omitted(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="RUNNING")
        task = _make_export_task(task=ee_task, task_id="t1")
        assert task.task_status == "RUNNING"
        assert task.status == "PENDING"

    def test_explicit_task_status_overrides_task_state(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="RUNNING")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.task_status == "READY"
        assert task.status == "PENDING"

    def test_raises_when_task_has_no_usable_state(self):
        # Setter reads value.state; missing state must fail before init completes
        with pytest.raises(AttributeError):
            _make_export_task(task=SimpleNamespace(id=None))

    def test_id_falls_back_to_task_id_then_uuid(self, mocker):
        task = _make_export_task(task_id="from-task", task_status="CREATED")
        assert task.id == "from-task"

        mocker.patch(
            "gee_toolbox.batch.tasks.exports.uuid.uuid4",
            return_value=mocker.Mock(__str__=lambda _: "generated-uuid"),
        )
        task2 = _make_export_task()
        assert task2.id == "generated-uuid"


class TestExportTaskSetter:
    def test_assigning_none_clears_task(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        task.task = None
        assert task.task is None
        assert task.task_id is None
        assert task.task_status == "NO_TASK"
        assert task.status == "NO_TASK"

    def test_assigning_task_updates_id_and_status(self, mocker):
        task = _make_export_task()
        ee_task = _make_ee_task(mocker, task_id="new-id", state="COMPLETED")
        task.task = ee_task
        assert task.task_id == "new-id"
        assert task.task_status == "COMPLETED"
        assert task.status == "COMPLETED"

    def test_update_status_rejects_unknown(self):
        task = _make_export_task()
        with pytest.raises(ValueError, match="Unknown export status"):
            task._update_status("not-real")


class TestExportTaskStartTask:
    def test_warns_and_returns_when_no_ee_task(self, mocker):
        warn = mocker.patch("gee_toolbox.batch.tasks.exports.logger.warning")
        task = _make_export_task(task_status="CREATED")
        assert task.start_task() == "CREATED"
        warn.assert_called_once()

    def test_starts_when_not_started(self, mocker):
        ee_task = _make_ee_task(mocker, task_id=None, state="UNSUBMITTED")

        def _start():
            ee_task.id = "started-1"
            ee_task.state = "READY"

        ee_task.start.side_effect = _start
        task = _make_export_task(task=ee_task, task_status="UNSUBMITTED")
        assert task.start_task() == "READY"
        assert task.task_id == "started-1"
        assert task.status == "PENDING"
        ee_task.start.assert_called_once()

    def test_skips_start_when_already_pending(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.start_task() == "READY"
        ee_task.start.assert_not_called()

    def test_marks_failed_on_ee_exception(self, mocker):
        ee_task = _make_ee_task(mocker, task_id=None, state="UNSUBMITTED")
        ee_task.start.side_effect = EEException("quota exceeded")
        task = _make_export_task(task=ee_task, task_status="UNSUBMITTED")
        assert task.start_task() == "FAILED"
        assert task.status == "FAILED"
        assert "quota exceeded" in task.error  # type: ignore


class TestExportTaskQueryStatus:
    def test_returns_current_when_no_task_or_id(self, mocker):
        warn = mocker.patch("gee_toolbox.batch.tasks.exports.logger.warning")
        task = _make_export_task(task_status="CREATED")
        assert task.query_status() == "CREATED"
        warn.assert_called_once()

    def test_skips_query_for_terminal_status(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="COMPLETED")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="COMPLETED")
        assert task.query_status() == "COMPLETED"
        ee_task.status.assert_not_called()

    def test_updates_from_task_status(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        ee_task.status.return_value = {"state": "RUNNING", "error_message": None}
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.query_status() == "RUNNING"
        assert task.status == "PENDING"

    def test_falls_back_to_get_task_status(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        ee_task.status.side_effect = EEException("status failed")
        mock_get = mocker.patch(
            "gee_toolbox.batch.tasks.exports.ee.data.getTaskStatus",
            return_value=[{"state": "RUNNING"}],
        )
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.query_status() == "RUNNING"
        mock_get.assert_called_once_with("t1")

    def test_queries_by_task_id_only(self, mocker):
        mocker.patch(
            "gee_toolbox.batch.tasks.exports.ee.data.getTaskStatus",
            return_value=[{"state": "COMPLETED"}],
        )
        task = _make_export_task(task_id="orphan-id", task_status="READY")
        assert task.query_status() == "COMPLETED"
        assert task.status == "COMPLETED"

    def test_not_started_swallows_ee_exception_without_task_id(self, mocker):
        ee_task = _make_ee_task(mocker, task_id=None, state="UNSUBMITTED")
        ee_task.status.side_effect = EEException("not submitted")
        task = _make_export_task(task=ee_task, task_status="UNSUBMITTED")
        assert task.query_status() == "UNSUBMITTED"

    def test_raises_runtime_error_on_generic_failure(self, mocker):
        ee_task = _make_ee_task(mocker, task_id=None, state="READY")
        # task_id None path with PENDING status and non-EE exception
        ee_task.id = None
        ee_task.status.side_effect = RuntimeError("boom")
        task = _make_export_task(task=ee_task, task_id=None, task_status="READY")
        # After init, setter may have set task_id from task; force clear for this path
        task._task_id = None
        with pytest.raises(RuntimeError, match="Failed to update status"):
            task.query_status()
        assert task._status_update_failures == 1

    def test_marks_failed_to_get_status_after_max_failures(self, mocker):
        task = _make_export_task(task_id="t1", task_status="READY")
        task._status_update_failures = exports.MAX_STATUS_UPDATE_FAILURES
        assert task.query_status() == "FAILED_TO_GET_STATUS"
        assert task.status == "FAILED"


class TestExportTaskCancelTask:
    def test_returns_current_when_no_task_or_id(self, mocker):
        warn = mocker.patch("gee_toolbox.batch.tasks.exports.logger.warning")
        task = _make_export_task(task_status="CREATED")
        assert task.cancel_task() == "CREATED"
        warn.assert_called_once()

    def test_skips_when_already_completed(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="COMPLETED")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="COMPLETED")
        assert task.cancel_task() == "COMPLETED"
        ee_task.cancel.assert_not_called()

    def test_cancels_via_task(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.cancel_task() == "CANCEL_REQUESTED"
        assert task.status == "PENDING"
        ee_task.cancel.assert_called_once()

    def test_falls_back_to_cancel_task_api(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        ee_task.cancel.side_effect = EEException("cancel failed")
        mock_cancel = mocker.patch("gee_toolbox.batch.tasks.exports.ee.data.cancelTask")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        assert task.cancel_task() == "CANCEL_REQUESTED"
        mock_cancel.assert_called_once_with("t1")

    def test_cancels_by_task_id_only(self, mocker):
        mock_cancel = mocker.patch("gee_toolbox.batch.tasks.exports.ee.data.cancelTask")
        task = _make_export_task(task_id="orphan-id", task_status="RUNNING")
        assert task.cancel_task() == "CANCEL_REQUESTED"
        mock_cancel.assert_called_once_with("orphan-id")

    def test_raises_on_cancel_failure_when_pending(self, mocker):
        ee_task = _make_ee_task(mocker, task_id=None, state="READY")
        ee_task.cancel.side_effect = EEException("nope")
        task = _make_export_task(task=ee_task, task_id=None, task_status="READY")
        task._task_id = None
        with pytest.raises(RuntimeError, match="Failed to cancel"):
            task.cancel_task()


class TestExportTaskDunders:
    def test_repr_and_str(self):
        task = _make_export_task(id="x")
        assert "ExportTask(" in repr(task)
        assert "name=export_a" in str(task)
        assert "status=NO_TASK" in str(task)

    def test_equality_and_hash(self):
        a = _make_export_task(id="1", task_id="t1", task_status="READY")
        b = _make_export_task(id="2", task_id="t1", task_status="READY")
        c = _make_export_task(name="other", task_id="t1", task_status="READY")
        assert a == b
        assert a != c
        assert a != "not-a-task"
        assert hash(a) == hash(b)
        assert len({a, b, c}) == 2

    def test_deepcopy_shares_ee_task_handle(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        cloned = copy.deepcopy(task)
        assert cloned is not task
        assert cloned.task is ee_task
        assert cloned == task
        assert cloned.id == task.id


class TestExportTaskToDictAndSave:
    def test_to_dict_excludes_ee_task(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        task = _make_export_task(
            id="local",
            task=ee_task,
            task_id="t1",
            task_status="READY",
            error=None,
        )
        data = task.to_dict()
        assert data == {
            "id": "local",
            "type": "image",
            "name": "export_a",
            "target": "assets",
            "path": "users/test/export_a",
            "storage_bucket": None,
            "task_id": "t1",
            "task_status": "READY",
            "status": "PENDING",
            "error": None,
        }
        assert "task" not in data

    def test_save_writes_json(self, tmp_path):
        task = _make_export_task(id="local", task_status="CREATED")
        path = tmp_path / "task.json"
        task.save(path)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["id"] == "local"
        assert loaded["task_status"] == "CREATED"


# ---------------------------------------------------------------------------
# ExportTaskList
# ---------------------------------------------------------------------------


class TestExportTaskListInitAndMutations:
    def test_empty_list(self):
        task_list = ExportTaskList()
        assert len(task_list) == 0
        assert list(task_list) == []

    def test_init_with_single_task_deep_copies(self, mocker):
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        original = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        task_list = ExportTaskList(original)
        assert len(task_list) == 1
        assert task_list[0] is not original
        assert task_list[0].task is ee_task

    def test_append_rejects_non_export_task(self):
        task_list = ExportTaskList()
        with pytest.raises(TypeError, match="Invalid type for task"):
            task_list.append("nope")  # type: ignore

    def test_extend_from_list_and_other_task_list(self):
        a = _make_export_task(name="a", id="1")
        b = _make_export_task(name="b", id="2")
        task_list = ExportTaskList([a])
        task_list.extend(ExportTaskList([b]))
        assert [t.name for t in task_list] == ["a", "b"]

    def test_clear(self):
        task_list = ExportTaskList(_make_export_task())
        task_list.clear()
        assert len(task_list) == 0

    def test_getitem_setitem_delitem(self):
        task_list = ExportTaskList(_make_export_task(name="a", id="1"))
        replacement = _make_export_task(name="b", id="2")
        task_list[0] = replacement
        assert task_list[0].name == "b"
        assert task_list[0] is not replacement
        with pytest.raises(TypeError):
            task_list[0] = "bad"  # type: ignore
        del task_list[0]
        assert len(task_list) == 0

    def test_add_combines_lists(self):
        left = ExportTaskList(_make_export_task(name="a", id="1"))
        right = ExportTaskList(_make_export_task(name="b", id="2"))
        combined = left + right
        assert isinstance(combined, ExportTaskList)
        assert [t.name for t in combined] == ["a", "b"]
        assert ExportTaskList.__add__(left, "x") is NotImplemented  # type: ignore

    def test_str_and_repr(self):
        task_list = ExportTaskList(_make_export_task(id="1"))
        assert "export_a" in str(task_list)
        assert "Total ExportTasks: 1" in repr(task_list)


class TestExportTaskListCount:
    def test_count_all_and_filters(self):
        task_list = ExportTaskList(
            [
                _make_export_task(name="a", type="image", target="assets", id="1"),
                _make_export_task(name="b", type="table", target="drive", id="2"),
                _make_export_task(name="a", type="table", target="assets", id="3"),
            ]
        )
        assert task_list.count() == 3
        assert task_list.count(name="a") == 2
        assert task_list.count(type="table") == 2
        assert task_list.count(target="gee") == 2  # alias -> assets
        assert task_list.count(name="a", type="table") == 1


class TestExportTaskListRemove:
    def test_remove_requires_filters(self):
        task_list = ExportTaskList(_make_export_task(id="1"))
        task_list.remove()
        assert len(task_list) == 1

    def test_remove_by_criteria(self):
        task_list = ExportTaskList(
            [
                _make_export_task(name="a", type="image", target="assets", id="1"),
                _make_export_task(name="b", type="table", target="drive", id="2"),
                _make_export_task(name="a", type="table", target="assets", id="3"),
            ]
        )
        task_list.remove(name="a", type="image")
        assert [t.id for t in task_list] == ["2", "3"]
        task_list.remove(id="2")
        assert [t.id for t in task_list] == ["3"]


class TestExportTaskListAddTask:
    def test_add_task_appends_new_export_task(self):
        task_list = ExportTaskList()
        task_list.add_task(
            type="table",
            name="tbl",
            target="gcs",
            path="bucket/path",
            storage_bucket="my-bucket",
            task_status="CREATED",
            id="new-1",
        )
        assert len(task_list) == 1
        task = task_list[0]
        assert task.type == "table"
        assert task.target == "storage"
        assert task.storage_bucket == "my-bucket"
        assert task.id == "new-1"


class TestExportTaskListSummary:
    def test_summary_by_status(self):
        task_list = ExportTaskList(
            [
                _make_export_task(id="1", task_status="READY"),
                _make_export_task(id="2", name="b", task_status="COMPLETED"),
                _make_export_task(
                    id="3", name="c", target="drive", task_status="READY"
                ),
            ]
        )
        assert task_list.summary() == {"PENDING": 2, "COMPLETED": 1}
        assert task_list.summary(target="assets") == {"PENDING": 1, "COMPLETED": 1}

    def test_summary_extended(self):
        task_list = ExportTaskList(
            [
                _make_export_task(id="1", task_status="READY"),
                _make_export_task(id="2", name="b", task_status="RUNNING"),
            ]
        )
        assert task_list.summary(extended=True) == {
            ("PENDING", "READY"): 1,
            ("PENDING", "RUNNING"): 1,
        }

    def test_summary_rejects_invalid_target(self):
        with pytest.raises(ValueError, match="Invalid filter"):
            ExportTaskList().summary(target="ftp")

    def test_pretty_summary_empty_and_filled(self):
        empty = ExportTaskList()
        assert empty.pretty_summary() == "No Export tasks"

        task_list = ExportTaskList(_make_export_task(id="1", task_status="READY"))
        text = task_list.pretty_summary()
        assert "PENDING" in text
        assert "Count" in text

        extended = task_list.pretty_summary(extended=True)
        assert "Task Status" in extended
        assert "READY" in extended


class TestExportTaskListStartExports:
    def test_starts_only_not_started_with_ee_task(self, mocker):
        ee_ready = _make_ee_task(mocker, task_id=None, state="UNSUBMITTED")

        def _start():
            ee_ready.id = "started"
            ee_ready.state = "READY"

        ee_ready.start.side_effect = _start
        startable = _make_export_task(id="1", task=ee_ready, task_status="UNSUBMITTED")
        already = _make_export_task(id="2", name="b", task_status="READY", task_id="x")
        no_ee = _make_export_task(id="3", name="c", task_status="CREATED")

        task_list = ExportTaskList([startable, already, no_ee])
        summary = task_list.start_exports()
        assert summary["PENDING"] >= 1
        ee_ready.start.assert_called_once()
        assert task_list[0].task_status == "READY"


class TestExportTaskListQueryStatus:
    def test_continues_after_individual_failures(self, mocker):
        good_ee = _make_ee_task(mocker, task_id="g1", state="READY")
        good_ee.status.return_value = {"state": "COMPLETED"}
        bad_ee = _make_ee_task(mocker, task_id="b1", state="READY")
        bad_ee.status.side_effect = RuntimeError("fail")
        # Ensure bad path has no task_id fallback so query_status raises
        bad = _make_export_task(
            id="bad", name="bad", task=bad_ee, task_id="b1", task_status="READY"
        )
        bad._task_id = None
        bad_ee.id = None

        good = _make_export_task(
            id="good", name="good", task=good_ee, task_id="g1", task_status="READY"
        )
        task_list = ExportTaskList([bad, good])
        summary = task_list.query_status()
        assert summary.get("COMPLETED") == 1


class TestExportTaskListTrackExports:
    def test_tracks_until_terminal(self, mocker):
        mocker.patch("gee_toolbox.batch.tasks.exports.sleep")
        ee_task = _make_ee_task(mocker, task_id="t1", state="READY")
        states = iter(
            [
                {"state": "RUNNING"},
                {"state": "COMPLETED"},
            ]
        )
        ee_task.status.side_effect = lambda: next(states)
        task = _make_export_task(task=ee_task, task_id="t1", task_status="READY")
        task_list = ExportTaskList([task])
        summary = task_list.track_exports(sleep_time=0)
        assert summary == {"COMPLETED": 1}
        assert ee_task.status.call_count == 2

    def test_skips_already_finished_on_first_pass(self, mocker):
        mock_sleep = mocker.patch("gee_toolbox.batch.tasks.exports.sleep")
        done = _make_export_task(id="1", task_status="COMPLETED")
        task_list = ExportTaskList([done])
        assert task_list.track_exports(sleep_time=5) == {"COMPLETED": 1}
        mock_sleep.assert_not_called()

    def test_stops_after_max_status_failures(self, mocker):
        mocker.patch("gee_toolbox.batch.tasks.exports.sleep")
        ee_task = _make_ee_task(mocker, task_id=None, state="READY")
        ee_task.status.side_effect = RuntimeError("unreachable")
        task = _make_export_task(task=ee_task, task_id=None, task_status="READY")
        task._task_id = None
        # Pre-seed so next failure hits the max and track finishes
        task._status_update_failures = exports.MAX_STATUS_UPDATE_FAILURES - 1
        task_list = ExportTaskList([task])
        summary = task_list.track_exports(sleep_time=0)
        failures = task_list[0]._status_update_failures
        assert failures >= exports.MAX_STATUS_UPDATE_FAILURES
        assert isinstance(summary, dict)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class TestSerializationHelpers:
    def test_export_task_to_dict_and_back(self):
        task = _make_export_task(
            id="local",
            task_id="t1",
            task_status="READY",
            storage_bucket="bucket",
            target="gcs",
            path="folder/item",
            error="oops",
        )
        data = export_task_to_dict(task)
        restored = dict_to_export_task(data)
        assert restored.id == "local"
        assert restored.target == "storage"
        assert restored.task_id == "t1"
        assert restored.task_status == "READY"
        assert restored.status == "PENDING"
        assert restored.error == "oops"
        assert restored.task is None

    def test_dict_to_export_task_drops_non_task_payload(self):
        data = export_task_to_dict(_make_export_task(id="1", task_status="CREATED"))
        data["task"] = {"not": "an ee task"}
        restored = dict_to_export_task(data)
        assert restored.task is None

    def test_save_and_load_export_task_list(self, tmp_path):
        task_list = ExportTaskList(
            [
                _make_export_task(id="1", task_status="CREATED"),
                _make_export_task(
                    id="2", name="b", target="drive", task_status="READY"
                ),
            ]
        )
        path = tmp_path / "nested" / "tasks.json"
        save_export_task_list(task_list, str(path))
        assert path.exists()

        as_dict = export_task_list_to_dict(task_list)
        assert len(as_dict["tasks"]) == 2

        loaded = load_export_task_list(path)
        assert len(loaded) == 2
        assert loaded[0].id == "1"
        assert loaded[1].target == "drive"
        assert loaded[1].task_status == "READY"
