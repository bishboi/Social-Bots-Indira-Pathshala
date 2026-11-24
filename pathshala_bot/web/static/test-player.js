// Online test player: timer, sections, palette, mark-for-review, autosave; answers are scored on the server.
(function () {
  const EN = {
    questions: 'Questions', marks: 'Marks', minutes: 'Minutes', section: 'Section', marksEach: 'Marks each', negative: 'Negative',
    name: 'Your name', nameOpt: 'Your name (optional, for the rank list)', phone: 'Mobile number', email: 'Email (optional)',
    privacy: 'We use your number only to send you new tests and results.', start: 'Start test', resume: 'Resume test', restart: 'Start again',
    submit: 'Submit', q: 'Question', mark: 'Mark for review', unmark: 'Unmark', clear: 'Clear', prev: '← Previous', next: 'Save & next →',
    palette: 'Questions', answered: 'Answered', marked: 'Marked', notAnswered: 'Not answered', notVisited: 'Not visited',
    score: 'Score', rank: 'Rank', percentile: 'Percentile', accuracy: 'Accuracy', correct: 'Correct', wrong: 'Wrong', skipped: 'Skipped',
    time: 'Time', solutions: 'Solutions', showHint: 'Show hint', hint: 'Hint', hintsUsed: 'hint(s) used', used: 'used',
    solution: 'Solution', explanation: 'Detailed explanation', nextTests: 'Next in this series', allTests: 'All tests',
    f_all: 'All', f_wrong: 'Wrong', f_skipped: 'Skipped', f_correct: 'Correct',
    sure: n => `Submit the test? ${n} question(s) are not answered.`, timeUp: 'Time is up – your test was submitted.',
  };
  const HI = Object.assign({}, EN, {
    questions: 'प्रश्न', marks: 'अंक', minutes: 'मिनट', section: 'खंड', marksEach: 'प्रति प्रश्न अंक', negative: 'ऋणात्मक',
    name: 'आपका नाम', nameOpt: 'आपका नाम (वैकल्पिक, रैंक सूची के लिए)', phone: 'मोबाइल नंबर', email: 'ईमेल (वैकल्पिक)',
    privacy: 'आपका नंबर केवल नए टेस्ट और परिणाम भेजने के लिए उपयोग होगा।', start: 'टेस्ट शुरू करें', resume: 'टेस्ट जारी रखें', restart: 'फिर से शुरू करें',
    submit: 'सबमिट करें', q: 'प्रश्न', mark: 'रिव्यू के लिए चिह्नित करें', unmark: 'चिह्न हटाएँ', clear: 'उत्तर हटाएँ', prev: '← पिछला', next: 'सेव करें और आगे →',
    palette: 'प्रश्न सूची', answered: 'उत्तर दिया', marked: 'चिह्नित', notAnswered: 'उत्तर नहीं दिया', notVisited: 'नहीं देखा',
    score: 'अंक', rank: 'रैंक', percentile: 'पर्सेंटाइल', accuracy: 'शुद्धता', correct: 'सही', wrong: 'गलत', skipped: 'छोड़े गए',
    time: 'समय', solutions: 'हल', showHint: 'संकेत देखें', hint: 'संकेत', hintsUsed: 'संकेत देखे', used: 'देखा',
    solution: 'हल', explanation: 'विस्तृत व्याख्या', nextTests: 'इस सीरीज़ में आगे', allTests: 'सभी टेस्ट',
    f_all: 'सभी', f_wrong: 'गलत', f_skipped: 'छोड़े गए', f_correct: 'सही',
    sure: n => `टेस्ट सबमिट करें? ${n} प्रश्नों का उत्तर नहीं दिया गया है।`, timeUp: 'समय समाप्त – आपका टेस्ट सबमिट हो गया।',
  });
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    del(k) { try { localStorage.removeItem(k); } catch (e) {} },
  };

  window.player = function () {
    const m = JSON.parse(document.getElementById('meta').textContent);
    const key = `pathshala-test:${m.series}:${m.test}`;
    return {
      m, L: m.language === 'hi' ? HI : EN, phase: 'intro', error: '', busy: false, form: {name: '', phone: '', email: ''},
      token: '', qs: [], byId: {}, cur: 0, answers: {}, marked: {}, seen: {}, hinted: {}, deadline: 0, left: 0, spent: 0, started: 0,
      res: null, filter: 'all', resumable: false, ticker: null,
      init() {
        const saved = store.get(key);
        if (saved && saved.deadline > Date.now() && saved.token) this.resumable = true;
        if (saved && saved.form) this.form = Object.assign(this.form, saved.form);
      },
      totalQ() { return m.sections.reduce((a, s) => a + s.questions, 0); },
      letter(j) { return 'ABCDEFGHIJ'[j]; },
      async post(url, body) {
        const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || r.statusText);
        return d;
      },
      async start() {
        this.error = ''; this.busy = true;
        const saved = this.resumable ? store.get(key) : null;
        try {
          if (saved) {
            Object.assign(this, {token: saved.token, qs: saved.qs, answers: saved.answers || {}, marked: saved.marked || {}, hinted: saved.hinted || {},
              seen: saved.seen || {}, cur: saved.cur || 0, deadline: saved.deadline, started: saved.started});
          } else {
            const d = await this.post(`/t/${m.series}/${m.test}/start`, this.form);
            Object.assign(this, {token: d.token, qs: d.questions, answers: {}, marked: {}, seen: {}, hinted: {}, cur: 0,
              started: Date.now(), deadline: Date.now() + m.minutes * 60000});
          }
          this.byId = Object.fromEntries(this.qs.map(q => [q.id, q]));
          this.phase = 'test'; this.go(this.cur); this.tick();
          this.ticker = setInterval(() => this.tick(), 1000);
        } catch (e) { this.error = e.message; }
        this.busy = false;
      },
      discard() { store.del(key); this.resumable = false; },
      save() {
        store.set(key, {token: this.token, qs: this.qs, answers: this.answers, marked: this.marked, seen: this.seen, hinted: this.hinted, cur: this.cur,
          deadline: this.deadline, started: this.started, form: {name: this.form.name}});
      },
      tick() {
        this.left = Math.max(0, Math.round((this.deadline - Date.now()) / 1000));
        if (this.left === 0 && this.phase === 'test') { this.error = this.L.timeUp; this.submit(); }
      },
      clock() { return this.mmss(this.left); },
      mmss(s) { const h = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60), ss = s % 60;
        return (h ? h + ':' + String(mm).padStart(2, '0') : mm) + ':' + String(ss).padStart(2, '0'); },
      q() { return this.qs[this.cur]; },
      go(i) { if (i < 0 || i >= this.qs.length) return; this.cur = i; this.seen[this.qs[i].id] = true; this.save(); window.scrollTo(0, 0); },
      goSection(s) { const i = this.qs.findIndex(q => q.section === s); if (i >= 0) this.go(i); },
      choose(j) { this.answers[this.q().id] = j; this.save(); },
      clear() { delete this.answers[this.q().id]; this.save(); },
      toggleMark() { const id = this.q().id; this.marked[id] = !this.marked[id]; this.save(); },
      showHint() { this.hinted[this.q().id] = true; this.save(); },
      hintCount() { return Object.values(this.hinted).filter(Boolean).length; },
      answeredCount() { return Object.keys(this.answers).length; },
      paletteClass(x, i) {
        const c = [];
        if (this.marked[x.id]) c.push('mark'); else if (x.id in this.answers) c.push('ans'); else if (this.seen[x.id]) c.push('seen');
        if (i === this.cur) c.push('cur');
        return c.join(' ');
      },
      confirmSubmit() {
        const left = this.qs.length - this.answeredCount();
        if (!left || window.confirm(this.L.sure(left))) this.submit();
      },
      async submit() {
        if (this.busy) return;
        this.busy = true;
        clearInterval(this.ticker);
        this.spent = Math.round((Math.min(Date.now(), this.deadline) - this.started) / 1000);
        try {
          this.res = await this.post(`/t/${m.series}/${m.test}/submit`, {token: this.token, answers: this.answers, seconds: this.spent});
          store.del(key); this.phase = 'result'; window.scrollTo(0, 0);
        } catch (e) { this.error = e.message; if (this.left > 0) this.ticker = setInterval(() => this.tick(), 1000); }
        this.busy = false;
      },
    };
  };
})();
