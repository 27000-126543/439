import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
import time
import uuid
from typing import Callable, Any, Dict, List, Optional
import config
from logging_system import concurrency_logger


class Task:
    def __init__(self, func: Callable, *args, **kwargs):
        self.id = str(uuid.uuid4())
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.status = "pending"
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.started_at = None
        self.completed_at = None
        self.timeout = kwargs.pop("timeout", config.CONCURRENCY_SETTINGS["task_timeout"])
        self.priority = kwargs.pop("priority", 5)
    
    async def execute_async(self):
        self.status = "running"
        self.started_at = time.time()
        try:
            if asyncio.iscoroutinefunction(self.func):
                self.result = await asyncio.wait_for(
                    self.func(*self.args, **self.kwargs),
                    timeout=self.timeout
                )
            else:
                loop = asyncio.get_event_loop()
                self.result = await asyncio.wait_for(
                    loop.run_in_executor(None, self.func, *self.args, **self.kwargs),
                    timeout=self.timeout
                )
            self.status = "completed"
        except asyncio.TimeoutError:
            self.status = "timeout"
            self.error = f"Task timed out after {self.timeout} seconds"
            concurrency_logger.error(f"Task {self.id} timed out")
        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            concurrency_logger.error(f"Task {self.id} failed: {str(e)}")
        finally:
            self.completed_at = time.time()
        return self


class ConcurrentTaskManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.max_workers = config.CONCURRENCY_SETTINGS["max_workers"]
        self.queue_limit = config.CONCURRENCY_SETTINGS["queue_limit"]
        self.task_queue: deque = deque()
        self.active_tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, Task] = {}
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._loop = None
        self._running = False
        self._semaphore = asyncio.Semaphore(self.max_workers)
    
    async def _start_async_loop(self):
        self._running = True
        while self._running:
            if self.task_queue:
                task = self.task_queue.popleft()
                asyncio.create_task(self._execute_task_with_semaphore(task))
            await asyncio.sleep(0.01)
    
    async def _execute_task_with_semaphore(self, task: Task):
        async with self._semaphore:
            self.active_tasks[task.id] = task
            try:
                await task.execute_async()
            finally:
                del self.active_tasks[task.id]
                self.completed_tasks[task.id] = task
                if len(self.completed_tasks) > 10000:
                    old_tasks = list(self.completed_tasks.keys())[:5000]
                    for tid in old_tasks:
                        del self.completed_tasks[tid]
    
    def submit_async(self, func: Callable, *args, **kwargs) -> Optional[str]:
        if len(self.task_queue) >= self.queue_limit:
            concurrency_logger.warning("Task queue is full, rejecting task")
            return None
        
        task = Task(func, *args, **kwargs)
        self.task_queue.append(task)
        concurrency_logger.debug(f"Task {task.id} submitted, queue size: {len(self.task_queue)}")
        return task.id
    
    def submit_sync(self, func: Callable, *args, **kwargs) -> Optional[Any]:
        future = self.executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=config.CONCURRENCY_SETTINGS["task_timeout"])
        except Exception as e:
            concurrency_logger.error(f"Sync task failed: {str(e)}")
            return None
    
    def submit_batch(self, tasks: List[Dict]) -> List[str]:
        task_ids = []
        for task_spec in tasks:
            if len(self.task_queue) >= self.queue_limit:
                concurrency_logger.warning("Task queue full during batch submission")
                break
            func = task_spec["func"]
            args = task_spec.get("args", ())
            kwargs = task_spec.get("kwargs", {})
            task_id = self.submit_async(func, *args, **kwargs)
            if task_id:
                task_ids.append(task_id)
        concurrency_logger.info(f"Batch submitted {len(task_ids)} tasks")
        return task_ids
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            return {
                "id": task.id,
                "status": task.status,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "duration": time.time() - task.started_at if task.started_at else 0
            }
        elif task_id in self.completed_tasks:
            task = self.completed_tasks[task_id]
            return {
                "id": task.id,
                "status": task.status,
                "result": task.result,
                "error": task.error,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "duration": task.completed_at - task.started_at if task.completed_at and task.started_at else 0
            }
        return None
    
    def get_queue_stats(self) -> Dict:
        return {
            "queue_size": len(self.task_queue),
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len(self.completed_tasks),
            "max_workers": self.max_workers,
            "queue_limit": self.queue_limit
        }
    
    async def run(self):
        self._loop = asyncio.get_event_loop()
        await self._start_async_loop()
    
    def shutdown(self):
        self._running = False
        self.executor.shutdown(wait=True)
        concurrency_logger.info("Concurrent task manager shutdown")


task_manager = ConcurrentTaskManager()


def run_concurrent_async(funcs: List[Callable], max_concurrent: int = None) -> List[Any]:
    if max_concurrent is None:
        max_concurrent = config.CONCURRENCY_SETTINGS["max_workers"]
    
    results = []
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def bounded_exec(func):
        async with semaphore:
            return await func() if asyncio.iscoroutinefunction(func) else func()
    
    async def run_all():
        tasks = [bounded_exec(func) for func in funcs]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    return asyncio.run(run_all())


def run_concurrent_sync(funcs: List[Callable], max_workers: int = None) -> List[Any]:
    if max_workers is None:
        max_workers = config.CONCURRENCY_SETTINGS["max_workers"]
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(func) for func in funcs]
        results = []
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                results.append(e)
                concurrency_logger.error(f"Concurrent task error: {str(e)}")
        return results
