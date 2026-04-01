import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)


class TaskDispatcher(ABC):
    @abstractmethod
    def dispatch(self, task_fn: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any) -> None:
        """Dispatch an async task for background execution."""


class BackgroundTaskDispatcher(TaskDispatcher):
    def __init__(self, background_tasks: BackgroundTasks):
        self._bg = background_tasks

    def dispatch(self, task_fn: Callable[..., Coroutine[Any, Any, Any]], *args: Any, **kwargs: Any) -> None:
        logger.info("Dispatching background task: %s", task_fn.__name__)
        self._bg.add_task(task_fn, *args, **kwargs)
