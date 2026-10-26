"""Persist task boundaries and structured outputs, not provider credentials or chat transcripts."""

from contextlib import contextmanager

from sqlalchemy import select

from app.database import AgentTaskRecord, new_id, now


class TaskJournal:
    def __init__(self, factory=None, run_id: str | None = None):
        self.factory = factory
        self.run_id = run_id

    @contextmanager
    def task(self, role: str, task_input: dict):
        task_id = new_id()
        if self.factory:
            with self.factory.begin() as db:
                db.add(
                    AgentTaskRecord(
                        id=task_id,
                        run_id=self.run_id,
                        role=role,
                        status="running",
                        input_snapshot=task_input,
                    )
                )
        result = TaskResult()
        try:
            yield result
        except Exception:
            self._finish(task_id, "failed", {}, "该子任务执行失败，请查看本次运行状态。")
            raise
        else:
            self._finish(task_id, result.status, result.output, None)

    def _finish(self, task_id: str, status: str, output: dict, error: str | None):
        if self.factory:
            with self.factory.begin() as db:
                task = db.get(AgentTaskRecord, task_id)
                task.status, task.result, task.error, task.finished_at = status, output, error, now()


class TaskResult:
    def __init__(self):
        self.status = "completed"
        self.output: dict = {}

    def complete(self, output: dict, *, status: str = "completed"):
        self.output, self.status = output, status


def task_views(db, run_id: str) -> list[dict]:
    return [
        {
            "id": task.id,
            "role": task.role,
            "status": task.status,
            "input": task.input_snapshot,
            "result": task.result,
            "error": task.error,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
        }
        for task in db.scalars(
            select(AgentTaskRecord)
            .where(AgentTaskRecord.run_id == run_id)
            .order_by(AgentTaskRecord.started_at, AgentTaskRecord.id)
        )
    ]
