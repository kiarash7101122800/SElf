"""Central outbound queue with Telegram FloodWait backoff.

Automated features submit outbound operations here instead of racing each
other.  A FloodWait pauses the whole account queue, then retries the same job
without losing its position.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon.errors import FloodWaitError


Operation = Callable[[], Awaitable[Any]]
StateCallback = Callable[[dict[str, Any]], Any]


@dataclass(order=True)
class _QueuedOperation:
    priority: int
    sequence: int
    operation: Operation = field(compare=False)
    future: asyncio.Future = field(compare=False)
    description: str = field(compare=False, default="")
    attempts: int = field(compare=False, default=0)


class SmartSendQueue:
    """Serialize sends and apply account-wide FloodWait backoff."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = 0.9,
        max_size: int = 1000,
        state_callback: StateCallback | None = None,
        sleep_func: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        max_floodwait_retries: int = 5,
    ) -> None:
        self.min_interval_seconds = max(0.05, float(min_interval_seconds))
        self.queue: asyncio.PriorityQueue[_QueuedOperation] = (
            asyncio.PriorityQueue(maxsize=max(10, int(max_size)))
        )
        self.state_callback = state_callback
        self.sleep = sleep_func
        self.worker_task: asyncio.Task | None = None
        self.sequence = 0
        self.pause_until = 0.0
        self.last_operation_at = 0.0
        self.last_floodwait_seconds = 0
        self.closed = False
        self.max_floodwait_retries = max(0, int(max_floodwait_retries))
        self.current_job: _QueuedOperation | None = None

    def start(self) -> None:
        if self.worker_task is None or self.worker_task.done():
            self.closed = False
            self.worker_task = asyncio.create_task(self._worker())
            self._publish_state()

    async def close(self) -> None:
        self.closed = True
        task = self.worker_task
        current = self.current_job
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.worker_task = None

        close_error = RuntimeError("صف ارسال بسته شد.")
        self.current_job = None
        if current is not None and not current.future.done():
            current.future.set_exception(close_error)
        while True:
            try:
                job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if not job.future.done():
                    job.future.set_exception(close_error)
            finally:
                self.queue.task_done()
        self._publish_state()

    async def execute(
        self,
        operation: Operation,
        *,
        description: str = "",
        priority: int = 50,
    ) -> Any:
        if self.closed:
            raise RuntimeError("صف ارسال بسته است.")
        if self.worker_task is None or self.worker_task.done():
            self.start()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.sequence += 1
        job = _QueuedOperation(
            priority=max(0, min(int(priority), 1000)),
            sequence=self.sequence,
            operation=operation,
            future=future,
            description=str(description or "")[:200],
        )
        await self.queue.put(job)
        self._publish_state()
        return await future

    async def send_message(
        self,
        client: Any,
        entity: Any,
        message: Any,
        *,
        priority: int = 50,
        **kwargs: Any,
    ) -> Any:
        return await self.execute(
            lambda: client.send_message(entity, message, **kwargs),
            description=f"send_message:{entity}",
            priority=priority,
        )

    async def send_file(
        self,
        client: Any,
        entity: Any,
        file: Any,
        *,
        priority: int = 50,
        **kwargs: Any,
    ) -> Any:
        return await self.execute(
            lambda: client.send_file(entity, file, **kwargs),
            description=f"send_file:{entity}",
            priority=priority,
        )

    async def edit_message(
        self,
        client: Any,
        entity: Any,
        message: Any,
        text: str,
        *,
        priority: int = 30,
        **kwargs: Any,
    ) -> Any:
        return await self.execute(
            lambda: client.edit_message(entity, message, text, **kwargs),
            description=f"edit_message:{entity}",
            priority=priority,
        )

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            self.current_job = job
            self._publish_state()
            try:
                if job.future.cancelled():
                    continue
                await self._respect_backoff()
                elapsed = time.monotonic() - self.last_operation_at
                if elapsed < self.min_interval_seconds:
                    await self.sleep(self.min_interval_seconds - elapsed)
                try:
                    result = await job.operation()
                except FloodWaitError as exc:
                    seconds = max(1, int(getattr(exc, "seconds", 60)))
                    job.attempts += 1
                    self.last_floodwait_seconds = seconds
                    if job.attempts > self.max_floodwait_retries:
                        if not job.future.done():
                            job.future.set_exception(
                                RuntimeError(
                                    f"عملیات پس از {self.max_floodwait_retries} بار FloodWait متوقف شد."
                                )
                            )
                        continue
                    self.pause_until = max(
                        self.pause_until,
                        time.monotonic() + seconds + 1,
                    )
                    self._publish_state()
                    await self._respect_backoff()
                    await self.queue.put(job)
                    continue
                except Exception as exc:
                    if not job.future.done():
                        job.future.set_exception(exc)
                else:
                    self.last_operation_at = time.monotonic()
                    if not job.future.done():
                        job.future.set_result(result)
            finally:
                if self.current_job is job:
                    self.current_job = None
                self.queue.task_done()
                self._publish_state()

    async def _respect_backoff(self) -> None:
        remaining = self.pause_until - time.monotonic()
        if remaining > 0:
            await self.sleep(remaining)

    def snapshot(self) -> dict[str, Any]:
        return {
            "queue_depth": self.queue.qsize(),
            "paused_seconds": max(
                0,
                int(round(self.pause_until - time.monotonic())),
            ),
            "last_floodwait_seconds": self.last_floodwait_seconds,
            "running": bool(
                self.worker_task is not None and not self.worker_task.done()
            ),
        }

    def _publish_state(self) -> None:
        if self.state_callback is None:
            return
        try:
            result = self.state_callback(self.snapshot())
            if inspect.isawaitable(result):
                asyncio.create_task(result)
        except Exception:
            pass
