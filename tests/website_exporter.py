"""Import of website/scripts/export_providers.py, a script outside any package."""

import functools
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[1] / "website" / "scripts" / "export_providers.py"


@functools.cache
def load_exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_providers", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass resolves annotations through sys.modules[__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
