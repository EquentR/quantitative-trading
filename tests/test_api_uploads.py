from pathlib import Path

import pytest

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
