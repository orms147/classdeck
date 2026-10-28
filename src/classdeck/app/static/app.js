/* ClassDeck local app: hash-routed single page, no build step, no network beyond its own API. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ helpers

  const view = document.getElementById('view');
  const topStatus = document.getElementById('topStatus');
  const overlay = document.getElementById('overlay');

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem('cd.' + key);
      window.localStorage.setItem('cd.' + key, value);
    } catch (e) { /* storage may be blocked */ }
    return null;
  }

  let lang = store('lang') === 'en' ? 'en' : 'vi';

  function t(key, vars) {
    const table = window.I18N || {};
    let s = (table[lang] && table[lang][key]);
    if (s === undefined) s = table.en && table.en[key];
    if (s === undefined) s = key;
    if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] === undefined ? '' : vars[k]));
    return s;
  }

  function tx(prefix, value) {
    const key = prefix + '.' + value;
    const s = t(key);
    return s === key ? String(value).replace(/_/g, ' ') : s;
  }

  function el(tag, props) {
    const node = document.createElement(tag);
    const kids = Array.prototype.slice.call(arguments, 2);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'on') for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k in node && k !== 'list') node[k] = v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    append(node, kids);
    return node;
  }

  function append(node, kids) {
    for (const kid of kids) {
      if (kid === null || kid === undefined || kid === false) continue;
      if (Array.isArray(kid)) append(node, kid);
      else node.appendChild(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
  }

  const ICONS = {
    objective: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
    vocabulary: '<rect x="4" y="5" width="16" height="14" rx="2.5"/><path d="M8 10h8M8 14h5"/>',
    sentence_pattern: '<path d="M4 7h16M4 12h10M4 17h13"/>',
    dialogue: '<path d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    phonics: '<path d="M4 12v0M8 8v8M12 5v14M16 9v6M20 11v2"/>',
    activity: '<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>',
    teacher_note: '<path d="M5 4h14v11l-5 5H5z"/><path d="M14 20v-5h5"/>',
    summary: '<path d="m5 12 4 4 10-10"/>',
    reading: '<path d="M4 5h7a3 3 0 0 1 3 3v11a2 2 0 0 0-2-2H4zM20 5h-6"/>',
    match: '<circle cx="6" cy="7" r="2"/><circle cx="18" cy="17" r="2"/><path d="M8 7h3l2 10h3"/>',
    gap_fill: '<path d="M4 17h16M7 12h3m4 0h3"/>',
    choice: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    true_false: '<path d="m4 12 4 4 6-8M16 8l4 8m0-8-4 8"/>',
    rule_box: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v5m0 3v.01"/>',
    question_set: '<path d="M9 9a3 3 0 1 1 4.5 2.6c-1 .6-1.5 1.2-1.5 2.4m0 3v.01"/>',
    image_ref: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.5"/><path d="m21 16-5-5-8 8"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    download: '<path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14"/>',
    picture: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.5"/><path d="m21 16-5-5-8 8"/>',
    check: '<path d="M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    tick: '<path d="m5 12 4 4 10-10"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    lessons: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    upload: '<path d="M12 16V5m0 0-4 4m4-4 4 4M5 20h14"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    sparkle: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="M3 3l18 18M10.6 5.1A9.7 9.7 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.5 6.6C3.8 8.3 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 4-.9M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    back: '<path d="m15 6-6 6 6 6"/>',
  };

  function icon(name, cls) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'icon' + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    svg.innerHTML = ICONS[name] || '';
    return svg;
  }

  function toast(message, kind) {
    const box = document.getElementById('toasts');
    const node = el('div', { class: 'toast ' + (kind || 'info'), role: kind === 'error' ? 'alert' : 'status' },
      el('span', { class: 'toast-text', text: message }),
      el('button', { class: 'toast-x', type: 'button', 'aria-label': t('close'), on: { click: () => node.remove() } }, icon('close')));
    box.appendChild(node);
    setTimeout(() => node.remove(), kind === 'error' ? 9000 : 4500);
  }

  async function api(path, opts) {
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw Object.assign(new Error(t('err.network')), { status: 0 });
    }
    const type = res.headers.get('content-type') || '';
    const data = type.includes('json') ? await res.json() : null;
    if (!res.ok) {
      throw Object.assign(new Error((data && data.error) || res.statusText || 'Error'), { status: res.status, data });
    }
    return data;
  }

  function post(path, body) {
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  function fail(e) {
    toast(plain(e && e.message ? e.message : String(e)), 'error');
  }

  async function download(url, filename) {
    try {
      const res = await fetch(url);
      if (!res.ok) {
        let message = res.statusText;
        try { message = (await res.json()).error || message; } catch (e) { /* not json */ }
        throw new Error(message);
      }
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const link = el('a', { href, download: filename });
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10000);
    } catch (e) {
      fail(e);
    }
  }

  function fmtDate(iso) {
    try {
      return new Intl.DateTimeFormat(lang === 'vi' ? 'vi-VN' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso));
    } catch (e) { return iso; }
  }

  function fmtUsd(value) {
    return value === null || value === undefined ? t('price.unknown') : '$' + value.toFixed(2);
  }

  function busy(button, working) {
    button.disabled = working;
    button.classList.toggle('is-busy', working);
  }

  // ------------------------------------------------------------------ state

  const state = {
    config: null,
    lessons: [],
    payload: null, // current lesson payload
    selected: [],
    theme: store('theme') || null,
    endpoint: null,
  };

  let routeToken = 0;

  async function loadConfig() {
    state.config = await api('/api/config');
    const cfg = state.config;
    if (!state.theme || !cfg.themes.some((x) => x.id === state.theme)) state.theme = cfg.default_theme;
    state.endpoint = cfg.default_endpoint;
  }

  async function refreshLessons() {
    try {
      state.lessons = (await api('/api/lessons')).lessons;
    } catch (e) {
      state.lessons = [];
    }
  }

  // ------------------------------------------------------------------ chrome

  function applyStaticText() {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.dataset.i18n); });
    document.getElementById('navHome').textContent = t('nav.lessons');
    const gear = document.getElementById('navSettings');
    gear.replaceChildren(icon('gear'));
    gear.setAttribute('aria-label', t('nav.settings'));
    gear.setAttribute('title', t('nav.settings'));
    document.querySelectorAll('.lang button').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
    });
  }

  function markNav() {
    const hash = location.hash || '#/';
    const settings = hash.startsWith('#/settings');
    document.getElementById('navHome').classList.toggle('active', !settings);
    document.getElementById('navSettings').classList.toggle('active', settings);
  }

  function setStatus() {
    topStatus.replaceChildren();
    if (!state.config) return;
    if (state.config.provider_mode === 'mock') {
      topStatus.appendChild(el('span', { class: 'chip warn', title: t('mode.mock.hint'), text: t('mode.mock') }));
    }
  }

  /** Plain words for teachers: internal ids never reach the screen. */
  function plain(text) {
    return String(text === undefined || text === null ? '' : text)
      .replace(/\bta\d+\.u\d+\.o(\d+)\b/g, (_, o) => t('plain.outcome', { n: o }))
      .replace(/\bta\d+\.u(\d+)\b/g, (_, u) => t('plain.unit', { n: u }))
      .replace(/\b[bl]_[0-9a-f]{2,}\b/g, t('plain.item'))
      .replace(/\bd_[0-9a-f]{6,}\b/g, t('plain.doc'));
  }

  function noKeyBanner() {
    const cfg = state.config;
    if (!cfg || cfg.key_set || cfg.provider_mode === 'mock') return null;
    return el('div', { class: 'banner', role: 'note' },
      el('span', { class: 'banner-icon', 'aria-hidden': 'true' }, icon('sparkle')),
      el('span', { class: 'banner-text', text: t('banner.nokey') }),
      el('a', { class: 'btn small', href: '#/settings', text: t('banner.nokey.cta') }));
  }

  /** Small preview of a deck theme: its paper colour and accent. */
  const THEME_SWATCH = {
    sunny: ['#fff7db', '#1cb0f6'],
    candy: ['#fbf5ff', '#9b7bff'],
    chalkboard: ['#2f5a43', '#ffd84d'],
    kids: ['#fffdf7', '#4d96ff'],
    plain: ['#ffffff', '#111111'],
    storybook: ['#f6ebd6', '#d9774f'],
    studio: ['#fafaf7', '#1d4ed8'],
  };

  function themeChips(name, selected, onChange) {
    const group = el('div', { class: 'theme-chips', role: 'radiogroup', 'aria-label': t('new.theme') });
    const order = ['sunny', 'kids', 'candy', 'storybook', 'studio', 'chalkboard', 'plain'];
    const themes = [...state.config.themes].sort((a, b) => (order.indexOf(a.id) + 99) % 99 - (order.indexOf(b.id) + 99) % 99);
    for (const th of themes) {
      const [paper, accent] = THEME_SWATCH[th.id] || ['#f1f3f8', '#4f46e5'];
      const input = el('input', { type: 'radio', name: 'theme-' + name, value: th.id, checked: th.id === selected });
      input.addEventListener('change', () => onChange(th.id));
      group.appendChild(el('label', { class: 'theme-chip', title: th.description },
        input,
        el('span', { class: 'theme-card' },
          el('span', { class: 'theme-swatch', style: 'background:' + paper },
            el('span', { class: 'swatch-bar', style: 'background:' + accent }),
            el('span', { class: 'swatch-line', style: 'background:' + accent })),
          el('span', { class: 'theme-name', text: th.label }))));
    }
    return group;
  }

  function closeOverlay() {
    overlay.replaceChildren();
    document.body.classList.remove('has-overlay');
  }

  function openOverlay(node) {
    overlay.replaceChildren(node);
    document.body.classList.add('has-overlay');
  }

  document.addEventListener('click', (ev) => {
    document.querySelectorAll('details.menu[open]').forEach((d) => { if (!d.contains(ev.target)) d.open = false; });
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && overlay.firstChild) {
      const close = overlay.querySelector('[data-close]');
      if (close) close.click();
    }
  });

  // ------------------------------------------------------------------ router

  async function route() {
    const token = ++routeToken;
    closeOverlay();
    const hash = location.hash || '#/';
    let m;
    try {
      if ((m = hash.match(/^#\/lessons\/([^/?]+)/))) await viewLesson(m[1], token);
      else if ((m = hash.match(/^#\/jobs\/([^/?]+)/))) viewJob(m[1], token);
      else if (hash.startsWith('#/new')) viewNew();
      else if (hash.startsWith('#/settings')) await viewSettings(token);
      else await viewHome(token);
    } catch (e) {
      if (token !== routeToken) return;
      view.replaceChildren(errorState(e));
    }
    markNav();
    window.scrollTo(0, 0);
  }

  function errorState(e) {
    return el('div', { class: 'empty' },
      el('h2', { text: t('err.title') }),
      el('p', { class: 'muted', text: e && e.message ? e.message : String(e) }),
      el('a', { class: 'btn', href: '#/', text: t('err.home') }));
  }

  // ------------------------------------------------------------------ home

  async function viewHome(token) {
    view.className = 'page';
    view.replaceChildren(el('div', { class: 'skeleton' }));
    await refreshLessons();
    if (token !== routeToken) return;
    const head = el('div', { class: 'page-head' },
      el('div', null, el('h1', { text: t('home.title') })),
      el('a', { class: 'btn primary', href: '#/new' }, icon('plus'), t('nav.new')));
    const banner = noKeyBanner();
    if (!state.lessons.length) {
      view.replaceChildren(...[banner, el('div', { class: 'empty card' },
        el('div', { class: 'empty-art', 'aria-hidden': 'true' }, icon('sparkle')),
        el('h2', { text: t('home.empty.title') }),
        el('p', { class: 'muted', text: t('home.empty.body') }),
        el('a', { class: 'btn primary lg', href: '#/new' }, icon('plus'), t('nav.new')))].filter(Boolean));
      return;
    }
    const list = el('div', { class: 'lesson-list' });
    for (const lesson of state.lessons) {
      list.appendChild(el('a', { class: 'lesson-card card hoverable', href: '#/lessons/' + lesson.id },
        el('div', { class: 'lc-main' },
          el('div', { class: 'lc-title', text: lesson.title }),
          el('div', { class: 'lc-unit muted', text: lesson.unit })),
        el('div', { class: 'lc-side' },
          lesson.failures ? el('span', { class: 'chip danger', text: t('lesson.failures', { n: lesson.failures }) })
            : lesson.warnings ? el('span', { class: 'chip warn', text: t('lesson.warnings', { n: lesson.warnings }) }) : null,
          el('span', { class: 'muted small', text: fmtDate(lesson.updated_at) }),
          icon('chevron', 'go'))));
    }
    view.replaceChildren(...[banner, head, list].filter(Boolean));
  }

  // ------------------------------------------------------------------ new lesson
  // The seven textbook lessons of a grade 6-9 unit, in teaching order.
  const LESSON_TYPES = ['Getting Started', 'A Closer Look 1', 'A Closer Look 2', 'Communication', 'Skills 1', 'Skills 2', 'Looking Back & Project'];

  function radioGroup(name, options, value, onChange, cls) {
    const group = el('div', { class: 'seg ' + (cls || ''), role: 'radiogroup' });
    for (const opt of options) {
      const input = el('input', { type: 'radio', name, value: String(opt.value), checked: String(opt.value) === String(value), disabled: !!opt.disabled });
      input.addEventListener('change', () => onChange(opt.value));
      group.appendChild(el('label', { class: opt.disabled ? 'disabled' : '' }, input, el('span', { text: opt.label })));
    }
    return group;
  }

  function field(label, control, hint, cls) {
    return el('div', { class: 'field' + (cls ? ' ' + cls : '') },
      el('div', { class: 'field-label', text: label }),
      control,
      hint ? el('div', { class: 'field-hint muted small', text: hint }) : null);
  }

  /** A number box with big minus / plus buttons. */
  function numberStepper(value, min, max, onChange, label) {
    const input = el('input', { type: 'number', min, max, step: 1, value, inputmode: 'numeric', 'aria-label': label });
    const clamp = (n) => Math.min(max, Math.max(min, Number.isFinite(n) ? n : min));
    const set = (n) => { input.value = String(clamp(n)); onChange(Number(input.value)); };
    input.addEventListener('change', () => set(parseInt(input.value, 10)));
    const step = (delta, text, name) => el('button', {
      type: 'button', class: 'step-btn', 'aria-label': name, text,
      on: { click: () => set((parseInt(input.value, 10) || min) + delta) },
    });
    return el('div', { class: 'num-step' }, step(-1, '−', t('new.unit.less')), input, step(1, '+', t('new.unit.more')));
  }

  function viewNew() {
    const cfg = state.config;
    view.className = 'page narrow';
    const secondary = (g) => g >= 6;
    const savedGrade = parseInt(store('grade'), 10);
    const form = {
      file: null,
      sample: null,
      reference: null,
      grade: savedGrade >= 1 && savedGrade <= 9 ? savedGrade : 3,
      unit: 1,
      lesson: 1,
      period: 1,
      model: '',
      pictures: false,
      theme: state.theme,
    };

    // 1. the file
    const fileInput = el('input', { type: 'file', accept: '.pdf,.docx,.pptx', class: 'visually-hidden', id: 'fileInput' });
    const dropBody = el('div', { class: 'drop-body' });
    const drop = el('label', { class: 'dropzone', for: 'fileInput', tabindex: '0' }, dropBody);
    const sampleSelect = el('select', { id: 'sampleSelect', 'aria-label': t('new.samples') });

    function renderDrop() {
      if (form.file || form.sample) {
        const sample = cfg.samples.find((s) => s.id === form.sample);
        const name = form.file ? form.file.name : t('new.sample.chosen', { title: sample ? sample.title : form.sample });
        dropBody.replaceChildren(
          el('div', { class: 'drop-icon ok' }, icon('tick')),
          el('div', { class: 'drop-text' },
            el('div', { class: 'drop-title', text: name }),
            el('div', { class: 'muted small', text: form.file ? (form.file.size / 1048576).toFixed(1) + ' MB · ' + t('new.drop.change') : t('new.drop.change') })));
      } else {
        dropBody.replaceChildren(
          el('div', { class: 'drop-icon' }, icon('upload')),
          el('div', { class: 'drop-text' },
            el('div', { class: 'drop-title', text: t('new.drop') }),
            el('div', { class: 'muted small', text: t('new.drop.hint', { mb: cfg.limits.upload_mb }) })));
      }
      drop.classList.toggle('has-file', !!(form.file || form.sample));
      sampleSelect.value = form.sample || '';
      update();
    }

    function setFile(file) {
      form.file = file;
      if (file) form.sample = null;
      renderDrop();
    }
    fileInput.addEventListener('change', () => setFile(fileInput.files[0] || null));
    drop.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fileInput.click(); } });
    ['dragenter', 'dragover'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (ev) => {
      const file = ev.dataTransfer && ev.dataTransfer.files[0];
      if (file) setFile(file);
    });

    sampleSelect.appendChild(el('option', { value: '', text: t('new.samples.none') }));
    for (const s of cfg.samples) {
      sampleSelect.appendChild(el('option', { value: s.id, text: t('grade.n', { n: s.grade }) + ' · Unit ' + s.unit + ' · ' + s.title }));
    }
    sampleSelect.addEventListener('change', () => {
      const s = cfg.samples.find((x) => x.id === sampleSelect.value);
      if (!s) { form.sample = null; renderDrop(); return; }
      form.sample = s.id;
      form.file = null;
      fileInput.value = '';
      form.grade = s.grade;
      form.unit = s.unit;
      form.lesson = 1;
      form.period = 1;
      renderChoices();
      renderDrop();
    });

    // 2. grade, unit, lesson, period
    const choices = el('div', { class: 'choices' });
    const reference = el('div', { class: 'ref-field' });

    function renderChoices() {
      const sec = secondary(form.grade);
      const lessons = sec ? 7 : 3;
      if (form.lesson > lessons) form.lesson = 1;
      choices.replaceChildren(
        field(t('new.grade'), radioGroup('grade', Array.from({ length: 9 }, (_, i) => ({ value: i + 1, label: String(i + 1) })), form.grade, (v) => {
          form.grade = Number(v);
          store('grade', String(form.grade));
          renderChoices();
        }, 'grades')),
        el('div', { class: 'row-3' },
          field(t('new.unit'), numberStepper(form.unit, 1, cfg.max_unit || 20, (n) => { form.unit = n; }, t('new.unit'))),
          field(t('new.lesson'), radioGroup('lesson', Array.from({ length: lessons }, (_, i) => ({ value: i + 1, label: String(i + 1) })), form.lesson, (v) => { form.lesson = Number(v); if (sec) renderChoices(); }),
            sec ? t('new.lesson') + ' ' + form.lesson + ' · ' + LESSON_TYPES[form.lesson - 1] : null),
          sec ? null : field(t('new.period'), radioGroup('period', [1, 2].map((n) => ({ value: n, label: String(n) })), form.period, (v) => { form.period = Number(v); }))));
      renderReference();
    }

    function renderReference() {
      const sec = secondary(form.grade);
      reference.hidden = !sec;
      if (!sec) { form.reference = null; return; }
      const input = el('input', { type: 'file', accept: '.docx', class: 'visually-hidden', id: 'refInput' });
      input.addEventListener('change', () => { form.reference = input.files[0] || null; renderReference(); });
      reference.replaceChildren(
        field(t('new.reference'),
          el('div', { class: 'ref-row' },
            input,
            el('label', { class: 'btn', for: 'refInput' }, icon('file'), form.reference ? form.reference.name : t('new.reference.pick')),
            form.reference ? el('button', { class: 'link-btn', type: 'button', text: t('new.reference.clear'), on: { click: () => { form.reference = null; renderReference(); } } }) : null),
          t('new.reference.hint')));
    }

    // 3. theme
    const themeBox = el('div', { class: 'field' },
      el('div', { class: 'field-label', text: t('new.theme') }),
      themeChips('new', form.theme, (id) => { form.theme = id; }));

    // more options
    const modelInput = el('input', { type: 'text', id: 'modelInput', placeholder: cfg.model, autocomplete: 'off', spellcheck: 'false', 'aria-label': t('new.model') });
    modelInput.addEventListener('input', () => { form.model = modelInput.value.trim(); });
    const picInput = el('input', { type: 'checkbox' });
    picInput.addEventListener('change', () => { form.pictures = picInput.checked; });
    const price = cfg.pictures.price_per_picture_usd;
    const priceNote = price === null ? t('new.pictures.hint.unknown') : t('new.pictures.hint', { n: cfg.pictures.default_max, usd: fmtUsd(price * cfg.pictures.default_max) });
    const more = el('details', { class: 'more' },
      el('summary', null, icon('chevron', 'chev'), el('span', { text: t('new.more') })),
      el('div', { class: 'more-body' },
        field(t('new.model'), modelInput, cfg.key_set ? t('new.model.hint') : t('new.model.hint.nokey')),
        el('label', { class: 'check-row' }, picInput, el('span', null, el('span', { class: 'check-title', text: t('new.pictures') }), el('span', { class: 'muted small block', text: priceNote }))),
        reference));

    // create
    const create = el('button', { class: 'btn primary xl', type: 'button', disabled: true }, icon('sparkle'), t('new.create'));
    const note = el('p', { class: 'create-note muted small', text: cfg.key_set || cfg.provider_mode === 'mock' ? '' : t('new.offline.note') });
    function update() { create.disabled = !(form.file || form.sample); }
    create.addEventListener('click', async () => {
      const data = new FormData();
      if (form.file) data.append('file', form.file);
      else data.append('sample', form.sample);
      if (form.reference && secondary(form.grade)) data.append('reference', form.reference);
      data.append('grade', form.grade);
      data.append('unit', form.unit);
      data.append('lesson', form.lesson);
      data.append('period', form.period);
      data.append('endpoint', cfg.default_endpoint || 'offline');
      if (form.model) data.append('model', form.model);
      data.append('pictures', form.pictures ? 'true' : 'false');
      data.append('max_pictures', cfg.pictures.default_max);
      data.append('theme', form.theme);
      busy(create, true);
      try {
        const res = await api('/api/jobs', { method: 'POST', body: data });
        state.theme = form.theme; store('theme', form.theme);
        location.hash = '#/jobs/' + res.job_id;
      } catch (e) {
        fail(e);
        busy(create, false);
        update();
      }
    });

    renderChoices();
    renderDrop();
    view.replaceChildren(...[
      noKeyBanner(),
      el('div', { class: 'page-head' }, el('div', null, el('h1', { text: t('new.title') }), el('p', { class: 'muted', text: t('new.sub') }))),
      el('section', { class: 'card pad new-card' },
        fileInput, drop,
        el('div', { class: 'sample-row' }, el('span', { class: 'muted small', text: t('new.samples') }), sampleSelect),
        choices,
        themeBox,
        create,
        note,
        more)].filter(Boolean));
  }

  // ------------------------------------------------------------------ settings

  async function viewSettings(token) {
    view.className = 'page narrow';
    view.replaceChildren(el('div', { class: 'skeleton' }));
    const current = await api('/api/settings');
    if (token !== routeToken) return;
    const draft = { theme: current.default_theme };

    // the key
    const keyInput = el('input', {
      type: 'password', id: 'keyInput', autocomplete: 'off', spellcheck: 'false',
      placeholder: current.key_set ? current.key_hint : 'sk-or-v1-…', 'aria-label': t('set.key'),
    });
    const eye = el('button', { class: 'icon-btn field-icon', type: 'button', 'aria-label': t('set.key.show'), title: t('set.key.show') }, icon('eye'));
    eye.addEventListener('click', () => {
      const show = keyInput.type === 'password';
      keyInput.type = show ? 'text' : 'password';
      eye.replaceChildren(icon(show ? 'eyeoff' : 'eye'));
      eye.setAttribute('aria-label', show ? t('set.key.hide') : t('set.key.show'));
      eye.setAttribute('title', show ? t('set.key.hide') : t('set.key.show'));
    });
    const keyResult = el('div', { class: 'test-result', hidden: true, role: 'status' });
    const keyTest = el('button', { class: 'btn', type: 'button' }, icon('check'), t('set.test'));

    // the model
    const modelInput = el('input', { type: 'text', id: 'modelField', value: current.model, placeholder: current.default_model, autocomplete: 'off', spellcheck: 'false', 'aria-label': t('set.model') });
    const modelResult = el('div', { class: 'test-result', hidden: true, role: 'status' });
    const modelTest = el('button', { class: 'btn', type: 'button' }, icon('check'), t('set.test'));

    async function runTest(button, out) {
      busy(button, true);
      out.hidden = false;
      out.className = 'test-result pending';
      out.textContent = t('set.testing');
      try {
        const body = { model: modelInput.value.trim() || undefined };
        if (keyInput.value.trim()) body.api_key = keyInput.value.trim();
        const res = await post('/api/settings/test', body);
        out.className = 'test-result ' + (res.ok ? 'ok' : 'bad');
        out.replaceChildren(
          icon(res.ok ? 'tick' : 'close'),
          el('span', { text: res.ok ? t('set.test.ok', { model: res.model, reply: res.reply }) : res.code === 'no_key' ? t('set.test.nokey') : res.code === 'offline' ? t('set.test.offline') : t('set.test.bad', { error: plain(res.error) }) }));
      } catch (e) {
        out.className = 'test-result bad';
        out.textContent = e.message;
      } finally {
        busy(button, false);
      }
    }
    keyTest.addEventListener('click', () => runTest(keyTest, keyResult));
    modelTest.addEventListener('click', () => runTest(modelTest, modelResult));

    const keyState = current.key_set
      ? el('div', { class: 'field-hint small' },
        el('span', { class: 'chip ok' }, icon('tick'), t('set.key.saved', { hint: current.key_hint })),
        current.key_source === 'environment'
          ? el('span', { class: 'muted', text: t('set.key.env') })
          : el('button', { class: 'link-btn', type: 'button', text: t('set.key.remove'), on: { click: removeKey } }))
      : el('div', { class: 'field-hint muted small', text: t('set.key.none') });

    async function removeKey() {
      try {
        await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: '' }) });
        await loadConfig();
        toast(t('set.removed'), 'success');
        route();
      } catch (e) { fail(e); }
    }

    // the folder
    const openBtn = el('button', { class: 'btn', type: 'button' }, icon('folder'), t('set.folder.open'));
    openBtn.addEventListener('click', async () => {
      busy(openBtn, true);
      try { await post('/api/open-folder'); } catch (e) { fail(e); } finally { busy(openBtn, false); }
    });

    // save
    const save = el('button', { class: 'btn primary xl', type: 'button' }, icon('tick'), t('set.save'));
    save.addEventListener('click', async () => {
      const body = { model: modelInput.value.trim(), default_theme: draft.theme };
      if (keyInput.value.trim()) body.api_key = keyInput.value.trim();
      busy(save, true);
      try {
        await api('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        state.theme = draft.theme; store('theme', draft.theme);
        await loadConfig();
        setStatus();
        toast(t('set.saved'), 'success');
        route();
      } catch (e) {
        fail(e);
        busy(save, false);
      }
    });

    view.replaceChildren(
      el('div', { class: 'page-head' }, el('div', null, el('h1', { text: t('set.title') }), el('p', { class: 'muted', text: t('set.sub') }))),
      el('section', { class: 'card pad settings-card' },
        el('div', { class: 'set-block' },
          el('label', { class: 'field-label', for: 'keyInput', text: t('set.key') }),
          el('div', { class: 'input-row' },
            el('div', { class: 'input-wrap' }, keyInput, eye),
            keyTest),
          keyResult,
          keyState,
          el('a', { class: 'small', href: 'https://openrouter.ai/keys', target: '_blank', rel: 'noopener noreferrer', text: t('set.key.get') })),
        el('div', { class: 'set-block' },
          el('label', { class: 'field-label', for: 'modelField', text: t('set.model') }),
          el('div', { class: 'input-row' }, el('div', { class: 'input-wrap' }, modelInput), modelTest),
          modelResult,
          el('div', { class: 'field-hint muted small', text: t('set.model.hint', { model: current.default_model }) })),
        el('div', { class: 'set-block' },
          el('div', { class: 'field-label', text: t('set.theme') }),
          themeChips('settings', draft.theme, (id) => { draft.theme = id; })),
        el('div', { class: 'set-block' },
          el('label', { class: 'field-label', for: 'folderField', text: t('set.folder') }),
          el('div', { class: 'input-row' },
            el('div', { class: 'input-wrap' }, el('input', { type: 'text', id: 'folderField', value: current.workdir, readOnly: true })),
            openBtn),
          el('div', { class: 'field-hint muted small', text: t('set.folder.hint') })),
        save));
  }

  // ------------------------------------------------------------------ job

  function viewJob(id, token) {
    view.className = 'page narrow';
    const title = el('h1', { text: t('job.running') });
    const sub = el('p', { class: 'muted', text: t('job.sub') });
    const steps = el('ol', { class: 'stepper' });
    const body = el('div', { class: 'card pad job-card' }, title, sub, steps);
    const footer = el('div', { class: 'job-foot' });
    view.replaceChildren(body, footer);

    function draw(job) {
      steps.replaceChildren();
      job.stages.forEach((name) => {
        const done = job.stages_done.includes(name);
        const skipped = job.stages_skipped.includes(name);
        const active = job.status === 'running' && job.stage === name;
        const failed = job.status === 'error' && !done && job.stage === name;
        const status = failed ? 'failed' : skipped ? 'skipped' : done ? 'done' : active ? 'active' : 'todo';
        steps.appendChild(el('li', { class: 'step ' + status, 'aria-current': active ? 'step' : null },
          el('span', { class: 'step-dot' }, status === 'done' ? icon('tick') : status === 'active' ? el('span', { class: 'spinner' }) : status === 'failed' ? icon('close') : ''),
          el('span', { class: 'step-body' },
            el('span', { class: 'step-name', text: t('stage.' + name) }),
            el('span', { class: 'step-sub muted small', text: status === 'skipped' ? t('stage.skipped') : active && job.detail ? tx('detail', job.detail) : t('stage.' + name + '.hint') }))));
      });
    }

    async function poll() {
      if (token !== routeToken) return;
      let job;
      try {
        job = await api('/api/jobs/' + id);
      } catch (e) {
        title.textContent = t('job.failed');
        footer.replaceChildren(el('div', { class: 'callout danger', text: e.message }), el('a', { class: 'btn', href: '#/new', text: t('job.back') }));
        return;
      }
      if (token !== routeToken) return;
      draw(job);
      if (job.status === 'running') {
        setTimeout(poll, 700);
        return;
      }
      if (job.status === 'error') {
        title.textContent = t('job.failed');
        sub.textContent = '';
        footer.replaceChildren(el('div', { class: 'callout danger', text: plain(job.error) }), el('a', { class: 'btn', href: '#/new', text: t('job.back') }));
        return;
      }
      title.textContent = t('job.done');
      sub.textContent = '';
      await refreshLessons();
      if (job.warnings.length) {
        footer.replaceChildren(
          el('div', { class: 'callout warn' }, el('strong', { text: t('job.warnings') }), el('ul', null, job.warnings.map((w) => el('li', { text: plain(w) })))),
          el('a', { class: 'btn primary', href: '#/lessons/' + job.lesson_id }, t('job.open')));
        return;
      }
      setTimeout(() => { if (token === routeToken) location.hash = '#/lessons/' + job.lesson_id; }, 500);
    }
    draw({ stages: ['ingest', 'outline', 'draft', 'pictures', 'export'], stages_done: [], stages_skipped: [], stage: 'ingest', status: 'running' });
    poll();
  }

  // ------------------------------------------------------------------ block content

  function humanise(value) {
    return String(value).replace(/_/g, ' ');
  }

  function ipaText(ipa) {
    return ipa ? '/' + String(ipa).replace(/^\/|\/$/g, '') + '/' : '';
  }

  function fieldsOf(block) {
    const c = block.content;
    const f = [];
    const add = (key, label, text, extra) => { if (text !== undefined && text !== null && String(text).trim() !== '') f.push(Object.assign({ key, label, text: String(text) }, extra || {})); };
    switch (block.kind) {
      case 'objective':
        add('text', t('f.objective'), c.text);
        add('skills', t('f.skills'), (c.skills || []).map((s) => tx('skill', s)).join(', '));
        break;
      case 'vocabulary':
        (c.items || []).forEach((it) => add('w:' + it.word.toLowerCase(), it.word,
          [ipaText(it.ipa), it.pos, it.meaning_vi, it.example ? '“' + it.example + '”' : ''].filter(Boolean).join('  ·  '), { strong: true }));
        break;
      case 'sentence_pattern':
        add('pattern', t('f.pattern'), c.pattern, { strong: true });
        add('function', t('f.function'), c.function);
        (c.examples || []).forEach((ex, i) => add('ex:' + i, t('f.example'), ex));
        add('notes', t('f.notes'), c.notes);
        break;
      case 'dialogue':
        add('context', t('f.context'), c.context);
        (c.lines || []).forEach((l, i) => add('l:' + i, l.speaker || '–', l.text, { speaker: true }));
        break;
      case 'phonics':
        add('sound', t('f.sound'), '/' + c.sound + '/  ' + c.letters, { strong: true });
        add('words', t('f.words'), (c.words || []).join(', '));
        (c.chant_lines || []).forEach((l, i) => add('chant:' + i, t('f.chant'), l));
        break;
      case 'activity':
        add('title', t('f.title'), c.title, { strong: true });
        add('meta', t('f.meta'), [tx('act', c.type), tx('inter', c.interaction), c.minutes + ' ' + t('min'), (c.skills || []).map((s) => tx('skill', s)).join('/')].filter(Boolean).join('  ·  '));
        add('instr', t('f.instructions'), c.instructions);
        (c.teacher_steps || []).forEach((s, i) => add('ts:' + i, t('f.teacher') + ' ' + (i + 1), s, { step: 'teacher' }));
        (c.student_steps || []).forEach((s, i) => add('ss:' + i, t('f.pupils') + ' ' + (i + 1), s, { step: 'pupil' }));
        add('materials', t('f.materials'), (c.materials || []).join(', '));
        break;
      case 'teacher_note':
        add('text', t('f.note'), c.text);
        break;
      case 'summary':
        (c.bullets || []).forEach((b, i) => add('b:' + i, '•', b));
        break;
      case 'image_ref':
        add('caption', t('f.caption'), c.caption);
        add('alt', t('f.alt'), c.alt);
        break;
      case 'reading':
        add('title', t('f.title'), c.title, { strong: true });
        add('instr', t('f.instructions'), c.instruction);
        (c.paragraphs || []).forEach((p, i) => add('p:' + i, '¶' + (i + 1), p.text));
        break;
      case 'match':
        add('instr', t('f.instructions'), c.instruction);
        (c.left || []).forEach((l, i) => add('l:' + i, String(i + 1), l + '  →  ' + (c.right || [])[(c.answer || {})[i]]));
        break;
      case 'gap_fill':
        add('instr', t('f.instructions'), c.instruction);
        add('box', t('f.words'), (c.word_box || []).join(', '));
        (c.items || []).forEach((it, i) => add('i:' + i, String(i + 1), it.text));
        break;
      case 'choice':
        add('instr', t('f.instructions'), c.instruction);
        (c.items || []).forEach((it, i) => add('i:' + i, String(i + 1), it.stem + '  ·  ' + (it.options || []).join(' / ')));
        break;
      case 'true_false':
        add('instr', t('f.instructions'), c.instruction);
        (c.items || []).forEach((it, i) => add('i:' + i, String(i + 1), it.statement + '  ·  ' + (it.answer ? 'T' : 'F')));
        break;
      case 'rule_box':
        add('title', t('f.title'), c.title, { strong: true });
        (c.points || []).forEach((p, i) => add('pt:' + i, '•', p));
        (c.examples || []).forEach((ex, i) => add('ex:' + i, t('f.example'), ex));
        break;
      case 'question_set':
        add('instr', t('f.instructions'), c.instruction);
        add('prompt', t('f.instructions'), c.prompt);
        (c.questions || []).forEach((q, i) => add('q:' + i, String(i + 1), q.question));
        break;
      default:
        break;
    }
    if (block.teacher_note) add('tnote', t('f.note'), block.teacher_note);
    return f;
  }

  function lcs(a, b) {
    const n = a.length;
    const m = b.length;
    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const keepA = new Set();
    const keepB = new Set();
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) { keepA.add(i); keepB.add(j); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
      else j++;
    }
    return { keepA, keepB };
  }

  /** Text nodes for `mine` with the words that `other` does not share highlighted. */
  function diffNodes(mine, other, side) {
    const a = mine.split(/(\s+)/);
    const b = other.split(/(\s+)/);
    const { keepA, keepB } = side === 'before' ? lcs(a, b) : (() => { const r = lcs(b, a); return { keepA: r.keepB, keepB: r.keepA }; })();
    const out = [];
    a.forEach((token, i) => {
      if (/^\s+$/.test(token) || keepA.has(i)) out.push(token);
      else out.push(el('mark', { class: side === 'before' ? 'del' : 'ins', text: token }));
    });
    return out;
  }

  function renderFields(fields, otherFields, side) {
    const list = el('dl', { class: 'fields' + (side ? ' diff' : '') });
    if (!fields.length) list.appendChild(el('div', { class: 'muted small', text: t('f.empty') }));
    fields.forEach((fld) => {
      const other = otherFields ? otherFields.find((x) => x.key === fld.key) : null;
      let cls = 'frow' + (fld.strong ? ' strong' : '') + (fld.step ? ' ' + fld.step : '') + (fld.speaker ? ' speaker' : '');
      let content;
      if (otherFields && !other) {
        cls += side === 'before' ? ' removed' : ' added';
        content = fld.text;
      } else if (other && other.text !== fld.text) {
        content = diffNodes(fld.text, other.text, side);
      } else {
        content = fld.text;
      }
      list.appendChild(el('div', { class: cls },
        el('dt', { text: fld.label }),
        el('dd', null, content)));
    });
    return list;
  }

  // ------------------------------------------------------------------ badges

  function provBadge(prov) {
    const map = { extracted: 'source', derived: 'derived', generated: 'ai' };
    return el('span', { class: 'badge ' + (map[prov] || 'ai'), text: t('prov.' + prov) });
  }

  function statusDot(status) {
    return el('span', { class: 'dot ' + status, title: t('status.' + status), role: 'img', 'aria-label': t('status.' + status) });
  }

  // ------------------------------------------------------------------ lesson screen

  async function viewLesson(id, token) {
    view.className = 'lesson-screen';
    view.replaceChildren(el('div', { class: 'skeleton' }));
    const payload = await api('/api/lessons/' + encodeURIComponent(id));
    if (token !== routeToken) return;
    state.payload = payload;
    state.selected = payload.summary.blocks.length ? [payload.summary.blocks[0].id] : [];
    buildLesson();
  }

  function blockRow(id) {
    return state.payload.summary.blocks.find((b) => b.id === id);
  }

  function buildLesson() {
    const { lesson, summary } = state.payload;

    // header
    const undoBtn = el('button', { class: 'icon-btn lg', type: 'button', 'aria-label': t('act.undo'), title: t('act.undo'), disabled: !summary.can_undo, on: { click: () => doUndoRedo('undo', undoBtn) } }, icon('undo'));
    const redoBtn = el('button', { class: 'icon-btn lg', type: 'button', 'aria-label': t('act.redo'), title: t('act.redo'), disabled: !summary.can_redo, on: { click: () => doUndoRedo('redo', redoBtn) } }, icon('redo'));
    const menu = downloadMenu(lesson.id);
    const tools = toolsMenu();

    const issues = summary.failures
      ? el('span', { class: 'chip danger', text: t('lesson.failures', { n: summary.failures }) })
      : summary.warnings
        ? el('span', { class: 'chip warn', text: t('lesson.warnings', { n: summary.warnings }) })
        : null;
    const head = el('div', { class: 'lesson-head' },
      el('a', { class: 'back-link', href: '#/', 'aria-label': t('nav.lessons'), title: t('nav.lessons') }, icon('back')),
      el('div', { class: 'lh-title' },
        el('h1', { text: summary.label }),
        el('div', { class: 'lh-sub' },
          el('span', { class: 'lh-unit', text: summary.unit_line }),
          issues)),
      el('div', { class: 'lh-actions' }, undoBtn, redoBtn, tools, menu));

    const blocksPane = el('section', { class: 'pane blocks', 'aria-label': t('pane.blocks') });
    const deckPane = el('section', { class: 'pane deck', 'aria-label': t('pane.deck') });
    const inspector = el('section', { class: 'pane inspector', 'aria-label': t('pane.block') });
    view.replaceChildren(head, el('div', { class: 'panes' }, blocksPane, deckPane, inspector));

    const ui = { blocksPane, deckPane, inspector, frame: null };
    state.ui = ui;
    renderBlocksPane();
    buildDeckPane(ui);
    renderInspector();
  }

  function downloadStem() {
    const label = state.payload ? state.payload.summary.label : 'lesson';
    return label.replace(/ · /g, '-').replace(/\s+/g, '-').replace(/[^A-Za-z0-9-]/g, '') || 'lesson';
  }

  function downloadMenu(lessonId) {
    const item = (fmt, label) => el('button', {
      type: 'button',
      role: 'menuitem',
      on: {
        click: (ev) => {
          ev.currentTarget.closest('details').open = false;
          const q = fmt === 'html' ? '?theme=' + encodeURIComponent(state.theme) : '';
          download('/api/lessons/' + lessonId + '/export/' + fmt + q, downloadStem() + '.' + fmt);
        },
      },
    }, el('span', { class: 'menu-name', text: label }), el('span', { class: 'muted small', text: t('dl.' + fmt) }));
    const details = el('details', { class: 'menu' },
      el('summary', { class: 'btn primary' }, icon('download'), t('act.download'), icon('chevron', 'chev')),
      el('div', { class: 'menu-pop', role: 'menu' }, item('html', 'HTML')));
    return details;
  }

  function toolsMenu() {
    const item = (label, hint, run) => el('button', {
      type: 'button',
      role: 'menuitem',
      on: { click: (ev) => { const b = ev.currentTarget; b.closest('details').open = false; run(b); } },
    }, el('span', { class: 'menu-name', text: label }), el('span', { class: 'muted small', text: hint }));
    return el('details', { class: 'menu' },
      el('summary', { class: 'icon-btn lg', 'aria-label': t('act.more'), title: t('act.more') }, icon('more')),
      el('div', { class: 'menu-pop', role: 'menu' },
        item(t('act.check'), t('act.check.hint'), doCheck),
        item(t('act.pictures'), t('act.pictures.hint'), doIllustrate)));
  }

  // -- left pane

  function renderBlocksPane() {
    const pane = state.ui.blocksPane;
    const { summary } = state.payload;
    pane.replaceChildren(el('div', { class: 'pane-head' }, el('h2', { text: t('pane.blocks') }), el('span', { class: 'muted small', text: t('blocks.hint') })));
    const body = el('div', { class: 'pane-body' });
    summary.sections.forEach((section) => {
      const rows = summary.blocks.filter((b) => b.section_id === section.id);
      if (!rows.length) return;
      body.appendChild(el('div', { class: 'phase phase-' + section.phase },
        el('span', { class: 'phase-name', text: tx('phase', section.phase) }),
        el('span', { class: 'muted small', text: section.minutes ? section.minutes + ' ' + t('min') : '' })));
      rows.forEach((b) => {
        const on = state.selected.includes(b.id);
        body.appendChild(el('button', {
          type: 'button',
          class: 'block-row' + (on ? ' selected' : ''),
          'aria-pressed': String(on),
          dataset: { id: b.id },
          on: { click: (ev) => selectBlock(b.id, ev.shiftKey || ev.ctrlKey || ev.metaKey) },
        },
        el('span', { class: 'kind-icon k-' + b.kind }, icon(b.kind)),
        el('span', { class: 'br-body' },
          el('span', { class: 'br-kind', text: tx('kind', b.kind) }),
          el('span', { class: 'br-label', text: b.label })),
        el('span', { class: 'br-side' }, provBadge(b.provenance), b.status !== 'pass' ? statusDot(b.status) : null)));
      });
    });
    pane.appendChild(body);
  }

  function selectBlock(id, additive) {
    if (additive) {
      state.selected = state.selected.includes(id) ? state.selected.filter((x) => x !== id) : [...state.selected, id];
      if (!state.selected.length) state.selected = [id];
    } else {
      state.selected = [id];
    }
    renderBlocksPane();
    renderInspector();
    syncDeck();
  }

  // -- centre pane

  function deckUrl() {
    return '/api/lessons/' + encodeURIComponent(state.payload.lesson.id) + '/deck?theme=' + encodeURIComponent(state.theme);
  }

  function buildDeckPane(ui) {
    const themeSeg = el('select', { class: 'theme-select', 'aria-label': t('new.theme') });
    state.config.themes.forEach((th) => themeSeg.appendChild(el('option', { value: th.id, selected: th.id === state.theme, text: th.label, title: th.description })));
    themeSeg.addEventListener('change', () => {
      state.theme = themeSeg.value;
      store('theme', state.theme);
      loadDeck();
    });
    const present = el('button', { class: 'btn', type: 'button', on: { click: () => window.open(deckUrl() + '#' + (slideIndex() + 1), '_blank', 'noopener') } }, icon('play'), el('span', { class: 'btn-text', text: t('act.present') }));
    ui.frame = el('iframe', { class: 'deck-frame', title: t('pane.deck'), allow: 'fullscreen', allowfullscreen: true });
    ui.deckNote = el('div', { class: 'deck-note', hidden: true });
    ui.frame.addEventListener('load', onDeckLoad);
    ui.deckPane.replaceChildren(
      el('div', { class: 'pane-head' }, el('h2', { text: t('pane.deck') }), el('div', { class: 'head-tools' }, themeSeg, present)),
      el('div', { class: 'pane-body deck-body' }, el('div', { class: 'deck-stage' }, ui.frame, ui.deckNote), el('p', { class: 'muted small deck-hint', text: t('deck.hint') })));
    loadDeck();
  }

  let wantedSlide = 0;

  function loadDeck() {
    const ui = state.ui;
    ui.deckNote.hidden = true;
    ui.frame.dataset.loadedFor = '';
    // After a save made on the slide the deck reopens on the slide the teacher was editing.
    if (state.pendingSlide != null) {
      wantedSlide = state.pendingSlide;
      state.pendingSlide = null;
      state.keepSlide = true;
    } else {
      wantedSlide = slideIndex();
    }
    ui.frame.src = deckUrl() + '#' + (wantedSlide + 1);
  }

  function slideDoc() {
    try { return state.ui.frame.contentDocument; } catch (e) { return null; }
  }

  function slideIndex() {
    const doc = slideDoc();
    const primary = state.selected[0];
    if (!doc || !primary || !doc.querySelector('.slide')) return 0;
    const slides = Array.from(doc.querySelectorAll('.slide'));
    const mark = doc.querySelector('[data-block-id="' + CSS.escape(primary) + '"]');
    if (!mark) return 0;
    const slide = mark.closest('.slide');
    const idx = slide ? slides.indexOf(slide) : -1;
    return idx < 0 ? 0 : idx;
  }

  function onDeckLoad() {
    const ui = state.ui;
    const doc = slideDoc();
    if (!doc) return;
    const text = (doc.body && doc.body.textContent || '').trim();
    if (!doc.querySelector('.slide') && text.startsWith('{')) {
      let message = text;
      try { message = JSON.parse(text).error || text; } catch (e) { /* keep raw text */ }
      ui.deckNote.hidden = false;
      ui.deckNote.replaceChildren(el('strong', { text: t('deck.blocked') }), el('p', { text: message }));
      return;
    }
    ui.deckNote.hidden = true;
    if (state.keepSlide) state.keepSlide = false;
    else syncDeck();
  }

  function syncDeck() {
    const ui = state.ui;
    const doc = slideDoc();
    if (!ui || !doc || !doc.querySelector('.slide')) return;
    const idx = slideIndex();
    const win = ui.frame.contentWindow;
    const want = '#' + (idx + 1);
    const have = win.location.hash || '#1';
    if (have !== want) {
      win.location.hash = want;
      win.location.reload();
    }
  }

  // -- right pane

  function renderInspector() {
    const pane = state.ui.inspector;
    const { lesson } = state.payload;
    pane.replaceChildren();
    if (!state.selected.length) {
      pane.appendChild(el('div', { class: 'empty small-empty' }, el('p', { class: 'muted', text: t('insp.none') })));
      return;
    }
    const body = el('div', { class: 'pane-body' });
    const rows = state.selected.map(blockRow).filter(Boolean);
    const blocks = state.selected.map((id) => lesson.blocks[id]).filter(Boolean);
    if (blocks.length === 1) {
      const block = blocks[0];
      const row = rows[0];
      pane.appendChild(el('div', { class: 'pane-head' },
        el('div', { class: 'insp-title' }, el('span', { class: 'kind-icon k-' + block.kind }, icon(block.kind)), el('h2', { text: tx('kind', block.kind) })),
        provBadge(block.provenance)));
      body.appendChild(el('section', { class: 'sect' }, renderFields(fieldsOf(block), null, null)));
      body.appendChild(sourcesSection(block, row));
      body.appendChild(checksSection(block));
    } else {
      pane.appendChild(el('div', { class: 'pane-head' }, el('h2', { text: t('insp.multi', { n: blocks.length }) })));
      body.appendChild(el('ul', { class: 'multi-list' }, rows.map((r) => el('li', null, el('span', { class: 'kind-icon k-' + r.kind }, icon(r.kind)), el('span', { text: r.label })))));
    }
    body.appendChild(editBox(blocks));
    pane.appendChild(body);
  }

  function sourcesSection(block, row) {
    const box = el('section', { class: 'sect' }, el('h3', { text: t('insp.sources') }));
    const cites = row ? row.citations : [];
    if (!cites.length) {
      box.appendChild(el('p', { class: 'muted small', text: block.provenance === 'generated' ? t('insp.nosource.ai') : t('insp.nosource') }));
      return box;
    }
    cites.forEach((c) => {
      box.appendChild(el('figure', { class: 'cite' },
        el('figcaption', null, el('span', { class: 'chip', text: t('insp.page', { n: c.page }) }), el('span', { class: 'muted small', text: c.filename || t('plain.doc') })),
        c.quote ? el('blockquote', { text: c.quote }) : null));
    });
    return box;
  }

  function checksSection(block) {
    const box = el('section', { class: 'sect' }, el('h3', { text: t('insp.checks') }));
    if (!block.checks.length) {
      box.appendChild(el('p', { class: 'muted small', text: t('insp.nochecks') }));
      return box;
    }
    const list = el('ul', { class: 'checks' });
    block.checks.forEach((c) => {
      list.appendChild(el('li', { class: 'check ' + c.status },
        statusDot(c.status),
        el('span', { class: 'check-body' },
          el('span', { class: 'check-name', text: tx('check', c.name) }),
          c.detail ? el('span', { class: 'muted small', text: plain(c.detail) }) : null)));
    });
    box.appendChild(list);
    return box;
  }

  function editBox(blocks) {
    const cfg = state.config;
    const kinds = new Set(blocks.map((b) => b.kind));
    const presets = cfg.presets.filter((p) => Array.from(kinds).every((k) => p.applies_to.includes(k)));
    const hasDefault = cfg.endpoints.some((e) => e.name === 'default');
    const defaultHost = (cfg.endpoints.find((e) => e.name === 'default') || {}).host;
    const models = cfg.endpoints.filter((e) => e.configured && e.name !== 'offline' && !(hasDefault && e.name !== 'default' && e.host === defaultHost));
    const area = el('textarea', { rows: 3, placeholder: t('edit.placeholder'), 'aria-label': t('edit.instruction') });
    const numberRow = el('div', { class: 'number-row', hidden: true });
    const proposeBtn = el('button', { class: 'btn primary', type: 'button', disabled: !models.length }, icon('sparkle'), t('edit.propose'));
    const select = el('select', { 'aria-label': t('new.model') });
    models.forEach((m) => select.appendChild(el('option', { value: m.name, selected: m.name === state.endpoint, text: m.label })));
    select.addEventListener('change', () => { state.endpoint = select.value; });

    const presetBox = el('div', { class: 'presets' });
    presets.forEach((p) => {
      presetBox.appendChild(el('button', {
        type: 'button',
        class: 'preset',
        title: p.instruction,
        on: {
          click: () => {
            if (!p.takes_number) {
              area.value = p.instruction;
              numberRow.hidden = true;
              area.focus();
              return;
            }
            const input = el('input', { type: 'number', min: 1, max: 20, value: 4, 'aria-label': 'N' });
            numberRow.hidden = false;
            numberRow.replaceChildren(
              el('span', { class: 'small', text: lang === 'vi' ? p.label_vi : p.label_en }),
              input,
              el('button', { class: 'btn small', type: 'button', text: 'OK', on: { click: () => { area.value = p.instruction.replace(/\{\{n\}\}/g, input.value || '4'); numberRow.hidden = true; area.focus(); } } }));
            input.focus();
          },
        },
        text: lang === 'vi' ? p.label_vi : p.label_en,
      }));
    });

    proposeBtn.addEventListener('click', async () => {
      const instruction = area.value.trim();
      if (!instruction) { toast(t('edit.empty'), 'error'); area.focus(); return; }
      busy(proposeBtn, true);
      try {
        const proposal = await post('/api/lessons/' + state.payload.lesson.id + '/proposals', {
          block_ids: state.selected, instruction, endpoint: select.value || state.endpoint,
        });
        showProposal(proposal);
      } catch (e) {
        fail(e);
      } finally {
        busy(proposeBtn, false);
      }
    });

    return el('section', { class: 'sect edit' },
      el('h3', { text: t('edit.title') }),
      presets.length ? presetBox : null,
      numberRow,
      area,
      models.length ? null : el('p', { class: 'callout warn small' }, t('edit.nomodel'), ' ', el('a', { href: '#/settings', text: t('banner.nokey.cta') })),
      el('div', { class: 'edit-foot' }, models.length ? select : null, proposeBtn));
  }

  // ------------------------------------------------------------------ proposal

  function showProposal(proposal) {
    const lessonId = state.payload.lesson.id;
    const acceptBtn = el('button', { class: 'btn primary', type: 'button' }, icon('tick'), t('prop.accept'));
    const discard = async () => {
      closeOverlay();
      try { await api('/api/lessons/' + lessonId + '/proposals/' + proposal.id, { method: 'DELETE' }); } catch (e) { /* already gone */ }
    };
    const discardBtn = el('button', { class: 'btn', type: 'button', dataset: { close: '' }, on: { click: discard } }, t('prop.discard'));

    acceptBtn.addEventListener('click', async () => {
      busy(acceptBtn, true);
      try {
        const next = await post('/api/lessons/' + lessonId + '/proposals/' + proposal.id + '/accept');
        applyPayload(next);
        closeOverlay();
        toast(t('prop.accepted'), 'success');
      } catch (e) {
        fail(e);
        busy(acceptBtn, false);
      }
    });

    const labelOf = (change) => {
      const row = blockRow(change.block_id);
      return row ? row.label : (change.after || change.before).kind;
    };
    const changes = proposal.changes.map((change) => {
      const kind = (change.after || change.before).kind;
      const beforeFields = change.before ? fieldsOf(change.before) : [];
      const afterFields = change.after ? fieldsOf(change.after) : [];
      const sameBoth = change.before && change.after;
      return el('section', { class: 'change card' },
        el('header', { class: 'change-head' },
          el('span', { class: 'kind-icon k-' + kind }, icon(kind)),
          el('strong', { text: tx('kind', kind) }),
          el('span', { class: 'chip ' + (change.op === 'remove' ? 'danger' : change.op === 'add' ? 'ok' : ''), text: t('prop.op.' + change.op) }),
          change.op === 'replace' ? el('span', { class: 'muted small trunc', text: labelOf(change) }) : null),
        el('div', { class: 'ba' },
          el('div', { class: 'ba-col before' }, el('div', { class: 'ba-label', text: t('prop.before') }),
            change.before ? renderFields(beforeFields, sameBoth ? afterFields : [], 'before') : el('p', { class: 'muted small', text: t('prop.none') })),
          el('div', { class: 'ba-col after' }, el('div', { class: 'ba-label', text: t('prop.after') }),
            change.after ? renderFields(afterFields, sameBoth ? beforeFields : [], 'after') : el('p', { class: 'muted small', text: t('prop.removed') }))));
    });

    const affected = proposal.affected.length
      ? el('section', { class: 'sect' }, el('h3', { text: t('prop.affected') }),
        el('ul', { class: 'affected' }, proposal.affected.map((a) => {
          const row = blockRow(a.block_id);
          return el('li', null,
            el('span', { class: 'chip warn', text: tx('act2', a.suggested_action) }),
            el('span', { class: 'aff-body' }, el('strong', { text: row ? row.label : plain(a.block_id) }), el('span', { class: 'muted small', text: plain(a.reason) })));
        })))
      : null;

    const checks = proposal.checks_preview.filter((c) => c.status !== 'pass');
    const passes = proposal.checks_preview.length - checks.length;
    const checkBox = el('section', { class: 'sect' }, el('h3', { text: t('prop.checks') }),
      checks.length
        ? el('ul', { class: 'checks' }, checks.map((c) => el('li', { class: 'check ' + c.status },
          statusDot(c.status),
          el('span', { class: 'check-body' },
            el('span', { class: 'check-name', text: tx('check', c.name) + (c.block_id && blockRow(c.block_id) ? ' · ' + blockRow(c.block_id).label : '') }),
            c.detail ? el('span', { class: 'muted small', text: plain(c.detail) }) : null))))
        : null,
      el('p', { class: 'muted small', text: passes ? t('prop.checks.pass', { n: passes }) : '' }));

    const drawer = el('div', { class: 'drawer-wrap' },
      el('div', { class: 'backdrop', on: { click: discard } }),
      el('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('prop.title') },
        el('header', { class: 'drawer-head' }, el('div', null, el('h2', { text: t('prop.title') }), el('p', { class: 'muted small instr', text: '“' + proposal.instruction + '”' })),
          el('button', { class: 'icon-btn', type: 'button', 'aria-label': t('close'), on: { click: discard } }, icon('close'))),
        el('div', { class: 'drawer-body' },
          proposal.rationale ? el('div', { class: 'callout' }, el('strong', { text: t('prop.rationale') }), el('p', { text: proposal.rationale })) : null,
          changes.length ? changes : el('p', { class: 'muted', text: t('prop.nochange') }),
          affected,
          checkBox),
        el('footer', { class: 'drawer-foot' }, discardBtn, acceptBtn)));
    openOverlay(drawer);
    acceptBtn.focus();
  }

  function applyPayload(next) {
    state.payload = next;
    const ids = new Set(Object.keys(next.lesson.blocks));
    state.selected = state.selected.filter((id) => ids.has(id));
    if (!state.selected.length && next.summary.blocks.length) state.selected = [next.summary.blocks[0].id];
    buildLesson();
    refreshLessons();
  }

  // ------------------------------------------------------------------ lesson actions

  async function doUndoRedo(which, button) {
    busy(button, true);
    try {
      applyPayload(await post('/api/lessons/' + state.payload.lesson.id + '/' + which));
      toast(t(which === 'undo' ? 'act.undone' : 'act.redone'), 'success');
    } catch (e) {
      fail(e);
      busy(button, false);
    }
  }

  async function doCheck(button) {
    busy(button, true);
    try {
      applyPayload(await post('/api/lessons/' + state.payload.lesson.id + '/check', { endpoint: 'offline' }));
      toast(t('act.checked'), 'success');
    } catch (e) {
      fail(e);
      busy(button, false);
    }
  }

  async function doIllustrate(button) {
    const id = state.payload.lesson.id;
    const endpoint = state.endpoint || 'offline';
    const max = state.config.pictures.default_max;
    busy(button, true);
    try {
      const plan = await post('/api/lessons/' + id + '/illustrate', { max, endpoint, dry_run: true });
      busy(button, false);
      const r = plan.report;
      if (!r.planned) {
        toast(t('pic.none', { n: r.reused }), 'info');
        return;
      }
      confirmDialog({
        title: t('pic.title'),
        body: t('pic.body', { n: r.planned, model: r.model, usd: fmtUsd(r.estimate_usd), words: r.words.join(', ') }),
        ok: t('pic.go'),
        onOk: async (okBtn) => {
          busy(okBtn, true);
          try {
            const done = await post('/api/lessons/' + id + '/illustrate', { max, endpoint });
            closeOverlay();
            applyPayload(done);
            toast(t('pic.done', { n: done.report.generated, usd: fmtUsd(done.report.cost_usd) }), 'success');
          } catch (e) {
            fail(e);
            busy(okBtn, false);
          }
        },
      });
    } catch (e) {
      fail(e);
      busy(button, false);
    }
  }

  function confirmDialog(opts) {
    const ok = el('button', { class: 'btn primary', type: 'button', text: opts.ok });
    ok.addEventListener('click', () => opts.onOk(ok));
    const cancel = el('button', { class: 'btn', type: 'button', dataset: { close: '' }, text: t('cancel'), on: { click: closeOverlay } });
    openOverlay(el('div', { class: 'modal-wrap' },
      el('div', { class: 'backdrop', on: { click: closeOverlay } }),
      el('div', { class: 'modal card', role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title },
        el('h2', { text: opts.title }),
        el('p', { text: opts.body }),
        el('div', { class: 'modal-foot' }, cancel, ok))));
    ok.focus();
  }

  // ------------------------------------------------------------------ boot

  function setLang(next) {
    lang = next;
    store('lang', next);
    applyStaticText();
    setStatus();
    route();
  }

  // The deck in the middle pane saves edits itself (pencil and Save on the slide); it then hands
  // over the fresh lesson so the block list, the checks and Undo are up to date.
  window.addEventListener('message', (ev) => {
    const data = ev.data;
    if (ev.origin !== location.origin || !data || data.type !== 'classdeck:saved') return;
    if (!state.ui || !state.ui.frame || ev.source !== state.ui.frame.contentWindow) return;
    if (!data.payload || !data.payload.lesson) return;
    state.pendingSlide = Math.max(0, (parseInt(data.slide, 10) || 1) - 1);
    applyPayload(data.payload);
    toast(t('deck.saved'), 'success');
    (data.notices || []).forEach((message) => toast(String(message), 'info'));
  });

  async function boot() {
    document.querySelectorAll('.lang button').forEach((b) => b.addEventListener('click', () => setLang(b.dataset.lang)));
    applyStaticText();
    try {
      await loadConfig();
    } catch (e) {
      view.replaceChildren(errorState(e));
      return;
    }
    setStatus();
    window.addEventListener('hashchange', route);
    await refreshLessons();
    route();
  }

  boot();
}());
