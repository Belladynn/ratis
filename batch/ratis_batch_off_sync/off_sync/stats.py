"""Shared Stats dataclass for Open*Facts sync runs (multi-source)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Stats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0

    def add(self, inserted: int, updated: int, skipped: int, invalid: int) -> None:
        self.inserted += inserted
        self.updated += updated
        self.skipped += skipped
        self.invalid += invalid

    def __str__(self) -> str:
        return (
            f"{self.inserted} inserted, {self.updated} updated, "
            f"{self.skipped} skipped (source mismatch), {self.invalid} invalid"
        )
