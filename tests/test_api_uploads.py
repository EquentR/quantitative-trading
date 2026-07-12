import os
from pathlib import Path
from tempfile import mkstemp

import pytest

from quantitative_trading.api import uploads
from quantitative_trading.api.uploads import closed_temporary_upload


def test_closed_temporary_upload_is_reopenable_and_removed_after_error() -> None:
    captured_path: Path | None = None

    with pytest.raises(RuntimeError, match="stop"):
        with closed_temporary_upload(b"symbol,name\n600000,test\n", suffix=".csv") as path:
            captured_path = path
            assert path.read_bytes().startswith(b"symbol")
            raise RuntimeError("stop")

    assert captured_path is not None
    assert not captured_path.exists()


def test_closed_temporary_upload_is_removed_after_normal_exit() -> None:
    captured_path: Path | None = None

    with closed_temporary_upload(b"symbol,name\n600000,test\n", suffix=".csv") as path:
        captured_path = path
        assert path.read_bytes().startswith(b"symbol")

    assert captured_path is not None
    assert not captured_path.exists()


def test_closed_temporary_upload_closes_descriptor_when_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor, raw_path = mkstemp(suffix=".csv")
    path = Path(raw_path)

    monkeypatch.setattr(uploads, "mkstemp", lambda *, suffix: (descriptor, raw_path))

    def fail_fdopen(*args: object, **kwargs: object) -> None:
        raise OSError("fdopen setup failed")

    monkeypatch.setattr(uploads.os, "fdopen", fail_fdopen)

    try:
        with pytest.raises(OSError, match="fdopen setup failed"):
            with closed_temporary_upload(b"symbol,name\n600000,test\n", suffix=".csv"):
                pass

        assert not path.exists()
        with pytest.raises(OSError):
            os.fstat(descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
