from pathlib import Path


def test_readme_documents_manual_ledger_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "qt ledger add" in readme
    assert "qt ledger list" in readme
    assert "qt service check" in readme
    assert "不会自动下单" in readme
