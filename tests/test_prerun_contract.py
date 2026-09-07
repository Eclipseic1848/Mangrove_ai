"""自然语言前置会话的隔离契约，不调用模型、工具或数据库。"""

import json
import unittest

from src.agentic_runtime.prototypes.prerun_contract import ModelSelection, TaskSession


MODEL = ModelSelection(connection_id="connection-a", version="v1", model="chosen-model")


def submitted():
    return TaskSession(session_id="session-a", owner_id="owner-a", query="查找并整理资料", model=MODEL)


class PrerunContractTests(unittest.TestCase):
    def test_source_free_submission_clarifies_then_prepares_without_a_run(self):
        session = submitted()
        self.assertIsNone(session.handoff)
        clarified = session.clarify("owner-a")
        prepared = clarified.prepare("owner-a")
        self.assertEqual(prepared.phase, "preparing")
        self.assertEqual(prepared.session_id, "session-a")
        self.assertEqual(prepared.model, MODEL)
        self.assertIsNone(prepared.handoff)

    def test_freeze_requires_authorized_owner_and_sealed_sources_then_binds_once(self):
        session = submitted().prepare("owner-a")
        arguments = dict(owner_id="owner-a", account_active=True,
                         authorized_snapshot_ids=("snapshot-a",), task_id="task-a",
                         revision=1, run_id="run-a")
        for changed in ({"owner_id": "owner-b"}, {"account_active": False},
                        {"authorized_snapshot_ids": ()},
                        {"authorized_snapshot_ids": ("snapshot-a", "snapshot-a")},
                        {"revision": 0}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                session.freeze(**{**arguments, **changed})
        self.assertEqual(session.phase, "preparing")
        self.assertIsNone(session.handoff)
        frozen = session.freeze(**arguments)
        self.assertEqual(frozen.handoff.run_id, "run-a")
        self.assertEqual(frozen.handoff.revision, 1)
        self.assertEqual(frozen.session_id, "session-a")
        with self.assertRaises(ValueError):
            frozen.freeze(**{**arguments, "run_id": "run-b"})

    def test_pause_resume_and_cancel_preserve_identity_and_reject_other_owner(self):
        for session in (submitted(), submitted().prepare("owner-a").freeze(
                owner_id="owner-a", account_active=True,
                authorized_snapshot_ids=("snapshot-a",), task_id="task-a", revision=1, run_id="run-a")):
            for action in (session.pause, session.cancel):
                with self.assertRaises(ValueError):
                    action("owner-b")
            paused = session.pause("owner-a")
            with self.assertRaises(ValueError):
                paused.resume("owner-b")
            self.assertEqual(paused.resume("owner-a"), session)
            cancelled = paused.cancel("owner-a")
            self.assertEqual(cancelled.session_id, session.session_id)
            self.assertEqual(cancelled.handoff, session.handoff)
            for action in (cancelled.resume, cancelled.prepare, cancelled.pause):
                with self.assertRaises(ValueError):
                    action("owner-a")

    def test_calls_keep_chosen_model_and_deduplicate_late_usage_across_phases(self):
        session = submitted()
        other_model = MODEL.model_copy(update={"model": "fallback-model"})
        with self.assertRaises(ValueError):
            session.begin_call("owner-a", "call-1", other_model, "prerun")
        session = session.begin_call("owner-a", "call-1", MODEL, "prerun")
        session = session.record_usage("owner-a", "call-1", MODEL, "prerun", 12)
        self.assertEqual(session.record_usage("owner-a", "call-1", MODEL, "prerun", 12), session)
        with self.assertRaises(ValueError):
            session.record_usage("owner-a", "call-1", MODEL, "prerun", 13)
        frozen = session.prepare("owner-a").freeze(
            owner_id="owner-a", account_active=True, authorized_snapshot_ids=("snapshot-a",),
            task_id="task-a", revision=1, run_id="run-a")
        cancelled = frozen.begin_call("owner-a", "call-2", MODEL, "execution").cancel("owner-a")
        accounted = cancelled.record_usage("owner-a", "call-2", MODEL, "execution", None)
        self.assertEqual(accounted.usage_summary, {"known_tokens": 12, "unknown_calls": 1, "pending_calls": 0})
        self.assertEqual(accounted.session_id, "session-a")
        self.assertEqual(accounted.phase, "cancelled")
        with self.assertRaises(ValueError):
            accounted.begin_call("owner-a", "call-3", MODEL, "execution")
        with self.assertRaises(ValueError):
            accounted.record_usage("owner-a", "never-started", MODEL, "execution", 10)

    def test_unknown_result_requires_query_of_same_call_and_never_replays(self):
        started = submitted().begin_call("owner-a", "call-1", MODEL, "prerun")
        unknown = started.mark_unknown("owner-a", "call-1")
        for action in (unknown.resume, unknown.prepare, unknown.pause):
            with self.assertRaises(ValueError):
                action("owner-a")
        with self.assertRaises(ValueError):
            unknown.begin_call("owner-a", "call-2", MODEL, "prerun")
        with self.assertRaises(ValueError):
            unknown.resolve_unknown("owner-a", "different-call", "completed")
        resolved = unknown.resolve_unknown("owner-a", "call-1", "completed")
        self.assertEqual(resolved.phase, "submitted")
        self.assertEqual(resolved.session_id, "session-a")
        with self.assertRaises(ValueError):
            resolved.begin_call("owner-a", "call-1", MODEL, "prerun")
        cancelled = unknown.cancel("owner-a")
        self.assertEqual(cancelled.resolve_unknown("owner-a", "call-1", "failed").phase, "cancelled")

    def test_restart_validates_identity_version_and_binding_without_clearing_unknown(self):
        session = submitted().begin_call("owner-a", "call-1", MODEL, "prerun").mark_unknown("owner-a", "call-1")
        identity = dict(expected_owner="owner-a", expected_model=MODEL, expected_session_id="session-a")
        recovered = TaskSession.from_json(session.to_json(), **identity)
        self.assertEqual(recovered, session)
        with self.assertRaises(ValueError):
            recovered.resume("owner-a")
        for changed in ({"expected_owner": "owner-b"}, {"expected_session_id": "session-b"},
                        {"expected_model": MODEL.model_copy(update={"version": "v2"})}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                TaskSession.from_json(session.to_json(), **{**identity, **changed})
        snapshot = json.loads(session.to_json())
        for changed in ({"schema_version": 2}, {"resume_phase": None}, {"unknown_call_id": "missing"},
                        {"phase": "frozen"}, {"calls": snapshot["calls"] * 2},
                        {"calls": [{**snapshot["calls"][0], "purpose": "execution"}]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                TaskSession.from_json(json.dumps({**snapshot, **changed}), **identity)
        snapshot.pop("schema_version")
        with self.assertRaises(ValueError):
            TaskSession.from_json(json.dumps(snapshot), **identity)

    def test_query_resolution_is_retained_and_restarted_frozen_binding_does_not_change(self):
        session = submitted().begin_call("owner-a", "call-1", MODEL, "prerun")
        resolved = session.mark_unknown("owner-a", "call-1").resolve_unknown("owner-a", "call-1", "failed")
        self.assertEqual(resolved.calls[0].queried_outcome, "failed")
        with self.assertRaises(ValueError):
            resolved.mark_unknown("owner-a", "call-1")
        frozen = resolved.prepare("owner-a").freeze(
            owner_id="owner-a", account_active=True, authorized_snapshot_ids=("snapshot-a",),
            task_id="task-a", revision=1, run_id="run-a")
        paused = frozen.pause("owner-a")
        restored = TaskSession.from_json(paused.to_json(), expected_owner="owner-a",
                                        expected_model=MODEL, expected_session_id="session-a")
        self.assertEqual(restored.resume("owner-a"), frozen)

    def test_call_identity_normalization_cannot_bypass_no_replay(self):
        session = submitted().begin_call("owner-a", "call-1", MODEL, "prerun")
        with self.assertRaises(ValueError):
            session.begin_call("owner-a", " call-1 ", MODEL, "prerun")

    def test_restart_rejects_missing_nested_receipts_and_contradictory_query(self):
        session = submitted().begin_call("owner-a", "call-1", MODEL, "prerun")
        session = session.record_usage("owner-a", "call-1", MODEL, "prerun", 12)
        identity = dict(expected_owner="owner-a", expected_model=MODEL, expected_session_id="session-a")
        for field in ("receipt", "queried_outcome", "total_tokens"):
            snapshot = json.loads(session.to_json())
            target = snapshot["calls"][0]
            if field == "total_tokens":
                target = target["receipt"]
            target.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                TaskSession.from_json(json.dumps(snapshot), **identity)
        snapshot = json.loads(session.mark_unknown("owner-a", "call-1").to_json())
        snapshot["calls"][0]["queried_outcome"] = "completed"
        with self.assertRaises(ValueError):
            TaskSession.from_json(json.dumps(snapshot), **identity)

    def test_restart_rejects_snapshot_that_loses_call_ledger(self):
        session = submitted().begin_call("owner-a", "call-1", MODEL, "prerun")
        snapshot = json.loads(session.to_json())
        snapshot.pop("calls")
        with self.assertRaises(ValueError):
            TaskSession.from_json(json.dumps(snapshot), expected_owner="owner-a",
                                  expected_model=MODEL, expected_session_id="session-a")


if __name__ == "__main__":
    unittest.main()
