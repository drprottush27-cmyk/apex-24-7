from abc import ABC, abstractmethod
from typing import List
from .models import StrategyLifecycle, Signal
from src.scanner.models import MarketDataSummary

class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    Strategies process validated MarketDataSummaries and produce pure Signals.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The unique identifier for this strategy."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic versioning of the strategy logic."""
        pass

    @property
    @abstractmethod
    def lifecycle_state(self) -> StrategyLifecycle:
        """The current authorized deployment state of the strategy."""
        pass

    @abstractmethod
    def generate_signals(self, market_data: List[MarketDataSummary]) -> List[Signal]:
        """
        Analyze market data and generate read-only signals.
        Never side-effects. Never executes trades.
        """
        pass
