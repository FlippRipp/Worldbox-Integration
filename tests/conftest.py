import asyncio
import inspect
import sys
from pathlib import Path

# Tests import the module code directly, no WorldboxAI checkout needed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wb_toy_link"))


def pytest_pyfunc_call(pyfuncitem):
    """Run async test functions directly via asyncio when pytest-asyncio isn't installed."""
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None
    testfunction = pyfuncitem.obj
    if inspect.iscoroutinefunction(testfunction):
        sig = inspect.signature(testfunction)
        kwargs = {
            name: pyfuncitem.funcargs[name]
            for name in sig.parameters
            if name in pyfuncitem.funcargs
        }
        asyncio.run(testfunction(**kwargs))
        return True
    return None

