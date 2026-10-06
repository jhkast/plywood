// Plywood UI. All parsing and optimizing happens in Python; this file only manages the tables.

async function api(method, ...args) {
  const res = await fetch('/api/' + method, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return res.json();
}

// Table columns in order: pasted and copied rows use these.
const FIELDS = {
  parts: ['name', 'length', 'width', 'thickness', 'qty', 'grain', 'kind', 'tag'],
  stock: ['kind', 'length', 'width', 'thickness', 'qty', 'trim_edges', 'rough', 'tag'],
};

// Dimensional lumber: nominal size -> actual thickness and width in inches (as in core/matching.py).
const ACTUAL = { 1: 0.75, 2: 1.5, 3: 2.5, 4: 3.5, 6: 5.5, 8: 7.25, 10: 9.25, 12: 11.25 };
const DIMENSIONAL = Object.fromEntries(
  [[1, [2, 3, 4, 6, 8, 10, 12]], [2, [2, 3, 4, 6, 8, 10, 12]], [4, [4, 6]], [6, [6]]]
    .flatMap(([t, ws]) => ws.map((w) => [`${t}x${w}`, [ACTUAL[t], ACTUAL[w]]])),
);

function newRow(table) {
  if (table === 'parts') return { name: '', length: '', width: '', thickness: '', qty: '', grain: '', kind: 'sheet', tag: '' };
  return { kind: 'sheet', length: '', width: '', thickness: '', qty: '', trim_edges: null, rough: false, tag: '' };
}

// Kind as stored: older files said "board", which is hardwood now.
function kindOf(row) {
  return row.kind === 'board' ? { ...row, kind: 'hardwood' } : row;
}

function normTag(tag) {
  return String(tag || '').trim().toLowerCase();
}

function pairKey(a, b) {
  return [a, b].sort().join(' | ');
}

// Possibly the same material: close spelling, or every word of one appears in the other
// ("birch" / "baltic birch"). Only ever a question for the user, never applied automatically.
function similarTags(a, b) {
  const d = tagDistance(a, b);
  if (d <= (Math.min(a.length, b.length) >= 6 ? 2 : 1)) return true;
  const wa = a.split(/[^a-z0-9]+/).filter(Boolean);
  const wb = b.split(/[^a-z0-9]+/).filter(Boolean);
  const [short, long] = wa.length <= wb.length ? [wa, wb] : [wb, wa];
  return short.length > 0 && short.every((w) => long.some((x) => x === w || (w.length >= 5 && tagDistance(w, x) <= 1)));
}

// Edit distance, ignoring spaces and punctuation ("baltic-birch" == "baltic birch").
function tagDistance(a, b) {
  a = a.replace(/[^a-z0-9]/g, '');
  b = b.replace(/[^a-z0-9]/g, '');
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return d[a.length][b.length];
}

function isBlank(row) {
  return !['name', 'length', 'width', 'thickness', 'qty'].some((k) => String(row[k] ?? '').trim());
}

// Pasted spreadsheet values for select columns
function normalize(field, value) {
  const v = value.trim().toLowerCase();
  if (field === 'grain') return v.startsWith('l') ? 'length' : v.startsWith('w') ? 'width' : '';
  if (field === 'kind') return v.startsWith('d') ? 'dimensional' : /^(h|b|lum|wood|solid)/.test(v) ? 'hardwood' : 'sheet';
  if (field === 'rough') return /^(y|yes|true|x|1|rough)$/.test(v);
  if (field === 'trim_edges') return v === 'none' ? '' : 'lrbt'.split('').filter((e) => v.includes(e)).join('');
  return value.trim();
}

// A cell as copied to the clipboard (the inverse of normalize).
function cellText(row, field) {
  if (field === 'rough') return row.rough && row.kind === 'hardwood' ? 'yes' : '';
  return String(row[field] ?? '').replace(/[\t\r\n]+/g, ' ');
}

// Columns the CSV importers read (io/parts_csv.py), in table order.
const EXPORT_COLUMNS = {
  parts: ['name', 'length', 'width', 'thickness', 'qty', 'grain', 'kind', 'tag'],
  stock: ['kind', 'length', 'width', 'thickness', 'qty', 'trim', 'rough', 'tag'],
};

// 1.5 -> "1-1/2", in sixteenths.
function inchText(v) {
  const whole = Math.floor(v + 1e-9);
  let n = Math.round((v - whole) * 16), d = 16;
  if (!n) return String(whole);
  while (n % 2 === 0) { n /= 2; d /= 2; }
  return whole ? `${whole}-${n}/${d}` : `${n}/${d}`;
}

function csvCell(v) {
  v = String(v);
  return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

// Older files had a kerf per kind of cut, and before that one kerf for every saw
// (same mapping as migrate_settings in api.py).
const OLD_KERFS = { track_saw_kerf: 'sheet_kerf', table_saw_kerf: 'rip_kerf', miter_saw_kerf: 'crosscut_kerf', jig_saw_kerf: 'rough_crosscut_kerf' };
function migrateSettings(settings) {
  const s = { ...(settings || {}) };
  if (!Object.keys(OLD_KERFS).some((k) => k in s)) {
    if (Object.values(OLD_KERFS).some((k) => k in s)) {
      for (const [now, was] of Object.entries(OLD_KERFS)) if (was in s) s[now] = s[was];
      s.sheet_saw = 'track';
    } else if (s.kerf) {
      for (const k of Object.keys(OLD_KERFS)) s[k] = s.kerf;
    }
  }
  for (const k of ['kerf', ...Object.values(OLD_KERFS), 'board_width', 'board_length', 'default_boards', 'time_budget']) delete s[k];
  return s;
}

function plywood() {
  return {
    state: { job: '', units: 'in', denominator: 16, settings: {}, parts: [], stock: [] },
    tab: 'parts',
    result: null,
    plan: null, // the cut list being shown, saved with the state so it reopens unchanged
    errors: {},
    busy: false,
    switching: 0, // sheet number waiting on a layout switch
    flash: { text: '', kind: '' },
    seq: 0,
    keepNext: false,
    menu: null, // open top-row menu: 'import' | 'export'
    savedHash: null, // jobHash() when the job was last saved to or opened from a file
    unsaved: null,
    unsavedName: '',
    sel: { table: null, rows: [], anchor: 0 }, // rows picked by their row number
    dimensionalNames: Object.keys(DIMENSIONAL),
    dialog: null, // 'phone' | 'boards' | 'ask'
    question: { title: '', buttons: [] }, // for dialog 'ask'
    phone: { code: '', link: '', qr: '' },
    boardsText: '',
    timers: {},

    async init() {
      try {
        this.tab = localStorage.getItem('plywood.tab') || 'parts';
      } catch {}
      this.$watch('tab', (t) => {
        try { localStorage.setItem('plywood.tab', t); } catch {}
      });
      this.state = await api('load_state');
      this.plan = this.state.result;
      delete this.state.result; // kept apart so a new plan doesn't count as an edit
      this.savedHash = this.state.saved_hash ?? null;
      delete this.state.saved_hash;
      this.state.settings.priority ||= 'waste';
      this.state.tag_aliases ||= {};
      this.state.tag_distinct ||= [];
      this.ensureBlank('parts');
      this.ensureBlank('stock');
      this.savedHash ??= this.jobHash(); // older state: what's there counts as saved
      window.plywoodApp = this; // for the desktop window's "save before closing?"
      window.addEventListener('beforeunload', (e) => {
        if (!window.pywebview && this.unsaved) e.preventDefault(); // browser tab; the desktop window asks itself
      });
      let first = true;
      Alpine.effect(() => {
        JSON.stringify(this.state); // track every cell
        queueMicrotask(() => this.trackUnsaved()); // outside the effect: the cut list isn't an edit
        if (first) { first = false; return; }
        this.schedule();
      });
      this.keepNext = true;
      this.run();
    },

    // Hovering a cut step outlines its piece and highlights its cuts on that sheet's diagram.
    highlight(e, on) {
      const li = e.target.closest('li[data-step]');
      const svg = e.target.closest('.sheet')?.querySelector('svg');
      if (!li || !svg) return;
      const step = li.dataset.step, piece = li.dataset.piece;
      svg.querySelectorAll(`line[data-step="${step}"]`).forEach((l) => l.classList.toggle('hot', on));
      if (piece) svg.querySelector(`g.piece[data-piece="${piece}"]`)?.classList.toggle('hot', on);
    },

    // ---------------------------------------------------------------- tables

    isBlank,
    // Materials already used on rows of one kind (maple ply and solid maple never mix).
    materials(kind) {
      const tags = new Set();
      for (const row of [...this.state.parts, ...this.state.stock]) {
        const tag = normTag(row.tag);
        if (tag && row.kind === kind) tags.add(tag);
      }
      return [...tags].sort();
    },
    // The nominal size (2x4…) a dimensional stock row's width and thickness add up to.
    inches(text) {
      const t = String(text || '').trim();
      const m = t.match(/^(\d+)?(?:[- ]?(\d+)\/(\d+))?\s*("|in)?$/);
      if (this.state.units === 'mm' || /mm$/.test(t)) return parseFloat(t) / 25.4;
      if (/^\d*\.\d+"?$/.test(t)) return parseFloat(t);
      if (!m || (!m[1] && !m[2])) return NaN;
      return (Number(m[1]) || 0) + (m[2] ? Number(m[2]) / Number(m[3]) : 0);
    },
    nominalOf(row) {
      const t = this.inches(row.thickness), w = this.inches(row.width);
      return Object.keys(DIMENSIONAL).find((n) => Math.abs(DIMENSIONAL[n][0] - t) < 0.02 && Math.abs(DIMENSIONAL[n][1] - w) < 0.02) || '';
    },
    setNominal(row, name) {
      if (!DIMENSIONAL[name]) return;
      const [t, w] = DIMENSIONAL[name];
      const cell = (v) => (this.state.units === 'mm' ? String(+(v * 25.4).toFixed(2)) : inchText(v));
      row.thickness = cell(t);
      row.width = cell(w);
      this.ensureBlank('stock');
    },
    // Every tag with where it's used, split by sheet/board (maple ply and solid maple never mix).
    tagInfo() {
      const info = {};
      const add = (row, key, n) => {
        const tag = normTag(row.tag);
        if (!tag || isBlank(row)) return;
        info[tag] ||= { tag, sheetParts: 0, boardParts: 0, sheetStock: 0, boardStock: 0 };
        info[tag][(row.kind === 'sheet' ? 'sheet' : 'board') + key] += n;
      };
      for (const row of this.state.parts) add(row, 'Parts', Number(row.qty) || 1);
      for (const row of this.state.stock) add(row, 'Stock', 1);
      const tags = Object.values(info).sort((a, b) => a.tag.localeCompare(b.tag));
      for (const t of tags) {
        const missing = [];
        if (t.sheetParts && !t.sheetStock) missing.push('sheet');
        t.note = missing.length ? `no ${missing.join('/')} stock` : '';
      }
      return tags;
    },
    // Pairs of tags that might be the same material: typos, or one name inside the other.
    tagQuestions() {
      const tags = this.tagInfo();
      const used = (t) => t.sheetParts + t.boardParts + 3 * (t.sheetStock + t.boardStock);
      const out = [];
      for (let i = 0; i < tags.length; i++) {
        for (let j = i + 1; j < tags.length; j++) {
          const [a, b] = [tags[i], tags[j]];
          const key = pairKey(a.tag, b.tag);
          if (this.state.tag_distinct.includes(key) || !similarTags(a.tag, b.tag)) continue;
          const [likely, other] = used(a) >= used(b) ? [a.tag, b.tag] : [b.tag, a.tag];
          out.push({ key, likely, other });
        }
      }
      return out;
    },
    sameTag(from, to) {
      // Remember it, so the next Onshape import with the old spelling is fixed too.
      this.state.tag_aliases[from] = to;
      for (const [k, v] of Object.entries(this.state.tag_aliases)) if (v === from) this.state.tag_aliases[k] = to;
      delete this.state.tag_aliases[to];
      for (const row of [...this.state.parts, ...this.state.stock]) {
        if (normTag(row.tag) === from) row.tag = to;
      }
      this.say(`"${from}" is now "${to}", here and in future imports.`);
    },
    differentTags(key) {
      this.state.tag_distinct.push(key);
    },
    forgetAlias(from) {
      delete this.state.tag_aliases[from];
    },
    resolveTag(tag) {
      let t = normTag(tag);
      for (let i = 0; i < 10 && this.state.tag_aliases[t]; i++) t = this.state.tag_aliases[t];
      return t;
    },
    stockPlaceholder(row, f) {
      if (f === 'qty') return '0';
      if (f === 'length' && row.kind === 'dimensional' && !Number(row.qty)) return 'any';
      return '';
    },
    // Trimmed edges of a stock row: l/r = ends, b/t = long sides.
    hasEdge(row, e) {
      return (row.trim_edges || '').includes(e);
    },
    toggleEdge(row, e) {
      const on = new Set('lrbt'.split('').filter((x) => this.hasEdge(row, x)));
      on.has(e) ? on.delete(e) : on.add(e);
      row.trim_edges = 'lrbt'.split('').filter((x) => on.has(x)).join('');
    },
    countRows(table) {
      return this.state[table].filter((r) => !isBlank(r)).length || '';
    },
    ensureBlank(table) {
      const rows = this.state[table];
      if (!rows.length || !isBlank(rows[rows.length - 1])) rows.push(newRow(table));
    },
    removeRow(table, i) {
      this.state[table].splice(i, 1);
      this.clearSel();
      this.ensureBlank(table);
    },

    // ---------------------------------------------------------------- row selection

    isSelected(table, i) {
      return this.sel.table === table && this.sel.rows.includes(i);
    },
    clearSel() {
      if (this.sel.rows.length) this.sel = { table: null, rows: [], anchor: 0 };
    },
    // Click = just this row (again = none), Shift = range from the last click, Ctrl = add/remove.
    select(e, table, i) {
      const same = this.sel.table === table;
      let rows;
      if (e.shiftKey && same) {
        const [a, b] = [Math.min(this.sel.anchor, i), Math.max(this.sel.anchor, i)];
        rows = Array.from({ length: b - a + 1 }, (_, k) => a + k).filter((k) => !isBlank(this.state[table][k]));
        this.sel = { table, rows, anchor: this.sel.anchor };
        return;
      }
      if ((e.ctrlKey || e.metaKey) && same) {
        rows = this.sel.rows.includes(i) ? this.sel.rows.filter((k) => k !== i) : [...this.sel.rows, i];
      } else {
        rows = same && this.sel.rows.length === 1 && this.sel.rows[0] === i ? [] : [i];
      }
      this.sel = { table, rows: rows.sort((a, b) => a - b), anchor: i };
    },
    rowKeys(e) {
      if (!this.sel.rows.length || this.sel.table !== this.tab) return;
      const el = document.activeElement;
      if (el && ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (ctrl && key === 'd') this.duplicateRows();
      else if (ctrl && key === 'c') this.copyRows();
      else if (key === 'delete' || key === 'backspace') this.deleteRows();
      else if (key === 'escape') this.clearSel();
      else return;
      e.preventDefault();
    },
    duplicateRows() {
      const { table, rows } = this.sel;
      const list = this.state[table];
      const copies = rows.map((i) => JSON.parse(JSON.stringify(list[i])));
      const at = rows[rows.length - 1] + 1;
      list.splice(at, 0, ...copies);
      this.sel = { table, rows: copies.map((_, k) => at + k), anchor: at };
      this.ensureBlank(table);
    },
    // Tab-separated, one line per row: pastes into a spreadsheet, or back into either table.
    async copyRows() {
      const { table, rows } = this.sel;
      const text = rows.map((i) => FIELDS[table].map((f) => cellText(this.state[table][i], f)).join('\t')).join('\n') + '\n';
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = Object.assign(document.createElement('textarea'), { value: text });
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      this.say(`Copied ${rows.length} ${rows.length > 1 ? 'rows' : 'row'}`);
    },
    deleteRows() {
      const { table, rows } = this.sel;
      this.state[table] = this.state[table].filter((_, i) => !rows.includes(i));
      this.clearSel();
      this.ensureBlank(table);
    },
    clearTable(table) {
      const n = this.countRows(table);
      if (n && !confirm(`Remove all ${n} ${table === 'parts' ? 'parts' : 'stock rows'}?`)) return;
      this.state[table] = [newRow(table)];
      if (table === 'parts') this.state.skipped = [];
    },
    moveDown(e) {
      const td = e.target.closest('td');
      const next = td.parentElement.nextElementSibling;
      next?.children[td.cellIndex]?.querySelector('input')?.focus();
    },
    paste(e, table, i, field) {
      const text = e.clipboardData.getData('text');
      if (!/[\t\n]/.test(text.trim())) return; // single value: let the browser handle it
      e.preventDefault();
      this.clearSel();
      const cols = FIELDS[table];
      const start = cols.indexOf(field);
      const lines = text.replace(/\r/g, '').split('\n').filter((l) => l.trim());
      lines.forEach((line, k) => {
        if (!this.state[table][i + k]) this.state[table].push(newRow(table));
        const row = this.state[table][i + k];
        line.split('\t').forEach((v, j) => {
          const f = cols[start + j];
          if (f) row[f] = normalize(f, v);
        });
      });
      this.ensureBlank(table);
    },
    cellClass(table, i, field) {
      return this.errors[`${table}:${i}:${field}`] ? 'bad' : '';
    },
    cellError(table, i, field) {
      return this.errors[`${table}:${i}:${field}`] || '';
    },
    describe(e) {
      const where = e.table === 'settings' ? 'Settings' : `${e.table === 'parts' ? 'Part' : 'Stock'} row ${e.index + 1}`;
      if (e.table === 'settings') return `${where}, ${e.field.replaceAll('_', ' ')}: ${e.message}`;
      return `${where}, ${e.field}: ${e.message}`;
    },

    // ---------------------------------------------------------------- optimize + save

    schedule() {
      clearTimeout(this.timers.run);
      clearTimeout(this.timers.save);
      this.timers.run = setTimeout(() => this.run(), 450);
      this.timers.save = setTimeout(() => this.save(), 800);
    },
    withPlan() {
      return { ...this.state, result: this.plan, saved_hash: this.savedHash };
    },

    // ---------------------------------------------------------------- unsaved changes

    jobFile() {
      return {
        plywood_job: 1,
        job: this.state.job,
        units: this.state.units,
        denominator: this.state.denominator,
        settings: this.state.settings,
        parts: this.state.parts.filter((r) => !isBlank(r)),
        stock: this.state.stock.filter((r) => !isBlank(r)),
        result: this.plan,
      };
    },
    // What a job file would hold, minus the cut list's fingerprint (it changes on reopen).
    jobHash() {
      const text = JSON.stringify({ ...this.jobFile(), result: this.plan?.sheets ?? null });
      let h = 2166136261;
      for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
      return (h >>> 0).toString(36);
    },
    markSaved() {
      this.savedHash = this.jobHash();
      this.trackUnsaved();
      this.save(); // so a relaunch knows it was saved
    },
    // Tells the desktop window whether closing should ask to save. Empty jobs never ask.
    trackUnsaved() {
      const unsaved = !!(this.countRows('parts') || this.countRows('stock')) && this.jobHash() !== this.savedHash;
      if (unsaved === this.unsaved && this.state.job === this.unsavedName) return;
      this.unsaved = unsaved;
      this.unsavedName = this.state.job;
      api('set_unsaved', unsaved, this.state.job || 'Untitled').catch(() => {});
    },
    // Called by the desktop window after "Save" in its closing question.
    async saveAndClose() {
      if (await this.saveJob()) api('quit');
    },
    save() {
      clearTimeout(this.timers.save);
      return api('save_state', this.withPlan());
    },
    async call(method, ...args) {
      try {
        return await api(method, this.withPlan(), ...args);
      } catch (err) {
        return { ok: false, errors: [], message: 'Lost connection to Plywood: ' + err };
      }
    },
    show(r) {
      this.errors = {};
      for (const e of r.errors || []) this.errors[`${e.table}:${e.index}:${e.field}`] = e.message;
      this.result = r;
      if (r.plan && JSON.stringify(r.plan) !== JSON.stringify(this.plan)) {
        this.plan = r.plan;
        this.save();
        this.trackUnsaved();
      }
    },
    // `keepNext`: the next run shows the saved cut list as it is (after opening a file or
    // the app) instead of recalculating it.
    async run() {
      const keep = this.keepNext;
      this.keepNext = false;
      const my = ++this.seq;
      this.busy = true;
      const r = await this.call('optimize', keep);
      if (my !== this.seq) return; // a newer edit superseded this run
      this.busy = false;
      this.show(r);
    },
    async choose(s, position) {
      if (position < 0 || position >= s.options || this.switching) return;
      const my = ++this.seq;
      this.switching = s.number;
      const r = await this.call('choose', s.number, position);
      this.switching = 0;
      if (my !== this.seq) return; // an edit came in meanwhile; its run wins
      this.busy = false;
      this.show(r);
    },

    // ---------------------------------------------------------------- files

    say(text, kind = 'info') {
      this.flash = { text, kind };
      clearTimeout(this.timers.flash);
      this.timers.flash = setTimeout(() => (this.flash = { text: '', kind: '' }), 7000);
    },
    async saveText(name, text, mime) {
      if (window.pywebview?.api?.save_text) {
        const r = await window.pywebview.api.save_text(name, text);
        if (r.ok) this.say('Saved ' + r.path);
        else if (!r.cancelled) this.say(r.message, 'error');
        return r.ok;
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: mime }));
      a.download = name;
      a.click();
      URL.revokeObjectURL(a.href);
      return true;
    },
    fileBase() {
      return (this.state.job || 'cut list').replace(/[\\/:*?"<>|]+/g, '-').trim();
    },
    async importFile(e, table) {
      const file = e.target.files[0];
      e.target.value = '';
      if (!file) return;
      const text = await file.text();
      const r = await api(table === 'parts' ? 'import_parts' : 'import_stock', text, this.state.units, this.state.denominator);
      if (!r.ok) return this.say(`Couldn't import ${file.name}: ${r.message}`, 'error');
      const existing = this.state[table].filter((row) => !isBlank(row));
      let replace = true;
      if (existing.length) {
        const n = existing.length;
        const noun = table === 'parts' ? (n === 1 ? 'part' : 'parts') : (n === 1 ? 'stock row' : 'stock rows');
        const choice = await this.ask(`The table already has ${n} ${noun}.`,
          [['replace', 'Replace', true], ['add', 'Add'], ['cancel', 'Cancel']]);
        if (choice === 'cancel') return;
        replace = choice === 'replace';
      }
      for (const row of r.rows) if (row.tag) row.tag = this.resolveTag(row.tag);
      this.state[table] = [...(replace ? [] : existing), ...r.rows];
      this.ensureBlank(table);
      if (table === 'parts' && (!this.state.job || this.state.job === 'Untitled')) {
        this.state.job = file.name.replace(/\.csv$/i, '');
      }
      let msg = `Imported ${r.rows.length} ${table === 'parts' ? 'parts' : 'stock rows'}`;
      if (r.source === 'onshape') msg += ' from Onshape BOM';
      if (table === 'parts') { // kept on the Parts tab until dismissed
        const before = replace ? [] : this.state.skipped || [];
        this.state.skipped = [...new Set([...before, ...(r.skipped || [])])];
      }
      this.say(msg);
    },
    async changeUnits(to) {
      if (to === this.state.units) return;
      this.state = await api('convert_units', JSON.parse(JSON.stringify(this.state)), to);
    },
    // A question with buttons ([value, label, primary?]); resolves to the value clicked, or
    // 'cancel' for Esc or a click outside.
    ask(title, buttons) {
      this.question = { title, buttons };
      this.dialog = 'ask';
      return new Promise((resolve) => (this.answerWith = resolve));
    },
    answer(value) {
      this.dialog = null;
      this.answerWith?.(value);
      this.answerWith = null;
    },
    closeDialog() {
      if (this.dialog === 'ask') this.answer('cancel');
      else this.dialog = null;
    },
    // Before New or Open replaces the job: offer to save it if it has unsaved changes.
    async readyToLeave() {
      if (!this.unsaved) return true;
      const choice = await this.ask(`Save changes to ${this.state.job || 'Untitled'}?`,
        [['save', 'Save', true], ['discard', "Don't save"], ['cancel', 'Cancel']]);
      if (choice === 'save') return this.saveJob();
      return choice === 'discard';
    },
    async newJob() {
      if (!(await this.readyToLeave())) return;
      this.state.job = 'Untitled';
      this.state.parts = [newRow('parts')];
      this.state.stock = [newRow('stock')];
      this.state.skipped = [];
      this.markSaved();
    },
    async saveJob() {
      const ok = await this.saveText(this.fileBase() + '.json', JSON.stringify(this.jobFile(), null, 1), 'application/json');
      if (ok) this.markSaved();
      return ok;
    },
    async open() {
      if (!(await this.readyToLeave())) return;
      if (window.pywebview?.api?.open_text) { // native dialog: no need for a fresh click after saving
        const r = await window.pywebview.api.open_text();
        if (r.ok) this.loadJob(r.name, r.text);
        else if (!r.cancelled) this.say(r.message, 'error');
        return;
      }
      this.$refs.openJob.click();
    },
    async openJob(e) {
      const file = e.target.files[0];
      e.target.value = '';
      if (file) this.loadJob(file.name, await file.text());
    },
    async loadJob(name, text) {
      const file = { name };
      let job;
      try {
        job = JSON.parse(text);
        if (!Array.isArray(job.parts)) throw new Error('no parts list');
      } catch (err) {
        return this.say(`${file.name} isn't a Plywood job: ${err.message}`, 'error');
      }
      // Older job files have no stock: keep the current stock, in the job's units so every
      // bare number means the same thing.
      if (job.units && job.units !== this.state.units) {
        this.state = await api('convert_units', JSON.parse(JSON.stringify(this.state)), job.units);
      }
      this.state.job = job.job || file.name.replace(/\.json$/i, '');
      if (job.denominator) this.state.denominator = job.denominator;
      this.state.settings = { ...this.state.settings, ...migrateSettings(job.settings) };
      this.state.parts = job.parts.map((r) => ({ ...newRow('parts'), ...kindOf(r) }));
      if (Array.isArray(job.stock)) this.state.stock = job.stock.map((r) => ({ ...newRow('stock'), ...kindOf(r) }));
      this.plan = job.result || null;
      this.keepNext = true; // shown exactly as saved
      this.ensureBlank('parts');
      this.ensureBlank('stock');
      this.markSaved();
      this.say('Opened ' + file.name);
    },
    // ---------------------------------------------------------------- export / import one tab

    exportTable(table) {
      const rows = this.state[table].filter((r) => !isBlank(r));
      if (!rows.length) return this.say(`No ${table} to export.`, 'error');
      const cols = EXPORT_COLUMNS[table];
      const value = (row, c) => {
        if (c === 'rough') return row.rough && row.kind === 'hardwood' ? 'yes' : '';
        if (c === 'trim') return row.trim_edges == null ? '' : row.trim_edges || 'none';
        return row[c] ?? '';
      };
      const header = cols.map((c) => (c === 'tag' ? 'material' : c));
      const lines = [header.join(','), ...rows.map((r) => cols.map((c) => csvCell(value(r, c))).join(','))];
      this.saveText(`${this.fileBase()} ${table}.csv`, lines.join('\r\n') + '\r\n', 'text/csv');
    },
    exportSettings() {
      const file = { plywood_settings: 1, units: this.state.units, denominator: this.state.denominator, settings: this.state.settings };
      this.saveText('plywood settings.json', JSON.stringify(file, null, 1), 'application/json');
    },
    async importSettings(e) {
      const file = e.target.files[0];
      e.target.value = '';
      if (!file) return;
      let data;
      try {
        data = JSON.parse(await file.text());
        if (!data.settings || typeof data.settings !== 'object') throw new Error('no settings');
      } catch (err) {
        return this.say(`${file.name} has no Plywood settings: ${err.message}`, 'error');
      }
      let settings = migrateSettings(data.settings);
      if (data.units && data.units !== this.state.units) { // lengths are in the file's units
        const conv = await api('convert_units', { units: data.units, denominator: this.state.denominator, settings, parts: [], stock: [] }, this.state.units);
        settings = conv.settings;
      }
      this.state.settings = { ...this.state.settings, ...settings };
      if (data.denominator) this.state.denominator = data.denominator;
      this.say('Loaded settings from ' + file.name);
    },
    reoptimize() {
      this.plan = null;
      this.run();
    },

    // ---------------------------------------------------------------- phone shopping list

    async openPhone() {
      if (this.result?.stale) return this.say(this.result.message, 'error');
      const r = await this.call('phone_job');
      if (!r.ok) return this.say(r.message, 'error');
      this.phone.code = r.code;
      this.makePhoneLink();
      this.dialog = 'phone';
    },
    makePhoneLink() {
      const base = String(this.state.phone_url || '').trim().replace(/#.*$/, '');
      this.phone.link = base ? base + '#j=' + this.phone.code : '';
      this.phone.qr = '';
      if (!this.phone.link || typeof qrcode === 'undefined') return;
      try {
        const qr = qrcode(0, 'L'); // a screen is clean: least error correction, biggest squares
        qr.addData(this.phone.link);
        qr.make();
        this.phone.qr = qr.createSvgTag({ cellSize: 4, margin: 4, scalable: true });
      } catch {} // too long for any QR code
    },
    async copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const ta = Object.assign(document.createElement('textarea'), { value: text });
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        ta.remove();
      }
      this.say('Copied');
    },
    async addBoards() {
      const r = await api('import_boards', this.boardsText, this.state.units, this.state.denominator);
      if (!r.ok) return this.say(`Couldn't read that: ${r.message}`, 'error');
      const n = r.rows.reduce((sum, row) => sum + Number(row.qty), 0);
      this.state.stock = [...this.state.stock.filter((row) => !isBlank(row)), ...r.rows];
      this.ensureBlank('stock');
      this.dialog = null;
      this.tab = 'stock';
      this.say(`Added ${n} ${n === 1 ? 'board' : 'boards'} on hand`);
    },
    async exportCsv() {
      if (this.result?.stale) return this.say(this.result.message, 'error');
      const r = await this.call('report');
      if (!r.ok) return this.say(r.message, 'error');
      await this.saveText(this.fileBase() + ' cut list.csv', r.csv, 'text/csv');
    },
    async printReport() {
      if (this.result?.stale) return this.say(this.result.message, 'error');
      const r = await this.call('open_report');
      if (!r.ok) return this.say(r.message, 'error');
      this.say('Report opened in your browser; print it from there (Ctrl+P).');
    },
  };
}
