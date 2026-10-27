/* ClassDeck local app: hash-routed single page, no build step, no network beyond its own API. */
(function () {
  'use strict';

  // ------------------------------------------------------------------ helpers

  const view = document.getElementById('view');
  const crumbs = document.getElementById('crumbs');
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
    toast(e && e.message ? e.message : String(e), 'error');
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
    endpoint: store('endpoint') || null,
  };

  let routeToken = 0;

  async function loadConfig() {
    state.config = await api('/api/config');
    const cfg = state.config;
    if (!state.theme || !cfg.themes.some((x) => x.id === state.theme)) state.theme = cfg.default_theme;
    const known = cfg.endpoints.find((x) => x.name === state.endpoint && x.configured);
    if (!known) state.endpoint = cfg.default_endpoint;
    document.getElementById('navVersion').textContent = 'v' + cfg.version;
  }

  async function refreshLessons() {
    try {
      state.lessons = (await api('/api/lessons')).lessons;
    } catch (e) {
      state.lessons = [];
    }
    renderNav();
  }

  // ------------------------------------------------------------------ chrome

  function applyStaticText() {
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-i18n]').forEach((node) => { node.textContent = t(node.dataset.i18n); });
    const navNew = document.getElementById('navNew');
    navNew.replaceChildren(icon('plus'), el('span', { text: t('nav.new') }));
    const navHome = document.getElementById('navHome');
    navHome.replaceChildren(icon('lessons'), el('span', { text: t('nav.lessons') }));
    document.querySelectorAll('.lang button').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
    });
  }

  function renderNav() {
    const box = document.getElementById('navLessons');
    const current = (location.hash.match(/^#\/lessons\/([^/?]+)/) || [])[1];
    box.replaceChildren();
    if (!state.lessons.length) {
      box.appendChild(el('div', { class: 'muted small nav-empty', text: t('nav.none') }));
      return;
    }
    for (const lesson of state.lessons.slice(0, 12)) {
      box.appendChild(el('a', {
        class: 'nav-lesson' + (lesson.id === current ? ' active' : ''),
        href: '#/lessons/' + lesson.id,
        title: lesson.title,
        'aria-current': lesson.id === current ? 'page' : null,
      },
      el('span', { class: 'nav-lesson-title', text: lesson.title }),
      el('span', { class: 'nav-lesson-meta', text: t('grade.n', { n: lesson.grade }) + ' · ' + lesson.unit_id })));
    }
  }

  function setCrumbs(parts) {
    crumbs.replaceChildren();
    parts.forEach((part, i) => {
      if (i) crumbs.appendChild(el('span', { class: 'crumb-sep', 'aria-hidden': 'true', text: '/' }));
      crumbs.appendChild(part.href
        ? el('a', { href: part.href, text: part.text })
        : el('span', { class: 'crumb-current', text: part.text }));
    });
  }

  function setStatus() {
    topStatus.replaceChildren();
    if (!state.config) return;
    const cfg = state.config;
    const mode = cfg.provider_mode === 'mock'
      ? el('span', { class: 'chip warn', title: t('mode.mock.hint'), text: t('mode.mock') })
      : null;
    topStatus.append(...[mode].filter(Boolean));
  }

  function closeOverlay() {
    overlay.replaceChildren();
    document.body.classList.remove('has-overlay');
  }

  function openOverlay(node) {
    overlay.replaceChildren(node);
    document.body.classList.add('has-overlay');
  }

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
      else await viewHome(token);
    } catch (e) {
      if (token !== routeToken) return;
      view.replaceChildren(errorState(e));
    }
    renderNav();
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
    setCrumbs([{ text: t('nav.lessons') }]);
    view.className = 'page';
    view.replaceChildren(el('div', { class: 'skeleton' }));
    await refreshLessons();
    if (token !== routeToken) return;
    const head = el('div', { class: 'page-head' },
      el('div', null, el('h1', { text: t('home.title') }), el('p', { class: 'muted', text: t('home.sub') })),
      el('a', { class: 'btn primary', href: '#/new' }, icon('plus'), t('nav.new')));
    if (!state.lessons.length) {
      view.replaceChildren(head, el('div', { class: 'empty card' },
        el('div', { class: 'empty-art', 'aria-hidden': 'true' }, icon('sparkle')),
        el('h2', { text: t('home.empty.title') }),
        el('p', { class: 'muted', text: t('home.empty.body') }),
        el('a', { class: 'btn primary', href: '#/new' }, icon('plus'), t('nav.new'))));
      return;
    }
    const list = el('div', { class: 'lesson-list' });
    for (const lesson of state.lessons) {
      list.appendChild(el('a', { class: 'lesson-card card hoverable', href: '#/lessons/' + lesson.id },
        el('div', { class: 'lc-main' },
          el('div', { class: 'lc-title', text: lesson.title }),
          el('div', { class: 'chips' },
            el('span', { class: 'chip', text: t('grade.n', { n: lesson.grade }) }),
            el('span', { class: 'chip', text: lesson.unit_id }),
            lesson.lesson_no ? el('span', { class: 'chip', text: t('lesson.lp', { l: lesson.lesson_no, p: lesson.period || 1 }) }) : null)),
        el('div', { class: 'lc-side' },
          lesson.failures ? el('span', { class: 'chip danger', text: t('lesson.failures', { n: lesson.failures }) })
            : lesson.warnings ? el('span', { class: 'chip warn', text: t('lesson.warnings', { n: lesson.warnings }) })
              : el('span', { class: 'chip ok', text: t('lesson.clean') }),
          el('span', { class: 'muted small', text: t('lesson.blocks', { n: lesson.blocks }) }),
          el('span', { class: 'muted small', text: fmtDate(lesson.updated_at) }))));
    }
    view.replaceChildren(head, list);
  }

  // ------------------------------------------------------------------ new lesson

  function radioGroup(name, options, value, onChange, cls) {
    const group = el('div', { class: 'seg ' + (cls || ''), role: 'radiogroup' });
    for (const opt of options) {
      const input = el('input', { type: 'radio', name, value: String(opt.value), checked: String(opt.value) === String(value), disabled: !!opt.disabled });
      input.addEventListener('change', () => onChange(opt.value));
      group.appendChild(el('label', { class: opt.disabled ? 'disabled' : '' }, input, el('span', { text: opt.label })));
    }
    return group;
  }

  function field(label, control, hint) {
    return el('div', { class: 'field' },
      el('div', { class: 'field-label', text: label }),
      control,
      hint ? el('div', { class: 'field-hint muted small', text: hint }) : null);
  }

  function viewNew() {
    const cfg = state.config;
    setCrumbs([{ text: t('nav.lessons'), href: '#/' }, { text: t('nav.new') }]);
    view.className = 'page';
    const form = {
      file: null,
      sample: null,
      grade: 3,
      unit: null,
      lesson: 1,
      period: 1,
      endpoint: state.endpoint,
      pictures: false,
      theme: state.theme,
    };
    const gradeInfo = (g) => cfg.grades.find((x) => x.grade === g);
    form.unit = gradeInfo(3) ? gradeInfo(3).units[0].id : null;

    // source card
    const fileInput = el('input', { type: 'file', accept: '.pdf,.docx,.pptx', class: 'visually-hidden', id: 'fileInput' });
    const dropName = el('div', { class: 'drop-name' });
    const drop = el('label', { class: 'dropzone', for: 'fileInput', tabindex: '0' },
      el('div', { class: 'drop-icon' }, icon('upload')),
      el('div', { class: 'drop-title', text: t('new.drop') }),
      el('div', { class: 'muted small', text: t('new.drop.hint', { mb: cfg.limits.upload_mb }) }),
      dropName);
    const sampleBox = el('div', { class: 'samples' });

    function setFile(file) {
      form.file = file;
      if (file) form.sample = null;
      dropName.replaceChildren(file ? el('span', { class: 'chip ok' }, icon('file'), file.name + ' · ' + (file.size / 1048576).toFixed(1) + ' MB') : '');
      drop.classList.toggle('has-file', !!file);
      renderSamples();
      update();
    }
    fileInput.addEventListener('change', () => setFile(fileInput.files[0] || null));
    drop.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fileInput.click(); } });
    ['dragenter', 'dragover'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (ev) => {
      const file = ev.dataTransfer && ev.dataTransfer.files[0];
      if (file) setFile(file);
    });

    function renderSamples() {
      sampleBox.replaceChildren();
      if (!cfg.samples.length) return;
      sampleBox.appendChild(el('div', { class: 'field-label', text: t('new.samples') }));
      const grid = el('div', { class: 'sample-grid' });
      for (const s of cfg.samples) {
        const on = form.sample === s.id;
        grid.appendChild(el('button', {
          type: 'button',
          class: 'sample' + (on ? ' on' : ''),
          'aria-pressed': String(on),
          on: {
            click: () => {
              form.sample = on ? null : s.id;
              if (form.sample) {
                form.file = null;
                fileInput.value = '';
                dropName.replaceChildren();
                drop.classList.remove('has-file');
                form.grade = s.grade;
                if (gradeInfo(s.grade) && gradeInfo(s.grade).units.some((u) => u.id === s.unit_id)) form.unit = s.unit_id;
                renderSettings();
              }
              renderSamples();
              update();
            },
          },
        },
        el('span', { class: 'sample-title', text: s.title }),
        el('span', { class: 'muted small', text: t('grade.n', { n: s.grade }) + ' · Unit ' + s.unit })));
      }
      sampleBox.appendChild(grid);
    }

    // settings card
    const settingsBox = el('div', { class: 'settings' });
    const priceNote = () => {
      const price = cfg.pictures.price_per_picture_usd;
      return price === null ? t('new.pictures.hint.unknown') : t('new.pictures.hint', { n: cfg.pictures.default_max, usd: fmtUsd(price * cfg.pictures.default_max) });
    };

    function renderSettings() {
      const units = (gradeInfo(form.grade) || { units: [] }).units;
      const unitSelect = el('select', { id: 'unitSelect' });
      for (const u of units) unitSelect.appendChild(el('option', { value: u.id, selected: u.id === form.unit, text: 'Unit ' + u.id.split('.u')[1] + ' — ' + u.title }));
      unitSelect.addEventListener('change', () => { form.unit = unitSelect.value; });

      const models = el('div', { class: 'model-list' });
      for (const ep of cfg.endpoints) {
        const input = el('input', { type: 'radio', name: 'endpoint', value: ep.name, checked: form.endpoint === ep.name, disabled: !ep.configured });
        input.addEventListener('change', () => { form.endpoint = ep.name; });
        models.appendChild(el('label', { class: 'model' + (ep.configured ? '' : ' disabled') },
          input,
          el('span', { class: 'model-body' },
            el('span', { class: 'model-name', text: ep.name === 'offline' ? t('ep.offline') : ep.label }),
            el('span', { class: 'muted small', text: ep.name === 'offline' ? t('ep.offline.hint') : (ep.configured ? ep.model : t('ep.missing')) }))));
      }

      const picInput = el('input', { type: 'checkbox', checked: form.pictures });
      picInput.addEventListener('change', () => { form.pictures = picInput.checked; });
      const themeSelect = el('select', { id: 'themeSelect' });
      for (const th of cfg.themes) themeSelect.appendChild(el('option', { value: th.id, selected: th.id === form.theme, text: th.label }));
      themeSelect.addEventListener('change', () => { form.theme = themeSelect.value; });

      settingsBox.replaceChildren(
        el('div', { class: 'row-2' },
          field(t('new.grade'), radioGroup('grade', cfg.grades.map((g) => ({ value: g.grade, label: String(g.grade) })), form.grade, (v) => {
            form.grade = Number(v);
            const list = (gradeInfo(form.grade) || { units: [] }).units;
            form.unit = list.length ? list[0].id : null;
            form.sample = null;
            renderSamples();
            renderSettings();
            update();
          })),
          field(t('new.lesson'), radioGroup('lesson', [1, 2, 3].map((n) => ({ value: n, label: String(n) })), form.lesson, (v) => { form.lesson = Number(v); })),
          field(t('new.period'), radioGroup('period', [1, 2].map((n) => ({ value: n, label: String(n) })), form.period, (v) => { form.period = Number(v); }))),
        field(t('new.unit'), unitSelect),
        field(t('new.model'), models, t('new.model.hint')),
        el('label', { class: 'check-row' }, picInput, el('span', null, el('span', { class: 'check-title', text: t('new.pictures') }), el('span', { class: 'muted small block', text: priceNote() }))),
        field(t('new.theme'), themeSelect));
    }

    const create = el('button', { class: 'btn primary lg', type: 'button', disabled: true }, icon('sparkle'), t('new.create'));
    function update() { create.disabled = !(form.file || form.sample) || !form.unit; }
    create.addEventListener('click', async () => {
      const data = new FormData();
      if (form.file) data.append('file', form.file);
      else data.append('sample', form.sample);
      data.append('grade', form.grade);
      data.append('unit', form.unit);
      data.append('lesson', form.lesson);
      data.append('period', form.period);
      data.append('endpoint', form.endpoint || 'offline');
      data.append('pictures', form.pictures ? 'true' : 'false');
      data.append('max_pictures', cfg.pictures.default_max);
      data.append('theme', form.theme);
      busy(create, true);
      try {
        const res = await api('/api/jobs', { method: 'POST', body: data });
        state.endpoint = form.endpoint; store('endpoint', form.endpoint);
        location.hash = '#/jobs/' + res.job_id;
      } catch (e) {
        fail(e);
        busy(create, false);
        update();
      }
    });

    renderSamples();
    renderSettings();
    view.replaceChildren(
      el('div', { class: 'page-head' }, el('div', null, el('h1', { text: t('new.title') }), el('p', { class: 'muted', text: t('new.sub') }))),
      el('div', { class: 'new-grid' },
        el('section', { class: 'card pad' }, el('h2', { class: 'card-title', text: t('new.source') }), fileInput, drop, sampleBox),
        el('section', { class: 'card pad' }, el('h2', { class: 'card-title', text: t('new.settings') }), settingsBox)),
      el('div', { class: 'actions-bar' }, create));
  }

  // ------------------------------------------------------------------ job

  function viewJob(id, token) {
    setCrumbs([{ text: t('nav.lessons'), href: '#/' }, { text: t('nav.new'), href: '#/new' }, { text: t('job.title') }]);
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
        footer.replaceChildren(el('div', { class: 'callout danger', text: job.error }), el('a', { class: 'btn', href: '#/new', text: t('job.back') }));
        return;
      }
      title.textContent = t('job.done');
      sub.textContent = '';
      await refreshLessons();
      if (job.warnings.length) {
        footer.replaceChildren(
          el('div', { class: 'callout warn' }, el('strong', { text: t('job.warnings') }), el('ul', null, job.warnings.map((w) => el('li', { text: w })))),
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
    setCrumbs([{ text: t('nav.lessons'), href: '#/' }, { text: lesson.title }]);

    // header
    const undoBtn = el('button', { class: 'btn', type: 'button', disabled: !summary.can_undo, on: { click: () => doUndoRedo('undo', undoBtn) } }, icon('undo'), t('act.undo'));
    const redoBtn = el('button', { class: 'btn', type: 'button', disabled: !summary.can_redo, on: { click: () => doUndoRedo('redo', redoBtn) } }, icon('redo'), t('act.redo'));
    const checkBtn = el('button', { class: 'btn', type: 'button', on: { click: () => doCheck(checkBtn) } }, icon('check'), t('act.check'));
    const picBtn = el('button', { class: 'btn', type: 'button', on: { click: () => doIllustrate(picBtn) } }, icon('picture'), t('act.pictures'));
    const menu = downloadMenu(lesson.id);

    const issues = summary.failures
      ? el('span', { class: 'chip danger', text: t('lesson.failures', { n: summary.failures }) })
      : summary.warnings
        ? el('span', { class: 'chip warn', text: t('lesson.warnings', { n: summary.warnings }) })
        : el('span', { class: 'chip ok', text: t('lesson.clean') });
    const head = el('div', { class: 'lesson-head' },
      el('div', { class: 'lh-title' },
        el('h1', { text: lesson.title }),
        el('div', { class: 'chips' },
          el('span', { class: 'chip', text: t('grade.n', { n: lesson.grade }) }),
          el('span', { class: 'chip', text: lesson.curriculum.unit_id }),
          lesson.curriculum.lesson_no ? el('span', { class: 'chip', text: t('lesson.lp', { l: lesson.curriculum.lesson_no, p: lesson.curriculum.period || 1 }) }) : null,
          issues,
          el('span', { class: 'muted small', text: t('lesson.updated', { d: fmtDate(lesson.updated_at) }) }))),
      el('div', { class: 'lh-actions' }, undoBtn, redoBtn, el('span', { class: 'vsep' }), checkBtn, picBtn, menu));

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

  function downloadMenu(lessonId) {
    const item = (fmt, label) => el('button', {
      type: 'button',
      role: 'menuitem',
      on: {
        click: (ev) => {
          ev.currentTarget.closest('details').open = false;
          const q = fmt === 'html' ? '?theme=' + encodeURIComponent(state.theme) : '';
          download('/api/lessons/' + lessonId + '/export/' + fmt + q, lessonId + '.' + fmt);
        },
      },
    }, el('span', { class: 'menu-name', text: label }), el('span', { class: 'muted small', text: t('dl.' + fmt) }));
    const details = el('details', { class: 'menu' },
      el('summary', { class: 'btn primary' }, icon('download'), t('act.download'), icon('chevron', 'chev')),
      el('div', { class: 'menu-pop', role: 'menu' }, item('html', 'HTML'), item('pptx', 'PPTX'), item('docx', 'DOCX')));
    document.addEventListener('click', (ev) => { if (!details.contains(ev.target)) details.open = false; });
    return details;
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
    wantedSlide = slideIndex();
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
    syncDeck();
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
        el('figcaption', null, el('span', { class: 'chip', text: t('insp.page', { n: c.page }) }), el('span', { class: 'muted small', text: c.filename || c.doc_id })),
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
          c.detail ? el('span', { class: 'muted small', text: c.detail }) : null)));
    });
    box.appendChild(list);
    return box;
  }

  function editBox(blocks) {
    const cfg = state.config;
    const kinds = new Set(blocks.map((b) => b.kind));
    const presets = cfg.presets.filter((p) => Array.from(kinds).every((k) => p.applies_to.includes(k)));
    const models = cfg.endpoints.filter((e) => e.configured && e.name !== 'offline');
    const area = el('textarea', { rows: 3, placeholder: t('edit.placeholder'), 'aria-label': t('edit.instruction') });
    const numberRow = el('div', { class: 'number-row', hidden: true });
    const proposeBtn = el('button', { class: 'btn primary', type: 'button', disabled: !models.length }, icon('sparkle'), t('edit.propose'));
    const select = el('select', { 'aria-label': t('new.model') });
    models.forEach((m) => select.appendChild(el('option', { value: m.name, selected: m.name === state.endpoint, text: m.label })));
    select.addEventListener('change', () => { state.endpoint = select.value; store('endpoint', select.value); });

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
      models.length ? null : el('p', { class: 'callout warn small', text: t('edit.nomodel') }),
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
          el('span', { class: 'muted small mono', text: change.block_id }),
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
            el('span', { class: 'aff-body' }, el('strong', { text: row ? row.label : a.block_id }), el('span', { class: 'muted small', text: a.reason })));
        })))
      : null;

    const checks = proposal.checks_preview.filter((c) => c.status !== 'pass');
    const passes = proposal.checks_preview.length - checks.length;
    const checkBox = el('section', { class: 'sect' }, el('h3', { text: t('prop.checks') }),
      checks.length
        ? el('ul', { class: 'checks' }, checks.map((c) => el('li', { class: 'check ' + c.status },
          statusDot(c.status),
          el('span', { class: 'check-body' },
            el('span', { class: 'check-name', text: tx('check', c.name) + (c.block_id ? ' · ' + c.block_id : '') }),
            c.detail ? el('span', { class: 'muted small', text: c.detail }) : null))))
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
