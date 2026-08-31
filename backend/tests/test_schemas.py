import unittest

from pydantic import ValidationError

from app.schemas import TaskPatch, TaskPayload


class TaskTriggerSchemaTests(unittest.TestCase):
    def test_supported_trigger_types_are_accepted(self) -> None:
        for trigger_type in ("manual", "daily", "weekly"):
            payload = TaskPayload(name="任务", app_id=1, trigger_type=trigger_type)
            self.assertEqual(payload.trigger_type, trigger_type)

    def test_once_and_interval_are_rejected_for_new_tasks(self) -> None:
        for trigger_type in ("once", "interval"):
            with self.subTest(trigger_type=trigger_type):
                with self.assertRaises(ValidationError):
                    TaskPayload(name="任务", app_id=1, trigger_type=trigger_type)

    def test_once_and_interval_are_rejected_for_task_updates(self) -> None:
        for trigger_type in ("once", "interval"):
            with self.subTest(trigger_type=trigger_type):
                with self.assertRaises(ValidationError):
                    TaskPatch(trigger_type=trigger_type)


if __name__ == "__main__":
    unittest.main()
