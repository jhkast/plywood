// Plywood shopping list (phone). The job arrives in the link (#j=code) from the desktop app;
// boards go in the cart as you find them, and the Python optimizer (Pyodide, in a worker)
// re-plans what's still needed. Everything is kept in localStorage, so it works offline.

const QUARTERS = [4, 5, 6, 8, 10, 12, 16];
const QUICK_TRIES = 300; // Pyodide on a phone is many times slower than the desktop

function fromB64(code) {
  const b64 = code.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (code.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

function toB64(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function decodeCode(code) {
  const stream = new Blob([fromB64(code)]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return JSON.parse(await new Response(stream).text());
}

async function encodeCode(data) {
  const stream = new Blob([JSON.stringify(data)]).stream().pipeThrough(new CompressionStream('deflate-raw'));
  return toB64(new Uint8Array(await new Response(stream).arrayBuffer()));
}

function hashOf(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (Math.imul(31, h) + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem('plywood.shop.' + key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try { localStorage.setItem('plywood.shop.' + key, JSON.stringify(value)); } catch {}
  },
};

// The Python planner in a worker: one request at a time is plenty.
const planner = {
  worker: null,
  calls: {},
  seq: 0,
  ready: false,
  start(onReady) {
    this.worker = new Worker('worker.js', { type: 'module' });
    this.worker.onerror = (e) => onReady({ ready: false, error: e.message || 'the planner failed to start' });
    this.worker.onmessage = (e) => {
      if ('ready' in e.data) {
        this.ready = e.data.ready;
        onReady(e.data);
        return;
      }
      this.calls[e.data.id]?.(e.data.result);
      delete this.calls[e.data.id];
    };
  },
  call(method, ...args) {
    const id = ++this.seq;
    return new Promise((resolve) => {
      this.calls[id] = resolve;
      this.worker.postMessage({ id, method, args: JSON.parse(JSON.stringify(args)) });
    });
  },
};

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

function shop() {
  return {
    job: null,
    id: '',
    cart: [], // {length, width, thickness, tag, rough, group}
    ticks: {}, // buy line index -> how many checked off
    plan: { find: [], unplaced: [], cart: [], enough: false },
    form: {},
    busy: false,
    seq: 0,
    message: '',
    messageKind: '',
    boardsLink: '',
    pythonReady: false,
    offline: false,

    async init() {
      planner.start((r) => {
        this.pythonReady = r.ready;
        if (!r.ready) this.say("Couldn't load the planner: " + r.error, 'error');
      });
      navigator.serviceWorker?.ready.then(() => (this.offline = true));
      window.addEventListener('hashchange', () => this.load());
      await this.load();
    },

    async load() {
      const hash = location.hash.slice(1);
      this.boardsLink = hash.startsWith('b=') ? location.href : '';
      let code = hash.startsWith('j=') ? hash.slice(2) : store.get('last', '');
      if (!code) return;
      let job;
      try {
        job = await decodeCode(code);
      } catch {
        return this.say("This link doesn't hold a Plywood job.", 'error');
      }
      if (job.v !== 2) return this.say('This link is from an older Plywood. Make a new one on the computer.', 'error');
      this.job = job;
      this.id = hashOf(code);
      store.set('last', code);
      const saved = store.get('job.' + this.id, {});
      this.cart = saved.cart || [];
      this.ticks = saved.ticks || {};
      this.plan = saved.plan || { find: job.find, unplaced: job.unplaced, cart: [], enough: !job.find.length && !job.unplaced.length };
      for (const g of this.groups()) this.ensureForm(g);
    },

    save() {
      store.set('job.' + this.id, { cart: this.cart, ticks: this.ticks, plan: this.plan });
    },

    say(text, kind = '') {
      this.message = text;
      this.messageKind = kind;
      clearTimeout(this._flash);
      this._flash = setTimeout(() => (this.message = ''), 6000);
    },

    unitMark() {
      return this.job?.units === 'mm' ? 'mm' : 'in';
    },

    // Standard rough thicknesses, written in the job's units.
    thicknesses() {
      return QUARTERS.map((q) => ({
        label: q + '/4',
        value: this.job?.units === 'mm' ? String(+(q * 6.35).toFixed(2)) : String(q / 4),
      }));
    },

    ensureForm(g) {
      if (this.form[g.key]) return;
      const q = Number(g.key.split('|')[0]);
      const t = this.thicknesses().find((t) => t.label === q + '/4');
      this.form[g.key] = { length: '', width: '', thickness: t ? t.value : g.thickness, rough: true };
    },

    // Every board group: still to find, or covered by boards in the cart.
    groups() {
      if (!this.job) return [];
      const need = Object.fromEntries(this.plan.find.map((g) => [g.key, g]));
      const seen = new Map();
      for (const g of [...this.job.find, ...this.plan.find]) if (!seen.has(g.key)) seen.set(g.key, g);
      return [...seen.values()].map((g) => ({ key: g.key, label: g.label, tag: g.tag, thickness: g.thickness, need: need[g.key] }));
    },

    cartIn(key) {
      return this.cart.map((b, i) => ({ ...b, i })).filter((b) => b.group === key);
    },

    boardText(b) {
      const q = this.thicknesses().find((t) => t.value === b.thickness);
      const mark = this.unitMark() === 'mm' ? ' mm' : '"';
      const len = (v) => (/^[\d.\-\/ ]+$/.test(v) ? v + mark : v); // 8' stays 8'
      return `${len(b.length)} × ${len(b.width)} × ${q ? q.label : b.thickness}${b.rough ? '' : ' surfaced'}`;
    },

    feet(g) {
      return g.waste_pct ? `about ${Math.round(g.board_feet_total)}` : String(g.board_feet);
    },

    countText(b) {
      if (!b.spares) return String(b.count);
      return b.count ? `${b.count} + ${b.spares} spare` : `${b.spares} spare`;
    },

    ticked(li) {
      return this.ticks[li] || 0;
    },

    tick(li, n) {
      this.ticks[li] = this.ticked(li) === n ? n - 1 : n;
      this.save();
    },

    addBoard(g) {
      const f = this.form[g.key];
      if (!String(f.length).trim() || !String(f.width).trim()) return this.say('Enter the length and width.', 'error');
      this.cart.push({ length: f.length.trim(), width: f.width.trim(), thickness: f.thickness, tag: g.tag, rough: f.rough, group: g.key });
      f.length = '';
      f.width = '';
      this.save();
      this.replan();
      return true;
    },

    removeBoard(i) {
      this.cart.splice(i, 1);
      this.save();
      this.replan();
    },

    // A quick plan first; if it says you're short, a full one, so it never overbuys for
    // want of looking.
    async replan() {
      const my = ++this.seq;
      this.busy = true;
      if (!this.pythonReady) this.say('Loading the planner…');
      for (const tries of [QUICK_TRIES, null]) {
        const r = await planner.call('replan', this.job, this.cart, tries);
        if (my !== this.seq) return;
        if (!r.ok) {
          this.busy = false;
          return this.say(r.message, 'error');
        }
        if (this.message === 'Loading the planner…') this.message = '';
        this.plan = r;
        for (const g of this.groups()) this.ensureForm(g);
        this.save();
        if (r.enough || r.complete) break;
      }
      this.busy = false;
    },

    async sendBoards() {
      const code = await encodeCode({ v: 2, job: this.job.job, units: this.job.units, boards: this.cart });
      const link = location.origin + location.pathname + '#b=' + code;
      if (navigator.share) {
        try {
          await navigator.share({ title: 'Boards for ' + this.job.job, text: link });
          return;
        } catch (e) {
          if (e.name === 'AbortError') return;
        }
      }
      this.copy(link);
    },

    async copy(text) {
      try {
        await navigator.clipboard.writeText(text);
        this.say('Link copied');
      } catch {
        prompt('Copy this link', text);
      }
    },

    offlineText() {
      if (this.offline && this.pythonReady) return 'Ready offline';
      return this.pythonReady ? '' : 'Loading the planner…';
    },
  };
}
