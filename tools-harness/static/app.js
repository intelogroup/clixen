// ── Clixen App Entry Point ───────────────────────────────────────
// Orchestrates initialization of sub-modules

document.addEventListener('DOMContentLoaded', () => {
  // 1. Initialize core state
  ensureActiveSession();
  
  // 2. Initial renders
  renderSidebar();
  checkWelcome();
  initMainView();
  
  // 3. Refresh status and indicators
  refreshWorkspaceState();
  refreshHotModels();
  refreshOllamaHealth();
  
  // 4. Start background tasks
  setInterval(refreshAutomationRuns, 5000);
  setInterval(refreshWorkflows, 10000);
  setInterval(refreshHotModels, 12000);
  setInterval(refreshOllamaHealth, 15000);
  
  // 5. Final UI focus
  const inputEl = document.getElementById('input');
  if (inputEl) inputEl.focus();

  // 6. Overflow menu toggle
  const overflowBtn = document.getElementById('header-overflow-btn');
  const overflowMenu = document.getElementById('header-overflow-menu');
  if (overflowBtn && overflowMenu) {
    overflowBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = overflowMenu.classList.toggle('open');
      overflowBtn.setAttribute('aria-expanded', isOpen);
    });
    document.addEventListener('click', () => {
      overflowMenu.classList.remove('open');
      overflowBtn.setAttribute('aria-expanded', 'false');
    });
    overflowMenu.addEventListener('click', (e) => e.stopPropagation());
  }
  
  console.log('Clixen UI initialized.');
});

function closeOverflow() {
  const menu = document.getElementById('header-overflow-menu');
  const btn  = document.getElementById('header-overflow-btn');
  if (menu) menu.classList.remove('open');
  if (btn)  btn.setAttribute('aria-expanded', 'false');
}
