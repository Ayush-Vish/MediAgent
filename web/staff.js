import {api, element, safeLink} from './api.js';

let token = '';
let config;
const feedback = document.querySelector('#feedback');
const login = document.querySelector('#login-panel');
const workspace = document.querySelector('#workspace');
function status(text, error = false) { feedback.textContent = text; feedback.classList.toggle('error', error); }
const staffApi = (path, options = {}) => api(path, {...options, token});

function renderSources(sources) {
  const container = document.querySelector('#sources');
  container.replaceChildren();
  for (const source of sources) {
    const row = element('article', 'source-row');
    const details = element('div');
    details.append(element('strong', '', source.title));
    const url = safeLink(source.url);
    if (url) {
      const link = element('a', 'staff-note', url);
      link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; details.append(link);
    }
    details.append(element('p', '', source.text));
    details.append(element('span', 'status-label', `${source.approved ? 'Approved' : 'Pending approval'} · ${source.checked_at} · revision ${source.revision}${source.conflict ? ' · CONFLICT: excluded' : ''}`));
    const toggle = element('button', 'subtle-button', source.approved ? 'Withdraw' : 'Approve');
    toggle.addEventListener('click', async () => {
      toggle.disabled = true;
      try { await staffApi(`/api/staff/sources/${encodeURIComponent(source.id)}`, {method: 'PATCH', body: {approved: !source.approved}}); await refresh(); }
      catch (error) { status(error.message, true); }
      finally { toggle.disabled = false; }
    });
    row.append(details, toggle); container.append(row);
  }
  if (!sources.length) container.append(element('p', 'empty-state', 'No public sources yet.'));
}

function renderReviews(reviews) {
  const container = document.querySelector('#reviews');
  container.replaceChildren();
  for (const review of reviews.sort((a, b) => b.created - a.created)) {
    const isEmergency = review.severity === 'emergency' || review.status === 'urgent';
    const isSevere = review.severity === 'severe';
    const rowClass = isEmergency ? 'review-row urgent' : isSevere ? 'review-row severe' : 'review-row';
    const row = element('article', rowClass);

    // Severity & confidence badge
    if (isEmergency) {
      const conf = Math.round((review.confidence || 0.95) * 100);
      row.append(element('div', 'alert-badge badge-emergency', `🚨 EMERGENCY ALERT · ${conf}% confidence`));
    } else if (isSevere) {
      const conf = Math.round((review.confidence || 0.8) * 100);
      row.append(element('div', 'alert-badge badge-severe', `⚠️ SEVERE HEALTH CONDITION · ${conf}% confidence`));
    }

    // Patient Identity Box (if user authenticated)
    if (review.user) {
      const patientCard = element('div', 'patient-alert-card');
      const nameLine = element('div');
      const ageGender = [review.user.age ? `Age ${review.user.age}` : '', review.user.gender || ''].filter(Boolean).join(', ');
      nameLine.append(element('strong', '', `👤 Patient: ${review.user.name}${ageGender ? ` (${ageGender})` : ''}`));
      patientCard.append(nameLine);

      patientCard.append(element('p', 'staff-note', `📞 Phone: ${review.user.phone || 'N/A'} · Email: ${review.user.email}`));

      if (review.user.emergency_contact || review.user.emergency_phone) {
        const emDiv = element('div', 'danger');
        emDiv.style.marginTop = '4px';
        emDiv.style.fontWeight = '600';
        emDiv.textContent = `🆘 Emergency Contact: ${review.user.emergency_contact || 'None'} (Ph: ${review.user.emergency_phone || 'N/A'})`;
        patientCard.append(emDiv);
      }

      const viewBtn = element('button', 'subtle-button', '🩺 View Patient Symptom Timeline');
      viewBtn.style.marginTop = '8px';
      viewBtn.addEventListener('click', () => {
        document.querySelector('#patient-email-search').value = review.user.email;
        loadDoctorPatientSummary(review.user.email, null, 30);
        document.querySelector('#doctor-panel').scrollIntoView({behavior: 'smooth'});
      });
      patientCard.append(viewBtn);
      row.append(patientCard);
    }

    row.append(element('strong', '', review.question));

    if (review.summary) {
      row.append(element('p', 'staff-note', `Detected concern: ${review.summary}`));
    }

    row.append(element('p', '', `Status: ${review.status} · ${new Date(review.created * 1000).toLocaleString()}`));

    if (review.reply) {
      const replyBox = element('div', 'staff-reply');
      replyBox.append(element('strong', '', 'Staff reply: '));
      replyBox.append(element('span', '', review.reply));
      row.append(replyBox);
    } else {
      const form = element('form');
      const label = element('label', '', 'Guidance or clinical response for this patient');
      const input = element('textarea'); input.required = true; input.minLength = 3; input.maxLength = 2000;
      label.append(input);
      const button = element('button', 'primary-button', isEmergency ? 'Send urgent reply' : 'Send response');
      button.type = 'submit';
      form.append(label, button);
      form.addEventListener('submit', async event => {
        event.preventDefault(); button.disabled = true;
        try {
          await staffApi(`/api/staff/reviews/${encodeURIComponent(review.id)}/reply`, {method: 'POST', body: {reply: input.value}});
          await refresh();
          status('Reply saved. The user will see it in their chat.');
        } catch (error) {
          status(error.message, true);
        } finally {
          button.disabled = false;
        }
      });
      row.append(form);
    }
    container.append(row);
  }
  if (!reviews.length) container.append(element('p', 'empty-state', 'All clear. No reviews or alerts in the queue.'));
}

async function loadPatientList() {
  const select = document.querySelector('#patient-select');
  try {
    const res = await staffApi('/api/staff/patients');
    select.replaceChildren(element('option', '', '-- Choose registered patient --'));
    for (const p of res.patients || []) {
      const opt = element('option', '', `${p.name} (${p.email})`);
      opt.value = p.id;
      opt.dataset.email = p.email;
      select.append(opt);
    }
  } catch {
    // If not authenticated yet or empty
  }
}

async function loadDoctorPatientSummary(email, userId, days) {
  const container = document.querySelector('#doctor-patient-summary');
  container.innerHTML = '<div class="empty-state">Loading patient clinical timeline…</div>';

  const params = new URLSearchParams();
  if (email) params.set('email', email);
  if (userId) params.set('user_id', userId);
  params.set('since_days', days);

  try {
    const data = await staffApi(`/api/staff/patient-summary?${params.toString()}`);
    container.replaceChildren();

    const box = element('div', 'doctor-summary-box');

    // Header info
    const header = element('div');
    const p = data.patient;
    const ageGender = [p.age ? `Age: ${p.age}` : '', p.gender ? `Gender: ${p.gender}` : ''].filter(Boolean).join(' · ');
    header.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <h3 style="margin:0 0 4px; font-size:16px;">Clinical Summary: ${p.name}</h3>
          <span style="font-size:12px; color:#64748b;">${ageGender ? ageGender + ' · ' : ''}Email: ${p.email} · Phone: ${p.phone || 'N/A'}</span>
        </div>
        <span class="status-label" style="font-weight:600; font-size:11px;">${days === 0 ? 'All Time' : `Past ${days} Days`}</span>
      </div>
      ${p.emergency_contact ? `
        <div style="font-size:12px; color:#dc2626; margin-top:6px; background:#fef2f2; padding:6px 10px; border-radius:6px;">
          🆘 Emergency Contact: ${p.emergency_contact} (${p.emergency_phone || 'N/A'})
        </div>
      ` : ''}
    `;
    box.append(header);

    // Stat Grid
    const statGrid = element('div', 'summary-stat-grid');
    statGrid.innerHTML = `
      <div class="stat-card">
        <small>Total Inquiries</small>
        <strong>${data.total_events}</strong>
      </div>
      <div class="stat-card">
        <small>Peak Severity</small>
        <strong style="text-transform:uppercase; color:${data.peak_severity === 'emergency' ? '#dc2626' : data.peak_severity === 'severe' ? '#d97706' : '#2563eb'}">${data.peak_severity}</strong>
      </div>
      <div class="stat-card">
        <small>Severity Breakdown</small>
        <span style="font-size:11px; font-weight:600;">Em: ${data.severity_counts.emergency} · Sev: ${data.severity_counts.severe} · Mod: ${data.severity_counts.moderate}</span>
      </div>
      <div class="stat-card">
        <small>Progression</small>
        <span style="font-size:11px; color:#1e293b; font-weight:600;">${data.clinical_progression}</span>
      </div>
    `;
    box.append(statGrid);

    // Timeline List
    const timelineSection = element('div');
    timelineSection.innerHTML = '<h4 style="margin:16px 0 10px; font-size:13px; text-transform:uppercase; color:#64748b; letter-spacing:0.5px;">Chronological Symptom Evolution</h4>';

    if (!data.timeline.length) {
      timelineSection.append(element('p', 'empty-state', 'No symptoms reported by this patient within this time window.'));
    } else {
      for (const ev of [...data.timeline].reverse()) {
        const item = element('div', `clinical-timeline-item ${ev.severity}`);
        const timeStr = new Date(ev.timestamp * 1000).toLocaleString();
        item.innerHTML = `
          <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
            <span style="font-size:11px; font-weight:700; text-transform:uppercase; color:${ev.severity === 'emergency' ? '#dc2626' : ev.severity === 'severe' ? '#d97706' : '#2563eb'}">${ev.severity}</span>
            <span style="font-size:11px; color:#94a3b8;">${timeStr}</span>
          </div>
          <strong style="font-size:13px; display:block; margin-bottom:4px;">Patient Query: "${ev.query}"</strong>
          <p style="font-size:12px; color:#475569; margin:2px 0 6px;">Detected Issue: ${ev.summary}</p>
          <div style="font-size:11px; color:#64748b; background:#f1f5f9; padding:6px 8px; border-radius:5px;">
            System Advice: ${ev.answer_snippet}
          </div>
        `;
        timelineSection.append(item);
      }
    }
    box.append(timelineSection);
    container.append(box);
  } catch (error) {
    container.innerHTML = `<div class="empty-state danger">Failed to load clinical summary: ${error.message}</div>`;
  }
}

async function refresh() {
  const [sources, reviews] = await Promise.all([
    staffApi('/api/staff/sources'),
    staffApi('/api/staff/reviews'),
    loadPatientList(),
  ]);
  if (!Array.isArray(sources) || !Array.isArray(reviews)) throw new Error('Invalid workspace response.');
  renderSources(sources); renderReviews(reviews);
}

document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button'); button.disabled = true;
  try {
    if (config.local_staff) token = document.querySelector('#local-token').value;
    else {
      if (!config.supabase_url) throw new Error('Staff sign-in is not configured by the owner yet.');
      const response = await fetch(`${config.supabase_url}/auth/v1/token?grant_type=password`, {
        method: 'POST', headers: {'Content-Type': 'application/json', apikey: config.supabase_anon_key},
        body: JSON.stringify({email: document.querySelector('#email').value, password: document.querySelector('#password').value})});
      const data = await response.json();
      if (!response.ok || typeof data.access_token !== 'string') throw new Error('Sign-in failed. Check your staff credentials.');
      token = data.access_token;
    }
    await refresh();
    login.hidden = true; workspace.hidden = false;
    document.querySelector('#password').value = ''; document.querySelector('#local-token').value = '';
    status('Signed in. Your token is kept in memory until you close or reload this page.');
  } catch (error) { token = ''; status(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#logout').addEventListener('click', () => { token = ''; location.reload(); });
document.querySelector('#refresh').addEventListener('click', () => { void refresh().catch(error => status(error.message, true)); });

document.querySelector('#source-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  const fields = new FormData(form);
  const body = Object.fromEntries(fields.entries());
  body.conflict = fields.get('conflict') === 'on';
  const button = form.querySelector('button'); button.disabled = true;
  try { await staffApi('/api/staff/sources', {method: 'POST', body}); form.reset(); await refresh(); status('Source saved for approval.'); }
  catch (error) { status(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelector('#patient-select').addEventListener('change', event => {
  const opt = event.target.selectedOptions[0];
  if (opt && opt.dataset.email) {
    document.querySelector('#patient-email-search').value = opt.dataset.email;
  }
});

document.querySelector('#load-patient-summary-btn').addEventListener('click', () => {
  const select = document.querySelector('#patient-select');
  const emailInput = document.querySelector('#patient-email-search').value.trim();
  const days = parseInt(document.querySelector('#patient-interval-select').value, 10);
  const userId = select.value || null;

  if (!emailInput && !userId) {
    status('Please select a patient or enter a patient email.', true);
    return;
  }
  loadDoctorPatientSummary(emailInput || null, userId, days);
});

async function initialize() {
  config = await api('/api/config');
  document.querySelector('#local-field').hidden = !config.local_staff;
  document.querySelector('#cloud-fields').hidden = config.local_staff;
  const date = document.querySelector('[name="checked_at"]');
  date.value = new Date().toISOString().slice(0, 10); date.max = date.value;

  // Auto-connect in local environment for instant accessibility
  if (config.local_staff) {
    const autoToken = config.admin_token || 'local-staff';
    const localInput = document.querySelector('#local-token');
    if (localInput) localInput.value = autoToken;
    token = autoToken;
    try {
      await refresh();
      login.hidden = true;
      workspace.hidden = false;
      status('Connected to Staff Workspace (Local Access Mode).');
    } catch {
      token = '';
      login.hidden = false;
      workspace.hidden = true;
    }
  }
}
void initialize().catch(error => status(error.message, true));
