import {api, element, safeLink} from './api.js';

const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const send = document.querySelector('#send-button');
const feedback = document.querySelector('#feedback');
const conversation = document.querySelector('#conversation');
const welcome = document.querySelector('#welcome');
const main = document.querySelector('#main');
const scrollArea = document.querySelector('#chat-scroll');
const suggestions = document.querySelector('#starter-prompts');
const sidebar = document.querySelector('#sidebar');
const backdrop = document.querySelector('#sidebar-backdrop');
const sidebarOpenButton = document.querySelector('#sidebar-open');
const mobile = window.matchMedia('(max-width: 760px)');

// Auth & history elements
const authButton = document.querySelector('#auth-button');
const authLabel = document.querySelector('#auth-label');
const navAuth = document.querySelector('#nav-auth');
const navAuthLabel = document.querySelector('#nav-auth-label');
const authModal = document.querySelector('#auth-modal');
const authModalClose = document.querySelector('#auth-modal-close');
const authFeedback = document.querySelector('#auth-feedback');
const tabLogin = document.querySelector('#tab-login');
const tabRegister = document.querySelector('#tab-register');
const loginForm = document.querySelector('#login-modal-form');
const registerForm = document.querySelector('#register-modal-form');

const symptomHistoryBtn = document.querySelector('#symptom-history-btn');
const navSymptomHistory = document.querySelector('#nav-symptom-history');
const symptomHistoryModal = document.querySelector('#symptom-history-modal');
const historyModalClose = document.querySelector('#history-modal-close');
const copySummaryBtn = document.querySelector('#copy-summary-btn');
const intervalPills = document.querySelector('#interval-pills');

let busy = false;
let ready = false;
let pending = null;
const seenReplies = new Set();
let currentUser = null;
let currentIntervalDays = 30;
let cachedSummary = null;

function updateControls() {
  send.disabled = busy || !ready || !input.value.trim();
  input.disabled = busy;
  send.querySelector('svg').hidden = busy;
  send.querySelector('.loading-dot').hidden = !busy;
  form.setAttribute('aria-busy', String(busy));
  for (const button of document.querySelectorAll('[data-prompt], #reset-button, #header-new-chat')) {
    button.disabled = busy || !ready;
  }
}

function setSidebar(open) {
  document.body.classList.toggle('sidebar-mobile-open', mobile.matches && open);
  document.body.classList.toggle('sidebar-collapsed', !mobile.matches && !open);
  backdrop.hidden = !mobile.matches || !open;
  sidebar.inert = !open;
  main.inert = mobile.matches && open;
  sidebarOpenButton.setAttribute('aria-expanded', String(open));
}

function closeMobileSidebar() {
  if (mobile.matches) setSidebar(false);
}

function applyTheme(theme) {
  document.body.dataset.theme = theme;
  document.querySelector('#theme-label').textContent = theme === 'dark' ? 'Light appearance' : 'Dark appearance';
}

function showConversation() {
  welcome.hidden = true;
  suggestions.hidden = true;
  main.classList.add('has-messages');
  document.querySelector('#chat-title').textContent = 'Hospital conversation';
}

function scrollToLatest() {
  requestAnimationFrame(() => { scrollArea.scrollTop = scrollArea.scrollHeight; });
}

function copyButton(text) {
  const button = element('button', 'icon-button copy-button');
  button.type = 'button';
  button.title = 'Copy response';
  button.setAttribute('aria-label', 'Copy response');
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#copy');
  icon.append(use);
  button.append(icon);
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
      status('Response copied.');
    } catch { status('Copy is unavailable in this browser. Select the response text to copy it.', true); }
  });
  return button;
}

function status(message, error = false) {
  feedback.textContent = message;
  feedback.classList.toggle('error', error);
}

function authStatus(message, error = false) {
  authFeedback.textContent = message;
  authFeedback.classList.toggle('error', error);
}

function renderMessage(role, text, answer) {
  showConversation();
  const article = element('article', `message ${role}`);
  article.append(element('div', 'message-label', role === 'user' ? 'You' : role === 'staff' ? 'Demo staff' : 'MediAgent'));

  if (role !== 'user' && answer?.severity) {
    if (answer.severity.level === 'emergency') {
      const conf = Math.round((answer.severity.confidence || 0.95) * 100);
      const userTag = currentUser ? ` · Patient: ${currentUser.name}` : '';
      article.append(element('div', 'severity-banner emergency', `🚨 EMERGENCY ALERT (${conf}% confidence)${userTag} · Hospital triage notified`));
    } else if (answer.severity.level === 'severe' && (answer.severity.confidence || 0) >= 0.7) {
      const conf = Math.round(answer.severity.confidence * 100);
      const userTag = currentUser ? ` · Patient: ${currentUser.name}` : '';
      article.append(element('div', 'severity-banner severe', `⚠️ SEVERE HEALTH CONCERN (${conf}% confidence)${userTag} · Escalated to staff queue`));
    }
  }

  article.append(element('div', 'message-text', text));
  if (answer?.sources?.length) {
    const sources = element('div', 'source-list');
    for (const source of answer.sources) {
      const url = safeLink(source.url);
      if (!url) continue;
      const link = element('a', 'source-chip', `[${source.id}] ${source.title}`);
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.append(element('small', '', `Checked ${source.checked_at}${source.page ? ` · page ${source.page}` : ''}`));
      sources.append(link);
    }
    article.append(sources);
  }
  if (answer) {
    let metaText = answer.evidence === 'supported' ? 'Based on verified sources' : 'General health education · AI assisted';
    if (answer.review_id) metaText += ' · Staff alert ticket dispatched';
    if (currentUser) metaText += ` · Recorded to ${currentUser.name}'s symptom history`;
    article.append(element('div', 'answer-meta', metaText));
  }
  if (role !== 'user') {
    const references = (answer?.sources ?? []).map(source => `[${source.id}] ${source.url}`).join('\n');
    article.append(copyButton(text + (references ? `\n\n${references}` : '')));
  }
  conversation.append(article);
  return article;
}

function renderReplies(reviews) {
  for (const review of reviews) {
    const key = `${review.id}:${review.reply}`;
    if (review.reply && !seenReplies.has(key)) {
      renderMessage('staff', review.reply);
      seenReplies.add(key);
    }
  }
}

// ----------------- Auth UI Helpers -----------------
function updateAuthUI() {
  if (currentUser) {
    authLabel.textContent = currentUser.name.split(' ')[0];
    authButton.title = `Signed in as ${currentUser.name} (${currentUser.email}). Click to view account options.`;
    navAuthLabel.textContent = `Sign out (${currentUser.name.split(' ')[0]})`;
    symptomHistoryBtn.hidden = true;
    navSymptomHistory.hidden = true;
  } else {
    authLabel.textContent = 'Sign in';
    authButton.title = 'Sign in or register to enable emergency identification and doctor symptom history';
    navAuthLabel.textContent = 'Sign in / Register';
    symptomHistoryBtn.hidden = true;
    navSymptomHistory.hidden = true;
  }
}

function openAuthModal(tab = 'login') {
  authModal.hidden = false;
  authStatus('');
  if (tab === 'register') {
    tabRegister.click();
  } else {
    tabLogin.click();
  }
}

function closeAuthModal() {
  authModal.hidden = true;
  loginForm.reset();
  registerForm.reset();
  authStatus('');
}

async function checkAuth() {
  try {
    const res = await api('/api/auth/me');
    currentUser = res.user;
    updateAuthUI();
  } catch {
    currentUser = null;
    updateAuthUI();
  }
}

// ----------------- Doctor Symptom History Modal -----------------
function openSymptomModal() {
  if (!currentUser) {
    openAuthModal('login');
    return;
  }
  symptomHistoryModal.hidden = false;
  void loadSymptomHistory(currentIntervalDays);
}

function closeSymptomModal() {
  symptomHistoryModal.hidden = true;
}

async function loadSymptomHistory(days) {
  currentIntervalDays = days;
  const list = document.querySelector('#timeline-list');
  list.innerHTML = '<div class="empty-state">Loading clinical symptom history…</div>';

  for (const pill of intervalPills.querySelectorAll('.pill')) {
    pill.classList.toggle('active', Number(pill.dataset.days) === days);
  }

  try {
    const data = await api(`/api/user/symptom-history?since_days=${days}`);
    cachedSummary = data;

    // Fill Dashboard
    document.querySelector('#dash-patient-name').textContent = data.patient.name || 'Patient';
    const ageGender = [data.patient.age ? `Age ${data.patient.age}` : '', data.patient.gender || ''].filter(Boolean).join(', ');
    const phone = data.patient.phone ? `Ph: ${data.patient.phone}` : '';
    document.querySelector('#dash-patient-details').textContent = [ageGender, phone].filter(Boolean).join(' · ');

    document.querySelector('#dash-event-count').textContent = data.total_events;
    document.querySelector('#dash-interval-label').textContent = days === 0 ? 'All time consultations' : `Past ${days} days`;

    const peakBadge = document.querySelector('#dash-peak-badge');
    peakBadge.textContent = data.peak_severity.toUpperCase();
    peakBadge.className = `severity-pill ${data.peak_severity}`;

    document.querySelector('#dash-progression').textContent = data.clinical_progression;

    // Fill Timeline
    list.replaceChildren();
    if (!data.timeline.length) {
      list.innerHTML = `<div class="empty-state">No symptom consultations recorded in this interval (${days === 0 ? 'all time' : `past ${days} days`}). Describe your symptoms in the chat to track them.</div>`;
      return;
    }

    // Render newest first or chronological
    for (const ev of [...data.timeline].reverse()) {
      const item = element('div', `timeline-item ${ev.severity}`);
      const header = element('div', 'timeline-header');
      const pill = element('span', `severity-pill ${ev.severity}`, ev.severity.toUpperCase());
      const timeStr = new Date(ev.timestamp * 1000).toLocaleString(undefined, {
        dateStyle: 'medium', timeStyle: 'short'
      });
      const timeSpan = element('span', 'timeline-time', timeStr);
      header.append(pill, timeSpan);

      const queryDiv = element('div', 'timeline-query', `"${ev.query}"`);
      const summaryDiv = element('div', 'timeline-summary', `Clinical Concern: ${ev.summary}`);
      const adviceDiv = element('div', 'timeline-advice', `Guidance: ${ev.answer_snippet}`);

      item.append(header, queryDiv, summaryDiv, adviceDiv);
      list.append(item);
    }
  } catch (error) {
    list.innerHTML = `<div class="empty-state error">Failed to load history: ${error.message}</div>`;
  }
}

function copyClinicalSummary() {
  if (!cachedSummary) return;
  const p = cachedSummary.patient;
  const s = cachedSummary;
  const daysText = s.since_days === 0 ? 'All recorded consultations' : `Past ${s.since_days} days`;

  let text = `========================================================\n`;
  text += `PATIENT SYMPTOM PROGRESSION SUMMARY FOR DOCTOR APPOINTMENT\n`;
  text += `========================================================\n`;
  text += `Patient Name:     ${p.name}\n`;
  if (p.age) text += `Age / Gender:     ${p.age} ${p.gender ? `(${p.gender})` : ''}\n`;
  if (p.phone) text += `Contact Phone:    ${p.phone}\n`;
  if (p.emergency_contact) text += `Emergency Contact: ${p.emergency_contact} (${p.emergency_phone || 'N/A'})\n`;
  text += `Time Window:      ${daysText}\n`;
  text += `Total Consults:   ${s.total_events}\n`;
  text += `Peak Severity:    ${s.peak_severity.toUpperCase()} (Emergency: ${s.severity_counts.emergency}, Severe: ${s.severity_counts.severe}, Moderate: ${s.severity_counts.moderate}, General: ${s.severity_counts.general})\n`;
  text += `Progression Trend: ${s.clinical_progression}\n\n`;
  text += `--- Chronological Symptom Timeline ---\n`;

  s.timeline.forEach((ev, idx) => {
    const timeStr = new Date(ev.timestamp * 1000).toLocaleString();
    text += `${idx + 1}. [${timeStr}] [${ev.severity.toUpperCase()}]\n`;
    text += `   Patient Query: "${ev.query}"\n`;
    text += `   Concern: ${ev.summary}\n`;
    if (ev.answer_snippet) text += `   Advice: ${ev.answer_snippet.replace(/\n/g, ' ').slice(0, 150)}...\n`;
    text += `\n`;
  });

  navigator.clipboard.writeText(text).then(() => {
    copySummaryBtn.textContent = '✓ Copied to Clipboard!';
    setTimeout(() => {
      copySummaryBtn.innerHTML = '<svg style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"><use href="#copy"/></svg>Copy Clinical Summary for Doctor';
    }, 2500);
  }).catch(() => {
    alert('Please select and copy the text manually.');
  });
}

// ----------------- Initialization -----------------
async function initialize() {
  try {
    const [config, history] = await Promise.all([
      api('/api/config'),
      api('/api/session', {method: 'POST'}),
      checkAuth(),
    ]);
    document.querySelector('[data-hospital]').textContent = config.hospital;
    const portal = document.querySelector('#portal-link');
    const portalUrl = safeLink(config.portal_url);
    portal.hidden = !portalUrl;
    if (portalUrl) portal.href = portalUrl;
    document.querySelector('.disclaimer').textContent = `Independent demo, not affiliated with ${config.hospital}. For urgent symptoms, seek emergency care.`;
    for (const turn of history.turns) {
      renderMessage('user', turn.question);
      renderMessage('assistant', turn.answer.text, turn.answer);
    }
    renderReplies(history.reviews);
    ready = true;
    updateControls();
    scrollToLatest();
    if (history.turns.length) status('Restored conversation.');
  } catch (error) { status(error.message, true); }
}

async function submit(message) {
  if (busy || !message.trim()) return;
  if (!ready) { status('Connecting to your session. Please wait a moment or refresh the page.', true); return; }
  busy = true;
  updateControls();
  closeMobileSidebar();
  if (!pending || pending.message !== message) {
    pending = {message, request_id: crypto.randomUUID()};
    renderMessage('user', message);
  }
  status('Checking hospital information…');
  scrollToLatest();
  try {
    const answer = await api('/api/chat', {method: 'POST', body: pending});
    if (typeof answer.text !== 'string' || !Array.isArray(answer.sources)) throw new Error('Invalid answer received.');
    renderMessage('assistant', answer.text, answer);
    pending = null;
    input.value = '';
    status('');
  } catch (error) {
    status(`${error.message} Your question is still in the box so you can retry.`, true);
  } finally {
    busy = false;
    updateControls();
    input.focus();
    scrollToLatest();
  }
}

// Form & Input Listeners
form.addEventListener('submit', event => { event.preventDefault(); void submit(input.value.trim()); });
input.addEventListener('input', updateControls);
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});
for (const card of document.querySelectorAll('[data-prompt]')) {
  card.addEventListener('click', () => { if (!busy) { input.value = card.dataset.prompt; void submit(input.value); } });
}

document.querySelector('#reset-button').addEventListener('click', async () => {
  if (busy) return;
  busy = true;
  updateControls();
  closeMobileSidebar();
  try {
    await api('/api/session', {method: 'DELETE'});
    await api('/api/session', {method: 'POST'});
    conversation.replaceChildren();
    welcome.hidden = false;
    suggestions.hidden = false;
    main.classList.remove('has-messages');
    document.querySelector('#chat-title').textContent = 'New conversation';
    pending = null;
    seenReplies.clear();
    input.value = '';
    status('Previous conversation cleared.');
  } catch (error) { status(error.message, true); }
  finally { busy = false; updateControls(); }
});

document.querySelector('#review-button').addEventListener('click', async () => {
  closeMobileSidebar();
  try {
    await api('/api/reviews', {method: 'POST'});
    status('Demo review requested. This is not monitored by the hospital. Use “Check staff reply” for updates; contact the hospital directly for care.');
  } catch (error) { status(error.message, true); }
});

document.querySelector('#status-button').addEventListener('click', async () => {
  closeMobileSidebar();
  try {
    const history = await api('/api/history');
    renderReplies(history.reviews);
    scrollToLatest();
    status(history.reviews.some(r => r.status === 'pending') ? 'A demo staff review is pending.' : history.reviews.length ? 'Staff replies are up to date.' : 'No staff review requested yet.');
  } catch (error) { status(error.message, true); }
});

document.querySelector('#header-new-chat').addEventListener('click', () => document.querySelector('#reset-button').click());
document.querySelector('#current-chat').addEventListener('click', () => { closeMobileSidebar(); scrollToLatest(); input.focus(); });
sidebarOpenButton.addEventListener('click', () => { setSidebar(true); document.querySelector('#sidebar-close').focus(); });
document.querySelector('#sidebar-close').addEventListener('click', () => { setSidebar(false); sidebarOpenButton.focus(); });
backdrop.addEventListener('click', () => { setSidebar(false); sidebarOpenButton.focus(); });
mobile.addEventListener('change', () => setSidebar(!mobile.matches));

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    if (!authModal.hidden) closeAuthModal();
    if (!symptomHistoryModal.hidden) closeSymptomModal();
    if (mobile.matches && document.body.classList.contains('sidebar-mobile-open')) {
      setSidebar(false);
      sidebarOpenButton.focus();
    }
  }
});

document.querySelector('#theme-button').addEventListener('click', () => {
  const theme = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(theme);
  try { localStorage.setItem('mediagent-appearance', theme); } catch { /* Storage may be disabled. */ }
});
try { applyTheme(localStorage.getItem('mediagent-appearance') === 'light' ? 'light' : 'dark'); } catch { applyTheme('dark'); }

// Auth Modal Listeners
tabLogin.addEventListener('click', () => {
  tabLogin.classList.add('active');
  tabRegister.classList.remove('active');
  loginForm.hidden = false;
  registerForm.hidden = true;
  authStatus('');
});

tabRegister.addEventListener('click', () => {
  tabRegister.classList.add('active');
  tabLogin.classList.remove('active');
  registerForm.hidden = false;
  loginForm.hidden = true;
  authStatus('');
});

authButton.addEventListener('click', () => {
  if (currentUser) {
    if (confirm(`Signed in as ${currentUser.name} (${currentUser.email}).\n\nWould you like to sign out?`)) {
      api('/api/auth/logout', {method: 'POST'}).then(() => {
        currentUser = null;
        updateAuthUI();
        status('Signed out successfully.');
      });
    }
  } else {
    openAuthModal('login');
  }
});

navAuth.addEventListener('click', () => {
  closeMobileSidebar();
  if (currentUser) {
    if (confirm(`Signed in as ${currentUser.name}.\n\nWould you like to sign out?`)) {
      api('/api/auth/logout', {method: 'POST'}).then(() => {
        currentUser = null;
        updateAuthUI();
        status('Signed out successfully.');
      });
    }
  } else {
    openAuthModal('login');
  }
});

authModalClose.addEventListener('click', closeAuthModal);
authModal.addEventListener('click', event => {
  if (event.target === authModal) closeAuthModal();
});

loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const btn = loginForm.querySelector('button');
  btn.disabled = true;
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      body: {
        email: document.querySelector('#login-email').value,
        password: document.querySelector('#login-password').value,
      }
    });
    currentUser = res.user;
    updateAuthUI();
    closeAuthModal();
    status(`Welcome back, ${currentUser.name}! Emergency tracking and doctor symptom history active.`);
  } catch (error) {
    authStatus(error.message, true);
  } finally {
    btn.disabled = false;
  }
});

registerForm.addEventListener('submit', async event => {
  event.preventDefault();
  const btn = registerForm.querySelector('button');
  btn.disabled = true;
  const ageVal = document.querySelector('#reg-age').value;
  try {
    const res = await api('/api/auth/register', {
      method: 'POST',
      body: {
        name: document.querySelector('#reg-name').value,
        email: document.querySelector('#reg-email').value,
        password: document.querySelector('#reg-password').value,
        phone: document.querySelector('#reg-phone').value,
        age: ageVal ? parseInt(ageVal, 10) : null,
        gender: document.querySelector('#reg-gender').value,
        emergency_contact: document.querySelector('#reg-em-contact').value,
        emergency_phone: document.querySelector('#reg-em-phone').value,
      }
    });
    currentUser = res.user;
    updateAuthUI();
    closeAuthModal();
    status(`Account created! Welcome, ${currentUser.name}. In an emergency, staff can reach your emergency contacts directly.`);
  } catch (error) {
    authStatus(error.message, true);
  } finally {
    btn.disabled = false;
  }
});

// Doctor Symptom History Modal Listeners
symptomHistoryBtn.addEventListener('click', openSymptomModal);
navSymptomHistory.addEventListener('click', () => { closeMobileSidebar(); openSymptomModal(); });
historyModalClose.addEventListener('click', closeSymptomModal);
symptomHistoryModal.addEventListener('click', event => {
  if (event.target === symptomHistoryModal) closeSymptomModal();
});

intervalPills.addEventListener('click', event => {
  const pill = event.target.closest('.pill');
  if (pill && pill.dataset.days !== undefined) {
    void loadSymptomHistory(Number(pill.dataset.days));
  }
});

copySummaryBtn.addEventListener('click', copyClinicalSummary);

setSidebar(!mobile.matches);
updateControls();
void initialize();
