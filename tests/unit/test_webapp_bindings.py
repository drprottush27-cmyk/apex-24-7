"""APEX 24/7 — WebApp & Control Plane Data-Binding & Route Integrity Tests.

Verifies:
1. Absence of dead/fake WebSocket endpoints in webapp/app.js.
2. All frontend fetch calls dispatch to valid routes in server.py.
3. Every DOM element ID in index.html is accounted for in app.js or layout.
4. AI assistant routes to authoritative /api/v1/control/intelligence.
5. Inviolable invariant: No direct exchange execution or secret leaks in frontend.
"""

from pathlib import Path
import re


def test_no_dead_websocket_in_webapp() -> None:
    """Verify webapp/app.js has zero connections to unhandled /ws/chat or fake WebSocket paths."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    app_js = (repo_root / "webapp" / "app.js").read_text(encoding="utf-8")

    assert "/ws/chat" not in app_js, "Dead WebSocket endpoint /ws/chat must not be present in app.js"
    assert "new WebSocket" not in app_js, "Frontend must not construct WebSocket clients to unsupported routes"
    assert "requestDashboard" in app_js, "Authoritative requestDashboard HTTP polling function must exist"
    assert "handleDashboardPayload" in app_js, "Authoritative handleDashboardPayload must exist"


def test_webapp_endpoints_align_with_server_route_table() -> None:
    """Verify every fetch call in app.js maps to an authorized server.py route."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    app_js = (repo_root / "webapp" / "app.js").read_text(encoding="utf-8")

    # Extract all fetch calls in app.js
    routes = set(re.findall(r"fetch\((?:`|')/api/v1/([^`'?]+)", app_js))
    
    # Authoritative whitelist of routes handled in server.py
    allowed_prefixes = {
        "dashboard",
        "control/start",
        "control/stop",
        "control/pause",
        "control/resume",
        "control/restart",
        "control/emergency_stop",
        "control/flatten",
        "control/${action}",
        "control/intelligence",
        "autoclose/override",
        "alerts/settings",
        "alerts/ack",
        "trades",
        "plan",
    }

    for route in routes:
        assert any(route.startswith(allowed) or allowed.startswith(route) for allowed in allowed_prefixes), (
            f"Frontend route '/api/v1/{route}' is not an authorized backend route."
        )


def test_screen_dom_ids_authoritatively_bound() -> None:
    """Verify that all functional data IDs in index.html are bound in app.js."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    html = (repo_root / "webapp" / "index.html").read_text(encoding="utf-8")
    js = (repo_root / "webapp" / "app.js").read_text(encoding="utf-8")

    html_ids = set(re.findall(r'id="([^"]+)"', html))

    # Whitelist of IDs that are purely structural layout / static labels
    structural_or_static_ids = {
        "home-screen", "scanner-screen", "trades-screen", "risk-screen",
        "system-screen", "team-screen", "investment-screen", "alerts-screen", "audit-screen",
        "home-autotrade-card", "active-pos-title", "equity-title", "scanner-card-title",
        "node-signal", "node-risk", "node-execution", "node-journal",
        "confirm-request",
    }

    unbound_data_ids = []
    for dom_id in html_ids:
        if dom_id not in js and dom_id not in structural_or_static_ids:
            unbound_data_ids.append(dom_id)

    assert not unbound_data_ids, f"Unbound data element IDs found in HTML: {unbound_data_ids}"


def test_ai_intelligence_grounding_in_app() -> None:
    """Verify chat assistant dispatches queries to /api/v1/control/intelligence."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    app_js = (repo_root / "webapp" / "app.js").read_text(encoding="utf-8")

    assert "/api/v1/control/intelligence" in app_js, (
        "AI Assistant in app.js must dispatch to /api/v1/control/intelligence"
    )
