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
        self.status = "queued"
        self.result = None
        self.error = None
        self.created_at = time.time()
        self.started_at = None
        self.completed_at = None
        self.timeout = kwargs.pop("timeout", config.CONCURRENCY_SETTINGS["task_timeout"])
        self.priority = kwargs.pop("priority", 5)
    
    def execute_sync(self):
        self.status = "running"
        self.started_at = time.time()
        try:
            if asyncio.iscoroutinefunction(self.func):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.result = loop.run_until_complete(
                        asyncio.wait_for(
                            self.func(*self.args, **self.kwargs),
                            timeout=self.timeout
                        )
                    )
                finally:
                    loop.close()
            else:
                self.result = self.func(*self.args, **self.kwargs)
            self.status = "completed"
        except asyncio.TimeoutError:
            self.status = "timeout"
            self.error = f"Task timed out after {self.timeout} seconds"
            concurrency_logger.error(f"Task {self.id} timed out: {self.error}")
        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            concurrency_logger.error(f"Task {self.id} failed: {str(e)}", exc_info=True)
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
        self.failed_tasks: Dict[str, Task] = {}
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._queue_lock = threading.Lock()
        self._start_worker()
    
    def _start_worker(self):
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="TaskQueueWorker")
        self._worker_thread.start()
        concurrency_logger.info("Concurrent task manager worker thread started")
    
    def _worker_loop(self):
        while self._running:
            try:
                task = None
                with self._queue_lock:
                    if self.task_queue:
                        task = self.task_queue.popleft()
                
                if task:
                    self._execute_task(task)
                else:
                    time.sleep(0.05)
            except Exception as e:
                concurrency_logger.error(f"Worker loop error: {str(e)}", exc_info=True)
                time.sleep(0.1)
    
    def _execute_task(self, task: Task):
        self.active_tasks[task.id] = task
        try:
            future = self.executor.submit(task.execute_sync)
            try:
                future.result(timeout=task.timeout + 10)
            except Exception as e:
                if task.status == "running":
                    task.status = "failed"
                    task.error = f"Execution error: {str(e)}"
                    task.completed_at = time.time()
                    concurrency_logger.error(f"Task {task.id} execution error: {str(e)}")
        finally:
            if task.id in self.active_tasks:
                del self.active_tasks[task.id]
            if task.status in ("completed",):
                self.completed_tasks[task.id] = task
            else:
                self.failed_tasks[task.id] = task
                self.completed_tasks[task.id] = task
            
            total_completed = len(self.completed_tasks)
            if total_completed > 10000:
                old_tasks = list(self.completed_tasks.keys())[:5000]
                for tid in old_tasks:
                    if tid in self.completed_tasks:
                        del self.completed_tasks[tid]
                    if tid in self.failed_tasks:
                        del self.failed_tasks[tid]
    
    def submit_async(self, func: Callable, *args, **kwargs) -> Optional[str]:
        with self._queue_lock:
            if len(self.task_queue) >= self.queue_limit:
                concurrency_logger.warning("Task queue is full, rejecting task")
                return None
            
            task = Task(func, *args, **kwargs)
            self.task_queue.append(task)
        
        concurrency_logger.info(f"Task {task.id} submitted, queue size: {len(self.task_queue)}, func: {func.__name__ if hasattr(func, '__name__') else str(func)}")
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
        with self._queue_lock:
            for task_spec in tasks:
                if len(self.task_queue) >= self.queue_limit:
                    concurrency_logger.warning("Task queue full during batch submission")
                    break
                func = task_spec["func"]
                args = task_spec.get("args", ())
                kwargs = task_spec.get("kwargs", {})
                task = Task(func, *args, **kwargs)
                self.task_queue.append(task)
                task_ids.append(task.id)
        
        concurrency_logger.info(f"Batch submitted {len(task_ids)} tasks, queue size: {len(self.task_queue)}")
        return task_ids
    
    def get_task_status(self, task_id: str) -> Optional[Dict]:
        with self._queue_lock:
            for task in self.task_queue:
                if task.id == task_id:
                    return {
                        "id": task.id,
                        "status": task.status,
                        "created_at": task.created_at,
                        "started_at": None,
                        "duration": 0,
                        "result": None,
                        "error": None,
                        "completed_at": None
                    }
        
        if task_id in self.active_tasks:
            task = self.active_tasks[task_id]
            return {
                "id": task.id,
                "status": task.status,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "duration": time.time() - task.started_at if task.started_at else 0,
                "result": None,
                "error": None,
                "completed_at": None
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
    
    def wait_for_task(self, task_id: str, timeout: float = 60) -> Optional[Dict]:
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_task_status(task_id)
            if status and status["status"] in ("completed", "failed", "timeout"):
                return status
            time.sleep(0.1)
        concurrency_logger.warning(f"Wait for task {task_id} timed out after {timeout}s")
        return self.get_task_status(task_id)
    
    def wait_for_batch(self, task_ids: List[str], timeout: float = 120) -> List[Dict]:
        results = []
        for task_id in task_ids:
            result = self.wait_for_task(task_id, timeout)
            results.append(result)
        return results
    
    def get_queue_stats(self) -> Dict:
        with self._queue_lock:
            queue_size = len(self.task_queue)
        return {
            "queue_size": queue_size,
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len(self.completed_tasks),
            "failed_tasks": len(self.failed_tasks),
            "max_workers": self.max_workers,
            "queue_limit": self.queue_limit
        }
    
    def shutdown(self):
        self._running = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)
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
