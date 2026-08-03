async function fetchWorkspaceState() {
  const res = await fetch('/api/workspace', {credentials: 'same-origin'});
  if (!res.ok) throw new Error('Failed to load workspace state');
  return res.json();
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    method: options.method || 'GET',
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    credentials: 'same-origin',
    body: options.body,
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(text);
    }
  }
  if (!response.ok) {
    throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  }
  return data;
}

function setMode(hasAccount) {
  const title = document.getElementById('auth-page-title');
  const copy = document.getElementById('auth-page-copy');
  const kicker = document.getElementById('auth-kicker');
  const cardTitle = document.getElementById('auth-card-title');
  const submit = document.getElementById('auth-submit');
  const status = document.getElementById('auth-status');
  const footerText = document.getElementById('auth-footer-text');
  const footerMode = document.getElementById('auth-footer-mode');

  const displayNameField = document.getElementById('display-name-field');
  const workspaceNameField = document.getElementById('workspace-name-field');
  const roleField = document.getElementById('role-field');

  const footerNormal = document.getElementById('auth-footer-normal');
  const footerReset = document.getElementById('auth-footer-reset');
  const cancelReset = document.getElementById('auth-cancel-reset');

  const registerMode = !hasAccount;
  displayNameField.style.display = registerMode ? '' : 'none';
  workspaceNameField.style.display = registerMode ? '' : 'none';
  roleField.style.display = registerMode ? '' : 'none';

  footerNormal.style.display = registerMode ? '' : 'none';
  footerReset.style.display = hasAccount ? '' : 'none';
  cancelReset.style.display = 'none';

  if (registerMode) {
    title.textContent = 'Create the first local workspace account';
    copy.textContent = 'This device does not have an owner account yet. The first submission creates it and signs you in immediately.';
    kicker.textContent = 'First run';
    cardTitle.textContent = 'Create account';
    submit.textContent = 'Create local account';
    footerText.textContent = 'This machine is still unclaimed.';
    footerMode.textContent = 'owner account will be created here';
    status.textContent = 'No local account detected.';
  } else {
    title.textContent = 'Sign in to your local workspace';
    copy.textContent = 'Use the account stored on this machine to unlock chat, uploads, and local integrations.';
    kicker.textContent = 'Welcome back';
    cardTitle.textContent = 'Sign in';
    submit.textContent = 'Sign in';
    footerText.textContent = 'Local account detected.';
    footerMode.textContent = 'enter the saved email and password';
    status.textContent = 'Account found on this machine.';
  }

  status.className = 'auth-status';
}

function enterResetMode() {
  const cardTitle = document.getElementById('auth-card-title');
  const submit = document.getElementById('auth-submit');
  const cancelReset = document.getElementById('auth-cancel-reset');
  const status = document.getElementById('auth-status');
  const footerNormal = document.getElementById('auth-footer-normal');
  const footerReset = document.getElementById('auth-footer-reset');
  const emailField = document.getElementById('email');
  const confirmField = document.getElementById('confirm-password-field');
  const displayNameField = document.getElementById('display-name-field');
  const workspaceNameField = document.getElementById('workspace-name-field');
  const roleField = document.getElementById('role-field');

  emailField.readOnly = true;
  displayNameField.style.display = 'none';
  workspaceNameField.style.display = 'none';
  roleField.style.display = 'none';
  confirmField.style.display = '';
  footerNormal.style.display = 'none';
  footerReset.style.display = 'none';
  cancelReset.style.display = '';

  cardTitle.textContent = 'Reset password';
  submit.textContent = 'Reset password';
  status.textContent = 'Enter a new password (min 8 characters).';
  status.className = 'auth-status';
}

function exitResetMode(hasAccount) {
  const emailField = document.getElementById('email');
  const confirmField = document.getElementById('confirm-password-field');
  const passwordField = document.getElementById('password');
  const cancelReset = document.getElementById('auth-cancel-reset');

  emailField.readOnly = false;
  confirmField.style.display = 'none';
  passwordField.value = '';
  document.getElementById('confirm-password').value = '';
  cancelReset.style.display = 'none';

  setMode(hasAccount);
}

document.addEventListener('DOMContentLoaded', async () => {
  const form = document.getElementById('auth-form');
  const submit = document.getElementById('auth-submit');
  const cancelReset = document.getElementById('auth-cancel-reset');
  const status = document.getElementById('auth-status');
  const resetAction = document.getElementById('auth-footer-reset-action');
  let hasAccount = false;
  let resetMode = false;

  try {
    const state = await fetchWorkspaceState();
    hasAccount = !!state?.auth?.has_account;
    setMode(hasAccount);
    if (state?.user?.authenticated) {
      window.location.assign('/app');
      return;
    }
  } catch (err) {
    status.textContent = err.message || 'Failed to initialize login page.';
    status.className = 'auth-status error';
  }

  if (resetAction) {
    resetAction.addEventListener('click', () => {
      resetMode = true;
      enterResetMode();
    });
  }

  if (cancelReset) {
    cancelReset.addEventListener('click', () => {
      resetMode = false;
      exitResetMode(hasAccount);
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    submit.disabled = true;
    status.className = 'auth-status';

    const formData = new FormData(form);
    const payload = Object.fromEntries(formData.entries());

    if (resetMode) {
      const password = payload.password || '';
      const confirm = payload.confirm_password || '';
      if (password.length < 8) {
        status.textContent = 'Password must be at least 8 characters.';
        status.className = 'auth-status error';
        submit.disabled = false;
        return;
      }
      if (password !== confirm) {
        status.textContent = 'Passwords do not match.';
        status.className = 'auth-status error';
        submit.disabled = false;
        return;
      }
      status.textContent = 'Resetting password…';
      try {
        await apiJson('/api/auth/reset-password', {
          method: 'POST',
          body: JSON.stringify({email: payload.email, password: password}),
        });
        status.textContent = 'Password reset. Opening workspace…';
        status.className = 'auth-status success';
        window.location.assign('/app');
      } catch (err) {
        status.textContent = err.message || 'Password reset failed.';
        status.className = 'auth-status error';
      } finally {
        submit.disabled = false;
      }
      return;
    }

    status.textContent = hasAccount ? 'Signing in…' : 'Creating account…';

    const endpoint = hasAccount ? '/api/auth/login' : '/api/auth/register';
    const body = hasAccount
      ? {email: payload.email, password: payload.password}
      : payload;

    try {
      await apiJson(endpoint, {method: 'POST', body: JSON.stringify(body)});
      status.textContent = 'Success. Opening workspace…';
      status.className = 'auth-status success';
      window.location.assign('/app');
    } catch (err) {
      status.textContent = err.message || 'Authentication failed.';
      status.className = 'auth-status error';
    } finally {
      submit.disabled = false;
    }
  });
});
