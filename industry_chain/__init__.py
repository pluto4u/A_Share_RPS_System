"""Structured industry-chain research and bottleneck scoring."""

from .database import IndustryChainDB, initialize_database
from .scoring import rank_company_exposures

__all__ = ["IndustryChainDB", "initialize_database", "rank_company_exposures"]
