import platform
import sys
from pathlib import Path

TS_LIB_PATH = Path(__file__).parent.parent.parent / "lib"


def get_builtin_lib_path(parent_dir: Path = TS_LIB_PATH) -> Path:
    if sys.platform.startswith("linux"):
        machine = platform.machine()
        if machine not in {"x86_64", "amd64"}:
            raise RuntimeError("graphlocator_gcc bundles only Linux x86_64 tree-sitter library; got " + machine)
        lib_path = parent_dir / "languages-linux-x86_64.so"
    else:
        raise RuntimeError("graphlocator_gcc bundles only Linux x86_64 tree-sitter library; got " + sys.platform)
    return lib_path
