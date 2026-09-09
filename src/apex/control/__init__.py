"""APEX Unified Control Plane package."""
from apex.control.events import ControlEvent, ControlPlaneEventBus
from apex.control.investment import InvestmentResearchManager, InvestmentThesis
from apex.control.plane import ApexControlPlane, get_control_plane, set_control_plane
from apex.control.reports import ExecutiveReport, ReportManager
from apex.control.state import (
    ControlPlaneState,
    ControlPlaneStateMachine,
    ControlPlaneStateSnapshot,
    TradingMode,
)
from apex.control.team import AgentState, ManagementTeam
from apex.control.xai import GrokIntelligenceClient

__all__ = [
    "ApexControlPlane",
    "get_control_plane",
    "set_control_plane",
    "ControlPlaneState",
    "ControlPlaneStateMachine",
    "ControlPlaneStateSnapshot",
    "TradingMode",
    "ControlEvent",
    "ControlPlaneEventBus",
    "ManagementTeam",
    "AgentState",
    "InvestmentResearchManager",
    "InvestmentThesis",
    "ReportManager",
    "ExecutiveReport",
    "GrokIntelligenceClient",
]
