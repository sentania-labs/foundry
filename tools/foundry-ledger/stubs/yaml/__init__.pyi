# Minimal local typing stub for the parts of PyYAML this tool uses.
# Kept in-tree so mypy --strict passes without a stubs package dependency.
from typing import Any, IO

def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any: ...
def safe_dump(
    data: Any,
    stream: IO[str] | None = ...,
    *,
    default_flow_style: bool | None = ...,
    sort_keys: bool = ...,
    allow_unicode: bool = ...,
    width: int | None = ...,
    indent: int | None = ...,
) -> str: ...

class YAMLError(Exception): ...
