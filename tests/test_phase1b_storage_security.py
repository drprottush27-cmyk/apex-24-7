"""Phase 1B: FileStorage security hardening tests.

Tests cover: path traversal, symlink attacks, absolute paths, unsafe
filenames, null bytes, directory escape, dot-only names, and safe
normal operations.
"""
from pathlib import Path

import pytest

from apex.models.market import (
    OrderBook,
    OrderBookLevel,
    ProviderName,
    Symbol,
    Ticker,
)
from apex.storage.impl import FileStorage


class TestFileStoragePathTraversal:
    """Path traversal attack resistance."""

    def _make_storage(self, tmp_path: Path) -> FileStorage:
        return FileStorage(str(tmp_path / "storage"))

    @pytest.mark.asyncio
    async def test_dot_dot_in_symbol_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="path separator|'..'"):
            s._safe_path("ticker_../../../etc/passwd.json")

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="path separator"):
            s._safe_path("/etc/passwd")

    @pytest.mark.asyncio
    async def test_null_byte_injection_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="null bytes"):
            s._safe_path("ticker_BTCUSDT\x00.json")

    @pytest.mark.asyncio
    async def test_dot_only_name_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="dot-only"):
            s._safe_path(".")
        with pytest.raises(ValueError, match="dot-only"):
            s._safe_path("..")
        with pytest.raises(ValueError, match="dot-only"):
            s._safe_path("...")

    @pytest.mark.asyncio
    async def test_empty_filename_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="empty"):
            s._safe_path("")
        with pytest.raises(ValueError, match="empty"):
            s._safe_path("   ")

    @pytest.mark.asyncio
    async def test_slash_in_filename_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="path separator"):
            s._safe_path("sub/file.json")

    @pytest.mark.asyncio
    async def test_backslash_in_filename_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="path separator"):
            s._safe_path("sub\\file.json")

    @pytest.mark.asyncio
    async def test_long_filename_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        long_name = "a" * 201 + ".json"
        with pytest.raises(ValueError, match="exceeds"):
            s._safe_path(long_name)

    @pytest.mark.asyncio
    async def test_unsafe_characters_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        for c in ["<", ">", ":", '"', "|", "?", "*", " ", "\t", "\n"]:
            with pytest.raises(ValueError, match="unsafe characters"):
                s._safe_path(f"ticker_{c}test.json")

    @pytest.mark.asyncio
    async def test_symlink_escape_rejected(self, tmp_path: Path):
        """Symlink pointing outside storage root is rejected."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        # Create symlink inside storage pointing outside
        evil_link = storage_dir / "evil.json"
        evil_link.symlink_to("/etc/passwd")
        s = FileStorage(str(storage_dir))
        with pytest.raises(ValueError, match="escapes storage root|symlink.*outside"):
            s._safe_path("evil.json")

    @pytest.mark.asyncio
    async def test_dotdot_component_in_name_rejected(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        with pytest.raises(ValueError, match="'..'"):
            s._safe_path("file..traversal.json")

    @pytest.mark.asyncio
    async def test_safe_filename_accepted(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        # These should NOT raise
        p = s._safe_path("ticker_BTCUSDT.json")
        assert p.name == "ticker_BTCUSDT.json"

    @pytest.mark.asyncio
    async def test_safe_filename_with_underscore_dash(self, tmp_path: Path):
        s = self._make_storage(tmp_path)
        p = s._safe_path("candles_BTC-USDT_1h.json")
        assert p.name == "candles_BTC-USDT_1h.json"


class TestFileStorageResolveConfinement:
    """Ensure resolved paths are within storage root."""

    @pytest.mark.asyncio
    async def test_resolved_path_within_root(self, tmp_path: Path):
        s = FileStorage(str(tmp_path / "data"))
        p = s._safe_path("test.json")
        root = (tmp_path / "data").resolve()
        assert str(p).startswith(str(root))

    @pytest.mark.asyncio
    async def test_storage_creates_directory(self, tmp_path: Path):
        storage_dir = tmp_path / "deep" / "nested" / "storage"
        s = FileStorage(str(storage_dir))
        assert storage_dir.exists()

    @pytest.mark.asyncio
    async def test_save_and_load_with_safe_paths(self, tmp_path: Path):
        """End-to-end: safe filenames work correctly."""
        s = FileStorage(str(tmp_path / "data"))
        t = Ticker(
            symbol=Symbol("BTCUSDT"),
            provider=ProviderName.BINANCE,
            last_price="64000",
            bid="63999",
            ask="64001",
            high_24h="65000",
            low_24h="63000",
            volume_24h="1000",
            timestamp_ms=1_700_000_000_000,
        )
        await s.save_ticker(t)
        loaded = await s.get_ticker(Symbol("BTCUSDT"))
        assert loaded is not None
        assert loaded.last_price == t.last_price

    @pytest.mark.asyncio
    async def test_order_book_roundtrip_with_safe_paths(self, tmp_path: Path):
        s = FileStorage(str(tmp_path / "data"))
        book = OrderBook(
            symbol=Symbol("ETHUSDT"),
            provider=ProviderName.OKX,
            bids=(OrderBookLevel("3000", "1"),),
            asks=(OrderBookLevel("3001", "2"),),
            timestamp_ms=1_700_000_000_000,
        )
        await s.save_order_book(book)
        loaded = await s.get_order_book(Symbol("ETHUSDT"))
        assert loaded is not None
        assert loaded.bids[0].price == book.bids[0].price
