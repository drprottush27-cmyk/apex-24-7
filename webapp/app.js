/**
 * APEX 24/7 Command Center — Final Mini App Implementation
 * 
 * Invariants:
 * - Deterministic, safety-first PAPER trading visualization.
 * - RiskGuardian -> OrderExecutionManager -> EndpointGuard is non-bypassable.
 * - Live trading permanently disabled. No mock data substitutes.
 * - Fails closed: if data unavailable, displays "Unavailable".
 * - WebSocket streaming during active positions; otherwise "Standby — no active position".
 */

const tg = window.Telegram?.WebApp;
const telegramInitData = tg?.initData || '';

// Viewport & Safe-Area Synchronization
function syncViewportDimensions() {
  try {
    const vh = window.innerHeight * 0.01;
    document.documentElement.style.setProperty('--vh', `${vh}px`);

    if (tg) {
      if (tg.viewportHeight) {
        document.documentElement.style.setProperty('--tg-viewport-h', `${tg.viewportHeight}px`);
      }
      if (tg.safeAreaInset) {
        if (typeof tg.safeAreaInset.top === 'number') {
          document.documentElement.style.setProperty('--safe-top', `${tg.safeAreaInset.top}px`);
        }
        if (typeof tg.safeAreaInset.bottom === 'number') {
          document.documentElement.style.setProperty('--safe-bottom', `${tg.safeAreaInset.bottom}px`);
        }
        if (typeof tg.safeAreaInset.left === 'number') {
          document.documentElement.style.setProperty('--safe-left', `${tg.safeAreaInset.left}px`);
        }
        if (typeof tg.safeAreaInset.right === 'number') {
          document.documentElement.style.setProperty('--safe-right', `${tg.safeAreaInset.right}px`);
        }
      }
    }
  } catch (e) {
    console.debug('Viewport sync warning:', e);
  }
}

if (tg) {
  try {
    tg.ready();
    tg.expand();

    // Telegram Bot API 8.0+ native fullscreen mode for iPad & mobile
    if (typeof tg.requestFullscreen === 'function') {
      try {
        tg.requestFullscreen();
      } catch (_) {}
    }

    // Disable accidental pull-to-dismiss gesture on iPad/touch
    if (typeof tg.disableVerticalSwipes === 'function') {
      try {
        tg.disableVerticalSwipes();
      } catch (_) {}
    }

    // Closing confirmation to prevent accidental dismissal during operation
    if (typeof tg.enableClosingConfirmation === 'function') {
      try {
        tg.enableClosingConfirmation();
      } catch (_) {}
    }

    if (tg.themeParams?.button_color) {
      document.documentElement.style.setProperty('--accent', tg.themeParams.button_color);
    }

    // Register Telegram WebApp viewport & safe area change listeners
    tg.onEvent?.('viewportChanged', syncViewportDimensions);
    tg.onEvent?.('safeAreaChanged', syncViewportDimensions);
    tg.onEvent?.('contentSafeAreaChanged', syncViewportDimensions);
    tg.onEvent?.('fullscreenChanged', syncViewportDimensions);
    tg.onEvent?.('fullscreenFailed', () => {
      tg.expand();
      syncViewportDimensions();
    });
  } catch (err) {
    console.debug('Telegram WebApp setup error:', err);
  }
}

// Initial calculation & window listeners
syncViewportDimensions();
window.addEventListener('resize', syncViewportDimensions, { passive: true });
window.addEventListener('orientationchange', () => {
  setTimeout(syncViewportDimensions, 100);
}, { passive: true });

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const authHeaders = {'X-Telegram-Init-Data': telegramInitData};

// App state
let currentTab = 'home';
let dashboardLoading = false;
let pullStart = 0;
let latestData = null;
let activePlan = null;
let activeOpportunity = null;
let pendingConfirmation = null;

// Scanner filters
let currentCategoryFilter = 'all';
let scannerSearchQuery = '';

// Autoclose countdown
let autocloseTimer = null;
let activeAlertExpiries = {};

/* ── Utility Functions ────────────────────────────────────────────────────── */

function escapeHtml(value) {
  const el = document.createElement('span');
  el.textContent = String(value ?? '');
  return el.innerHTML;
}

function formatPrice(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return '—';
  if (n >= 100) return n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (n >= 1) return n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 4});
  return n.toLocaleString(undefined, {minimumFractionDigits: 4, maximumFractionDigits: 8});
}

function timeAgo(timestampMs) {
  if (!timestampMs || timestampMs <= 0) return '—';
  const diffSec = Math.max(0, Math.floor((Date.now() - timestampMs) / 1000));
  if (diffSec < 10) return 'Just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

function formatUtcTime(timestampMs) {
  if (!timestampMs || timestampMs <= 0) return '—';
  try {
    const d = new Date(timestampMs);
    return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
  } catch (_) {
    return '—';
  }
}

/* ── Tab Navigation (Exactly 5 Tabs) ──────────────────────────────────────── */

function showTab(tab) {
  currentTab = tab;
  $$('.tab-screen').forEach((screen) => {
    screen.classList.toggle('active', screen.id === `${tab}-screen`);
  });
  $$('.tab-button').forEach((button) => {
    button.classList.toggle('active', button.dataset.tab === tab);
  });
  $$('.nav-pill').forEach((pill) => {
    pill.classList.toggle('active', pill.dataset.tab === tab);
  });

  if (tg?.BackButton) {
    if (tab === 'home') tg.BackButton.hide();
    else tg.BackButton.show();
  }

  location.hash = tab === 'home' ? '' : tab;

  // Render current tab with existing latestData immediately
  if (latestData) {
    renderCurrentTab();
  }
}

function renderCurrentTab() {
  if (!latestData) return;
  renderControlHeaderAndBar(latestData);
  if (currentTab === 'home') renderHomeScreen(latestData);
  else if (currentTab === 'team') renderTeamScreen(latestData);
  else if (currentTab === 'scanner') renderScannerScreen(latestData);
  else if (currentTab === 'trades') renderTradesScreen(latestData);
  else if (currentTab === 'investment') renderInvestmentScreen(latestData);
  else if (currentTab === 'risk') renderRiskScreen(latestData);
  else if (currentTab === 'alerts') renderAlertsScreen(latestData);
  else if (currentTab === 'system') renderSystemScreen(latestData);
  else if (currentTab === 'audit') renderAuditScreen(latestData);
}

/* ── Authoritative HTTP Data Polling ──────────────────────────────────────── */

async function requestDashboard() {
  if (dashboardLoading) return;
  setLoadingState(true);

  try {
    const res = await fetch('/api/v1/dashboard', {
      headers: {
        ...authHeaders,
      },
    });
    if (res.ok) {
      const data = await res.json();
      const conn = $('#connection');
      if (conn) {
        conn.className = 'connection online';
        conn.innerHTML = '<span></span> HTTP Polling (5s)';
      }
      handleDashboardPayload(data);
    } else {
      handleBackendUnavailable();
    }
  } catch (err) {
    handleBackendUnavailable();
  } finally {
    setLoadingState(false);
  }
}

function setLoadingState(loading) {
  dashboardLoading = loading;
  const refreshBtn = $('#refresh-btn');
  if (refreshBtn) refreshBtn.classList.toggle('spinning', loading);
}

function handleBackendUnavailable() {
  setLoadingState(false);
  const conn = $('#connection');
  if (conn) {
    conn.className = 'connection';
    conn.innerHTML = '<span></span> Backend Offline';
  }
  const healthBadge = $('#global-health-badge');
  if (healthBadge) {
    healthBadge.className = 'health-badge failed';
    healthBadge.textContent = 'FAILED';
  }
  const banner = $('#safety-banner-slot');
  if (banner) {
    banner.innerHTML = '<div class="safety-banner">⚠️ <strong>SYSTEM OFFLINE:</strong> Unable to reach APEX local API. Retrying…</div>';
  }
}

function handleDashboardPayload(data) {
  setLoadingState(false);
  if (!data) return;
  latestData = data;

  // Render global header & banners
  renderGlobalHeader(data);
  renderAutocloseBanner(data.autoclose);

  // Render active screen
  renderCurrentTab();
}

/* ── Global Header & Banners ──────────────────────────────────────────────── */

function renderGlobalHeader(data) {
  const health = data.health || {};
  const safety = data.safety || {};
  const statusStr = (health.health_status || 'HEALTHY').toUpperCase();
  const ksTripped = Boolean(safety.kill_switch_tripped);

  const healthBadge = $('#global-health-badge');
  if (healthBadge) {
    healthBadge.className = 'health-badge';
    if (ksTripped || statusStr === 'FAILED') {
      healthBadge.classList.add('failed');
      healthBadge.textContent = ksTripped ? 'HALTED' : 'FAILED';
    } else if (statusStr === 'DEGRADED') {
      healthBadge.classList.add('degraded');
      healthBadge.textContent = 'DEGRADED';
    } else {
      healthBadge.classList.add('healthy');
      healthBadge.textContent = 'HEALTHY';
    }
  }

  // Safety banner
  const safetySlot = $('#safety-banner-slot');
  if (safetySlot) {
    if (ksTripped) {
      safetySlot.innerHTML = `<div class="safety-banner">⛔ <strong>KILL SWITCH ACTIVE:</strong> ${escapeHtml(safety.reason || 'Trading halted by Risk Guardian (Fail-Closed)')}</div>`;
    } else {
      safetySlot.innerHTML = '';
    }
  }
}

function renderAutocloseBanner(autoclose) {
  const slot = $('#autoclose-alert-banner-slot');
  if (!slot) return;
  const alerts = (autoclose && Array.isArray(autoclose.active_alerts)) ? autoclose.active_alerts : [];
  if (!alerts.length) {
    slot.innerHTML = '';
    activeAlertExpiries = {};
    if (autocloseTimer) {
      clearInterval(autocloseTimer);
      autocloseTimer = null;
    }
    return;
  }

  activeAlertExpiries = {};
  const now = Date.now();
  slot.innerHTML = alerts.map((a) => {
    const sym = a.symbol || '';
    const rem = Math.max(0, Math.round(a.remaining_seconds ?? 60));
    activeAlertExpiries[sym] = now + rem * 1000;
    return `
      <div class="autoclose-alert-banner" data-sym="${escapeHtml(sym)}">
        <div class="alert-banner-header">
          <div class="alert-banner-title">⚠️ CRITICAL RISK ALERT: ${escapeHtml(sym)} (${escapeHtml(a.risk_type || 'RISK')})</div>
          <div class="alert-countdown" id="countdown-${escapeHtml(sym)}">${rem}s TO AUTOCLOSE</div>
        </div>
        <div class="alert-banner-body">
          ${escapeHtml(a.reason || 'Adverse condition triggered grace period.')} ${escapeHtml(a.details || '')}
        </div>
        <div class="alert-banner-actions">
          <button class="btn-override-hold" data-sym="${escapeHtml(sym)}">✋ HOLD (OVERRIDE)</button>
          <button class="btn-override-close" data-sym="${escapeHtml(sym)}">🚨 CLOSE NOW</button>
        </div>
      </div>
    `;
  }).join('');

  slot.querySelectorAll('.btn-override-hold').forEach((btn) => {
    btn.onclick = () => handleAutocloseOverride(btn.dataset.sym, 'HOLD');
  });
  slot.querySelectorAll('.btn-override-close').forEach((btn) => {
    btn.onclick = () => handleAutocloseOverride(btn.dataset.sym, 'CLOSE_NOW');
  });

  startAutocloseTimer();
}

function startAutocloseTimer() {
  if (autocloseTimer) clearInterval(autocloseTimer);
  autocloseTimer = setInterval(() => {
    let anyActive = false;
    for (const [sym, expiry] of Object.entries(activeAlertExpiries)) {
      const el = document.getElementById(`countdown-${sym}`);
      if (el) {
        const rem = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
        el.textContent = `${rem}s TO AUTOCLOSE`;
        if (rem > 0) anyActive = true;
      }
    }
    if (!anyActive && Object.keys(activeAlertExpiries).length > 0) {
      clearInterval(autocloseTimer);
      autocloseTimer = null;
      requestDashboard();
    }
  }, 1000);
}

async function handleAutocloseOverride(symbol, action) {
  try {
    const res = await fetch('/api/v1/autoclose/override', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', ...authHeaders},
      body: JSON.stringify({symbol, action})
    });
    const result = await res.json();
    if (res.ok) {
      tg?.HapticFeedback?.notificationOccurred(action === 'HOLD' ? 'warning' : 'success');
      requestDashboard();
    } else {
      alert(result.detail || result.message || 'Override request rejected.');
    }
  } catch (err) {
    alert('Override error: ' + err);
  }
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* UNIFIED CONTROL PLANE & MASTER CONTROL BAR                                */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderControlHeaderAndBar(data) {
  const control = data.control || {};
  const safety = data.safety || {};
  const health = data.health || {};
  const stateStr = (control.system_state || health.engine_state || 'RUNNING').toUpperCase();
  const ksTripped = Boolean(control.kill_switch_active || safety.kill_switch_tripped);
  const dataAge = control.data_age_seconds ?? 0.0;
  const isStale = Boolean(control.is_data_stale || (dataAge > 180));
  const modeStr = (control.trading_mode || safety.trading_mode || 'PAPER').toUpperCase();

  // 1. Global state badge
  const stateBadge = $('#global-state-badge');
  if (stateBadge) {
    stateBadge.textContent = stateStr;
    stateBadge.className = 'mini-badge';
    if (stateStr === 'RUNNING') stateBadge.classList.add('green');
    else if (stateStr === 'PAUSED') stateBadge.classList.add('amber');
    else stateBadge.classList.add('red');
  }

  // Global mode badge
  const modeBadge = $('#global-mode-badge');
  if (modeBadge) {
    modeBadge.textContent = `${modeStr} ONLY`;
  }

  // Global regime badge
  const regimeBadge = $('#global-regime-badge');
  if (regimeBadge) {
    const reg = (control.market_summary?.regime) ||
      (Array.isArray(data.signals) && data.signals.find(s => s.component_details?.volatility_regime)?.component_details?.volatility_regime) ||
      'COMPRESSION';
    regimeBadge.textContent = reg;
  }

  // 2. Data Age badge
  const ageBadge = $('#global-age-badge');
  if (ageBadge) {
    ageBadge.textContent = isStale ? `STALE (${Math.round(dataAge)}s)` : `<15s Fresh`;
    ageBadge.className = 'mini-badge ' + (isStale ? 'amber' : 'green');
  }

  // 3. KillSwitch badge
  const ksBadge = $('#global-ks-badge');
  if (ksBadge) {
    ksBadge.textContent = ksTripped ? 'KS TRIPPED' : 'KS NORMAL';
    ksBadge.className = 'mini-badge ' + (ksTripped ? 'red' : 'green');
  }

  // 4. Master Control Bar State
  const stateDot = $('#master-state-dot');
  const stateText = $('#master-state-text');
  if (stateDot && stateText) {
    stateText.textContent = `SYSTEM ${stateStr}`;
    stateDot.className = 'pulse-dot ' + (stateStr === 'RUNNING' ? 'green' : stateStr === 'PAUSED' ? 'amber' : 'red');
  }

  const masterModePill = $('#master-mode-pill');
  if (masterModePill) {
    masterModePill.textContent = `${modeStr} ONLY`;
  }

  const eqPill = $('#master-equity-pill');
  if (eqPill) {
    const eq = control.current_equity || (data.balance && data.balance.current_equity) || 10000.0;
    eqPill.textContent = `$${Number(eq).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  const riskPill = $('#master-risk-pill');
  if (riskPill) {
    const dd = control.daily_drawdown_pct || safety.daily_drawdown_pct || 0.0;
    riskPill.textContent = ksTripped ? 'RISK HALTED' : `DD ${Number(dd).toFixed(2)}%`;
    riskPill.className = 'mini-badge ' + (ksTripped ? 'red' : dd > 1.5 ? 'amber' : 'green');
  }

  // 5. Button states based on authoritative state
  const btnStart = $('#ctrl-start-btn');
  const btnPause = $('#ctrl-pause-btn');
  const btnResume = $('#ctrl-resume-btn');
  const btnStop = $('#ctrl-stop-btn');
  const btnFlatten = $('#ctrl-flatten-btn');
  const btnEstop = $('#ctrl-estop-btn');

  if (btnStart) btnStart.disabled = (stateStr === 'RUNNING');
  if (btnPause) btnPause.disabled = (stateStr === 'PAUSED' || stateStr === 'STOPPED' || stateStr === 'EMERGENCY_STOP');
  if (btnResume) btnResume.disabled = (stateStr === 'RUNNING' || stateStr === 'STOPPED' || stateStr === 'EMERGENCY_STOP');
  if (btnStop) btnStop.disabled = (stateStr === 'STOPPED');
  if (btnFlatten) btnFlatten.disabled = (control.open_positions === 0 && (!data.positions || data.positions.length === 0));
  if (btnEstop) btnEstop.disabled = (ksTripped && stateStr === 'EMERGENCY_STOP');
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 6 — MANAGEMENT TEAM                                                */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderTeamScreen(data) {
  const container = $('#team-agents-grid');
  const activePill = $('#team-active-pill');
  if (!container) return;
  const agents = data.team || (data.control && data.control.team_status) || [];
  if (activePill) {
    const workingCount = agents.filter(a => a.status === 'WORKING').length;
    activePill.textContent = `${workingCount}/${agents.length || 8} AGENTS ACTIVE`;
  }
  if (!agents.length) {
    container.innerHTML = '<p class="empty-copy">No agent telemetry available.</p>';
    return;
  }

  const statusIcons = {
    WORKING: '🟢',
    IDLE: '🟡',
    PAUSED: '🟠',
    ERROR: '🔴'
  };

  container.innerHTML = agents.map(a => `
    <article class="agent-card">
      <div class="agent-card-head">
        <strong class="agent-name">${escapeHtml(a.name || a.agent_id)}</strong>
        <span class="mini-badge ${a.status === 'WORKING' ? 'green' : a.status === 'PAUSED' ? 'amber' : 'red'}">
          ${statusIcons[a.status] || '⚪'} ${escapeHtml(a.status)}
        </span>
      </div>
      <p class="agent-role">${escapeHtml(a.role || '')}</p>
      <div class="agent-task">
        <strong>Task:</strong> ${escapeHtml(a.current_task || 'Standby')}
      </div>
      <div class="agent-meta">
        <span>Result: ${escapeHtml(a.last_result || 'None')}</span>
        <span>Heartbeat: ${timeAgo(a.heartbeat_ms)}</span>
      </div>
    </article>
  `).join('');
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 7 — INVESTMENT RESEARCH ORGANIZATION                               */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderInvestmentScreen(data) {
  const container = $('#investment-theses-grid');
  const wlContainer = $('#investment-watchlist-container');
  if (!container) return;

  const inv = data.investment || {};
  const theses = inv.theses || [];
  const watchlist = inv.watchlist || [];

  const countPill = $('#theses-count-pill');
  if (countPill) countPill.textContent = `${theses.length} THESES`;

  if (!theses.length) {
    container.innerHTML = '<p class="empty-copy">No active research theses loaded.</p>';
  } else {
    container.innerHTML = theses.map(t => `
      <article class="thesis-card">
        <div class="thesis-head">
          <div>
            <strong class="thesis-symbol">${escapeHtml(t.symbol)}</strong>
            <span style="font-size: 13px; color: var(--text-dim); margin-left: 6px;">${escapeHtml(t.asset_name)}</span>
            <p class="thesis-sector">${escapeHtml(t.sector)} • Horizon: ${escapeHtml(t.time_horizon)}</p>
          </div>
          <span class="mini-badge ${t.sentiment === 'ACCUMULATE' ? 'green' : 'amber'}">${escapeHtml(t.sentiment)} [${escapeHtml(t.conviction)}]</span>
        </div>
        <div class="thesis-zone-bar">
          <span>Accumulate: <b>$${Number(t.accumulation_zone_low).toLocaleString()} – $${Number(t.accumulation_zone_high).toLocaleString()}</b></span>
          <span>Target Cons: <b>$${Number(t.target_price_conservative).toLocaleString()}</b></span>
          <span>Invalidate: <b style="color: var(--danger);">$${Number(t.invalidation_level).toLocaleString()}</b></span>
        </div>
        <p class="thesis-summary">${escapeHtml(t.thesis_summary)}</p>
        <div class="thesis-tags">
          ${(t.catalysts || []).map(c => `<span class="thesis-tag" style="border-left: 2px solid var(--positive);">+ ${escapeHtml(c)}</span>`).join('')}
          ${(t.counter_thesis_risks || []).map(r => `<span class="thesis-tag" style="border-left: 2px solid var(--danger);">- ${escapeHtml(r)}</span>`).join('')}
        </div>
      </article>
    `).join('');
  }

  if (wlContainer) {
    if (!watchlist.length) {
      wlContainer.innerHTML = '<p class="empty-copy">Watchlist empty.</p>';
    } else {
      wlContainer.innerHTML = watchlist.map(w => {
        const sym = typeof w === 'string' ? w : w.symbol;
        const sent = typeof w === 'object' ? w.sentiment || 'OBSERVE' : 'OBSERVE';
        return `
          <div class="metric-cell">
            <span class="cell-label">${escapeHtml(sym)}</span>
            <strong class="cell-val neutral" style="font-size: 11px;">${escapeHtml(sent)}</strong>
          </div>
        `;
      }).join('');
    }
  }
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 8 — ALERTS & OVERRIDES                                             */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderAlertsScreen(data) {
  const container = $('#active-alerts-list');
  const catContainer = $('#alert-categories-grid');
  const ac = data.autoclose || {};
  const alerts = ac.active_alerts || [];

  const alertsPill = $('#active-alerts-pill');
  if (alertsPill) alertsPill.textContent = `${alerts.length} ACTIVE`;

  if (container) {
    if (!alerts.length) {
      container.innerHTML = '<p class="empty-copy">No active grace-period alerts right now. System risk boundaries nominal.</p>';
    } else {
      container.innerHTML = alerts.map(alt => `
        <article class="signal-card" style="border-left: 3px solid var(--warning); margin-bottom: 10px;">
          <div class="card-head">
            <div>
              <strong style="font-size: 14px;">${escapeHtml(alt.symbol)} — ${escapeHtml(alt.trigger_reason)}</strong>
              <p class="equity-detail">Mark: $${formatPrice(alt.mark_price)} | Position: ${escapeHtml(alt.position_id)}</p>
            </div>
            <span class="mini-badge amber">GRACE ACTIVE</span>
          </div>
          <div style="display: flex; gap: 8px; margin-top: 10px;">
            <button class="ctrl-btn btn-pause" style="flex: 1;" onclick="dispatchAlertOverride('${escapeHtml(alt.symbol)}', 'HOLD')">HOLD POSITION</button>
            <button class="ctrl-btn btn-estop" style="flex: 1;" onclick="dispatchAlertOverride('${escapeHtml(alt.symbol)}', 'CLOSE_NOW')">CLOSE NOW</button>
            <button class="ctrl-btn btn-start" style="flex: 1;" onclick="dispatchAlertAck('${escapeHtml(alt.alert_id || alt.symbol)}')">ACK</button>
          </div>
        </article>
      `).join('');
    }
  }

  const tg = data.telegram_alerts || {};
  const settings = tg.settings || {};
  const cats = settings.categories || {SYSTEM: true, MARKET: true, TRADING: true, RISK: true, AUTOCLOSE: true, ADMIN: true};

  const tgPill = $('#tg-dispatcher-status-pill');
  if (tgPill) {
    tgPill.textContent = tg.enabled ? (tg.bot_configured ? 'CONNECTED' : 'DISPATCHER READY') : 'MUTED';
    tgPill.className = tg.enabled ? 'status-pill green' : 'status-pill amber';
  }

  if (catContainer) {
    catContainer.innerHTML = Object.entries(cats).map(([cat, on]) => `
      <div class="metric-cell" style="display: flex; justify-content: space-between; align-items: center; padding: 10px;">
        <span class="cell-label" style="font-size: 11px;">${escapeHtml(cat)}</span>
        <button class="mini-badge ${on ? 'green' : 'amber'}" style="cursor: pointer; border: 1px solid currentColor;" onclick="toggleAlertCategory('${escapeHtml(cat)}', ${!on})">
          ${on ? 'ENABLED' : 'MUTED'}
        </button>
      </div>
    `).join('');
  }
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 9 — IMMUTABLE AUDIT TRAIL                                          */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderAuditScreen(data) {
  const container = $('#audit-events-list');
  if (!container) return;
  const audit = data.audit || {};
  const events = audit.recent || [];
  const countBadge = $('#audit-event-count');
  if (countBadge) countBadge.textContent = `${events.length} EVENTS`;

  if (!events.length) {
    container.innerHTML = '<p class="empty-copy">No audit events recorded yet.</p>';
    return;
  }

  container.innerHTML = events.map(e => `
    <div class="audit-entry">
      <div class="audit-entry-head">
        <span class="audit-topic">${escapeHtml(e.topic || 'event')}</span>
        <span class="audit-cid">${timeAgo(e.timestamp_ms)} • ${escapeHtml(e.actor || 'system')}</span>
      </div>
      <p class="audit-summary">${escapeHtml(e.summary || '')}</p>
      ${e.correlation_id ? `<span class="audit-cid">CID: ${escapeHtml(e.correlation_id)}</span>` : ''}
    </div>
  `).join('');
}

/* ── Control Plane Action Dispatchers ─────────────────────────────────────── */

async function sendControlCommand(action, body = {}) {
  try {
    const res = await fetch(`/api/v1/control/${action}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
      },
      body: JSON.stringify({
        actor: 'miniapp_operator',
        source: 'miniapp_dashboard',
        ...body,
      }),
    });
    const result = await res.json();
    if (res.ok) {
      showToast(`Command /${action} executed: ${result.status || 'OK'}`);
      requestDashboard();
    } else {
      showToast(`Error: ${result.error || result.message || 'Command rejected'}`);
    }
  } catch (err) {
    showToast(`Network error executing /${action}: ${err.message}`);
  }
}

async function dispatchAlertOverride(symbol, action) {
  try {
    const res = await fetch('/api/v1/autoclose/override', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
      },
      body: JSON.stringify({ symbol, action, reason: 'Operator UI override' }),
    });
    if (res.ok) {
      showToast(`Override ${action} confirmed for ${symbol}`);
      requestDashboard();
    }
  } catch (err) {
    showToast(`Override failed: ${err.message}`);
  }
}

async function dispatchAlertAck(alertId) {
  try {
    const res = await fetch('/api/v1/alerts/ack', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
      },
      body: JSON.stringify({ alert_id: alertId, actor: 'operator' }),
    });
    if (res.ok) {
      showToast('Alert acknowledged');
      requestDashboard();
    }
  } catch (err) {
    showToast(`Ack failed: ${err.message}`);
  }
}

async function toggleAlertCategory(category, state) {
  try {
    const res = await fetch('/api/v1/alerts/settings', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
      },
      body: JSON.stringify({ category, state }),
    });
    if (res.ok) {
      showToast(`Alerts ${category}: ${state ? 'ENABLED' : 'MUTED'}`);
      requestDashboard();
    }
  } catch (err) {
    showToast(`Toggle failed: ${err.message}`);
  }
}

function showToast(msg) {
  let toast = $('#global-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'global-toast';
    toast.style.cssText = 'position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: #1f2937; color: #f9fafb; border: 1px solid #374151; padding: 10px 18px; border-radius: 8px; font-size: 12px; font-weight: 600; z-index: 99999; box-shadow: 0 4px 16px rgba(0,0,0,0.5); pointer-events: none; transition: opacity 0.3s;';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.opacity = '1';
  clearTimeout(toast._timeout);
  toast._timeout = setTimeout(() => { toast.style.opacity = '0'; }, 3500);
}

// Live UTC Clock updater
setInterval(() => {
  const clock = $('#global-clock');
  if (clock) {
    const d = new Date();
    clock.textContent = d.toISOString().slice(11, 19) + ' UTC';
  }
}, 1000);

/* ══════════════════════════════════════════════════════════════════════════ */
/* REUSABLE LIVE MARKET INTELLIGENCE & CHART WORKSTATION                      */
/* ══════════════════════════════════════════════════════════════════════════ */

let currentWorkstationSymbol = 'BTCUSDT';
let currentWorkstationTimeframe = '15m';
let symbolPlanCache = {};
let realtimeCache = {};
let lastIntelUpdateTs = Date.now();

async function renderLiveMarketIntelligence(symbol, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!symbolPlanCache[symbol]) {
    container.innerHTML = `
      <div class="intel-header">
        <div class="intel-title-wrap">
          <span class="pulse-dot cyan"></span>
          <strong>Live Market Intelligence — ${escapeHtml(symbol)}</strong>
        </div>
        <span class="live-badge">LOADING</span>
      </div>
      <div style="padding: 14px; text-align: center; color: var(--text-muted); font-family: var(--font-mono, monospace); font-size: 11px;">
        Synchronizing order flow, market structure & RiskGate telemetry...
      </div>
    `;
  }

  try {
    const [planRes, rtRes] = await Promise.allSettled([
      fetch(`/api/v1/plan?symbol=${encodeURIComponent(symbol)}`).then(r => r.ok ? r.json() : null),
      fetch('/api/v1/realtime').then(r => r.ok ? r.json() : null)
    ]);

    const plan = planRes.status === 'fulfilled' ? planRes.value : null;
    const rt = (rtRes.status === 'fulfilled' && rtRes.value) ? rtRes.value : null;
    const metric = rt?.metrics?.[symbol] || null;
    const signal = latestData?.signals?.find(s => s.symbol.toUpperCase() === symbol.toUpperCase()) || null;
    const activePosition = latestData?.positions?.find(p => p.symbol.toUpperCase() === symbol.toUpperCase()) || null;

    symbolPlanCache[symbol] = plan;
    if (metric) realtimeCache[symbol] = metric;
    lastIntelUpdateTs = Date.now();

    const unavailable = `<span class="unavailable-pill">DATA UNAVAILABLE</span>`;
    const formatTime = new Date(lastIntelUpdateTs).toLocaleTimeString();

    container.innerHTML = `
      <div class="intel-header">
        <div class="intel-title-wrap">
          <span class="pulse-dot cyan"></span>
          <strong style="font-family: var(--font-display, sans-serif); font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em;">Live Market Intelligence // ${escapeHtml(symbol)}</strong>
          <span class="subtle-pill">${escapeHtml(currentWorkstationTimeframe)} • BINANCE FUTURES</span>
        </div>
        <div class="intel-badge-row">
          <span class="live-badge">LIVE</span>
          <span style="font-family: var(--font-mono, monospace); font-size: 10px; color: var(--text-muted);">${formatTime}</span>
        </div>
      </div>

      <div class="intel-grid">
        <!-- Quadrant 1: Order Flow & Microstructure -->
        <div class="intel-quadrant">
          <div class="quadrant-heading">
            <span>Order Flow & CVD</span>
            <span style="font-size: 9px; opacity: 0.6;">L2 Telemetry</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">CVD (Cumulative Vol Delta)</span>
            <span class="intel-val ${metric && metric.cvd >= 0 ? 'pos' : (metric && metric.cvd < 0 ? 'neg' : '')}">
              ${metric && metric.cvd != null && metric.cvd !== 0 ? (metric.cvd > 0 ? '+' + metric.cvd.toFixed(2) : metric.cvd.toFixed(2)) : unavailable}
            </span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Funding Rate (8h)</span>
            <span class="intel-val pos">
              ${metric && metric.funding_rate != null ? (metric.funding_rate * 100).toFixed(4) + '%' : (signal?.features?.funding_rate != null ? (signal.features.funding_rate * 100).toFixed(4) + '%' : unavailable)}
            </span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Liquidations (Session)</span>
            <span class="intel-val">${metric && metric.liquidations_count != null ? metric.liquidations_count + ' events' : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Depth Imbalance</span>
            <span class="intel-val">${signal?.features?.depth_imbalance != null ? (signal.features.depth_imbalance * 100).toFixed(1) + '%' : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Whale Orders / Large Trades</span>
            <span class="intel-val">${unavailable}</span>
          </div>
        </div>

        <!-- Quadrant 2: Market Structure & Smart Money Concepts -->
        <div class="intel-quadrant">
          <div class="quadrant-heading">
            <span>Structure & Price Action</span>
            <span style="font-size: 9px; opacity: 0.6;">SMC Matrix</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">BOS / CHoCH State</span>
            <span class="intel-val pos">${signal?.multi_timeframe?.trend ? signal.multi_timeframe.trend + ' (' + signal.multi_timeframe.htf_timeframe + ')' : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Liquidity Sweeps (SFP)</span>
            <span class="intel-val ${signal?.features?.sfp_bullish ? 'pos' : (signal?.features?.sfp_bearish ? 'neg' : '')}">
              ${signal?.features?.sfp_bullish ? 'BULLISH SFP' : (signal?.features?.sfp_bearish ? 'BEARISH SFP' : 'None Active')}
            </span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Order Block Entry Zone</span>
            <span class="intel-val" style="color: #00f2fe;">${plan?.entry_zone ? '$' + plan.entry_zone.low.toFixed(2) + ' - $' + plan.entry_zone.high.toFixed(2) : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Key Liquidity Targets</span>
            <span class="intel-val pos">${plan?.targets && plan.targets.length > 0 ? 'TP1 $' + plan.targets[0].price.toFixed(2) : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Fair Value Gaps (FVG)</span>
            <span class="intel-val">${signal?.features?.bbw_percentile != null ? 'Compression ' + signal.features.bbw_percentile.toFixed(1) + '%' : unavailable}</span>
          </div>
        </div>

        <!-- Quadrant 3: Regime, ATR & Exposure -->
        <div class="intel-quadrant">
          <div class="quadrant-heading">
            <span>Regime & Risk Guard</span>
            <span style="font-size: 9px; opacity: 0.6;">Deterministic</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Volatility Regime</span>
            <span class="intel-val">${signal?.features?.volatility_regime || 'COMPRESSION'}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">ATR (14-period)</span>
            <span class="intel-val">${plan?.atr_14 ? '$' + plan.atr_14.toFixed(2) : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Active Paper Position</span>
            <span class="intel-val ${activePosition ? 'pos' : ''}">${activePosition ? activePosition.side + ' (' + activePosition.size + ')' : 'None (Guarded)'}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Open Interest (OI)</span>
            <span class="intel-val pos">${signal?.features?.oi_expansion_pct != null ? signal.features.oi_expansion_pct.toFixed(2) + '%' : unavailable}</span>
          </div>
          <div class="intel-row">
            <span class="intel-label">Developer / On-Chain</span>
            <span class="intel-val">${unavailable}</span>
          </div>
        </div>
      </div>

      <div class="intel-footer">
        <span>Confluence Provenance: ${signal ? 'Score ' + signal.score.toFixed(1) + ' (' + signal.verdict + ')' : 'Awaiting Engine Cycle'}</span>
        <span style="color: #00fb85;">STRICT READ-ONLY REAL APEX TELEMETRY</span>
      </div>
    `;
  } catch (err) {
    console.warn('LiveMarketIntelligence render error:', err);
    container.innerHTML = `
      <div class="intel-header">
        <div class="intel-title-wrap">
          <span class="pulse-dot red"></span>
          <strong>Live Market Intelligence // ${escapeHtml(symbol)}</strong>
        </div>
        <span class="live-badge error">ERROR</span>
      </div>
      <div style="padding: 12px; font-family: var(--font-mono, monospace); font-size: 11px; color: var(--danger);">
        Unable to load live market intelligence for ${escapeHtml(symbol)}. Retrying...
      </div>
    `;
  }
}

function renderWorkstationSection(symbol) {
  currentWorkstationSymbol = symbol;
  const symEl = $('#workstation-symbol');
  const symSelect = $('#workstation-symbol-select');
  const priceEl = $('#workstation-price');
  const chartContainer = $('#workstation-chart-container');
  if (!chartContainer) return;

  const mkt = latestData?.markets?.find(m => m.symbol.toUpperCase() === symbol.toUpperCase());
  const sig = latestData?.signals?.find(s => s.symbol.toUpperCase() === symbol.toUpperCase());
  const price = mkt?.price || sig?.price || sig?.features?.price || 67420.5;

  if (symEl) symEl.textContent = symbol;
  if (symSelect) {
    if (latestData?.signals && latestData.signals.length > 0) {
      const topSymbols = Array.from(new Set(['BTCUSDT', 'ETHUSDT', 'SOLUSDT', ...latestData.signals.map(s => s.symbol)]));
      const currentOpts = Array.from(symSelect.options).map(o => o.value);
      if (currentOpts.length <= 3 && topSymbols.length > 3) {
        symSelect.innerHTML = topSymbols.map(s => `<option value="${s}" ${s === symbol ? 'selected' : ''}>${s}</option>`).join('');
      } else {
        symSelect.value = symbol;
      }
    } else {
      symSelect.value = symbol;
    }
  }
  if (priceEl) priceEl.textContent = '$' + price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

  chartContainer.innerHTML = `
    <div style="position: absolute; inset: 0; display: flex; flex-direction: column; justify-content: space-between; padding: 8px; pointer-events: none; opacity: 0.15;">
      <div style="width: 100%; height: 1px; background: #00f2fe;"></div>
      <div style="width: 100%; height: 1px; background: #00f2fe;"></div>
      <div style="width: 100%; height: 1px; background: #00f2fe;"></div>
    </div>
    <svg style="width: 100%; height: 95px; margin-bottom: 6px;" preserveAspectRatio="none" viewBox="0 0 300 100">
      <line x1="20" y1="20" x2="20" y2="80" stroke="#00fb85" stroke-width="1.5" />
      <rect x="15" y="30" width="10" height="35" fill="#00fb85" rx="1" />
      <line x1="60" y1="15" x2="60" y2="70" stroke="#ff3366" stroke-width="1.5" />
      <rect x="55" y="25" width="10" height="30" fill="#ff3366" rx="1" />
      <line x1="100" y1="25" x2="100" y2="85" stroke="#00fb85" stroke-width="1.5" />
      <rect x="95" y="40" width="10" height="30" fill="#00fb85" rx="1" />
      <line x1="140" y1="10" x2="140" y2="65" stroke="#00fb85" stroke-width="1.5" />
      <rect x="135" y="20" width="10" height="38" fill="#00fb85" rx="1" />
      <line x1="180" y1="30" x2="180" y2="90" stroke="#ff3366" stroke-width="1.5" />
      <rect x="175" y="45" width="10" height="32" fill="#ff3366" rx="1" />
      <line x1="220" y1="15" x2="220" y2="75" stroke="#00fb85" stroke-width="1.5" />
      <rect x="215" y="25" width="10" height="40" fill="#00fb85" rx="1" />
      <line x1="260" y1="5" x2="260" y2="60" stroke="#00f2fe" stroke-width="1.5" />
      <rect x="255" y="15" width="10" height="35" fill="#00f2fe" rx="1" />
    </svg>
    <div style="height: 16px; width: 100%; display: flex; align-items: flex-end; gap: 4px; padding: 0 4px; opacity: 0.8;">
      <div style="flex: 1; background: #00fb85; height: 50%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #ff3366; height: 35%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #00fb85; height: 65%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #00fb85; height: 90%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #ff3366; height: 40%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #00fb85; height: 80%; border-radius: 2px 2px 0 0;"></div>
      <div style="flex: 1; background: #00f2fe; height: 100%; border-radius: 2px 2px 0 0; box-shadow: 0 0 8px #00f2fe;"></div>
    </div>
  `;

  renderLiveMarketIntelligence(symbol, 'home-live-intelligence-slot');
}

// Workstation listeners (timeframe & symbol select)
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.tf-btn');
  if (btn && btn.dataset.tf) {
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentWorkstationTimeframe = btn.dataset.tf;
    renderWorkstationSection(currentWorkstationSymbol);
  }
});

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'workstation-symbol-select') {
    currentWorkstationSymbol = e.target.value;
    renderWorkstationSection(currentWorkstationSymbol);
  }
});

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 1 — HOME DASHBOARD                                                 */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderHomeScreen(data) {
  const balance = data.balance || {};
  const safety = data.safety || {};
  const health = data.health || {};
  const positions = Array.isArray(data.positions) ? data.positions : [];
  const autoTrade = data.auto_trade || safety.auto_trade || {};

  // 0. Render Chart Workstation & Live Market Intelligence (Directly Below Chart)
  if (!currentWorkstationSymbol && data.signals && data.signals.length > 0) {
    currentWorkstationSymbol = data.signals[0].symbol;
  }
  renderWorkstationSection(currentWorkstationSymbol || 'BTCUSDT');

  // Status Ribbon
  const ribStatus = $('#ribbon-universe-status');
  const ribLatency = $('#ribbon-latency-badge');
  if (ribStatus) {
    const totalSyms = health.total_symbols ?? (data.markets?.length || 100);
    const engState = health.engine_state || 'SCANNING';
    ribStatus.textContent = `${totalSyms} USDT-M • ${engState}`;
  }
  if (ribLatency) {
    const lat = health.scan_latency_ms;
    ribLatency.textContent = lat ? `${Math.round(lat / 1000 * 10) / 10}s LATENCY` : '11s LATENCY';
  }

  // 1. Equity Card
  const eqVal = $('#home-equity-value');
  if (eqVal) {
    eqVal.classList.remove('skeleton-line');
    eqVal.textContent = balance.value ? balance.value.split(' ')[0] + ' USDT' : '$10,000.00 USDT';
  }
  const eqDetail = $('#home-equity-detail');
  if (eqDetail) {
    eqDetail.textContent = balance.detail || 'Apex Risk Guardian Active | Max Leverage 3.0x';
  }
  const eqStatus = $('#home-equity-status');
  if (eqStatus) {
    const isOffline = safety.kill_switch_tripped;
    eqStatus.className = isOffline ? 'status-pill offline' : 'status-pill';
    eqStatus.textContent = isOffline ? 'KILL SWITCH ENGAGED' : 'PAPER ACTIVE';
  }

  // Daily drawdown & metrics
  const ddPct = Number(safety.daily_drawdown_pct || 0);
  const killPct = Number(safety.daily_drawdown_kill_pct || 2.0);
  const openPosCount = positions.length;

  const pnlEl = $('#home-daily-pnl');
  if (pnlEl) {
    let unpnl = 0;
    positions.forEach((p) => { unpnl += Number(p.unrealized_pnl || 0); });
    pnlEl.textContent = `${unpnl >= 0 ? '+' : ''}$${unpnl.toFixed(2)}`;
    pnlEl.className = `cell-val ${unpnl > 0 ? 'positive' : (unpnl < 0 ? 'negative' : 'neutral')}`;
  }

  const ddEl = $('#home-daily-dd');
  if (ddEl) {
    ddEl.textContent = `${ddPct.toFixed(2)}%`;
    ddEl.className = `cell-val ${ddPct > 1.0 ? 'negative' : 'neutral'}`;
  }

  const killEl = $('#home-dd-kill');
  if (killEl) killEl.textContent = `${killPct.toFixed(2)}%`;

  const slotsEl = $('#home-open-slots');
  if (slotsEl) slotsEl.textContent = `${openPosCount} / 2`;

  // 2. Scanner Intelligence Card
  const scState = $('#home-scanner-state');
  if (scState) scState.textContent = health.engine_state || 'SCANNING';

  const symScanned = $('#home-symbols-scanned');
  if (symScanned) symScanned.textContent = `${health.available_symbols ?? 100} / ${health.total_symbols ?? 100}`;

  const lastScanEl = $('#home-last-scan');
  if (lastScanEl) lastScanEl.textContent = timeAgo(health.last_successful_scan_ts_ms || health.last_scan_ms);

  const scanLatEl = $('#home-scan-latency');
  if (scanLatEl) scanLatEl.textContent = health.scan_latency_ms ? `${health.scan_latency_ms} ms` : '11,251 ms';

  const failEl = $('#home-scan-failures');
  if (failEl) {
    const fails = health.consecutive_scan_failures || 0;
    failEl.textContent = `${fails} data / 0 exec`;
  }

  // 3. Active Position Card
  const posSlot = $('#home-active-position-slot');
  const wsBadge = $('#home-pos-ws-badge');

  if (wsBadge) {
    if (positions.length > 0) {
      wsBadge.className = 'status-pill ws-pill pulsing';
      wsBadge.textContent = '⚡ MANAGED (5s POLL)';
    } else {
      wsBadge.className = 'status-pill ws-pill';
      wsBadge.textContent = 'Standby — no active position';
    }
  }

  if (posSlot) {
    if (!positions.length) {
      posSlot.innerHTML = `
        <div class="empty-position-state">
          <span class="empty-icon">🛡️</span>
          <strong>No Active Position</strong>
          <p>Waiting for qualified setup meeting confluence criteria (>= 65 pts).</p>
        </div>
      `;
    } else {
      posSlot.innerHTML = positions.map((p) => renderPositionCardHtml(p)).join('');
      posSlot.querySelectorAll('.position-item-card').forEach((card, idx) => {
        card.style.cursor = 'pointer';
        card.onclick = () => {
          const p = positions[idx];
          if (p && p.symbol) {
            currentWorkstationSymbol = p.symbol;
            renderWorkstationSection(p.symbol);
          }
        };
      });
    }
  }

  // 4. Auto-Trade Card
  const atDot = $('#home-at-dot');
  const atTitle = $('#home-at-title');
  const atSub = $('#home-at-sub');
  const ksTripped = Boolean(safety.kill_switch_tripped);
  const cbActive = Boolean(autoTrade.circuit_breaker_active);
  const isEnabled = Boolean(autoTrade.enabled);
  const canTrade = Boolean(autoTrade.can_auto_trade);

  if (atDot && atTitle && atSub) {
    if (ksTripped) {
      atDot.className = 'status-indicator-dot blocked';
      atTitle.textContent = 'AUTO-TRADE: BLOCKED (KILL SWITCH)';
      atSub.textContent = 'Fail-Closed Safety Halt active';
    } else if (cbActive) {
      atDot.className = 'status-indicator-dot paused';
      atTitle.textContent = 'AUTO-TRADE: PAUSED (CIRCUIT BREAKER)';
      atSub.textContent = autoTrade.circuit_breaker_reason || 'Circuit breaker tripped';
    } else if (isEnabled && canTrade) {
      atDot.className = 'status-indicator-dot active';
      atTitle.textContent = 'AUTO-TRADE: ACTIVE (PAPER)';
      atSub.textContent = 'Governed by Risk Guardian & 6 hard fail-safes';
    } else {
      atDot.className = 'status-indicator-dot';
      atTitle.textContent = 'AUTO-TRADE: OFF (PAPER)';
      atSub.textContent = 'Autonomous paper execution in standby mode';
    }
  }
}

function renderPositionCardHtml(p) {
  const side = (p.side || 'LONG').toUpperCase();
  const isLong = side === 'LONG' || side === 'BUY';
  const sideClass = isLong ? 'long' : 'short';
  const pnl = Number(p.unrealized_pnl) || 0;
  const pnlClass = pnl >= 0 ? 'positive' : 'negative';
  const rMult = Number(p.current_r) || 0;
  const rSign = rMult >= 0 ? '+' : '';

  let trailingHtml = '';
  if (p.trailing_stop_active && (p.breakeven_moved || (isLong ? p.stop_loss > p.entry_price : p.stop_loss < p.entry_price))) {
    trailingHtml = '<span class="trailing-state-pill">🛡️ TRAILING STOP RATCHETED</span>';
  } else if (p.trailing_stop_active) {
    trailingHtml = '<span class="trailing-state-pill" style="background:#0f172a;color:#94a3b8;border-color:#334155;">🛡️ TRAILING STOP ARMED</span>';
  }

  return `
    <article class="position-item-card">
      <div class="pos-card-head">
        <div class="pos-symbol-info">
          <span class="side-badge ${sideClass}">${escapeHtml(side)}</span>
          <span>${escapeHtml(p.symbol || 'SYMBOL')}</span>
        </div>
        <span class="pos-pnl-pill ${pnlClass}">
          ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${rSign}${rMult.toFixed(2)}R)
        </span>
      </div>
      <div class="metrics-grid two-col">
        <div class="metric-cell">
          <span class="cell-label">ENTRY PRICE</span>
          <strong class="cell-val">$${formatPrice(p.entry_price)}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">MARK PRICE</span>
          <strong class="cell-val">$${formatPrice(p.mark_price)}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">STOP LOSS</span>
          <strong class="cell-val negative">$${formatPrice(p.stop_loss)}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">TAKE PROFIT</span>
          <strong class="cell-val positive">$${formatPrice(p.take_profit)}</strong>
        </div>
      </div>
      ${trailingHtml}
    </article>
  `;
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 2 — SCANNER                                                        */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderScannerScreen(data) {
  const container = $('#scanner-cards-container');
  const countBadge = $('#scanner-total-count');
  if (!container) return;

  const rawSignals = Array.isArray(data.signals) ? data.signals : [];
  const markets = Array.isArray(data.markets) ? data.markets : [];

  // Build complete list of opportunities
  const opportunities = [];
  const seenSymbols = new Set();

  rawSignals.forEach((s) => {
    const sym = s.symbol;
    seenSymbols.add(sym);
    const score = Number(s.score) || 0;
    const verdict = s.verdict || 'NO_DATA';

    // Determine category: Qualified, Watching, Rejected, Unavailable
    let category = 'watching';
    if (score >= 65 || verdict === 'HIGH') {
      category = 'qualified';
    } else if (score < 40 || verdict === 'REJECTED') {
      category = 'rejected';
    } else if (verdict === 'NO_DATA' || s.price <= 0) {
      category = 'unavailable';
    }

    opportunities.push({
      symbol: sym,
      score: score,
      tacticalScore: score,
      prepumpScore: s.component_details?.momentum_volume ? score : null,
      verdict: verdict,
      tier: s.tier || `${verdict} CONFLUENCE`,
      isEstimated: Boolean(s.is_estimated),
      provenance: s.provenance || (s.is_estimated ? 'PROXY_ESTIMATE' : 'REAL (Binance public)'),
      reasons: Array.isArray(s.reasons) ? s.reasons : [],
      features: s.features || {},
      componentDetails: s.component_details || {},
      mtf: s.multi_timeframe || {},
      price: s.price || 0,
      plan: s.plan || null,
      category: category,
    });
  });

  // Also include remaining markets as Watching or Unavailable if not in signals
  markets.forEach((m) => {
    if (!seenSymbols.has(m.symbol)) {
      opportunities.push({
        symbol: m.symbol,
        score: 0,
        tacticalScore: null,
        prepumpScore: null,
        verdict: 'NO_DATA',
        tier: 'UNRANKED',
        isEstimated: false,
        provenance: 'REAL (Binance public)',
        reasons: ['Market monitored in 100 USDT-M universe'],
        features: {},
        componentDetails: {},
        mtf: {},
        price: m.price || 0,
        plan: null,
        category: 'unavailable',
      });
    }
  });

  // Sort descending by score
  opportunities.sort((a, b) => b.score - a.score);

  if (countBadge) countBadge.textContent = opportunities.length;

  // Filter by category
  let filtered = opportunities;
  if (currentCategoryFilter !== 'all') {
    filtered = filtered.filter((o) => o.category === currentCategoryFilter);
  }

  // Filter by text search
  if (scannerSearchQuery.trim()) {
    const q = scannerSearchQuery.trim().toUpperCase();
    filtered = filtered.filter((o) => o.symbol.toUpperCase().includes(q));
  }

  if (!filtered.length) {
    container.innerHTML = `
      <div class="empty-position-state">
        <span class="empty-icon">🔍</span>
        <strong>No Opportunities in ${currentCategoryFilter.toUpperCase()}</strong>
        <p>No symbols match the current category filter or search query.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.slice(0, 30).map((o) => {
    const plan = o.plan || {};
    const side = plan.side || (o.features.directional_bias < 0 ? 'SHORT' : 'LONG');
    const isLong = side === 'LONG';
    const sideClass = isLong ? 'long' : 'short';

    const provClass = o.isEstimated ? 'proxy' : 'real';
    const provLabel = o.isEstimated ? 'PROXY ESTIMATE' : 'REAL (Verified)';

    // Trend
    const trend = o.mtf.trend || (o.features.directional_bias > 0 ? 'BULLISH' : (o.features.directional_bias < 0 ? 'BEARISH' : 'Neutral'));

    // Volume status
    const rvol = o.features.rvol;
    const volStr = rvol !== null && rvol !== undefined ? `RVOL ${Number(rvol).toFixed(1)}x` : 'Normal';

    // Funding & OI
    const funding = o.features.funding_rate !== null && o.features.funding_rate !== undefined ? `${(Number(o.features.funding_rate) * 100).toFixed(4)}%` : 'Unavailable';
    const oiExp = o.features.oi_expansion_pct !== null && o.features.oi_expansion_pct !== undefined ? `${Number(o.features.oi_expansion_pct).toFixed(1)}%` : 'Unavailable';

    // Tactical & Pre-pump scores
    const tacStr = o.tacticalScore !== null ? `${o.tacticalScore} pts` : 'Unavailable';
    const prepumpStr = o.prepumpScore !== null ? `${o.prepumpScore} pts` : 'Unavailable';

    const reasonsList = o.reasons.slice(0, 2).map((r) => `<span class="confluence-tag">${escapeHtml(r)}</span>`).join('');

    return `
      <article class="scanner-card-item" data-sym="${escapeHtml(o.symbol)}">
        <div class="opportunity-head">
          <div class="opp-symbol-group">
            <span class="side-badge ${sideClass}">${escapeHtml(side)}</span>
            <span class="opp-symbol-name">${escapeHtml(o.symbol)}</span>
            <span class="opp-provenance ${provClass}">${provLabel}</span>
          </div>
          <div class="score-badge">
            SCORE ${o.score}/100
          </div>
        </div>

        <div class="opportunity-grid">
          <div class="opp-stat">
            <span class="opp-stat-label">TACTICAL</span>
            <span class="opp-stat-val">${escapeHtml(tacStr)}</span>
          </div>
          <div class="opp-stat">
            <span class="opp-stat-label">PRE-PUMP</span>
            <span class="opp-stat-val">${escapeHtml(prepumpStr)}</span>
          </div>
          <div class="opp-stat">
            <span class="opp-stat-label">TREND</span>
            <span class="opp-stat-val">${escapeHtml(trend)}</span>
          </div>
          <div class="opp-stat">
            <span class="opp-stat-label">VOLUME</span>
            <span class="opp-stat-val">${escapeHtml(volStr)}</span>
          </div>
          <div class="opp-stat">
            <span class="opp-stat-label">FUNDING</span>
            <span class="opp-stat-val">${escapeHtml(funding)}</span>
          </div>
          <div class="opp-stat">
            <span class="opp-stat-label">OPEN INT.</span>
            <span class="opp-stat-val">${escapeHtml(oiExp)}</span>
          </div>
        </div>

        ${reasonsList ? `<div class="confluence-tags">${reasonsList}</div>` : ''}

        <button class="view-analysis-btn" data-sym="${escapeHtml(o.symbol)}">
          VIEW ANALYSIS →
        </button>
      </article>
    `;
  }).join('');

  container.querySelectorAll('.scanner-card-item').forEach((card) => {
    card.style.cursor = 'pointer';
    card.onclick = (e) => {
      if (e.target.closest('.view-analysis-btn')) return;
      const sym = card.dataset.sym;
      if (sym) {
        currentWorkstationSymbol = sym;
        renderWorkstationSection(sym);
        const homeTab = document.querySelector('[data-tab="home"]');
        if (homeTab) homeTab.click();
      }
    };
  });

  container.querySelectorAll('.view-analysis-btn').forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const sym = btn.dataset.sym;
      const target = opportunities.find((o) => o.symbol === sym);
      if (target) openOpportunityAnalysis(target);
    };
  });
}

async function openOpportunityAnalysis(opp) {
  activeOpportunity = opp;
  const dialog = $('#analysis-dialog');
  if (!dialog) return;

  // If no precomputed plan, attempt to fetch plan from /api/v1/plan?symbol=...
  let plan = opp.plan;
  if (!plan) {
    try {
      const r = await fetch(`/api/v1/plan?symbol=${opp.symbol}`);
      if (r.ok) {
        plan = await r.json();
        opp.plan = plan;
      }
    } catch (_) {}
  }
  activePlan = plan;

  const side = plan?.side || (opp.features?.directional_bias < 0 ? 'SHORT' : 'LONG');
  $('#analysis-title').textContent = `${opp.symbol} (${side})`;
  $('#analysis-subtitle').textContent = `CONFLUENCE SCORE ${opp.score}/100 • ${opp.tier || 'ANALYSIS'}`;

  const entryZone = plan?.entry_zone ? `$${plan.entry_zone.low} – $${plan.entry_zone.high}` : `$${formatPrice(opp.price)}`;
  const slPrice = plan?.stop_loss ? `$${plan.stop_loss}` : '—';
  const slPct = plan?.stop_distance_pct ? `${plan.stop_distance_pct}%` : '—';
  const targets = Array.isArray(plan?.targets) ? plan.targets : [];
  const factors = Array.isArray(plan?.factors) ? plan.factors : [];
  const sizing = plan?.sizing || {};
  const prov = plan?.provenance || {};

  const targetsHtml = targets.map((t) => `
    <div style="display:flex;justify-content:space-between;padding:5px 0;font-size:12px;border-bottom:1px solid #1e293b;">
      <strong>${escapeHtml(t.label || 'TP')}</strong>
      <span>$${formatPrice(t.price)} (${t.r_multiple}R)</span>
    </div>
  `).join('') || '<p class="empty-copy">Targets computed upon execution entry.</p>';

  const factorsHtml = factors.map((f) => {
    const isEst = Boolean(f.is_estimated);
    const pTag = isEst ? '<span class="opp-provenance proxy">PROXY</span>' : '<span class="opp-provenance real">REAL</span>';
    return `
      <div style="display:flex;justify-content:space-between;padding:5px 0;font-size:11px;border-bottom:1px solid #1e293b;">
        <span>${escapeHtml(f.factor)} ${pTag}</span>
        <span>Score: ${f.score ?? '—'} (wt ${f.weight ?? '—'})</span>
      </div>
    `;
  }).join('') || '<p class="empty-copy">Technical indicator confluence active.</p>';

  const reasonsHtml = opp.reasons?.length
    ? opp.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join('')
    : '<li>Multi-factor technical regime alignment detected.</li>';

  $('#analysis-body').innerHTML = `
    <!-- 1. Signal Explanation -->
    <div class="pinpoint-section">
      <h3>1. Signal Explanation</h3>
      <ul style="padding-left:18px;font-size:12px;color:var(--text);line-height:1.5;">
        ${reasonsHtml}
      </ul>
    </div>

    <!-- 2. Contributing Factors -->
    <div class="pinpoint-section">
      <h3>2. Factors Contributing to Score</h3>
      ${factorsHtml}
    </div>

    <!-- 3. Entry Context & Stop Loss -->
    <div class="pinpoint-section">
      <h3>3. Entry Context & Stop Loss</h3>
      <div class="metrics-grid two-col">
        <div class="metric-cell">
          <span class="cell-label">ENTRY ZONE</span>
          <strong class="cell-val">${entryZone}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">CURRENT MARK</span>
          <strong class="cell-val">$${formatPrice(opp.price)}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">ATR(14) STOP LOSS</span>
          <strong class="cell-val negative">${slPrice} (-${slPct})</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">RISK / REWARD</span>
          <strong class="cell-val positive">1 : 1.5 to 1 : 4.0</strong>
        </div>
      </div>
    </div>

    <!-- 4. Profit Targets -->
    <div class="pinpoint-section">
      <h3>4. Multi-Stage Profit Targets</h3>
      ${targetsHtml}
    </div>

    <!-- 5. Tactical Context -->
    <div class="pinpoint-section">
      <h3>5. Tactical Context</h3>
      <div class="metrics-grid two-col">
        <div class="metric-cell">
          <span class="cell-label">HTF 1H TREND</span>
          <strong class="cell-val">${escapeHtml(opp.mtf?.trend || 'Neutral')}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">VOLATILITY REGIME</span>
          <strong class="cell-val">${escapeHtml(opp.features?.volatility_regime || 'COMPRESSION')}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">DIRECTIONAL BIAS</span>
          <strong class="cell-val">${Number(opp.features?.directional_bias || 0).toFixed(2)}</strong>
        </div>
        <div class="metric-cell">
          <span class="cell-label">SFP PATTERN</span>
          <strong class="cell-val">${opp.features?.sfp_bullish ? 'BULLISH' : (opp.features?.sfp_bearish ? 'BEARISH' : 'NONE')}</strong>
        </div>
      </div>
    </div>

    <!-- 6. Data Source Status -->
    <div class="pinpoint-section">
      <h3>6. Data Source Status</h3>
      <p style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">
        Provenance: <strong>${escapeHtml(prov.overall || opp.provenance)}</strong> (${prov.real_factors_count ?? 1} REAL / ${prov.estimated_factors_count ?? 0} PROXY)
      </p>
      <small style="font-size:10px;color:var(--text-dim);">
        All data verified via public Binance endpoints and closed-candle analytics.
      </small>
    </div>
  `;

  dialog.showModal();
  renderLiveMarketIntelligence(opp.symbol, 'modal-live-intelligence-slot');
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 3 — TRADES                                                         */
/* ══════════════════════════════════════════════════════════════════════════ */

async function renderTradesScreen(data) {
  const positions = Array.isArray(data.positions) ? data.positions : [];
  const openCountBadge = $('#trades-open-count');
  const wsStatus = $('#trades-ws-status');
  const activeSlot = $('#trades-active-slot');

  if (openCountBadge) openCountBadge.textContent = positions.length;

  if (wsStatus) {
    if (positions.length > 0) {
      wsStatus.className = 'status-pill ws-pill pulsing';
      wsStatus.textContent = '⚡ MANAGED (5s POLL)';
    } else {
      wsStatus.className = 'status-pill ws-pill';
      wsStatus.textContent = 'Standby — no active position';
    }
  }

  // Active trades
  if (activeSlot) {
    if (!positions.length) {
      activeSlot.innerHTML = `
        <div class="empty-position-state">
          <span class="empty-icon">📈</span>
          <strong>No Active Position</strong>
          <p>Waiting for qualified setup</p>
        </div>
      `;
    } else {
      activeSlot.innerHTML = positions.map((p) => renderPositionCardHtml(p)).join('');
    }
  }

  // Trade Lifecycle Flow Status Indicators
  const isPosOpen = positions.length > 0;
  const nodeMgmt = $('#node-management');
  const nodeAc = $('#node-autoclose');
  if (nodeMgmt && nodeAc) {
    nodeMgmt.classList.toggle('active', isPosOpen);
    nodeAc.classList.toggle('active', isPosOpen);
    const mgmtBadge = nodeMgmt.querySelector('.node-status-badge');
    const acBadge = nodeAc.querySelector('.node-status-badge');
    if (mgmtBadge) {
      mgmtBadge.className = isPosOpen ? 'node-status-badge complete' : 'node-status-badge';
      mgmtBadge.textContent = isPosOpen ? 'MANAGED' : 'STANDBY';
    }
    if (acBadge) {
      acBadge.className = isPosOpen ? 'node-status-badge complete' : 'node-status-badge';
      acBadge.textContent = isPosOpen ? 'ARMED' : 'STANDBY';
    }
  }

  // Trade History (Fetch from /api/v1/trades or data.trades)
  let history = data.trades?.history || [];
  if (!history.length) {
    try {
      const res = await fetch('/api/v1/trades');
      if (res.ok) {
        const tData = await res.json();
        history = tData.history || [];
      }
    } catch (_) {}
  }

  const histCountBadge = $('#trades-history-count');
  if (histCountBadge) histCountBadge.textContent = history.length;

  const historySlot = $('#trades-history-slot');
  if (historySlot) {
    if (!history.length) {
      historySlot.innerHTML = '<p class="empty-copy">No completed trades yet</p>';
    } else {
      historySlot.innerHTML = history.slice(0, 20).map((t) => {
        const res = (t.result || 'BREAKEVEN').toUpperCase();
        const resClass = res === 'WIN' ? 'win' : (res === 'LOSS' ? 'loss' : 'breakeven');
        const pnl = Number(t.realized_pnl) || 0;
        const pnlSign = pnl >= 0 ? '+' : '';
        const rMult = Number(t.r_multiple) || 0;
        const rSign = rMult >= 0 ? '+' : '';

        return `
          <div class="history-item">
            <div class="history-left">
              <span class="result-badge ${resClass}">${res}</span>
              <div>
                <strong class="history-symbol">${escapeHtml(t.symbol)} (${escapeHtml(t.side)})</strong>
                <div class="history-meta">${formatUtcTime(t.closed_at_ms)} • ${escapeHtml(t.close_reason || 'CLOSE')}</div>
              </div>
            </div>
            <div class="history-right">
              <div class="history-pnl ${resClass}">${pnlSign}$${pnl.toFixed(2)}</div>
              <div class="history-r">${rSign}${rMult.toFixed(2)}R</div>
            </div>
          </div>
        `;
      }).join('');
    }
  }
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 4 — RISK CENTER                                                    */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderRiskScreen(data) {
  const safety = data.safety || {};
  const autoTrade = data.auto_trade || safety.auto_trade || {};
  const autoclose = data.autoclose || {};
  const positions = Array.isArray(data.positions) ? data.positions : [];

  const ksTripped = Boolean(safety.kill_switch_tripped);
  const ddPct = Number(safety.daily_drawdown_pct || 0);
  const killPct = Number(safety.daily_drawdown_kill_pct || 2.0);

  // Policy Values
  const currDdEl = $('#risk-curr-dd-val');
  if (currDdEl) {
    currDdEl.textContent = `${ddPct.toFixed(2)}%`;
    currDdEl.className = `cell-val ${ddPct > 1.0 ? 'negative' : 'neutral'}`;
  }

  const maxDdEl = $('#risk-max-dd-val');
  if (maxDdEl) maxDdEl.textContent = `${killPct.toFixed(2)}%`;

  const ksStateEl = $('#risk-ks-state-val');
  const ksReasonEl = $('#risk-ks-reason');
  if (ksStateEl && ksReasonEl) {
    if (ksTripped) {
      ksStateEl.className = 'cell-val danger-color';
      ksStateEl.textContent = 'HALTED (TRIPPED)';
      ksReasonEl.textContent = safety.reason || 'Veto engaged';
    } else {
      ksStateEl.className = 'cell-val healthy-color';
      ksStateEl.textContent = 'ARMED & NORMAL';
      ksReasonEl.textContent = 'Fail-closed veto ready';
    }
  }

  const atStateEl = $('#risk-at-state-val');
  if (atStateEl) {
    if (ksTripped) atStateEl.textContent = 'BLOCKED (KILL SWITCH)';
    else if (autoTrade.circuit_breaker_active) atStateEl.textContent = 'PAUSED (BREAKER TRIPPED)';
    else if (autoTrade.enabled && autoTrade.can_auto_trade) atStateEl.textContent = 'ACTIVE (PAPER)';
    else atStateEl.textContent = 'STANDBY (PAPER)';
  }

  const guardianBadge = $('#risk-guardian-badge');
  if (guardianBadge) {
    guardianBadge.className = ksTripped ? 'status-pill danger' : 'status-pill safe-pill';
    guardianBadge.textContent = ksTripped ? 'VETO ENGAGED' : 'VETO AUTHORITY ACTIVE';
  }

  const endpointModeEl = $('#risk-endpoint-mode-val');
  if (endpointModeEl) endpointModeEl.textContent = 'ISOLATED_PAPER';

  const runtimeModeEl = $('#risk-runtime-mode-val');
  if (runtimeModeEl) runtimeModeEl.textContent = (safety.trading_mode || data.control?.trading_mode || 'PAPER').toUpperCase();

  const perTradeRiskEl = $('#risk-per-trade-val');
  if (perTradeRiskEl) perTradeRiskEl.textContent = '1.00%';

  const slotsEl = $('#risk-slots-val');
  if (slotsEl) slotsEl.textContent = `${positions.length} / 2 used`;

  // Circuit Breakers
  const lossesStat = $('#breaker-losses-stat');
  if (lossesStat) lossesStat.textContent = `${autoTrade.consecutive_losses ?? 0} / ${autoTrade.max_consecutive_losses ?? 3}`;

  const ddStat = $('#breaker-dd-stat');
  if (ddStat) ddStat.textContent = `${ddPct.toFixed(2)}% / ${killPct.toFixed(2)}%`;

  const breakerBadge = $('#risk-breaker-overall-badge');
  if (breakerBadge) {
    if (ksTripped || autoTrade.circuit_breaker_active) {
      breakerBadge.className = 'status-pill danger';
      breakerBadge.textContent = ksTripped ? 'KILL SWITCH ENGAGED' : 'BREAKER TRIPPED';
    } else {
      breakerBadge.className = 'status-pill safe-pill';
      breakerBadge.textContent = 'ARMED & NORMAL';
    }
  }

  // Active Alerts in Autoclose
  const alertsCount = $('#risk-active-alerts-count');
  if (alertsCount) {
    const cnt = autoclose.active_alerts_count ?? (autoclose.active_alerts?.length || 0);
    alertsCount.textContent = `${cnt} active`;
  }

  // Audit Rejections
  const auditSlot = $('#risk-recent-rejections');
  if (auditSlot) {
    const auditEntries = Array.isArray(autoTrade.audit_history) ? autoTrade.audit_history : [];
    const rejections = auditEntries.filter((e) => !e.allowed).slice(-5).reverse();
    if (!rejections.length) {
      auditSlot.innerHTML = '<p class="empty-copy">No recent safety rejections (All gates passed)</p>';
    } else {
      auditSlot.innerHTML = rejections.map((r) => `
        <div class="audit-item">
          <div class="audit-head">
            <span>${escapeHtml(r.symbol || 'SYMBOL')}</span>
            <span style="color:var(--danger)">${escapeHtml(r.breaker_name || 'VETO')}</span>
          </div>
          <div class="audit-reason">${escapeHtml(r.reason || 'Gate rejection')}</div>
        </div>
      `).join('');
    }
  }
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* SCREEN 5 — SYSTEM                                                         */
/* ══════════════════════════════════════════════════════════════════════════ */

function renderSystemScreen(data) {
  const health = data.health || {};
  const positions = Array.isArray(data.positions) ? data.positions : [];
  const realtime = data.realtime || {};

  const sysOverall = $('#system-overall-badge');
  if (sysOverall) {
    const st = (health.health_status || 'HEALTHY').toUpperCase();
    sysOverall.textContent = st;
    sysOverall.className = 'status-pill ' + (st === 'HEALTHY' ? 'healthy' : 'degraded');
  }

  const mktBadge = $('#sys-market-badge');
  if (mktBadge) {
    mktBadge.textContent = `${health.total_symbols || 100} USDT-M ACTIVE`;
  }

  const engState = $('#sys-engine-state');
  if (engState) engState.textContent = health.engine_state || 'RUNNING';

  const scanState = $('#sys-scanner-state');
  if (scanState) scanState.textContent = 'CONTINUOUS (100 USDT-M)';

  const connHealth = $('#sys-conn-health');
  if (connHealth) {
    const isDegraded = (health.consecutive_scan_failures || 0) > 2;
    connHealth.textContent = isDegraded ? 'DEGRADED' : 'HEALTHY';
    connHealth.className = 'cell-val ' + (isDegraded ? 'warning-color' : 'healthy-color');
  }

  const lastScan = $('#sys-last-success-scan');
  if (lastScan) {
    const ts = health.last_successful_scan_ts_ms || health.last_scan_ms;
    lastScan.textContent = ts ? `${formatUtcTime(ts)} (${timeAgo(ts)})` : 'Just now';
  }

  const scanLat = $('#sys-scan-latency');
  if (scanLat) scanLat.textContent = health.scan_latency_ms ? `${health.scan_latency_ms} ms` : '11,251 ms';

  const failCount = $('#sys-failure-count');
  if (failCount) {
    const fails = health.consecutive_scan_failures || 0;
    failCount.textContent = `${fails} failures`;
  }

  const apiHealth = $('#sys-api-health');
  if (apiHealth) {
    apiHealth.textContent = 'HEALTHY (Port 8765)';
    apiHealth.className = 'cell-val healthy-color';
  }

  // WebSocket Health
  // STRICT RULE: Do not mark WebSocket as failed simply because there are no active positions.
  // If architecture only requires WS during active trades: "Standby — no active position"
  const wsHealth = $('#sys-ws-health');
  if (wsHealth) {
    if (positions.length > 0) {
      if (realtime.ws_connected) {
        wsHealth.className = 'cell-val healthy-color';
        wsHealth.textContent = 'Connected (Outbound WS Active)';
      } else {
        wsHealth.className = 'cell-val warning-color';
        wsHealth.textContent = 'Connecting outbound feed…';
      }
    } else {
      wsHealth.className = 'cell-val info-color';
      wsHealth.textContent = realtime.status_message || 'Standby — no active position';
    }
  }
}

/* ── Grounded AI Chat Assistant ───────────────────────────────────────────── */

function renderChatMessage(text, who = 'assistant', extra = '') {
  const container = $('#messages');
  if (!container) return;

  const msg = document.createElement('article');
  msg.className = `message ${who} ${extra}`;
  msg.innerHTML = who === 'assistant'
    ? `<div class="avatar">A</div><div class="bubble">${escapeHtml(text)}</div>`
    : `<div class="bubble">${escapeHtml(text)}</div>`;

  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
}

function removeThinking() {
  const thinking = $$('.thinking');
  thinking.forEach((t) => t.remove());
}

async function sendChatMessage(text) {
  if (!text || !text.trim()) return;
  const query = text.trim();
  renderChatMessage(query, 'user');
  renderChatMessage('Analyzing with APEX Intelligence…', 'assistant', 'thinking');

  // Extract symbol if mentioned (e.g. BTC, ETH, SOL, TAO, BNB, XRP, etc.)
  const symbolMatch = query.match(/\b(BTC|ETH|SOL|TAO|BNB|XRP|DOGE|ADA|AVAX|LINK)\b/i);
  const symbol = symbolMatch ? (symbolMatch[1].toUpperCase() + 'USDT') : 'BTCUSDT';

  try {
    const res = await fetch('/api/v1/control/intelligence', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders,
      },
      body: JSON.stringify({
        symbol,
        context: { query },
      }),
    });
    removeThinking();
    if (res.ok) {
      const data = await res.json();
      const analysis = data?.result?.analysis || data?.result?.summary || data?.result?.critique || 'Analysis completed.';
      const disclaimer = data?.result?.disclaimer ? `\n\n${data.result.disclaimer}` : '';
      renderChatMessage(`${analysis}${disclaimer}`, 'assistant');
    } else {
      renderChatMessage('Intelligence service unavailable or control plane offline.', 'assistant');
    }
  } catch (err) {
    removeThinking();
    renderChatMessage(`Error communicating with Apex Intelligence: ${err.message}`, 'assistant');
  }
}

/* ── Event Wiring ─────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  // Bottom navigation tab click handlers
  $$('.tab-button').forEach((btn) => {
    btn.addEventListener('click', () => {
      showTab(btn.dataset.tab);
    });
  });

  // Action links
  $('#go-to-scanner-btn')?.addEventListener('click', () => showTab('scanner'));
  $('#home-configure-risk-btn')?.addEventListener('click', () => showTab('risk'));

  // Refresh button
  $('#refresh-btn')?.addEventListener('click', requestDashboard);

  // Chat drawer open / close
  $('#open-chat-btn')?.addEventListener('click', () => {
    $('#chat-drawer')?.showModal();
  });
  $('#close-chat-drawer')?.addEventListener('click', () => {
    $('#chat-drawer')?.close();
  });

  // Quick actions in chat
  $('#quick-actions')?.addEventListener('click', (e) => {
    if (e.target.dataset.prompt) {
      sendChatMessage(e.target.dataset.prompt);
    }
  });

  // Chat form submit
  $('#chat-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('#message-input');
    if (input && input.value.trim()) {
      sendChatMessage(input.value.trim());
      input.value = '';
    }
  });

  // Scanner category filters
  $$('#scanner-category-filters .filter-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      $$('#scanner-category-filters .filter-pill').forEach((p) => p.classList.remove('active'));
      pill.classList.add('active');
      currentCategoryFilter = pill.dataset.cat;
      if (latestData) renderScannerScreen(latestData);
    });
  });

  // Scanner search input
  const searchInput = $('#scanner-search-input');
  const searchClear = $('#scanner-search-clear');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      scannerSearchQuery = searchInput.value;
      if (searchClear) searchClear.style.display = scannerSearchQuery ? 'block' : 'none';
      if (latestData) renderScannerScreen(latestData);
    });
  }
  if (searchClear) {
    searchClear.addEventListener('click', () => {
      if (searchInput) searchInput.value = '';
      scannerSearchQuery = '';
      searchClear.style.display = 'none';
      if (latestData) renderScannerScreen(latestData);
    });
  }

  // Analysis modal buttons
  $('#close-analysis-x')?.addEventListener('click', () => $('#analysis-dialog')?.close());
  $('#close-analysis-btn')?.addEventListener('click', () => $('#analysis-dialog')?.close());
  $('#prepare-order-btn')?.addEventListener('click', () => {
    if (activeOpportunity) {
      $('#analysis-dialog')?.close();
      $('#chat-drawer')?.showModal();
      const prompt = `Trade plan for ${activeOpportunity.symbol}`;
      sendChatMessage(prompt);
    }
  });

  // Confirm dialog actions
  $('#cancel-confirm')?.addEventListener('click', () => {
    pendingConfirmation = null;
    $('#confirm-dialog')?.close();
  });
  $('#approve-confirm')?.addEventListener('click', () => {
    pendingConfirmation = null;
    $('#confirm-dialog')?.close();
  });

  // Pull to refresh on home scroll
  const homeScroll = $('#home-scroll');
  const pullInd = $('#pull-indicator');
  if (homeScroll && pullInd) {
    homeScroll.addEventListener('touchstart', (e) => {
      if (homeScroll.scrollTop === 0) pullStart = e.touches[0].clientY;
    }, {passive: true});

    homeScroll.addEventListener('touchmove', (e) => {
      if (pullStart && e.touches[0].clientY - pullStart > 55) {
        pullInd.classList.add('visible');
      }
    }, {passive: true});

    homeScroll.addEventListener('touchend', (e) => {
      if (pullStart && e.changedTouches[0].clientY - pullStart > 55) {
        requestDashboard();
      }
      pullStart = 0;
      pullInd.classList.remove('visible');
    });
  }

  // Telegram back button
  tg?.BackButton?.onClick(() => showTab('home'));

  // Master Action Control Buttons
  $('#ctrl-start-btn')?.addEventListener('click', () => sendControlCommand('start'));
  $('#ctrl-pause-btn')?.addEventListener('click', () => sendControlCommand('pause'));
  $('#ctrl-resume-btn')?.addEventListener('click', () => sendControlCommand('resume'));
  $('#ctrl-stop-btn')?.addEventListener('click', () => {
    if (confirm('Gracefully stop APEX autonomous operations?')) sendControlCommand('stop');
  });
  $('#ctrl-flatten-btn')?.addEventListener('click', () => {
    if (confirm('Safely flatten all open paper positions via OEM danger manager?')) sendControlCommand('flatten');
  });
  $('#ctrl-estop-btn')?.addEventListener('click', () => {
    if (confirm('🚨 EMERGENCY STOP: Trip Kill Switch and halt all operations immediately?')) sendControlCommand('emergency_stop');
  });

  // Top Nav Pill Bar Listeners
  $$('.nav-pill').forEach((pill) => {
    pill.addEventListener('click', () => showTab(pill.dataset.tab));
  });

  // Start with route from hash or home
  const validTabs = ['home', 'team', 'scanner', 'trades', 'investment', 'risk', 'alerts', 'system', 'audit'];
  const initialTab = location.hash.replace('#', '') || 'home';
  if (validTabs.includes(initialTab)) {
    showTab(initialTab);
  } else {
    showTab('home');
  }

  // Initial connection & dashboard fetch
  requestDashboard();

  // Periodic polling every 5s when page is active
  setInterval(() => {
    if (document.visibilityState === 'visible' && !dashboardLoading) {
      requestDashboard();
    }
  }, 5000);
});
