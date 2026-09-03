"""Helper classes and functions for managing Google Earth Engine (GEE) export tasks."""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from time import sleep
from typing import Literal

import ee.batch
import ee.data
import prettytable
from ee.ee_exception import EEException

# Google Drive Issue
# https://community.latenode.com/t/getting-storage-quota-exceeded-error-403-with-google-drive-api-service-account/32433


logger = logging.getLogger(__name__)

EXPORT_TARGET_MAP = {
    "gee": "assets",
    "assets": "assets",
    "gee-assets": "assets",
    "gdrive": "drive",
    "drive": "drive",
    "google-drive": "drive",
    "gcs": "storage",
    "storage": "storage",
    "google-storage": "storage",
}
"""Map of accepted export-target aliases to normalized destinations."""

EXPORT_TARGETS = list[str](EXPORT_TARGET_MAP.keys())
"""Accepted export target names (aliases included)."""

# Maps high-level export status -> GEE task states (task_status).
# status is the export-level grouping; task_status is the GEE task state.
# GEE Task.State values (ee.batch.Task.State):
#   UNSUBMITTED, READY, RUNNING, COMPLETED, FAILED,
#   CANCEL_REQUESTED, CANCELLED
# GEE / Operations API may also return SUCCEEDED (kept as first-class
# task_status under COMPLETED, not aliased away).
# Local synthetic states: NO_TASK, EXCLUDED, SUBMITTED,
#   FAILED_TO_GET_STATUS
# GEE getTaskStatus may return UNKNOWN when the task id does not exist (404)
EXPORT_TASK_STATUS_MAP = {
    "NO_TASK": ["NO_TASK"],
    "EXCLUDED": ["EXCLUDED"],
    "NOT_STARTED": ["UNSUBMITTED"],
    "PENDING": ["SUBMITTED", "READY", "RUNNING", "CANCEL_REQUESTED"],
    "COMPLETED": ["COMPLETED", "CANCELLED", "SUCCEEDED"],
    "FAILED": ["FAILED", "FAILED_TO_GET_STATUS"],
    "UNKNOWN": ["UNKNOWN"],
}
"""Map of high-level export statuses to underlying GEE task states."""

EXPORT_TASK_STATES = list[str](EXPORT_TASK_STATUS_MAP.keys())
"""High-level export status names used by ExportTask.status."""

GEE_TASK_STATES = [
    status for statuses in EXPORT_TASK_STATUS_MAP.values() for status in statuses
]
"""Flattened list of GEE task state strings recognized by this module."""

GEE_TASK_RUNNING_STATES = EXPORT_TASK_STATUS_MAP["PENDING"]
"""GEE task states treated as still in progress."""

GEE_TASK_TERMINAL_STATES = (
    EXPORT_TASK_STATUS_MAP["COMPLETED"] + EXPORT_TASK_STATUS_MAP["FAILED"]
)
"""GEE task states that mean the export has finished (success or failure)."""

_MAX_STATUS_UPDATE_FAILURES = 3
"""Consecutive status-query failures allowed before marking a task failed."""

# GEE default max request rate is 100/s (~10ms). Doubled for safety.
# https://developers.google.com/earth-engine/guides/usage
_GEE_API_MIN_INTERVAL_SEC = 0.02
"""Minimum seconds between Earth Engine API calls (start/status/cancel)."""

_last_gee_api_call_at: float = 0.0

# Cloud Operations API states / alternate spellings -> ee.batch.Task.State values.
# See ee._cloud_api_utils.TASK_TO_OPERATION_STATE (inverted).
_TASK_STATUS_ALIASES = {
    "SUCCESS": "COMPLETED",
    "PENDING": "READY",  # Operation PENDING == Task READY
    "ACTIVE": "RUNNING",
    "CANCELLING": "CANCEL_REQUESTED",
    "CANCELED": "CANCELLED",
}


def _throttle_gee_api() -> None:
    """Sleep so consecutive GEE API calls respect GEE_API_MIN_INTERVAL_SEC."""
    global _last_gee_api_call_at
    if _GEE_API_MIN_INTERVAL_SEC <= 0:
        _last_gee_api_call_at = time.monotonic()
        return
    now = time.monotonic()
    wait = _GEE_API_MIN_INTERVAL_SEC - (now - _last_gee_api_call_at)
    if wait > 0:
        sleep(wait)
    _last_gee_api_call_at = time.monotonic()


def _normalize_task_status(value: Enum | str) -> str:
    """Normalize a GEE task state to an uppercase string.

    'ee.batch.Task.State' is a string Enum. 'str(State.UNSUBMITTED)' yields
    'State.UNSUBMITTED' (Python 3.11+), so callers must use '.value'
    rather than 'str(...)'. Plain API strings (e.g. from 'getTaskStatus')
    pass through unchanged.
    """
    if isinstance(value, Enum):
        value = value.value
    normalized = str(value).strip().upper()
    if normalized.startswith("STATE."):
        normalized = normalized[len("STATE.") :]
    return _TASK_STATUS_ALIASES.get(normalized, normalized)


def _validate_task_status(value: Enum | str) -> str:
    """Validate a GEE task state is a valid status.

    See EXPORT_TASK_STATUS_MAP for valid statuses.

    Raises:
        ValueError: If the task status is not valid.

    """
    value = _normalize_task_status(value)
    for statuses in EXPORT_TASK_STATUS_MAP.values():
        if value in statuses:
            return value
    raise ValueError(f"Invalid task status: {value}.")


def _is_recognized_task_status(value: str) -> bool:
    return any(value in statuses for statuses in EXPORT_TASK_STATUS_MAP.values())


class ExportTask:
    """Represents an export task for Google Earth Engine (GEE) resources.

    Manages the lifecycle and status of an export operation, such as exporting
    images or tables to GEE assets, Google Drive or Google Cloud Storage (GCS).
    Provides convenient methods to start the task, query its status, cancel it,
    and handle errors during the export process.

    status and task_status are kept separately: task_status is the GEE task
    state from the last query (e.g. READY, RUNNING, COMPLETED) and status is a
    high-level grouping of those states (e.g. NOT_STARTED, PENDING, COMPLETED,
    FAILED). See EXPORT_TASK_STATUS_MAP.
    """

    name: str
    _type: Literal["image", "table"]
    _target: str
    _task: ee.batch.Task | None
    _task_id: str | None
    _task_status: str  # GEE Task Status
    _status: str  # Local ExportTask Status
    _id_set_from_task: bool = False
    _status_query_failures: int
    _inconclusive_failures: int

    def __init__(
        self,
        type: Literal["image", "table"],
        name: str,
        target: str,
        path: str | Path,
        storage_bucket: str | None = None,
        task: ee.batch.Task | None = None,
        task_id: str | None = None,
        task_status: str | None = None,
        error: str | None = None,
        id: str | None = None,
    ) -> None:
        """Create an ExportTask.

        Arbitrary task_id and task_status can only be set during initialization.
        After initialization, those values are driven by the ee.batch.Task (when
        one is assigned) or by start_task, query_status or cancel_task.

        Args:
            type: The type of export (image or table).
            name: The name of the exported asset at the target.
            target: The export destination. See EXPORT_TARGETS for aliases
                (normalized to assets, drive or storage).
            path: The path to the asset to be exported.
            storage_bucket: The bucket name for Google Cloud Storage exports.
            task: The underlying Earth Engine batch task, if already created.
            task_id: The id of the GEE task. Must match task.id when both are set.
            task_status: A status for the GEE task. If omitted, taken from
                task.state, UNKNOWN when only task_id is set (no task), or
                NO_TASK when there is no task and no task_id.
            error: The error message if the export task failed.
            id: Unique identifier for this ExportTask. Falls back to task_id
                or a generated uuid4.

        Raises:
            ValueError: If type or target is invalid, task_id does not match
                the task, or status cannot be resolved from the task.

        """
        self.id = id or task_id or str(uuid.uuid4())  # TODO make inmutable after init
        if type not in ["image", "table"]:
            raise ValueError(f"Can't create ExportTask, invalid type: {type}.")
        self._type = type
        self.name = name
        if target not in EXPORT_TARGETS:
            raise ValueError(f"Can't create ExportTask, invalid target: {target}.")
        self._target = EXPORT_TARGET_MAP[target]  # TODO make inmutable after init
        self.path = Path(path)  # TODO make inmutable after init
        self.storage_bucket = storage_bucket  # TODO make inmutable after init
        self._status_query_failures = 0
        self._inconclusive_failures = 0
        self.task = task  # Mutable but will reset task id and status

        # Arbitrary task id and status can only be set by user during init
        ### SET TASK ID - ONE TIME ONLY ###
        id_from_task = None
        status_from_task = None
        if self.task is not None:
            id_from_task = getattr(self.task, "id", None)
            status_from_task = getattr(self.task, "state", None)

        # if value is None, attempt to get from task
        if task_id is None:
            if id_from_task is None:
                self._task_id = None
            else:
                raise ValueError(
                    f"Can't set task_id to 'None', while task has id: '{id_from_task}'"
                )
        else:
            # task_id must match the task.id unless task is None or has no id yet
            if id_from_task is None or id_from_task == task_id:
                self._task_id = task_id
            else:
                raise ValueError(f"Task ID mismatch: {task_id} != {id_from_task}")

        ### SET TASK STATUS - ONE TIME ONLY ###
        if task_status is None:
            if self.task is None and self.task_id is None:
                self._update_status("NO_TASK")
            elif self.task is None and self.task_id is not None:
                self._update_status("UNKNOWN")
            elif status_from_task is not None:
                self._update_status(_normalize_task_status(status_from_task))
            else:
                # Shouldn't happen, task should have at least UNSUBMITTED
                # Don't attempt to query status at init
                raise ValueError("Could not get status from task")
        else:
            self._update_status(_validate_task_status(task_status))

        self.error = error

    @property
    def type(self) -> Literal["image", "table"]:
        """The type of export (image or table)."""
        return self._type

    @property
    def target(self) -> str:
        """Normalized export destination (assets, drive or storage)."""
        return self._target

    @property
    def task(self) -> ee.batch.Task | None:
        """The underlying Earth Engine batch task.

        Assigning a new task updates task_id and status from that task.
        Assigning None clears the task and sets status to NO_TASK.
        """
        return self._task

    @task.setter
    def task(self, value: ee.batch.Task | None) -> None:
        # On new task, update task_id and task_status from task
        if value is None:
            self._task = None
            self._task_id = None
            self._update_status("NO_TASK")
            return

        self._task = value
        self._task_id = getattr(value, "id", None)  # None if UNSUBMITTED
        # value.state may be an ee.batch.Task.State enum; normalize via _update_status
        self._update_status(value.state)
        return

    @property
    def task_id(self) -> str | None:
        """Id of the underlying GEE task, if submitted."""
        return self._task_id

    @property
    def task_status(self) -> str:
        """GEE task state from the last query."""
        return self._task_status

    def _update_status(self, value: str | Enum) -> None:
        value = _normalize_task_status(value)
        for key, statuses in EXPORT_TASK_STATUS_MAP.items():
            if value in statuses:
                self._task_status = value
                self._status = key
                return
        else:
            raise ValueError(f"Unknown export status: {value}")

    def _record_status_query_failure(
        self, error_context: str, exc: BaseException
    ) -> None:
        self._status_query_failures += 1
        error_msg = f"{error_context}: {exc}"
        logger.error(error_msg)
        if self._status_query_failures >= _MAX_STATUS_UPDATE_FAILURES:
            self._update_status("FAILED_TO_GET_STATUS")
            self.error = error_msg
        raise RuntimeError(error_msg) from exc

    def _apply_queried_status(self, status: dict) -> str:
        """Record status from task.status() or getTaskStatus dict."""
        state = _normalize_task_status(status["state"])

        if state == "UNKNOWN" or not _is_recognized_task_status(state):
            self._update_status("UNKNOWN")
            if state == "UNKNOWN":
                self.error = status.get("error_message")
            else:
                self.error = f"Unrecognized GEE task state: {state}"
            self._inconclusive_failures += 1
            return self.task_status

        self._update_status(state)
        # TODO: Parse error message from status
        self.error = status.get("error_message")
        self._inconclusive_failures = 0
        return self.task_status

    def _inconclusive_polls_exhausted(self) -> bool:
        return self._inconclusive_failures >= _MAX_STATUS_UPDATE_FAILURES

    def _query_failures_exhausted(self) -> bool:
        return self._status_query_failures >= _MAX_STATUS_UPDATE_FAILURES

    @property
    def status(self) -> str:
        """High-level export status. Superset of task_status."""
        return self._status

    def start_task(self) -> str:
        """Start the EE export task if set, otherwise return the current status.

        Only starts when status is NOT_STARTED. If there is no ee.batch.Task,
        logs a warning and returns the current task_status.

        After a successful ``task.start()``, status is set to SUBMITTED.

        Returns:
            str: The current GEE task_status after attempting to start.

        """
        if self.task is None:
            logger.warning(
                f"Export {self.name} ({self.id}) to {self.target} has no EE Task."
            )
            return self.task_status

        try:
            if self.status == "NOT_STARTED":
                _throttle_gee_api()
                self.task.start()
                self._task_id = self.task.id
                # EE leaves task.state as UNSUBMITTED; mark locally as submitted.
                # operation_name (task.name) is set by EE when start() assigns it.
                self._update_status("SUBMITTED")
        except EEException as e:
            self._update_status("FAILED")
            self.error = str(e)
            logger.error(
                f"Failed to start task for {self.name} ({self.id}) to {self.target}"
            )
            logger.error(e)
        return self.task_status

    def query_status(self) -> str:
        """Query status of the EE task, otherwise return the current status.

        Queries status if task or task_id is set. Tries with task.status() first, then
        falls back to task_id via ee.data.getTaskStatus.
        Repeated failures are counted; after MAX_STATUS_UPDATE_FAILURES the task_status
        is set to FAILED_TO_GET_STATUS. If GEE returns status 'UNKNOWN' or unrecognized
        state more than MAX_STATUS_UPDATE_FAILURES, query_status stops trying to query
        GEE.

        Returns:
            str: The current GEE task_status.

        Raises:
            RuntimeError: If the EE task status cannot be determined.

        """
        # Skip if no task to track and no task_id
        if self.task is None and self.task_id is None:
            logger.warning(
                f"Task {self.name} ({self.id}) to {self.target} has no task or "
                f"task_id. Preserving task_status: {self._task_status} but no "
                "status update was performed."
            )
            return self.task_status

        # If task_status is already settled, don't check again.
        if self.task_status in GEE_TASK_TERMINAL_STATES:
            return self.task_status
        if self.task_status == "UNKNOWN" and self._inconclusive_polls_exhausted():
            return self.task_status
        if self._query_failures_exhausted():
            if self.task_status != "FAILED_TO_GET_STATUS":
                self._update_status("FAILED_TO_GET_STATUS")
            return self.task_status

        pollable = (
            EXPORT_TASK_STATUS_MAP["PENDING"]
            + EXPORT_TASK_STATUS_MAP["NOT_STARTED"]
            + ["UNKNOWN"]
        )

        if self.task_status in pollable:
            error_context = (
                f"Failed to update status for Task {self.name} ({self.id}) "
                f"to {self.target}"
            )
            # Try first with task.status()
            if self.task is not None:
                try:
                    _throttle_gee_api()
                    status = self.task.status()
                    state = _normalize_task_status(status["state"])
                    # task.status() returns UNSUBMITTED when operation_name is
                    # missing; do not overwrite a submitted task_id that way.
                    if state == "UNSUBMITTED" and self.task_id is not None:
                        pass
                    else:
                        return self._apply_queried_status(status)
                except EEException as e:
                    if self.task_id is not None:
                        pass
                    elif self.status == "NOT_STARTED":
                        return self.task_status
                    else:
                        self._record_status_query_failure(error_context, e)
                except Exception as e:
                    self._record_status_query_failure(error_context, e)

            if self.task_id is not None:
                try:
                    _throttle_gee_api()
                    status = ee.data.getTaskStatus(self.task_id)
                    if isinstance(status, list):
                        status = status[0]
                    if status is not None:
                        return self._apply_queried_status(status)
                    return self.task_status
                except EEException as e:
                    if self.status == "NOT_STARTED":
                        return self.task_status
                    else:
                        self._record_status_query_failure(error_context, e)
                except Exception as e:
                    self._record_status_query_failure(error_context, e)
        return self.task_status

    def cancel_task(self) -> str:
        """Request to cancel the EE export, otherwise return the current status.

        Attempts cancellation if task or task_id is set.
        Only attempts cancellation when status is NOT_STARTED or PENDING.
        Tries task.cancel() first, then falls back to task_id via ee.data.cancelTask.

        Returns:
            str: The current GEE task_status after attempting to cancel.

        """
        if self.task is None and self.task_id is None:
            logger.warning(
                f"Task {self.name} ({self.id}) to {self.target} has no task or "
                f"task_id. Preserving task_status: {self._task_status} but no "
                f"cancellation was performed."
            )
            return self.task_status

        if self.status in ["NOT_STARTED", "PENDING"]:
            # Try first with task.cancel() if task is not None
            error_context = (
                f"Failed to cancel task {self.name} ({self.id}) to {self.target}"
            )
            if self.task is not None:
                try:
                    _throttle_gee_api()
                    self.task.cancel()
                    self._update_status("CANCEL_REQUESTED")
                    return self.task_status
                except EEException as e:
                    if self.task_id is not None:
                        pass
                    elif self.status == "NOT_STARTED":
                        return self.task_status
                    else:
                        error_msg = f"{error_context}: {e}"
                        logger.error(error_msg)
                        # TODO: See if there's a better error type to raise
                        raise RuntimeError(error_msg) from e
                except Exception as e:
                    error_msg = f"{error_context}: {e}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg) from e

            if self.task_id is not None:
                try:
                    _throttle_gee_api()
                    ee.data.cancelTask(self.task_id)
                    self._update_status("CANCEL_REQUESTED")
                    return self.task_status
                except EEException as e:
                    if self.status == "NOT_STARTED":
                        return self.task_status
                    else:
                        error_msg = f"{error_context}: {e}"
                        logger.error(error_msg)
                        # TODO: See if there's a better error type to raise
                        raise RuntimeError(error_msg) from e
                except Exception as e:
                    error_msg = f"{error_context}: {e}"
                    logger.error(error_msg)
                    # TODO: See if there's a better error type to raise
                    raise RuntimeError(error_msg) from e
        return self.task_status

    def __repr__(self) -> str:
        """Return a string representation of the ExportTask."""
        return (
            f"ExportTask(type={self.type}, name={self.name}, target={self.target}, "
            f"status={self.status}, task_status={self.task_status})"
        )

    def __str__(self) -> str:
        """Return a string representation of the ExportTask."""
        return (
            f"(type={self.type}, name={self.name}, target={self.target}, "
            f"status={self.status}, task_status={self.task_status})"
        )

    def __eq__(self, other: object) -> bool:
        """Return True if the ExportTask is equal to another ExportTask."""
        if not isinstance(other, ExportTask):
            return NotImplemented

        return (
            self.type == other.type
            and self.name == other.name
            and self.target == other.target
            and self.storage_bucket == other.storage_bucket
            and self.path == other.path
            and self.task_id == other.task_id
            #   and self.id == other.id # Ids might differ
        )

    def __hash__(self) -> int:
        """Return a hash of the ExportTask."""
        return hash(
            (
                self.type,
                self.name,
                self.target,
                self.storage_bucket,
                self.path,
                self.task_id,
            )
        )

    def __deepcopy__(self, memo: dict) -> ExportTask:
        """Deep-copy metadata, but share the ee.batch.Task handle.

        ee.batch.Task is a client handle for a single server-side operation; cloning
        it would create duplicate local wrappers for the same remote task.
        """
        cls = type(self)
        clone = cls.__new__(cls)
        memo[id(self)] = clone

        # Keep the same Task instance for any nested deepcopy that reaches it
        if self._task is not None:
            memo[id(self._task)] = self._task
        clone._task = self._task

        clone.id = self.id
        clone._type = self._type
        clone.name = self.name
        clone._target = self._target
        clone.path = copy.deepcopy(self.path, memo)
        clone.storage_bucket = self.storage_bucket
        clone._task_id = self._task_id
        clone._task_status = self._task_status
        clone._status = self._status
        clone.error = copy.deepcopy(self.error, memo)
        clone._status_query_failures = self._status_query_failures
        clone._inconclusive_failures = self._inconclusive_failures
        return clone

    def to_dict(self) -> dict:
        """Convert ExportTask to JSON-safe dict.

        `WARNING`: Excludes ee.batch.Task due to serialization issues.

        Returns:
            dict: JSON-safe dict of ExportTask.

        """
        return export_task_to_dict(self)

    def save(self, file_path: str | Path) -> None:
        """Save ExportTask to a JSON file.

        `WARNING`: Excludes ee.batch.Task due to serialization issues.

        Args:
            file_path: Path to the file to save the ExportTask to.

        """
        with open(file_path, "w") as f:
            json.dump(self.to_dict(), f)


class ExportTaskList:
    """Manages a list of ExportTask.

    Provides convenient methods to manage the lifecycle of multiple EE export
    tasks in one go, like starting them, querying their status and monitoring
    their progress. Also provides methods to count, remove, add tasks and get
    a summary of the tasks in the list.

    Tasks appended or assigned into the list are deep-copied. The underlying
    ee.batch.Task handle is shared so both copies refer to the same GEE task.

    """

    def __init__(self, tasks: list[ExportTask] | ExportTask | None = None) -> None:
        """Create an ExportTaskList.

        Args:
            tasks: A single ExportTask, a list of ExportTask, or None for an empty list.

        """
        self._tasks: list[ExportTask] = []
        if tasks is None:
            return
        if isinstance(tasks, ExportTask):
            self.append(tasks)
        else:
            self.extend(tasks)

    @property
    def tasks(self) -> list[ExportTask]:
        """Get the list of ExportTask instances."""
        return self._tasks

    def append(self, task: ExportTask) -> None:
        """Append a deep-copied ExportTask.

        ee.batch.Task handle is shared between the original and the copied ExportTask.
        """
        if not isinstance(task, ExportTask):
            # Runtime guard
            raise TypeError(f"Invalid type for task: {type(task)}")
        self._tasks.append(copy.deepcopy(task))

    def extend(self, tasks: list[ExportTask] | ExportTaskList) -> None:
        """Extend the list with ExportTask instances or another ExportTaskList.

        Each task is appended as a deep copy. The ee.batch.Task handle is shared.
        """
        source = tasks._tasks if isinstance(tasks, ExportTaskList) else tasks
        for task in source:
            self.append(task)

    def clear(self):
        """Remove all tasks from the list."""
        self._tasks.clear()

    def count(
        self,
        name: str | None = None,
        type: str | None = None,
        target: str | None = None,
    ) -> int:
        """Count the number of tasks with a specific name, type, and target.

        Only arguments that are not None are used as filters, combined with AND.
        For example, count(type="table", target="assets") returns the number of
        tasks where type == "table" and target == "assets", ignoring id and name.

        If no arguments are provided, returns the total number of tasks in the list.

        Args:
            name: Filter the tasks by name.
            type: Filter the tasks by type.
            target: Filter the tasks by target.

        Returns:
            int: The number of tasks matching the filters.

        """
        # Normalize target to the internal format
        target = EXPORT_TARGET_MAP[target] if target else None

        filters = {
            attr: value
            for attr, value in {
                "name": name,
                "type": type,
                "target": target,
            }.items()
            if value is not None
        }
        if not filters:
            return len(self._tasks)

        return len(
            [
                e_task
                for e_task in self._tasks
                if all(
                    getattr(e_task, attr) == value for attr, value in filters.items()
                )
            ]
        )

    def remove(
        self,
        id: str | None = None,
        name: str | None = None,
        type: str | None = None,
        target: str | None = None,
    ) -> None:
        """Remove tasks matching all provided (non-None) criteria.

        Only arguments that are not None are used as filters, combined with AND.
        For example, remove(type="table", target="assets") removes every task where
        type == "table" and target == "assets", ignoring id and name.

        If no arguments are provided, nothing is removed.

        Args:
            id: Filter the tasks by id.
            name: Filter the tasks by name.
            type: Filter the tasks by type.
            target: Filter the tasks by target.

        """
        # Normalize target to the internal format
        target = EXPORT_TARGET_MAP[target] if target else None

        filters = {
            attr: value
            for attr, value in {
                "id": id,
                "name": name,
                "type": type,
                "target": target,
            }.items()
            if value is not None
        }
        if not filters:
            return

        self._tasks = [
            export_task
            for export_task in self._tasks
            if not all(
                getattr(export_task, attr) == value for attr, value in filters.items()
            )
        ]

    def add_task(
        self,
        type: Literal["image", "table"],
        name: str,
        target: str,
        path: str | Path,
        storage_bucket: str | None = None,
        task: ee.batch.Task | None = None,
        task_id: str | None = None,
        task_status: str | None = None,
        error: str | None = None,
        id: str | None = None,
    ) -> None:
        """Create and add a new ExportTask to the list.

        Args:
            type: The type of export (image or table).
            name: The name of the exported asset at the target.
            target: The export destination. See EXPORT_TARGETS for options.
            path: The path to the asset to be exported.
            storage_bucket: The bucket name for Google Cloud Storage exports.
            task: The underlying Earth Engine batch task.
            task_id: The id of the underlying Earth Engine batch task.
            task_status: A status for the GEE task.
            error: The error message if the export task failed.
            id: Unique identifier for the export task, generated if not provided
                (e.g. uuid4).

        """
        self._tasks.append(
            ExportTask(
                type=type,
                name=name,
                target=target,
                path=path,
                storage_bucket=storage_bucket,
                task=task,
                task_id=task_id,
                task_status=task_status,
                error=error,
                id=id,
            )
        )

    def summary(self, target: str | None = None, extended: bool = False) -> dict:
        """Count the number of tasks per status.

        If target is provided, only tasks with that 'target' attribute are included.
        See EXPORT_TARGETS for options.

        Args:
            target: Filter the tasks by target. If None, all tasks are included.
            extended: If True, includes task_status in the summary.

        Returns:
            dict: Count of tasks per status. If extended is True, the key is a tuple of
                (status, task_status).

        """
        target = target.lower() if target else None
        if target is None:
            _filter = EXPORT_TARGETS
        elif target in EXPORT_TARGETS:
            _filter = [EXPORT_TARGET_MAP[target]]
        else:
            raise ValueError(
                f"Invalid filter: {target}. Must be one of {EXPORT_TARGETS}."
            )

        filtered_list = [t for t in self._tasks if t.target in _filter]

        summary = {}
        if extended:
            status_keys = [(t.status, t.task_status) for t in filtered_list]
            unique_status_keys = set(status_keys)
            for key in unique_status_keys:
                summary[key] = len([k for k in status_keys if k == key])

        else:
            status_keys = [t.status for t in filtered_list]
            unique_status_keys = set(status_keys)
            for key in unique_status_keys:
                summary[key] = len([k for k in status_keys if k == key])

        return summary

    def pretty_summary(self, target: str | None = None, extended: bool = False) -> str:
        """Count the number of tasks per status and returns values in a pretty table.

        If target is provided, only tasks with that 'target' attribute are included.
        See EXPORT_TARGETS for options.

        Args:
            target: Filter the tasks by target. If None, all tasks are included.
            extended: If True, includes task_status in the summary.

        Returns:
            str: A string representation of the pretty table.

        """
        status_dict = self.summary(target=target, extended=extended)

        if not list(status_dict.keys()):
            return "No Export tasks"

        # Create table
        table = prettytable.PrettyTable()
        # table.set_style(prettytable.TableStyle.MSWORD_FRIENDLY)
        if extended:
            table.field_names = ["Status", "Task Status", "Count"]
            table.align["Task Status"] = "l"
        else:
            table.field_names = ["Status", "Count"]
        table.align["Status"] = "l"
        table.sortby = "Status"

        # Add rows
        if extended:
            rows = [[k[0], k[1], v] for k, v in status_dict.items()]
        else:
            rows = [[k, v] for k, v in status_dict.items()]
        table.add_rows(rows)
        return table.get_string()

    def start_exports(self) -> dict[str, int]:
        """Start all export tasks.

        Skips ExportTasks that have already started, don't have a task, or
        are in any terminal state (e.g. CANCELLED, FAILED, COMPLETED).

        Returns:
            dict: Count of tasks per status after attempting to start.

        """
        logger.info("Starting export tasks...")

        ####### START TASKS #######
        started = 0
        skipped = 0
        for task in self._tasks:
            if task.status == "NOT_STARTED" and task.task is not None:
                task.start_task()
                started += 1
            else:
                skipped += 1
                logger.debug(
                    "Skipping task: %s (ID: %s) to %s with status %s (task_status: %s)",
                    task.name,
                    task.task_id,
                    task.target,
                    task.status,
                    task.task_status,
                )

        logger.info("Started Export Tasks: %i. Skipped %i", started, skipped)

        return self.summary()

    def start_tasks(self) -> dict[str, int]:
        """Start all export tasks.

        Skips ExportTasks that have already started, don't have a task, or
        are in any terminal state (e.g. CANCELLED, FAILED, COMPLETED).
        Alias for 'start_exports' method for naming clarity.

        Returns:
            dict: Count of tasks per status after attempting to start.

        """
        return self.start_exports()

    def query_status(self) -> dict[str, int]:
        """Query status of all export tasks.

        Failures on individual tasks are logged and skipped so the rest of the
        list can still be updated.

        Returns:
            dict: Count of tasks per status.

        """
        logger.debug("Querying status of export tasks...")

        failed = 0
        for task in self._tasks:
            try:
                task.query_status()
            except Exception as e:
                failed += 1
                logger.warning(
                    "Failed to query status of %s (ID: %s) to %s: %s",
                    task.name,
                    task.id,
                    task.target,
                    e,
                )
        if failed > 0:
            logger.warning("Failed to query status of %i export tasks", failed)

        return self.summary()

    def track_exports(self, sleep_time: int = 60) -> dict[str, int]:
        """Track export tasks, querying status at specified time intervals.

        Polls until every task is no longer in a running GEE state, has settled on
        UNKNOWN after repeated inconclusive polls, or failed to query GEE.

        Args:
            sleep_time: Time in seconds to sleep between checking task status.

        Returns:
            dict: Count of tasks per status when tracking finishes.

        """
        logger.info("Tracking status of export tasks...")

        finished_tasks = []
        continue_tracking = True
        while continue_tracking:
            continue_tracking = False
            for task in self._tasks:
                # Skip previously "finished" tasks to avoid logging multiple times
                if task.id not in finished_tasks:
                    if task.task_status not in GEE_TASK_RUNNING_STATES and not (
                        task.task_status == "UNKNOWN"
                        and not task._inconclusive_polls_exhausted()
                    ):
                        finished_tasks.append(task.id)
                        continue

                    try:
                        status = task.query_status()
                    except RuntimeError as e:
                        if task._query_failures_exhausted():
                            logger.warning(
                                "Failed to query status of %s (ID: %s) to %s: %s",
                                task.name,
                                task.id,
                                task.target,
                                e,
                            )
                            finished_tasks.append(task.id)
                        else:
                            # Transient failure: keep polling on the next interval
                            continue_tracking = True
                        continue
                    if status in GEE_TASK_RUNNING_STATES:
                        continue_tracking = True
                        continue
                    if status == "UNKNOWN" and not task._inconclusive_polls_exhausted():
                        continue_tracking = True
                        continue

                    logger.debug(
                        "Export task %s (ID: %s) to %s finished with status: %s "
                        "(Task Status: %s)",
                        task.name,
                        task.id,
                        task.target,
                        task.status,
                        status,
                    )
                    finished_tasks.append(task.id)

            if continue_tracking:
                sleep(sleep_time)

        return self.summary()

    def __str__(self) -> str:
        """Return a string representation of the ExportTaskList."""
        summary = "\n".join([str(task) for task in self._tasks])
        return f"{summary}"

    def __repr__(self) -> str:
        """Return a string representation of the ExportTaskList."""
        return f"ExportTaskList(Total ExportTasks: {len(self._tasks)})"

    def __getitem__(self, index: int) -> ExportTask:
        """Return the ExportTask at the given index."""
        return self._tasks[index]

    def __setitem__(self, index: int, value: ExportTask) -> None:
        """Set the ExportTask at the given index."""
        if not isinstance(value, ExportTask):
            raise TypeError(f"Invalid type for task: {type(value)}")
        # Same ownership rule as append: independent ExportTask, shared EE Task
        self._tasks[index] = copy.deepcopy(value)

    def __delitem__(self, index) -> None:
        """Delete the ExportTask at the given index."""
        del self._tasks[index]

    def __len__(self) -> int:
        """Return the number of ExportTasks in the list."""
        return len(self._tasks)

    def __iter__(self) -> Iterator[ExportTask]:
        """Return an iterator over the ExportTasks in the list."""
        return iter(self._tasks)

    def __add__(self, other: ExportTaskList) -> ExportTaskList:
        """Return a new ExportTaskList with the combined ExportTasks."""
        if not isinstance(other, ExportTaskList):
            return NotImplemented
        return ExportTaskList(self._tasks + other._tasks)


# --- Manual save ---


def export_task_to_dict(export_task: ExportTask) -> dict:
    """Convert ExportTask to JSON-safe dict.

    Excludes ee.batch.Task due to serialization issues.

    Returns:
        dict: JSON-safe dict of ExportTask.

    """
    return {
        "id": export_task.id,
        "type": export_task.type,
        "name": export_task.name,
        "target": export_task.target,
        "path": export_task.path.as_posix(),
        "storage_bucket": export_task.storage_bucket,
        "task_id": export_task.task_id,
        "task_status": export_task.task_status,
        "status": export_task.status,
        "error": export_task.error,
        # ee.batch.Task metadata for rehydration
    }


def export_task_list_to_dict(task_list: ExportTaskList) -> dict:
    """Convert ExportTaskList to a dict."""
    return {"tasks": [export_task_to_dict(t) for t in task_list._tasks]}


def save_export_task_list(task_list: ExportTaskList, file_path: str) -> None:
    """Save ExportTaskList to JSON."""
    data = export_task_list_to_dict(task_list)
    fp = Path(file_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------- Manual load (JSON) ---------


# def _rehydrate_task(
#     task_id: str, gee_task_list: list[ee.batch.Task] | None = None
# ) -> ee.batch.Task | None:
#     """Rebuild Task from saved metadata; returns None if insufficient data."""

#     if gee_task_list is None:
#         try:
#             gee_task_list = ee.batch.Task.list()
#         except EEException as e:
#             logger.error(f"Failed to list GEE tasks for rehydration: {e}")
#             gee_task_list = []

#     task = None
#     for t in gee_task_list:
#         if t.id == task_id:
#             task = t
#             break
#     return task


def dict_to_export_task(d: dict) -> ExportTask:
    """Rebuild an ExportTask from dict; task rehydrated if metadata present."""
    gee_task: ee.batch.Task | dict | None = d.get("task")
    if gee_task is not None and not isinstance(gee_task, ee.batch.Task):
        gee_task = None

    export_task = ExportTask(
        id=d["id"],  # Direct
        type=d["type"],  # Direct
        name=d["name"],  # Direct
        target=d["target"],  # Direct
        path=Path(d["path"]),  # Direct
        storage_bucket=d.get("storage_bucket"),  # Direct
        task=gee_task,
        task_id=d.get("task_id"),  # Direct
        task_status=d.get("task_status"),  # Direct
        error=d.get("error"),  # Direct
    )

    return export_task


def load_export_task_list(file_path: str | Path) -> ExportTaskList:
    """Load ExportTaskList from JSON and rehydrate tasks if possible."""
    fp = Path(file_path)
    data = json.loads(fp.read_text(encoding="utf-8"))
    tasks = [dict_to_export_task(td) for td in data.get("tasks", [])]
    return ExportTaskList(tasks=tasks)
