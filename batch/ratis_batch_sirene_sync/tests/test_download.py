"""Tests for sirene_sync.download — ensure_dump() caching + re-download logic.

All HTTP calls are mocked via ``unittest.mock.patch``; no real network access.
"""

from __future__ import annotations

import io
import time
import zipfile
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip_with_csv(csv_content: str) -> bytes:
    """Build an in-memory ZIP containing a single CSV file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("StockEtablissement_utf8.csv", csv_content)
    return buf.getvalue()


def _minimal_csv() -> str:
    """Minimal SIRENE CSV with 3 rows (enough to produce a valid Parquet)."""
    header = (
        "siret,siren,nic,etatAdministratifEtablissement,"
        "activitePrincipaleEtablissement,enseigne1Etablissement,"
        "denominationUsuelleEtablissement,denominationUniteLegale,"
        "numeroVoie,typeVoie,libelleVoie,libelleCommuneEtablissement,"
        "codePostalEtablissement,categorieJuridiqueUniteLegale,"
        "dateFermetureEtablissement"
    )
    rows = [
        "12345678901234,123456789,01234,A,47.11B,Epice Test,,SAS TEST,1,RUE,DE LA PAIX,PARIS,75001,5499,",
        "23456789012345,234567890,02345,F,47.11D,,Denom Usuelle,SAS TEST2,,,,,75002,5499,2023-06-01",
    ]
    return "\n".join([header, *rows])


def _fake_stream_response(zip_bytes: bytes):
    """Build a mock httpx response that streams ``zip_bytes``."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    chunk_size = 65536
    chunks = [zip_bytes[i : i + chunk_size] for i in range(0, len(zip_bytes), chunk_size)]

    def _iter_bytes(chunk_size=65536):
        yield from chunks

    mock_resp.iter_bytes = _iter_bytes
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnsureDump:
    def test_download_caches_when_fresh(self, tmp_path):
        """A fresh cache (mtime < ttl_days) must not trigger a re-download."""
        from sirene_sync.download import ensure_dump

        # Simulate an existing fresh Parquet file.
        parquet_path = tmp_path / "StockEtablissement_utf8.parquet"
        table = pa.table({"col": pa.array(["a", "b"], type=pa.string())})
        pq.write_table(table, str(parquet_path))
        # Touch mtime to "now".
        parquet_path.touch()

        with patch("httpx.stream") as mock_stream:
            result = ensure_dump(tmp_path, "http://fake/dump.zip", ttl_days=30)

        assert result == parquet_path
        mock_stream.assert_not_called()

    def test_download_refetch_when_stale(self, tmp_path):
        """A stale cache (mtime >= ttl_days) must trigger a re-download."""
        from sirene_sync.download import ensure_dump

        parquet_path = tmp_path / "StockEtablissement_utf8.parquet"
        table = pa.table({"col": pa.array(["old"], type=pa.string())})
        pq.write_table(table, str(parquet_path))

        # Artificially age the file by setting mtime to 40 days ago.
        old_mtime = time.time() - 40 * 86400
        import os

        os.utime(parquet_path, (old_mtime, old_mtime))

        zip_bytes = _make_zip_with_csv(_minimal_csv())
        mock_resp = _fake_stream_response(zip_bytes)

        with patch("httpx.stream", return_value=mock_resp) as mock_stream:
            result = ensure_dump(tmp_path, "http://fake/dump.zip", ttl_days=30)

        assert result == parquet_path
        mock_stream.assert_called_once()

    def test_download_force_flag(self, tmp_path):
        """force=True must re-download even when the cache is fresh."""
        from sirene_sync.download import ensure_dump

        parquet_path = tmp_path / "StockEtablissement_utf8.parquet"
        table = pa.table({"col": pa.array(["fresh"], type=pa.string())})
        pq.write_table(table, str(parquet_path))
        parquet_path.touch()

        zip_bytes = _make_zip_with_csv(_minimal_csv())
        mock_resp = _fake_stream_response(zip_bytes)

        with patch("httpx.stream", return_value=mock_resp) as mock_stream:
            result = ensure_dump(tmp_path, "http://fake/dump.zip", ttl_days=30, force=True)

        assert result == parquet_path
        mock_stream.assert_called_once()

    def test_download_creates_parquet_from_csv(self, tmp_path):
        """Downloading + converting must produce a valid readable Parquet file."""
        from sirene_sync.download import ensure_dump

        zip_bytes = _make_zip_with_csv(_minimal_csv())
        mock_resp = _fake_stream_response(zip_bytes)

        with patch("httpx.stream", return_value=mock_resp):
            result = ensure_dump(tmp_path, "http://fake/dump.zip", ttl_days=30)

        assert result.exists()
        table = pq.read_table(str(result))
        # The CSV has 2 data rows.
        assert len(table) == 2
        # SIRET values are preserved as strings (leading-zero safety).
        sirets = table.column("siret").to_pylist()
        assert "12345678901234" in sirets

    def test_download_removes_zip_after_conversion(self, tmp_path):
        """The intermediate ZIP and CSV files must be cleaned up after conversion."""
        from sirene_sync.download import ensure_dump

        zip_bytes = _make_zip_with_csv(_minimal_csv())
        mock_resp = _fake_stream_response(zip_bytes)

        with patch("httpx.stream", return_value=mock_resp):
            ensure_dump(tmp_path, "http://fake/dump.zip", ttl_days=30)

        zip_path = tmp_path / "StockEtablissement_utf8.zip"
        csv_path = tmp_path / "StockEtablissement_utf8.csv"
        assert not zip_path.exists(), "ZIP must be deleted after conversion"
        assert not csv_path.exists(), "CSV must be deleted after conversion"
