from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp


@contextmanager
def closed_temporary_upload(content: bytes, *, suffix: str) -> Iterator[Path]:
    descriptor, raw_path = mkstemp(suffix=suffix)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        yield path
    finally:
        path.unlink(missing_ok=True)
