import pytest
from pathlib import Path
from aaata_bus.definitions import ensure_dat
import aaata_bus.definitions as definitions

def test_ensure_dat_success(tmp_path, monkeypatch):
    data_dir = tmp_path / "dat"
    data_dir.mkdir()
    monkeypatch.setattr(definitions, "DATA_PATH", data_dir)
    assert ensure_dat() == data_dir

def test_ensure_dat_failure(tmp_path, monkeypatch):
    missing_dir = tmp_path / "nonexistent"
    monkeypatch.setattr(definitions, "DATA_PATH", missing_dir)
    with pytest.raises(AssertionError, match="No data directory found"):
        ensure_dat()
