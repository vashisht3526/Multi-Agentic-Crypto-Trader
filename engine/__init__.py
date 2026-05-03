"""
Kairos Engine - Core trading system package.

Subpackages:
    engine.analysis  - Technical analysis (ATR, ADX, NOFI, multi-TF)
    engine.data      - Market data ingestion (Binance WebSocket + REST)
    engine.agents    - LLM advisory agents (TrendStrategist, RiskEngine)
"""

from engine.database import FastTickDatabase
from engine.backbone import HierarchicalBackbone
from engine import config
from engine.settings import Settings, load_settings

__all__ = [
    "FastTickDatabase",
    "HierarchicalBackbone",
    "config",
    "Settings",
    "load_settings",
]
