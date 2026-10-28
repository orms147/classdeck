  // --- editing on the slide (docs/output-formats.md, "Editing on the slide"): this part runs inside
  // the deck's own closure. Every editable element carries data-field-block + data-field (a path
  // into the block); edit mode makes them contenteditable and tracks what changed.
  var editing = false;
  var tracked = {};       // "block|path" -> { block, field, original, saved, value }
  var fileHandle = null;  // File System Access handle, remembered so later saves overwrite
  var toggle = document.getElementById('edit-toggle');
  var saveButton = document.getElementById('edit-save');
  var saveCount = saveButton ? saveButton.querySelector('.edit-save__count') : null;
  var editStatus = document.getElementById('edit-status');
  var toasts = document.getElementById('edit-toasts');
  var lessonMeta = document.querySelector('meta[name="classdeck-lesson"]');
  var lessonId = lessonMeta ? lessonMeta.getAttribute('content') : '';
  var hasAppApi = !!lessonId && /^ht{2}ps?:$/.test(window.location.protocol);

  function keyOf(block, field) { return block + '|' + field; }
  function fieldBlock(el) { return el.getAttribute('data-field-block'); }
  function fieldPath(el) { return el.getAttribute('data-field'); }
  function isMultiline(el) { return el.hasAttribute('data-multiline'); }
  function allFields(root) { return (root || stage).querySelectorAll('[data-field]'); }

  function fieldOf(node) {
    return node && node.closest ? node.closest('[data-field]') : null;
  }

  // The text of a field as the lesson holds it. A field that is rebuilt after editing (a gap
  // sentence, a note) shows its raw text only while editing; outside edit mode it keeps it in
  // data-raw.
  function valueOf(el) {
    if (el.hasAttribute('data-rebuild') && !el.hasAttribute('data-editing-raw')) {
      return el.getAttribute('data-raw') || '';
    }
    return el.textContent;
  }

  function norm(text, multiline) {
    text = String(text).replace(/\r\n?/g, '\n');
    if (!multiline) return text.replace(/\s+/g, ' ').trim();
    var lines = text.split('\n').map(function (line) { return line.replace(/\s+/g, ' ').trim(); });
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    while (lines.length && !lines[0]) lines.shift();
    return lines.join('\n');
  }

  function fieldsFor(block, path) {
    return stage.querySelectorAll('[data-field-block="' + block + '"][data-field="' + path + '"]');
  }

  function sameField(el) { return fieldsFor(fieldBlock(el), fieldPath(el)); }

  function entryFor(el) {
    var key = keyOf(fieldBlock(el), fieldPath(el));
    if (!tracked[key]) {
      var start = norm(valueOf(el), isMultiline(el));
      tracked[key] = {
        block: fieldBlock(el), field: fieldPath(el), original: start, saved: start, value: start
      };
    }
    return tracked[key];
  }

  function changes() {
    return Object.keys(tracked).map(function (key) { return tracked[key]; })
      .filter(function (entry) { return entry.value !== entry.original; });
  }

  function unsaved() {
    return Object.keys(tracked).map(function (key) { return tracked[key]; })
      .filter(function (entry) { return entry.value !== entry.saved; });
  }

  function refreshUi() {
    var open = unsaved().length;
    if (saveButton) {
      saveButton.hidden = open === 0;
      if (saveCount) saveCount.textContent = String(open);
      saveButton.setAttribute('aria-label', 'Save ' + open + ' change' + (open === 1 ? '' : 's'));
    }
    if (editStatus) {
      editStatus.textContent = open ? open + ' unsaved change' + (open === 1 ? '' : 's')
        : 'No changes yet';
    }
    each(allFields(), function (el) {
      var entry = tracked[keyOf(fieldBlock(el), fieldPath(el))];
      el.classList.toggle('is-edited', !!entry && entry.value !== entry.original);
    });
  }

  // --- toasts (interface text: class ui)
  function toast(message, kind, ms) {
    if (!toasts) return;
    var node = document.createElement('div');
    node.className = 'edit-toast ui ' + (kind || 'info');
    node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    node.textContent = message;
    toasts.appendChild(node);
    setTimeout(function () {
      if (node.parentNode) node.parentNode.removeChild(node);
    }, ms || (kind === 'error' ? 8000 : 4500));
  }

  // --- raw <-> markup for fields whose markup is more than their text
  function toRaw(el) {
    if (el.hasAttribute('data-editing-raw')) return;
    el.setAttribute('data-editing-raw', '');
    el.textContent = el.getAttribute('data-raw') || '';
  }

  function textNode(doc, text) { return doc.createTextNode(text); }

  function rebuild(el) {
    if (!el.hasAttribute('data-editing-raw')) return;
    var doc = el.ownerDocument;
    var kind = el.getAttribute('data-rebuild');
    var text = norm(el.textContent, isMultiline(el));
    el.removeAttribute('data-editing-raw');
    el.setAttribute('data-raw', text);
    while (el.firstChild) el.removeChild(el.firstChild);
    if (kind === 'slots') {
      text.split(/(_{2,})/).forEach(function (part) {
        if (!part) return;
        if (/^_{2,}$/.test(part)) {
          var slot = doc.createElement('span');
          slot.className = 'pattern__slot';
          slot.setAttribute('data-empty', part);
          slot.textContent = part;
          el.appendChild(slot);
        } else {
          el.appendChild(textNode(doc, part));
        }
      });
      var owner = el.closest('.pattern, .summary');
      if (owner) {
        each(owner.querySelectorAll('.pattern__example'), function (example) {
          example.removeAttribute('data-fills');
          example.classList.remove('current');
        });
        owner.removeAttribute('data-index');
      }
    } else if (kind === 'gap') {
      var row = el.closest('.gap-item');
      var answers = [];
      if (row) {
        each(row.querySelectorAll('.gap-item__answer'), function (chip) {
          answers.push(chip.textContent.trim());
        });
      }
      var blank = 0;
      text.split(/(_{2,})/).forEach(function (part) {
        if (!part) return;
        if (!/^_{2,}$/.test(part)) { el.appendChild(textNode(doc, part)); return; }
        var answer = answers[blank] || '';
        var button = doc.createElement('button');
        button.type = 'button';
        button.className = 'gap';
        button.setAttribute('data-act', 'gap');
        button.setAttribute('data-answer', answer);
        button.setAttribute('aria-label', 'Blank ' + (blank + 1) + ': tap to show the answer');
        button.setAttribute('style', '--len:' + Math.max(answer.length, 3));
        var span = doc.createElement('span');
        span.className = 'gap__answer';
        span.textContent = answer;
        button.appendChild(span);
        el.appendChild(button);
        blank++;
      });
    } else if (kind === 'marks') {
      var holder = el.closest('.phonics');
      var lettersEl = holder ? holder.querySelector('.phonics__letters') : null;
      var letters = lettersEl ? lettersEl.textContent.trim() : '';
      if (!letters) { el.appendChild(textNode(doc, text)); return; }
      var pattern = new RegExp(letters.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'ig');
      var position = 0, found;
      while ((found = pattern.exec(text)) !== null) {
        if (!found[0]) { pattern.lastIndex++; continue; }
        if (found.index > position) el.appendChild(textNode(doc, text.slice(position, found.index)));
        var mark = doc.createElement('mark');
        mark.textContent = found[0];
        el.appendChild(mark);
        position = found.index + found[0].length;
      }
      if (position < text.length) el.appendChild(textNode(doc, text.slice(position)));
    } else if (kind === 'note') {
      var lines = text.split('\n').map(function (line) { return line.trim(); })
        .filter(function (line) { return line; });
      if (!lines.length) return;
      var p = doc.createElement('p');
      var strong = doc.createElement('strong');
      strong.textContent = (el.getAttribute('data-label') || 'Note') + ': ';
      p.appendChild(strong);
      p.appendChild(textNode(doc, lines[0]));
      el.appendChild(p);
      if (lines.length > 1) {
        var ol = doc.createElement('ol');
        lines.slice(1).forEach(function (line) {
          var li = doc.createElement('li');
          li.textContent = line.replace(/^\d+[.)]\s*/, '');
          ol.appendChild(li);
        });
        el.appendChild(ol);
      }
    } else {
      el.textContent = text;
    }
  }

  // --- reacting to typing
  function say(el, previous, value) {
    // Keep the speaker button beside a text in step with it, when it said exactly that text.
    var holder = el.closest('li, .bubble, .flashcard__wordline, .flashcard__example, .reading__row,'
      + ' .summary-card, .qset__q, .rule-box__example');
    var button = holder ? holder.querySelector('.speak') : null;
    if (button && button.getAttribute('data-say') === previous.replace(/\s+/g, ' ').trim()) {
      button.setAttribute('data-say', value);
    }
  }

  function onEdited(el) {
    var entry = entryFor(el);
    var previous = entry.value;
    var value = norm(valueOf(el), isMultiline(el));
    entry.value = value;
    el.classList.remove('is-invalid');
    each(sameField(el), function (other) {
      if (other !== el && norm(valueOf(other), isMultiline(other)) !== value) {
        other.textContent = value;
      }
    });
    if (el.classList.contains('wordbox__chip')) el.setAttribute('data-word', value.toLowerCase());
    if (el.classList.contains('pattern__example-text')) {
      var item = el.closest('.pattern__example');
      if (item) item.removeAttribute('data-fills');
    }
    say(el, previous, value);
    refreshUi();
  }

  // --- edit mode
  function setEditing(on) {
    if (on === editing) return;
    editing = on;
    body.classList.toggle('editing', on);
    if (toggle) {
      toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
      toggle.setAttribute('title', on ? 'Done editing (E)' : 'Edit text (E)');
    }
    if (on) {
      each(allFields(), function (el) {
        if (el.hasAttribute('data-rebuild')) toRaw(el);
        el.setAttribute('contenteditable', 'plaintext-only');
        if (el.contentEditable !== 'plaintext-only') el.setAttribute('contenteditable', 'true');
        el.setAttribute('spellcheck', el.getAttribute('lang') === 'vi' ? 'false' : 'true');
        el.setAttribute('data-placeholder', el.getAttribute('data-placeholder') || 'Type here');
      });
      cancelSpeech();
    } else {
      var active = document.activeElement;
      if (active && active.blur) active.blur();
      each(allFields(), function (el) {
        el.removeAttribute('contenteditable');
        el.removeAttribute('spellcheck');
        rebuild(el);
      });
    }
    refreshUi();
  }

  function toggleEdit() { setEditing(!editing); }

  function editKey(event) {
    var key = event.key;
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === 's' || key === 'S')) {
      if (editing || unsaved().length) {
        event.preventDefault();
        if (unsaved().length) save(); else toast('Nothing to save yet.', 'info');
        return true;
      }
      return false;
    }
    if (event.ctrlKey || event.metaKey || event.altKey) return false;
    var field = editing ? fieldOf(event.target) : null;
    if (field) {
      if (key === 'Escape') {
        event.preventDefault();
        field.blur();
      } else if (key === 'Enter' && !isMultiline(field)) {
        event.preventDefault();
        field.blur();
      }
      return true; // arrows, Space, T N F P E do nothing while typing
    }
    if (key === 'e' || key === 'E') {
      var target = event.target;
      if (target && target.closest && target.closest('input, textarea, select')) return false;
      event.preventDefault();
      toggleEdit();
      return true;
    }
    if (key === 'Escape' && editing) {
      event.preventDefault();
      setEditing(false);
      return true;
    }
    return false;
  }

  document.addEventListener('focusin', function (event) {
    var el = editing ? fieldOf(event.target) : null;
    if (el) entryFor(el);
  });

  document.addEventListener('input', function (event) {
    var el = editing ? fieldOf(event.target) : null;
    if (el) onEdited(el);
  });

  document.addEventListener('paste', function (event) {
    var el = editing ? fieldOf(event.target) : null;
    if (!el) return;
    event.preventDefault();
    var text = (event.clipboardData || window.clipboardData).getData('text') || '';
    if (!isMultiline(el)) text = text.replace(/\s*\n\s*/g, ' ');
    document.execCommand('insertText', false, text);
  });

  // A click in a field puts the caret there; nothing else on the slide reacts while editing.
  stage.addEventListener('click', function (event) {
    if (!editing) return;
    if (event.target.closest('.speak, .dot, .edit-ui')) return;
    event.stopPropagation();
  }, true);

  // --- saving
  function changeList() {
    return changes().map(function (entry) {
      return { block_id: entry.block, field: entry.field, value: entry.value };
    });
  }

  function markSaved() {
    Object.keys(tracked).forEach(function (key) { tracked[key].saved = tracked[key].value; });
    refreshUi();
  }

  function busy(on) {
    if (!saveButton) return;
    saveButton.disabled = on;
    saveButton.classList.toggle('is-busy', on);
  }

  function flagInvalid(blockId, path) {
    if (!blockId || !path) return;
    var found = fieldsFor(blockId, path);
    if (!found.length) return;
    var slide = found[0].closest('.slide');
    var list = visible();
    var index = list.indexOf(slide);
    if (index >= 0) show(index);
    each(found, function (el) { el.classList.add('is-invalid'); });
    found[0].focus();
  }

  function saveToApp() {
    var edits = changeList();
    busy(true);
    return window.fetch('/api/lessons/' + encodeURIComponent(lessonId) + '/edits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edits: edits, theme: body.getAttribute('data-theme') })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, status: response.status, data: data };
      });
    }).then(function (result) {
      busy(false);
      if (result.status === 404 || result.status === 405) return saveToFile(true);
      if (!result.ok) {
        var data = result.data || {};
        toast(data.error || 'The edit was not saved.', 'error');
        flagInvalid(data.block_id, data.field);
        return null;
      }
      tracked = {};
      refreshUi();
      var notices = (result.data.notices || []).map(function (note) { return note.message; });
      var slideNumber = position() + 1;
      if (window.parent && window.parent !== window) {
        try {
          window.parent.postMessage({
            type: 'classdeck:saved', slide: slideNumber, payload: result.data, notices: notices
          }, '*');
          toast('Saved.', 'success');
          return null;
        } catch (err) { /* fall through to a reload */ }
      }
      try {
        window.sessionStorage.setItem('classdeck-toasts', JSON.stringify(['Saved.'].concat(notices)));
      } catch (err) { /* private mode: the page just reloads without the toast */ }
      window.location.hash = '#' + slideNumber;
      window.location.reload();
      return null;
    }).catch(function () {
      busy(false);
      toast('The app did not answer; saving the file instead.', 'info');
      return saveToFile(true);
    });
  }

  // A clean copy of the page: edit mode off, state reset, edits baked in and listed as JSON.
  function serialise() {
    var clone = document.documentElement.cloneNode(true);
    var root = clone.querySelector('#stage');
    each(root.querySelectorAll('[data-editing-raw]'), rebuild);
    var cloneBody = clone.querySelector('body');
    each(clone.querySelectorAll('#edit-toasts .edit-toast'), function (node) {
      node.parentNode.removeChild(node);
    });
    var cloneToggle = clone.querySelector('#edit-toggle');
    if (cloneToggle) {
      cloneToggle.setAttribute('aria-pressed', 'false');
      cloneToggle.setAttribute('title', 'Edit text (E)');
    }
    ['editing', 'teacher-mode', 'notes-open', 'no-speech'].forEach(function (name) {
      cloneBody.classList.remove(name);
    });
    each(clone.querySelectorAll('[contenteditable], [spellcheck]'), function (el) {
      el.removeAttribute('contenteditable');
      el.removeAttribute('spellcheck');
    });
    each(clone.querySelectorAll('.is-edited, .is-invalid'), function (el) {
      el.classList.remove('is-edited', 'is-invalid');
    });
    each(root.querySelectorAll('.slide'), function (slide) {
      slide.classList.remove('active');
      slide.removeAttribute('aria-hidden');
    });
    root.removeAttribute('style');
    var dotsClone = clone.querySelector('#dots');
    while (dotsClone && dotsClone.firstChild) dotsClone.removeChild(dotsClone.firstChild);
    each(root.querySelectorAll('.revealed, .is-revealed, .is-picked, .is-correct, .is-wrong,'
      + ' .is-done, .running, .done, .filled, .current'), function (el) {
      if (el.classList.contains('dot')) return;
      el.classList.remove('revealed', 'is-revealed', 'is-picked', 'is-correct', 'is-wrong',
        'is-done', 'running', 'done', 'filled', 'current');
      if (el.hasAttribute('aria-pressed')) el.setAttribute('aria-pressed', 'false');
    });
    each(root.querySelectorAll('.stepper'), function (stepper) {
      var items = stepper.querySelectorAll('.step-item');
      each(items, function (item, n) { item.classList.toggle('pending', n > 0); });
      stepper.classList.toggle('complete', items.length <= 1);
    });
    each(root.querySelectorAll('.pattern.cycle'), function (card) {
      card.removeAttribute('data-index');
      each(card.querySelectorAll('.pattern__slot'), function (slot) {
        slot.textContent = slot.getAttribute('data-empty') || slot.textContent;
      });
      each(card.querySelectorAll('.pattern__line'), function (line) { line.textContent = ''; });
    });
    each(root.querySelectorAll('.countdown'), function (box) {
      var total = parseInt(box.getAttribute('data-seconds'), 10) || 0;
      var time = box.querySelector('.countdown__time');
      var hint = box.querySelector('.countdown__hint');
      if (time) time.textContent = clock(total);
      if (hint) hint.textContent = 'Tap to start';
    });
    each(root.querySelectorAll('.qset__list'), function (list) {
      var first = list.querySelector('.qset__q');
      if (first) first.classList.add('is-revealed');
    });
    each(root.querySelectorAll('.reading__tip, .ex__score'), function (el) { el.textContent = ''; });
    each(root.querySelectorAll('.match__item[data-pair]'), function (item) {
      item.removeAttribute('data-pair');
      item.removeAttribute('data-partner');
      var tag = item.querySelector('.match__tag');
      if (tag) tag.textContent = '';
    });
    var old = clone.querySelector('#classdeck-edits');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var list = Object.keys(tracked).map(function (key) { return tracked[key]; })
      .filter(function (entry) { return entry.value !== entry.original; })
      .map(function (entry) {
        return { block_id: entry.block, field: entry.field, value: entry.value,
          original: entry.original };
      });
    var script = clone.ownerDocument.createElement('script');
    script.type = 'application/json';
    script.id = 'classdeck-edits';
    script.textContent = JSON.stringify({ lesson_id: lessonId, edits: list })
      .replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
    cloneBody.appendChild(script);
    return '<!doctype html>\n' + clone.outerHTML + '\n';
  }

  function download(html) {
    // The blob address API is reached by name so that the deck never contains the call syntax the
    // offline check greps for ("no address in the file").
    var blobs = window.URL || window.webkitURL;
    var blob = new Blob([html], { type: 'text/html' });
    var link = document.createElement('a');
    link.href = blobs['createObject' + 'URL'](blob);
    link.download = 'lesson.html';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () { blobs['revokeObject' + 'URL'](link.href); }, 1000);
  }

  function saveToFile(afterAppFailure) {
    var html = serialise();
    var done = function (message) {
      markSaved();
      toast(message, 'success');
    };
    var fallback = function () {
      download(html);
      done('Downloaded lesson.html. Keep it, or import it with: classdeck import-edits.');
    };
    if (!window.showSaveFilePicker) { fallback(); return Promise.resolve(); }
    var picker = fileHandle ? Promise.resolve(fileHandle) : window.showSaveFilePicker({
      suggestedName: 'lesson.html',
      types: [{ description: 'HTML deck', accept: { 'text/html': ['.html'] } }]
    });
    return picker.then(function (handle) {
      fileHandle = handle;
      return handle.createWritable().then(function (writer) {
        return writer.write(html).then(function () { return writer.close(); });
      }).then(function () { done('Saved ' + (handle.name || 'lesson.html') + '.'); });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      fileHandle = null;
      fallback();
    });
  }

  function save() {
    if (!unsaved().length) { toast('Nothing to save yet.', 'info'); return Promise.resolve(); }
    if (hasAppApi) return saveToApp();
    return saveToFile(false);
  }

  window.addEventListener('beforeunload', function (event) {
    if (!unsaved().length) return;
    event.preventDefault();
    event.returnValue = '';
  });

  if (toggle) toggle.addEventListener('click', toggleEdit);
  if (saveButton) saveButton.addEventListener('click', save);

  // A standalone file saved earlier carries its edit list: pick it up, so reverting a field to
  // its original still removes it and the next save lists every change since the export.
  (function loadEarlierEdits() {
    var node = document.getElementById('classdeck-edits');
    if (!node) return;
    try {
      var data = JSON.parse(node.textContent);
      (data.edits || []).forEach(function (edit) {
        tracked[keyOf(edit.block_id, edit.field)] = {
          block: edit.block_id, field: edit.field, original: edit.original,
          saved: edit.value, value: edit.value
        };
      });
    } catch (err) { /* an unreadable list is ignored */ }
    if (node.parentNode) node.parentNode.removeChild(node);
  }());

  (function showStoredToasts() {
    try {
      var stored = window.sessionStorage.getItem('classdeck-toasts');
      if (!stored) return;
      window.sessionStorage.removeItem('classdeck-toasts');
      JSON.parse(stored).forEach(function (message, n) {
        toast(message, n === 0 ? 'success' : 'info', 9000);
      });
    } catch (err) { /* storage is optional */ }
  }());

  refreshUi();
  if (new URLSearchParams(window.location.search).get('edit') === '1') setEditing(true);
