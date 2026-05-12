import asyncio
import logging
from datetime import datetime
from typing import Callable, Any

# logger config
logger = logging.getLogger("EdgeConfigAPI")


# Helper function to create and run a periodic task
async def create_periodic_task(
    func: Callable,
    interval: float,
    initial_delay: float = 0,
    *args: Any,
    **kwargs: Any
) -> None:
    """
    Run a function periodically at the specified interval (in seconds).
    """
    while True:
        try:
            if initial_delay > 0:
                await asyncio.sleep(initial_delay)
                initial_delay = 0

            # Execute the function
            start_time = datetime.now()
            logger.info(f"Running periodic task {func.__name__}")
            
            # Support both async and non-async functions
            if asyncio.iscoroutinefunction(func):
                await func(*args, **kwargs)
            else:
                func(*args, **kwargs)
                
            logger.info(f"Task {func.__name__} completed in {(datetime.now() - start_time).total_seconds():.2f}s")
            
            # Wait for the next interval
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info(f"Task {func.__name__} was cancelled")
            break
        except Exception as e:
            logger.error(f"Error in task {func.__name__}: {e}")
            # Still wait before retrying
            await asyncio.sleep(interval)
