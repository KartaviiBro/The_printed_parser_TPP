# scraper/__init__.py
"""Scraper package and platform registry.

To add a new platform: create ``scraper/<name>.py`` with a ``BaseScraper``
subclass, then register it here. ``main.py`` and the web UI pick it up
automatically.
"""
from __future__ import annotations

from scraper.base import BaseScraper, ScrapedModel, deep_find
from scraper.cults3d import Cults3DScraper
from scraper.makerworld import MakerWorldScraper
from scraper.printables import PrintablesScraper

# name -> scraper class.
SCRAPERS: dict[str, type[BaseScraper]] = {
    PrintablesScraper.source: PrintablesScraper,
    MakerWorldScraper.source: MakerWorldScraper,
    Cults3DScraper.source: Cults3DScraper,
}

__all__ = [
    "BaseScraper",
    "ScrapedModel",
    "deep_find",
    "PrintablesScraper",
    "MakerWorldScraper",
    "Cults3DScraper",
    "SCRAPERS",
]
