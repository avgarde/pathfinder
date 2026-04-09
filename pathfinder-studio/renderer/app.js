/**
 * Pathfinder Studio - Main Renderer Process
 *
 * This is the heart of the IDE. It handles:
 * - WebSocket connection to Pathfinder server
 * - UI state management
 * - Event dispatching and rendering
 * - User interactions
 */

// ============================================
// Global State
// ============================================

const state = {
  connected: false,
  running: false,
  runId: null,
  currentStep: 0,
  ws: null,
  wsUrl: 'ws://localhost:9720',

  // Exploration data
  currentUrl: null,
  currentPageTitle: null,
  screenshot: null,

  // Model data
  screens: [],
  transitions: [],
  capabilities: [],
  coverage: 0,

  // Trace data
  steps: [],

  // Flow data
  flows: [],

  // Pending user input
  pendingInput: null
};

// ============================================
// UI Elements
// ============================================

const ui = {
  // Toolbar
  urlInput: document.getElementById('urlInput'),
  appNameInput: document.getElementById('appNameInput'),
  maxActionsInput: document.getElementById('maxActionsInput'),
  startBtn: document.getElementById('startBtn'),
  stopBtn: document.getElementById('stopBtn'),
  connectionStatus: document.getElementById('connectionStatus'),
  connectionStatusDot: document.querySelector('.status-dot'),
  connectionStatusText: document.querySelector('.status-text'),
  runIdDisplay: document.getElementById('runId'),
  runIdValue: document.getElementById('runIdValue'),

  // Device Viewport
  deviceTitle: document.getElementById('deviceTitle'),
  screenshotImg: document.getElementById('screenshotImg'),
  viewportPlaceholder: document.getElementById('viewportPlaceholder'),
  currentUrlDisplay: document.getElementById('currentUrl'),
  stepBadge: document.getElementById('stepBadge'),
  stepNumber: document.getElementById('stepNumber'),

  // Trace Panel
  traceList: document.getElementById('traceList'),

  // State Panel
  screenCount: document.getElementById('screenCount'),
  transitionCount: document.getElementById('transitionCount'),
  capabilityCount: document.getElementById('capabilityCount'),
  coveragePercent: document.getElementById('coveragePercent'),
  coverageBar: document.getElementById('coverageBar'),
  screenList: document.getElementById('screenList'),
  modelGraph: document.getElementById('modelGraph'),

  // Flow Panel
  flowList: document.getElementById('flowList'),

  // Modal
  inputModal: document.getElementById('inputModal'),
  inputField: document.getElementById('inputField'),
  submitInputBtn: document.getElementById('submitInputBtn'),
  cancelInputBtn: document.getElementById('cancelInputBtn'),

  // Toast
  toast: document.getElementById('toast')
};

// ============================================
// Connection Management
// ============================================

function connectWebSocket() {
  if (state.ws) {
    return;
  }

  state.ws = new WebSocket(state.wsUrl);

  state.ws.onopen = () => {
    console.log('[WS] Connected');
    setConnected(true);
  };

  state.ws.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      handleWebSocketMessage(message);
    } catch (err) {
      console.error('[WS] Failed to parse message:', err);
    }
  };

  state.ws.onerror = (error) => {
    console.error('[WS] Error:', error);
  };

  state.ws.onclose = () => {
    console.log('[WS] Disconnected');
    state.ws = null;
    setConnected(false);
    // Attempt reconnect after 2 seconds
    setTimeout(connectWebSocket, 2000);
  };
}

function sendCommand(command) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showToast('Not connected to server', 'error');
    return false;
  }

  state.ws.send(JSON.stringify(command));
  return true;
}

// ============================================
// WebSocket Message Handling
// ============================================

function handleWebSocketMessage(message) {
  const { event_type, ...data } = message;

  console.log('[Event]', event_type, data);

  switch (event_type) {
    case 'exploration_started':
      handleExplorationStarted(data);
      break;
    case 'step_started':
      handleStepStarted(data);
      break;
    case 'perception_complete':
      handlePerceptionComplete(data);
      break;
    case 'model_updated':
      handleModelUpdated(data);
      break;
    case 'action_planned':
      handleActionPlanned(data);
      break;
    case 'action_executed':
      handleActionExecuted(data);
      break;
    case 'input_required':
      handleInputRequired(data);
      break;
    case 'flow_detected':
      handleFlowDetected(data);
      break;
    case 'exploration_complete':
      handleExplorationComplete(data);
      break;
    default:
      console.warn('[Event] Unknown event type:', event_type);
  }
}

function handleExplorationStarted(data) {
  state.running = true;
  state.runId = data.run_id;
  state.currentStep = 0;
  state.steps = [];
  state.flows = [];
  state.screens = [];
  state.transitions = [];
  state.capabilities = [];
  state.coverage = 0;

  ui.startBtn.disabled = true;
  ui.stopBtn.disabled = false;
  ui.runIdValue.textContent = state.runId;
  ui.runIdDisplay.style.display = 'inline';

  ui.traceList.innerHTML = '';
  ui.flowList.innerHTML = '<div class="empty-state">No flows discovered yet</div>';
  ui.screenList.innerHTML = '<div class="empty-state-small">None yet</div>';

  updateStatePanel();
  showToast(`Exploration started: ${state.runId}`, 'success');
}

function handleStepStarted(data) {
  state.currentStep = data.step || data.step_number || 0;

  const entry = {
    step: state.currentStep,
    timestamp: new Date(),
    description: '',
    screenshot: null,
    action: null,
    actionSuccess: null
  };

  state.steps.push(entry);
  addTraceEntry(entry);
}

function handlePerceptionComplete(data) {
  // Find current step and update
  const step = state.steps[state.steps.length - 1];
  if (!step) return;

  step.description = data.screen_description || '';
  // screenshot_base64 from server is already a full data URI ("data:image/png;base64,...")
  step.screenshot = data.screenshot_base64 || null;
  state.currentPageTitle = data.page_title || 'Unknown';
  state.currentUrl = data.page_url || data.current_url || '';

  // Update device viewport with latest screenshot
  if (step.screenshot) {
    // Server sends a complete data URI — use it directly
    ui.screenshotImg.src = step.screenshot;
    ui.screenshotImg.style.display = 'block';
    ui.viewportPlaceholder.style.display = 'none';
    ui.stepBadge.style.display = 'block';
    ui.stepNumber.textContent = state.currentStep;
  }

  ui.deviceTitle.textContent = `Device — ${state.currentPageTitle}`;
  ui.currentUrlDisplay.textContent = state.currentUrl;

  // Update trace entry
  updateTraceEntry(state.steps.length - 1, {
    description: step.description,
    screenshot: step.screenshot
  });
}

function handleModelUpdated(data) {
  // Events send integer counts and a screen_id for the current screen
  const screenCount = data.screens_count || 0;
  const transitionCount = data.transitions_count || 0;
  const capabilityCount = data.capabilities_count || 0;
  state.coverage = (data.coverage_estimate || 0) * 100; // Server sends 0.0-1.0, we display %

  // Build up screen list incrementally from events
  if (data.screen_id && !state.screens.find(s => s.id === data.screen_id)) {
    state.screens.push({ id: data.screen_id, name: data.screen_id, isNew: data.new_screen });
  }

  // Keep counts in sync (model may know more than our incremental list)
  state.screenCount = screenCount;
  state.transitionCount = transitionCount;
  state.capabilityCount = capabilityCount;

  updateStatePanel();
  renderModelGraph();
}

function handleActionPlanned(data) {
  const step = state.steps[state.steps.length - 1];
  if (!step) return;

  step.action = {
    type: data.action_type,
    description: data.action_description,
    reasoning: data.reasoning
  };

  updateTraceEntry(state.steps.length - 1, {
    action: step.action
  });
}

function handleActionExecuted(data) {
  const step = state.steps[state.steps.length - 1];
  if (!step) return;

  step.actionSuccess = data.success;

  updateTraceEntry(state.steps.length - 1, {
    actionSuccess: data.success,
    error: data.error
  });
}

function handleInputRequired(data) {
  const fieldName = data.field_name || data.field || 'unknown';
  state.pendingInput = {
    field: fieldName,
    prompt: data.context || `Enter value for ${fieldName}:`
  };

  showInputModal(state.pendingInput);
}

function handleFlowDetected(data) {
  const flow = {
    name: data.flow_name || 'Unknown flow',
    category: data.flow_category || data.category || 'unknown',
    type: data.flow_type || 'observed',
    importance: data.importance || 0.5,
    stepCount: data.step_count || 0
  };

  state.flows.push(flow);
  addFlowItem(flow);
}

function handleExplorationComplete(data) {
  state.running = false;
  ui.startBtn.disabled = false;
  ui.stopBtn.disabled = true;
  ui.runIdDisplay.style.display = 'none';

  showToast('Exploration complete', 'success');
}

// ============================================
// UI State Management
// ============================================

function setConnected(connected) {
  state.connected = connected;

  if (connected) {
    ui.connectionStatus.classList.remove('disconnected');
    ui.connectionStatus.classList.add('connected');
    ui.connectionStatusText.textContent = 'Connected';
    ui.connectionStatusDot.style.display = 'inline-block';
  } else {
    ui.connectionStatus.classList.remove('connected');
    ui.connectionStatus.classList.add('disconnected');
    ui.connectionStatusText.textContent = 'Disconnected';
    ui.connectionStatusDot.style.display = 'inline-block';
  }
}

// ============================================
// Trace Panel Management
// ============================================

function addTraceEntry(step) {
  const entry = document.createElement('div');
  entry.className = 'trace-entry current';
  entry.dataset.stepIndex = state.steps.length - 1;
  entry.innerHTML = `
    <div class="trace-step-num">${step.step}</div>
    <div class="trace-details">
      <div class="trace-description">${escapeHtml(step.description || 'Loading...')}</div>
      <div class="trace-action" style="opacity: 0.5;">Waiting for action...</div>
      <div class="trace-timestamp">${formatTime(step.timestamp)}</div>
    </div>
  `;

  const stepIdx = state.steps.length - 1;
  entry.addEventListener('click', () => {
    showStepScreenshot(stepIdx);
  });

  // Remove 'current' class from previous entries
  ui.traceList.querySelectorAll('.trace-entry.current').forEach(el => el.classList.remove('current'));
  ui.traceList.appendChild(entry);

  // Auto-scroll to latest
  setTimeout(() => {
    ui.traceList.parentElement.scrollTop = ui.traceList.parentElement.scrollHeight;
  }, 0);
}

function updateTraceEntry(index, updates) {
  const step = state.steps[index];
  const entry = document.querySelector(`[data-step-index="${index}"]`);
  if (!entry) return;

  if (updates.description !== undefined) {
    const descDiv = entry.querySelector('.trace-description');
    if (descDiv) {
      descDiv.textContent = updates.description || 'Loading...';
    }
  }

  if (updates.action !== undefined || updates.actionSuccess !== undefined) {
    const actionDiv = entry.querySelector('.trace-action');
    if (actionDiv) {
      const action = step.action;
      if (action) {
        const icon = getActionIcon(action.type);
        actionDiv.innerHTML = `<span class="trace-action-icon">${icon}</span> ${escapeHtml(action.description)}`;
      }
    }

    // Update success/error styling
    if (updates.actionSuccess === true) {
      entry.classList.add('success');
      entry.classList.remove('error');
    } else if (updates.actionSuccess === false) {
      entry.classList.add('error');
      entry.classList.remove('success');
    }
  }
}

function showStepScreenshot(index) {
  const step = state.steps[index];
  if (!step || !step.screenshot) return;

  ui.screenshotImg.src = step.screenshot;
  ui.screenshotImg.style.display = 'block';
  ui.viewportPlaceholder.style.display = 'none';
  ui.stepNumber.textContent = step.step;
  ui.stepBadge.style.display = 'block';
}

function getActionIcon(actionType) {
  const icons = {
    tap: '🖱️',
    click: '🖱️',
    type: '⌨️',
    input: '⌨️',
    back: '↩️',
    scroll: '📜',
    wait: '⏳'
  };
  return icons[actionType] || '⚡';
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ============================================
// State Panel Management
// ============================================

function updateStatePanel() {
  ui.screenCount.textContent = state.screenCount || state.screens.length;
  ui.transitionCount.textContent = state.transitionCount || 0;
  ui.capabilityCount.textContent = state.capabilityCount || 0;
  ui.coveragePercent.textContent = Math.round(state.coverage) + '%';
  ui.coverageBar.style.width = Math.min(state.coverage, 100) + '%';

  // Update screen list from incrementally collected screens
  if (state.screens.length === 0) {
    ui.screenList.innerHTML = '<div class="empty-state-small">None yet</div>';
  } else {
    ui.screenList.innerHTML = state.screens
      .map((screen) => {
        return `<div class="screen-item visited">${escapeHtml(screen.name || screen.id || 'unknown')}</div>`;
      })
      .join('');
  }
}

function renderModelGraph() {
  const svg = ui.modelGraph;
  const width = svg.clientWidth || 300;
  const height = 200;

  // Clear previous
  svg.innerHTML = '';

  if (state.screens.length === 0) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#606070" font-size="12">No screens yet</text>';
    return;
  }

  // Simple circular layout
  const radius = Math.min(width, height) / 3;
  const centerX = width / 2;
  const centerY = height / 2;
  const angleStep = (Math.PI * 2) / Math.max(state.screens.length, 1);

  // Draw arrow marker (even if no transitions yet — needed once we add transition tracking)
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  marker.setAttribute('id', 'arrowhead');
  marker.setAttribute('markerWidth', '10');
  marker.setAttribute('markerHeight', '10');
  marker.setAttribute('refX', '9');
  marker.setAttribute('refY', '3');
  marker.setAttribute('orient', 'auto');
  const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
  polygon.setAttribute('points', '0 0, 10 3, 0 6');
  polygon.setAttribute('fill', '#444466');
  marker.appendChild(polygon);
  defs.appendChild(marker);
  svg.appendChild(defs);

  // Draw screens (circles)
  state.screens.forEach((screen, idx) => {
    const x = centerX + radius * Math.cos(idx * angleStep);
    const y = centerY + radius * Math.sin(idx * angleStep);

    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', x);
    circle.setAttribute('cy', y);
    circle.setAttribute('r', '12');
    circle.setAttribute('fill', '#7c5cff');
    circle.setAttribute('stroke', '#e0e0e0');
    circle.setAttribute('stroke-width', '1');
    svg.appendChild(circle);

    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', x);
    text.setAttribute('y', y + 4);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('font-size', '8');
    text.setAttribute('fill', '#fff');
    text.textContent = (idx + 1).toString();
    svg.appendChild(text);
  });
}

// ============================================
// Flow Panel Management
// ============================================

function addFlowItem(flow) {
  const item = document.createElement('div');
  item.className = 'flow-item';

  const categoryClass = `category-${flow.category || 'other'}`.toLowerCase();
  const typeClass = `type-${flow.type || 'observed'}`.toLowerCase();

  item.innerHTML = `
    <div class="flow-name">${escapeHtml(flow.name)}</div>
    <div class="flow-badges">
      <span class="flow-badge ${categoryClass}">${escapeHtml((flow.category || 'Other').toUpperCase())}</span>
      <span class="flow-badge ${typeClass}">${escapeHtml((flow.type || 'Observed').toUpperCase())}</span>
    </div>
    <div class="flow-meta">
      <span>${flow.stepCount || 0} steps</span>
    </div>
    <div class="flow-importance">
      <span>Importance:</span>
      <div class="importance-bar">
        <div class="importance-fill" style="width: ${(flow.importance || 0) * 100}%;"></div>
      </div>
      <span>${Math.round((flow.importance || 0) * 100)}%</span>
    </div>
  `;

  if (ui.flowList.querySelector('.empty-state')) {
    ui.flowList.innerHTML = '';
  }

  ui.flowList.appendChild(item);
}

// ============================================
// Input Modal
// ============================================

function showInputModal(input) {
  ui.inputField.value = '';
  ui.inputField.placeholder = `Enter value for "${input.field}"...`;
  ui.inputModal.style.display = 'flex';
  ui.inputField.focus();
}

function hideInputModal() {
  ui.inputModal.style.display = 'none';
  state.pendingInput = null;
}

function submitInput() {
  if (!state.pendingInput) return;

  const value = ui.inputField.value.trim();
  if (!value) {
    showToast('Please enter a value', 'warning');
    return;
  }

  const command = {
    command: 'supply_input',
    field: state.pendingInput.field,
    value: value
  };

  if (sendCommand(command)) {
    hideInputModal();
    showToast(`Input supplied for "${state.pendingInput.field}"`, 'success');
  }
}

// ============================================
// Toast Notifications
// ============================================

function showToast(message, type = 'info') {
  ui.toast.textContent = message;
  ui.toast.className = `toast ${type}`;
  ui.toast.style.display = 'block';

  setTimeout(() => {
    ui.toast.style.display = 'none';
  }, 3000);
}

// ============================================
// Event Handlers
// ============================================

ui.startBtn.addEventListener('click', () => {
  const url = ui.urlInput.value.trim();
  const appName = ui.appNameInput.value.trim() || 'Untitled Exploration';
  const maxActions = parseInt(ui.maxActionsInput.value) || 30;

  if (!url) {
    showToast('Please enter a URL', 'warning');
    return;
  }

  const command = {
    command: 'start_exploration',
    url: url,
    name: appName,
    max_actions: maxActions,
    headless: true
  };

  if (sendCommand(command)) {
    showToast('Starting exploration...', 'info');
  }
});

ui.stopBtn.addEventListener('click', () => {
  const command = { command: 'stop_exploration' };
  if (sendCommand(command)) {
    showToast('Stopping exploration...', 'info');
  }
});

ui.submitInputBtn.addEventListener('click', submitInput);
ui.cancelInputBtn.addEventListener('click', hideInputModal);

ui.inputField.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') {
    submitInput();
  }
});

ui.inputField.addEventListener('keypress', (e) => {
  if (e.key === 'Escape') {
    hideInputModal();
  }
});

// ============================================
// Initialization
// ============================================

function init() {
  console.log('[App] Initializing Pathfinder Studio');

  // Set default values
  ui.appNameInput.value = 'Untitled Exploration';
  ui.maxActionsInput.value = '30';

  // Initial state
  setConnected(false);
  updateStatePanel();

  // Connect to WebSocket
  connectWebSocket();

  console.log('[App] Ready');
}

// Start the app when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
