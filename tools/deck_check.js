// The page side of tools/deck_check.py: window.__deckCheck is driven step by step from Python.
(function () {
  const css = document.createElement('style');
  css.textContent = '*,*::before,*::after{animation:none!important;transition:none!important}';
  document.head.appendChild(css);

  const body = document.body;
  const TOL = 4;
  const key = (name) => document.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
  const label = (el) => {
    let s = el.tagName.toLowerCase();
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) s += '.' + cls.join('.');
    return s;
  };
  const snippet = (el) => (el.textContent || el.getAttribute('alt') || '').replace(/\s+/g, ' ').trim().slice(0, 40);

  function setState(state) {
    const toggle = document.getElementById('edit-toggle');
    if (body.classList.contains('editing')) toggle.click();
    if (body.classList.contains('teacher-mode')) key('t');
    if (state === 'teacher' || state === 'notes') {
      key('t');
      const open = body.classList.contains('notes-open');
      if ((state === 'teacher') === open) key('n');
    } else if (state === 'edit') {
      toggle.click();
    }
  }

  function slideCount() { return document.querySelectorAll('#dots .dot').length; }
  function currentSlide() { return document.querySelector('.slide.active'); }

  function revealAll(slide) {
    let clicks = 0;
    const click = (el) => { clicks++; el.click(); };
    slide.querySelectorAll('[data-act="show"]').forEach(click);
    slide.querySelectorAll('[data-act="check"]').forEach(click);
    slide.querySelectorAll('.tap:not(.countdown)').forEach((el) => {
      if (el.classList.contains('stepper')) {
        for (let n = 0; n < 40 && !el.classList.contains('complete'); n++) click(el);
      } else if (el.classList.contains('cycle')) {
        const count = el.querySelectorAll('.pattern__example').length;
        for (let n = 0; n < count; n++) click(el);
      } else if (!el.classList.contains('revealed')) {
        click(el);
      }
    });
    return clicks;
  }

  function visibleBox(el) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.opacity === '0') return false;
    return true;
  }

  function check(slide) {
    const bodyEl = slide.querySelector('.slide-body');
    const frame = slide.getBoundingClientRect();
    const k = frame.width / 1920;
    const box = (r) => ({ l: (r.left - frame.left) / k, t: (r.top - frame.top) / k,
      r: (r.right - frame.left) / k, b: (r.bottom - frame.top) / k });
    const b = box(bodyEl.getBoundingClientRect());
    const problems = [];
    const add = (kind, el, detail) => {
      if (problems.length < 8) problems.push({ kind, el: label(el), text: snippet(el), detail });
    };
    const chrome = [];
    const addChrome = (el) => {
      if (!el || !el.getClientRects().length) return;
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') return;
      chrome.push({ name: label(el), r: box(el.getBoundingClientRect()) });
    };
    ['#edit-toggle', '#edit-bar', '#edit-save'].forEach((s) => addChrome(document.querySelector(s)));
    document.querySelectorAll('#dots .dot').forEach(addChrome);
    slide.querySelectorAll('.footer, .badge, .notes').forEach(addChrome);

    const stack = Array.from(bodyEl.children).map((el) => ({ el, loose: false }));
    const hasText = (el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim());
    while (stack.length) {
      const item = stack.pop();
      const el = item.el;
      if (el.classList.contains('sr-only') || el.classList.contains('deco')) continue;
      if (!visibleBox(el)) continue;
      const rects = el.getClientRects();
      if (rects.length) {
        const r = box(el.getBoundingClientRect());
        if (r.r - r.l > 0 && r.b - r.t > 0) {
          const out = [];
          // a decoration placed on purpose (a watch in the corner) may bleed out of the body, but
          // not out of the stage; everything else must stay inside the body
          const floating = item.loose || ['absolute', 'fixed'].includes(getComputedStyle(el).position);
          item.loose = floating;
          const home = floating ? { l: 0, t: 0, r: 1920, b: 1080 } : b;
          // a tilted sticker or icon may poke out a little: its rotated box is not its layout
          const slack = getComputedStyle(el).transform !== 'none' ? 12 : TOL;
          if (r.l < home.l - slack) out.push('left ' + Math.round(home.l - r.l));
          if (r.r > home.r + slack) out.push('right ' + Math.round(r.r - home.r));
          if (r.t < home.t - slack) out.push('top ' + Math.round(home.t - r.t));
          if (r.b > home.b + slack) out.push('bottom ' + Math.round(r.b - home.b));
          if (out.length) add('outside', el, out.join(', '));
          if (el.clientWidth > 0 && el.clientHeight > 0) {
            // text that spills sideways out of its box, or a box that hides part of its content
            const style = getComputedStyle(el);
            const dw = el.scrollWidth - el.clientWidth, dh = el.scrollHeight - el.clientHeight;
            const hides = style.overflowX !== 'visible' || style.overflowY !== 'visible';
            if (hides && (dw > TOL || dh > TOL)) {
              add('clipped', el, 'scroll-client ' + dw + 'x' + dh);
            } else if (hasText(el)) {
              const range = document.createRange();
              range.selectNodeContents(el);
              const t = box(range.getBoundingClientRect());
              // a trailing space may hang out of a pre-wrapped field (edit mode): allow two of them
              if (t.r > r.r + 24 || t.l < r.l - 24) add('clipped', el, 'text spills sideways');
            }
          }
          if (hasText(el) || el.tagName === 'IMG' || el.tagName === 'BUTTON') {
            for (const c of chrome) {
              if (r.l < c.r.r - TOL && r.r > c.r.l + TOL && r.t < c.r.b - TOL && r.b > c.r.t + TOL) {
                add('chrome', el, 'under ' + c.name);
                break;
              }
            }
          }
        }
      }
      for (const child of el.children) stack.push({ el: child, loose: item.loose });
    }
    // the body itself must sit inside the stage
    if (b.l < 0 - TOL || b.t < 0 - TOL || b.r > 1920 + TOL || b.b > 1080 + TOL) {
      add('outside', bodyEl, 'the slide body leaves the stage');
    }
    return problems;
  }

  window.__deckCheck = {
    setState: setState,
    count: slideCount,
    goto: function (i) {
      const dots = document.querySelectorAll('#dots .dot');
      if (dots[i]) dots[i].click();
    },
    describe: function () {
      const slide = currentSlide();
      if (!slide) return null;
      const heading = slide.querySelector('.slide-title, .ex__instruction, .flashcard__word, h1, h2');
      return {
        kind: slide.getAttribute('data-kind') || '',
        title: heading ? snippet(heading) : '',
        exempt: slide.classList.contains('teacher'),
      };
    },
    reveal: function () { return revealAll(currentSlide()); },
    measure: function () {
      const slide = currentSlide();
      const fit = parseFloat(slide.style.getPropertyValue('--fit') || '1') || 1;
      return { fit: fit, problems: slide.classList.contains('teacher') ? [] : check(slide) };
    },
  };
  return true;
})();
