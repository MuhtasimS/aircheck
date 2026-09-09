"""Cross-process local persistence for snapshots, events, decisions, and authority."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Protocol

from aircheck.authority import Authorization, AuthorityError, DecisionRequest
from aircheck.domain.actions import Action
from aircheck.domain.events import (
    ActionCompletedPayload,
    ActionFailedPayload,
    ActionStartedPayload,
    LedgerEvent,
    LedgerEventType,
    StateTransitionPayload,
    event_hash,
)
from aircheck.domain.state_machine import RunStateSnapshot
from aircheck.domain.terminal import restore_terminal_verdict
from aircheck.domain.types import AuthorizationStatus, DecisionRequestStatus


class StoreConsistencyError(ValueError):
    pass


class RunStore(Protocol):
    def save_snapshot(self, snapshot: RunStateSnapshot) -> None: ...

    def load_snapshot(self, run_id: str) -> RunStateSnapshot: ...

    def append_event(self, event: LedgerEvent) -> None: ...

    def list_events(self, run_id: str) -> tuple[LedgerEvent, ...]: ...


class LocalDurableStore:
    """Atomic-file local adapter; the RunStore boundary remains storage-neutral."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()
        self._recover_action_transactions()
        self._recover_transition_transactions()

    def _run_dir(self, run_id: str) -> Path:
        return self._root / "runs" / run_id

    def _authorization_path(self, authorization_id: str) -> Path:
        return self._root / "authorizations" / f"{authorization_id}.json"

    def _decision_path(self, decision_id: str) -> Path:
        return self._root / "decisions" / f"{decision_id}.json"

    def _runtime_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "runtime.json"

    def _action_transaction_path(self, action_id: str) -> Path:
        return self._root / "transactions" / "actions" / f"{action_id}.json"

    def _transition_transaction_path(self, event_id: str) -> Path:
        return self._root / "transactions" / "transitions" / f"{event_id}.json"

    @contextmanager
    def _exclusive_file(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        for _ in range(500):
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                time.sleep(0.01)
        if descriptor is None:
            raise TimeoutError(f"could not acquire store lock: {path.name}")
        os.close(descriptor)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def list_events(self, run_id: str) -> tuple[LedgerEvent, ...]:
        path = self._run_dir(run_id) / "events.json"
        if not path.exists():
            return ()
        values = json.loads(path.read_text(encoding="utf-8"))
        return tuple(LedgerEvent.model_validate(value) for value in values)

    def append_event(self, event: LedgerEvent) -> None:
        path = self._run_dir(event.run_id) / "events.json"
        lock_path = path.with_suffix(".lock")
        with self._thread_lock, self._exclusive_file(lock_path):
            events = self.list_events(event.run_id)
            expected_sequence = len(events) + 1
            if event.seq != expected_sequence:
                raise StoreConsistencyError(
                    f"event sequence must be {expected_sequence}, got {event.seq}"
                )
            expected_previous = event_hash(events[-1]) if events else None
            if event.prev_hash != expected_previous:
                raise StoreConsistencyError("event prev_hash does not match the ledger head")
            if isinstance(event.payload, (ActionCompletedPayload, ActionFailedPayload)):
                started = any(
                    isinstance(previous.payload, ActionStartedPayload)
                    and previous.payload.action_id == event.payload.action_id
                    for previous in events
                )
                if not started:
                    raise StoreConsistencyError(
                        "ACTION_COMPLETED/ACTION_FAILED requires preceding ACTION_STARTED"
                    )
            serialized = [item.model_dump(mode="json") for item in (*events, event)]
            self._atomic_write(
                path,
                json.dumps(serialized, sort_keys=True, separators=(",", ":")),
            )

    def save_snapshot(
        self,
        snapshot: RunStateSnapshot,
        *,
        require_pending_decision: bool = True,
    ) -> None:
        events = self.list_events(snapshot.run_id)
        transitions = tuple(
            event.payload
            for event in events
            if isinstance(event.payload, StateTransitionPayload)
        )
        if transitions and transitions[-1].to_state is not snapshot.status:
            raise StoreConsistencyError(
                "snapshot status must equal the last transition event to_state"
            )
        if snapshot.pending_decision_id and require_pending_decision:
            path = self._decision_path(snapshot.pending_decision_id)
            if not path.exists():
                raise StoreConsistencyError("pending decision must be persisted before snapshot")
        self._atomic_write(
            self._run_dir(snapshot.run_id) / "snapshot.json",
            snapshot.model_dump_json(),
        )

    def load_snapshot(self, run_id: str) -> RunStateSnapshot:
        path = self._run_dir(run_id) / "snapshot.json"
        if not path.exists():
            raise KeyError(run_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("terminal_verdict") is not None:
            value["terminal_verdict"] = restore_terminal_verdict(value["terminal_verdict"])
        snapshot = RunStateSnapshot.model_validate(value)
        events = self.list_events(run_id)
        transitions = tuple(
            event.payload
            for event in events
            if isinstance(event.payload, StateTransitionPayload)
        )
        if transitions and transitions[-1].to_state is not snapshot.status:
            raise StoreConsistencyError("loaded snapshot disagrees with event ledger")
        return snapshot

    @staticmethod
    def _snapshot_from_value(value: dict[str, object]) -> RunStateSnapshot:
        if value.get("terminal_verdict") is not None:
            value["terminal_verdict"] = restore_terminal_verdict(value["terminal_verdict"])
        return RunStateSnapshot.model_validate(value)

    def commit_transition(self, snapshot: RunStateSnapshot, event: LedgerEvent) -> None:
        """WAL-backed logical commit of a transition event and its snapshot."""

        if not isinstance(event.payload, StateTransitionPayload):
            raise TypeError("commit_transition requires STATE_TRANSITION")
        if (snapshot.run_id, snapshot.status) != (
            event.run_id,
            event.payload.to_state,
        ):
            raise StoreConsistencyError("transition transaction binding mismatch")
        path = self._transition_transaction_path(event.event_id)
        self._atomic_write(
            path,
            json.dumps(
                {
                    "event": event.model_dump(mode="json"),
                    "snapshot": snapshot.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        self._finish_transition_transaction(path)

    def _finish_transition_transaction(self, path: Path) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        event = LedgerEvent.model_validate(value["event"])
        snapshot = self._snapshot_from_value(value["snapshot"])
        events = self.list_events(event.run_id)
        matching = tuple(item for item in events if item.event_id == event.event_id)
        if matching and matching != (event,):
            raise StoreConsistencyError("transition journal conflicts with the ledger")
        if not matching:
            self.append_event(event)
        self.save_snapshot(snapshot)
        path.unlink(missing_ok=True)

    def _recover_transition_transactions(self) -> None:
        root = self._root / "transactions" / "transitions"
        if not root.exists():
            return
        for path in sorted(root.glob("*.json")):
            self._finish_transition_transaction(path)

    def save_runtime_run(self, run: object) -> None:
        from aircheck.runtime.models import RuntimeRun

        if not isinstance(run, RuntimeRun):
            raise TypeError("save_runtime_run requires RuntimeRun")
        self._atomic_write(self._runtime_path(run.run_id), run.model_dump_json())

    def load_runtime_run(self, run_id: str):
        from aircheck.runtime.models import RuntimeRun

        path = self._runtime_path(run_id)
        if not path.exists():
            raise KeyError(run_id)
        return RuntimeRun.model_validate_json(path.read_text(encoding="utf-8"))

    def save_decision_request(self, request: DecisionRequest) -> None:
        path = self._decision_path(request.decision_id)
        if path.exists():
            raise StoreConsistencyError("decision request is write-once")
        self._atomic_write(path, request.model_dump_json())

    def update_decision_request(self, request: DecisionRequest) -> None:
        """Replace only the status of the exact previously persisted request."""

        path = self._decision_path(request.decision_id)
        with self._thread_lock, self._exclusive_file(path.with_suffix(".lock")):
            if not path.exists():
                raise StoreConsistencyError("decision request does not exist")
            existing = DecisionRequest.model_validate_json(path.read_text(encoding="utf-8"))
            if existing.model_copy(update={"status": request.status}) != request:
                raise StoreConsistencyError("decision request identity and binding are immutable")
            self._atomic_write(path, request.model_dump_json())

    def load_pending_decision(self, run_id: str) -> DecisionRequest:
        snapshot = self.load_snapshot(run_id)
        if snapshot.pending_decision_id is None:
            raise KeyError(f"run has no pending decision: {run_id}")
        request = DecisionRequest.model_validate_json(
            self._decision_path(snapshot.pending_decision_id).read_text(encoding="utf-8")
        )
        if request.status is not DecisionRequestStatus.PENDING:
            raise StoreConsistencyError("snapshot pending decision is not PENDING")
        return request

    def save(self, authorization: Authorization) -> None:
        path = self._authorization_path(authorization.authorization_id)
        with self._thread_lock, self._exclusive_file(path.with_suffix(".lock")):
            if path.exists():
                raise AuthorityError("authorization identifier already exists")
            self._atomic_write(path, authorization.model_dump_json())

    def get(self, authorization_id: str) -> Authorization | None:
        path = self._authorization_path(authorization_id)
        if not path.exists():
            return None
        return Authorization.model_validate_json(path.read_text(encoding="utf-8"))

    def consume(
        self,
        authorization_id: str,
        *,
        run_id: str,
        option_id: str,
        args_hash: str,
        action_id: str | None = None,
    ) -> Authorization:
        path = self._authorization_path(authorization_id)
        with self._thread_lock, self._exclusive_file(path.with_suffix(".lock")):
            authorization = self.get(authorization_id)
            if authorization is None:
                raise AuthorityError("authorization does not exist")
            if authorization.status is not AuthorizationStatus.ISSUED:
                raise AuthorityError("authorization must be ISSUED")
            if (
                authorization.run_id,
                authorization.option_id,
                authorization.args_hash,
            ) != (run_id, option_id, args_hash):
                raise AuthorityError("authorization binding mismatch")
            consumed = authorization.model_copy(
                update={
                    "status": AuthorizationStatus.CONSUMED,
                    "consumed_at": datetime.now(UTC),
                    "consumed_by_action_id": action_id,
                }
            )
            self._atomic_write(path, consumed.model_dump_json())
            return consumed

    def void(self, authorization_id: str, *, reason: str) -> Authorization:
        path = self._authorization_path(authorization_id)
        with self._thread_lock, self._exclusive_file(path.with_suffix(".lock")):
            authorization = self.get(authorization_id)
            if authorization is None:
                raise AuthorityError("authorization does not exist")
            if authorization.status is AuthorizationStatus.VOID:
                return authorization
            voided = authorization.model_copy(
                update={
                    "status": AuthorizationStatus.VOID,
                    "voided_at": datetime.now(UTC),
                    "void_reason": reason,
                }
            )
            self._atomic_write(path, voided.model_dump_json())
            return voided

    def list_authorizations(self, run_id: str) -> tuple[Authorization, ...]:
        root = self._root / "authorizations"
        if not root.exists():
            return ()
        return tuple(
            authorization
            for path in sorted(root.glob("*.json"))
            if (authorization := Authorization.model_validate_json(
                path.read_text(encoding="utf-8")
            )).run_id == run_id
        )

    def begin_authorized_action(self, action: Action) -> None:
        """Consume the exact capability and durably start its action via a WAL."""

        if action.authorization_id is None:
            raise AuthorityError("authorized action requires authorization")
        path = self._action_transaction_path(action.action_id)
        self._atomic_write(path, action.model_dump_json())
        try:
            self._finish_action_transaction(path)
        except AuthorityError:
            path.unlink(missing_ok=True)
            raise

    def _finish_action_transaction(self, path: Path) -> None:
        action = Action.model_validate_json(path.read_text(encoding="utf-8"))
        assert action.authorization_id is not None
        authorization = self.get(action.authorization_id)
        if authorization is None:
            raise AuthorityError("authorization does not exist")
        binding = (
            action.run_id,
            action.option_id,
            action.args_hash,
        )
        if (
            authorization.run_id,
            authorization.option_id,
            authorization.args_hash,
        ) != binding:
            raise AuthorityError("authorization binding mismatch")
        if authorization.status is AuthorizationStatus.ISSUED:
            self.consume(
                action.authorization_id,
                run_id=action.run_id,
                option_id=action.option_id,
                args_hash=action.args_hash,
                action_id=action.action_id,
            )
        elif (
            authorization.status is not AuthorizationStatus.CONSUMED
            or authorization.consumed_by_action_id != action.action_id
        ):
            raise AuthorityError("authorization must be ISSUED or bound to this recovery action")
        events = self.list_events(action.run_id)
        starts = tuple(
            event
            for event in events
            if isinstance(event.payload, ActionStartedPayload)
            and event.payload.action_id == action.action_id
        )
        if len(starts) > 1:
            raise StoreConsistencyError("action has duplicate start events")
        if not starts:
            self.append_action_started(action)
        path.unlink(missing_ok=True)

    def _recover_action_transactions(self) -> None:
        root = self._root / "transactions" / "actions"
        if not root.exists():
            return
        for path in sorted(root.glob("*.json")):
            self._finish_action_transaction(path)

    def append_action_started(self, action: Action) -> None:
        events = self.list_events(action.run_id)
        event = LedgerEvent(
            event_id=f"evt_{secrets.token_hex(10)}",
            run_id=action.run_id,
            seq=len(events) + 1,
            timestamp=datetime.now(UTC),
            type=LedgerEventType.ACTION_STARTED,
            actor=action.actor.value,
            summary=f"Action {action.action_id} started.",
            payload=ActionStartedPayload(
                action_id=action.action_id,
                tool=action.tool,
                authorization_id=action.authorization_id,
            ),
            refs=(action.option_id,),
            prev_hash=event_hash(events[-1]) if events else None,
        )
        self.append_event(event)

    def unfinished_actions(self, run_id: str) -> tuple[str, ...]:
        """Identify starts requiring deterministic recovery before work resumes."""

        events = self.list_events(run_id)
        started = tuple(
            event.payload.action_id
            for event in events
            if isinstance(event.payload, ActionStartedPayload)
        )
        terminal = {
            event.payload.action_id
            for event in events
            if isinstance(event.payload, (ActionCompletedPayload, ActionFailedPayload))
        }
        return tuple(action_id for action_id in started if action_id not in terminal)
