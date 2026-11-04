"""时间轴的纯函数（T-0104）：token 平均分、按时间归属、分桶、交给实例执行的消息。"""

import uuid
from datetime import UTC, datetime, timedelta

from app.domain import task_runs, timeline
from app.models import Task, TaskRun

BASE = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
INSTANCE = uuid.uuid4()


def _run(start: int, end: int | None, *, instance: uuid.UUID | None = INSTANCE) -> TaskRun:
    return TaskRun(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        executor_kind="instance" if instance else "user",
        instance_id=instance,
        executor_name="甲",
        started_at=BASE + timedelta(minutes=start),
        ended_at=BASE + timedelta(minutes=end) if end is not None else None,
    )


def _minutes(*rows: tuple[int, task_runs.Tokens]) -> task_runs.MinuteRows:
    return {INSTANCE: [(BASE + timedelta(minutes=m), tokens) for m, tokens in rows]}


def test_split_keeps_the_total_and_gives_the_rest_to_the_first() -> None:
    assert task_runs.split((7, 0, 1, 2), 3) == [(3, 0, 1, 1), (2, 0, 0, 1), (2, 0, 0, 0)]
    assert task_runs.split((5, 5, 5, 5), 1) == [(5, 5, 5, 5)]
    shares = task_runs.split((101, 7, 0, 3), 4)
    assert tuple(sum(column) for column in zip(*shares, strict=True)) == (101, 7, 0, 3)


def test_attribution_by_time_with_a_one_minute_lag() -> None:
    first, second = _run(0, 10), _run(5, None)
    minutes = _minutes(
        (2, (100, 0, 0, 0)), (7, (0, 60, 0, 0)), (10, (0, 0, 8, 0)), (30, (1, 1, 1, 1))
    )
    result = task_runs.attribute([first, second], minutes, now=BASE + timedelta(minutes=40))
    # 第 10 分钟的用量第 11 分钟前报上来：第一段（10 分钟结束）仍分到一半
    assert result[first.id].tokens == (100, 30, 4, 0) and result[first.id].shared
    assert result[second.id].tokens == (1, 31, 5, 1) and result[second.id].shared
    assert [m for m, _ in result[second.id].minutes] == [
        BASE + timedelta(minutes=7),
        BASE + timedelta(minutes=10),
        BASE + timedelta(minutes=30),
    ]


def test_attribution_ignores_people_and_other_instances() -> None:
    person = _run(0, 30, instance=None)
    alone = _run(0, 30)
    other = _run(0, 30, instance=uuid.uuid4())
    result = task_runs.attribute([person, alone, other], _minutes((5, (9, 0, 0, 0))), now=BASE)
    assert result[person.id].tokens == task_runs.ZERO
    assert result[alone.id].tokens == (9, 0, 0, 0) and not result[alone.id].shared
    assert result[other.id].tokens == task_runs.ZERO


def test_waits_are_clipped_to_the_run() -> None:
    run = _run(10, 20)
    waits = {
        INSTANCE: [
            task_runs.Wait(
                uuid.uuid4(), INSTANCE, "update_task", BASE, BASE + timedelta(minutes=12)
            ),
            task_runs.Wait(
                uuid.uuid4(), INSTANCE, "complete_task", BASE + timedelta(minutes=18), None
            ),
            task_runs.Wait(
                uuid.uuid4(), INSTANCE, "delete_task", BASE + timedelta(minutes=25), None
            ),
        ]
    }
    inside = task_runs.waits_in(run, waits, now=BASE + timedelta(minutes=30))
    assert [(w.tool, w.start, w.end) for w in inside] == [
        ("update_task", BASE + timedelta(minutes=10), BASE + timedelta(minutes=12)),
        # 还在等待，但这段已经结束：裁到这段的结束
        ("complete_task", BASE + timedelta(minutes=18), BASE + timedelta(minutes=20)),
    ]


def test_bucket_size_keeps_at_most_300_buckets() -> None:
    assert timeline.bucket_minutes(BASE, BASE + timedelta(hours=6)) == 5
    assert timeline.bucket_minutes(BASE, BASE + timedelta(days=1)) == 5
    assert timeline.bucket_minutes(BASE, BASE + timedelta(days=7)) == 60
    assert timeline.bucket_minutes(BASE, BASE + timedelta(days=31)) == 180


def test_dispatch_text_names_the_task_and_the_tools() -> None:
    task = Task(id=uuid.uuid4(), title="整理发布说明", due_at=BASE)
    text = timeline.dispatch_text(task)
    assert str(task.id) in text and "整理发布说明" in text and "2026-11-04 08:00（UTC）" in text
    assert "get_task" in text and "complete_task" in text
    assert "没有截止时间" in timeline.dispatch_text(Task(id=uuid.uuid4(), title="x", due_at=None))
