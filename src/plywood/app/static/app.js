// Plywood UI. All parsing and optimizing happens in Python; this file only manages the tables.

async function api(method, ...args) {
  const res = await fetch('/api/' + method, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  return res.json();
}

const FIELDS = {
  parts: ['name', 'length', 'width', 'thickness', 'qty', 'grain', 'kind', 'tag'],
  stock: ['name', 'length', 'width', 'thickness', 'qty', 'kind', 'tag'],
};

function newRow(table) {
  const row = { name: '', length: '', width: '', thickness: '', qty: '', kind: 'sheet', tag: '' };
  if (table === 'parts') row.grain = '';
  else row.trim_edges = null;
  return row;
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
  if (field === 'kind') return v.startsWith('b') || v.startsWith('lum') ? 'board' : 'sheet';
  return value.trim();
}

function plywood() {
  return {
    state: { job: '', units: 'in', denominator: 16, settings: {}, parts: [], stock: [] },
    tab: 'parts',
    result: null,
    errors: {},
    busy: false,
    flash: { text: '', kind: '' },
    seq: 0,
    timers: {},

    async init() {
      try {
        this.tab = localStorage.getItem('plywood.tab') || 'parts';
      } catch {}
      this.$watch('tab', (t) => {
        try { localStorage.setItem('plywood.tab', t); } catch {}
      });
      this.state = await api('load_state');
      this.state.settings.priority ||= 'waste';
      this.state.tag_aliases ||= {};
      this.state.tag_distinct ||= [];
      this.ensureBlank('parts');
      this.ensureBlank('stock');
      let first = true;
      Alpine.effect(() => {
        JSON.stringify(this.state); // track every cell
        if (first) { first = false; return; }
        this.schedule();
      });
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
    allTags() {
      const tags = new Set(['ply', 'mdf']);
      for (const row of [...this.state.parts, ...this.state.stock]) {
        const tag = String(row.tag || '').trim().toLowerCase();
        if (tag) tags.add(tag);
      }
      return [...tags].sort();
    },
    // Every tag with where it's used, split by sheet/board (maple ply and solid maple never mix).
    tagInfo() {
      const info = {};
      const add = (row, key, n) => {
        const tag = normTag(row.tag);
        if (!tag || isBlank(row)) return;
        info[tag] ||= { tag, sheetParts: 0, boardParts: 0, sheetStock: 0, boardStock: 0 };
        info[tag][(row.kind === 'board' ? 'board' : 'sheet') + key] += n;
      };
      for (const row of this.state.parts) add(row, 'Parts', Number(row.qty) || 1);
      for (const row of this.state.stock) add(row, 'Stock', 1);
      const tags = Object.values(info).sort((a, b) => a.tag.localeCompare(b.tag));
      for (const t of tags) {
        const missing = [];
        if (t.sheetParts && !t.sheetStock) missing.push('sheet');
        if (t.boardParts && !t.boardStock) missing.push('board');
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
      if (f === 'qty') return 'buy';
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
      this.ensureBlank(table);
    },
    clearTable(table) {
      const n = this.countRows(table);
      if (n && !confirm(`Remove all ${n} ${table === 'parts' ? 'parts' : 'stock rows'}?`)) return;
      this.state[table] = [newRow(table)];
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
      if (e.table === 'settings') return `${where}, ${e.field.replace('_', ' ')}: ${e.message}`;
      return `${where}, ${e.field}: ${e.message}`;
    },

    // ---------------------------------------------------------------- optimize + save

    schedule() {
      clearTimeout(this.timers.run);
      clearTimeout(this.timers.save);
      this.timers.run = setTimeout(() => this.run(), 450);
      this.timers.save = setTimeout(() => api('save_state', this.state), 800);
    },
    async run() {
      const my = ++this.seq;
      this.busy = true;
      let r;
      try {
        r = await api('optimize', this.state);
      } catch (err) {
        r = { ok: false, errors: [], message: 'Lost connection to Plywood: ' + err };
      }
      if (my !== this.seq) return; // a newer edit superseded this run
      this.busy = false;
      this.errors = {};
      for (const e of r.errors || []) this.errors[`${e.table}:${e.index}:${e.field}`] = e.message;
      this.result = r;
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
        return;
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: mime }));
      a.download = name;
      a.click();
      URL.revokeObjectURL(a.href);
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
      const replace = !existing.length || confirm(`Replace the ${existing.length} rows already in the table?\n\nOK = replace, Cancel = add to them`);
      for (const row of r.rows) if (row.tag) row.tag = this.resolveTag(row.tag);
      this.state[table] = [...(replace ? [] : existing), ...r.rows];
      this.ensureBlank(table);
      if (table === 'parts' && (!this.state.job || this.state.job === 'Untitled')) {
        this.state.job = file.name.replace(/\.csv$/i, '');
      }
      let msg = `Imported ${r.rows.length} ${table === 'parts' ? 'parts' : 'stock rows'}`;
      if (r.source === 'onshape') msg += ' from Onshape BOM';
      if (r.skipped?.length) msg += `. Skipped (no cut list dims): ${r.skipped.join(', ')}`;
      this.say(msg, r.skipped?.length ? 'warn' : 'info');
    },
    async changeUnits(to) {
      if (to === this.state.units) return;
      this.state = await api('convert_units', JSON.parse(JSON.stringify(this.state)), to);
    },
    newJob() {
      if (this.countRows('parts') && !confirm('Start a new job? Parts will be cleared; your stock list stays.')) return;
      this.state.job = 'Untitled';
      this.state.parts = [newRow('parts')];
    },
    async saveJob() {
      const job = {
        plywood_job: 1,
        job: this.state.job,
        units: this.state.units,
        denominator: this.state.denominator,
        settings: this.state.settings,
        parts: this.state.parts.filter((r) => !isBlank(r)),
      };
      await this.saveText(this.fileBase() + '.json', JSON.stringify(job, null, 1), 'application/json');
    },
    async openJob(e) {
      const file = e.target.files[0];
      e.target.value = '';
      if (!file) return;
      let job;
      try {
        job = JSON.parse(await file.text());
        if (!Array.isArray(job.parts)) throw new Error('no parts list');
      } catch (err) {
        return this.say(`${file.name} isn't a Plywood job: ${err.message}`, 'error');
      }
      // Bring the stock list into the job's units so every bare number means the same thing.
      if (job.units && job.units !== this.state.units) {
        this.state = await api('convert_units', JSON.parse(JSON.stringify(this.state)), job.units);
      }
      this.state.job = job.job || file.name.replace(/\.json$/i, '');
      if (job.denominator) this.state.denominator = job.denominator;
      this.state.settings = { ...this.state.settings, ...(job.settings || {}) };
      this.state.parts = job.parts.map((r) => ({ ...newRow('parts'), ...r }));
      this.ensureBlank('parts');
      this.say('Opened ' + file.name);
    },
    async exportCsv() {
      const r = await api('report', this.state);
      if (!r.ok) return this.say(r.message, 'error');
      await this.saveText(this.fileBase() + ' cut list.csv', r.csv, 'text/csv');
    },
    async printReport() {
      const r = await api('open_report', this.state);
      if (!r.ok) return this.say(r.message, 'error');
      this.say('Report opened in your browser; print it from there (Ctrl+P).');
    },
  };
}
