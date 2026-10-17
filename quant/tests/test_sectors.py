"""Sector cache: the parquet lands by temp file + rename, so a failed write
keeps the previous cache intact."""

import pandas as pd
import pytest

from quant.data import sectors


def test_failed_save_keeps_the_old_file(monkeypatch, tmp_path):
    path = tmp_path / "sectors.parquet"
    monkeypatch.setattr(sectors.config, "UNIVERSE_DIR", tmp_path)
    monkeypatch.setattr(sectors, "SECTORS_PARQUET", path)
    sectors._save({"AAA": "Tech"})
    before = path.read_bytes()

    real = pd.DataFrame.to_parquet

    def half_written(self, target, *a, **kw):
        real(self.head(0), target, *a, **kw)
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", half_written)
    with pytest.raises(OSError):
        sectors._save({"AAA": "Tech", "BBB": "Energy"})
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp-*")) == []
    monkeypatch.undo()
    monkeypatch.setattr(sectors, "SECTORS_PARQUET", path)
    assert sectors.load_sectors() == {"AAA": "Tech"}
