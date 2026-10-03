/* Optional Node.js regression test: actual panel scripts, minimal DOM harness. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {viewFor} = require('./analytics-workspace.js');

test('locked-call navigation includes the remaining-call anchor', () => {
  assert.equal(viewFor('#locked-call'), 'locked');
  assert.equal(viewFor('#remaining-calls'), 'locked');
  assert.equal(viewFor('#performance'), 'performance');
});

test('both checkpoint cards mount inside the midpoint pane without historical UI', async () => {
  const ids = new Map(), events = new Map();
  class Element {
    constructor(tag) { this.tagName=tag; this.children=[]; this.style={}; this.dataset={}; this.hidden=false; }
    set id(value) { this._id=value; ids.set(value,this); }
    get id() { return this._id; }
    set innerHTML(value) {
      this.html=value;
      for(const match of value.matchAll(/id="([^"]+)"/g)) {
        const child=new Element('div'); child.id=match[1]; this.append(child);
      }
      if(value.includes('<details')) { this.details=new Element('details'); this.append(this.details); }
    }
    append(child) { child.parent=this; this.children.push(child); }
    before(child) { child.parent=this.parent; this.parent.children.splice(this.parent.children.indexOf(this),0,child); }
    querySelector(selector) { return selector==='details'?this.details:ids.get(selector.slice(1))||null; }
    replaceChildren() { this.children=[]; }
    setAttribute() {}
  }
  const main=new Element('main');
  const document={createElement:tag=>new Element(tag),getElementById:id=>ids.get(id)||null,
    querySelector:selector=>selector==='main'?main:null};
  const context=vm.createContext({document,location:{hash:'#remaining-calls'},Date,Intl,Promise,
    fetch:async()=>({ok:false}),AbortSignal:{timeout:()=>null},setInterval:()=>0,
    addEventListener:(name,fn)=>events.set(name,fn)});
  for(const file of ['midpoint-panel.js','remaining-panel.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname,file),'utf8'),context,{filename:file});
  }
  await Promise.resolve();
  const pane=ids.get('locked-call'),remaining=ids.get('remaining-calls');
  assert.ok(pane);
  assert.ok(remaining,'remaining panel must mount without the deleted historical panel');
  assert.equal(remaining.parent,pane);
  assert.ok(pane.children.indexOf(remaining)<pane.children.indexOf(pane.details));
  assert.equal(ids.get('remaining-7-label').textContent,'WAITING FOR DATA');
  assert.equal(ids.get('remaining-4-label').textContent,'WAITING FOR DATA');
  assert.equal(pane.hidden,false);
  context.location.hash='#performance'; events.get('hashchange')(); assert.equal(pane.hidden,true);
  context.location.hash='#remaining-calls'; events.get('hashchange')(); assert.equal(pane.hidden,false);
  assert.equal(ids.has('timing-comparison'),false);
});

test('stats deck computes skill vs market, Wilson range and ranks sources', () => {
  const {computeStats, wilson} = require('./stats-panel.js');
  const s = computeStats({forecast_evaluation: {
    'market-mid-v1': {settled_markets: 100, correct: 55, accuracy: .55, brier: .24, log_loss: .67, horizon_cohorts: {}},
    'volatility-proxy-v1': {settled_markets: 100, correct: 60, accuracy: .6, brier: .228, log_loss: .65,
      horizon_cohorts: {'opening (10–15 min left)': {settled_markets: 40, brier: .25}}, mistakes: [{weak_direction: true}, {weak_direction: false}]}},
    learning: {live_training_markets: 100, required_training_markets: 200}});
  assert.equal(s.rows[0].source, 'volatility-proxy-v1');
  assert.ok(Math.abs(s.rows[0].brierSkill - .05) < 1e-9);
  assert.ok(Math.abs(s.rows[0].llDelta + .02) < 1e-9);
  assert.equal(s.rows[0].nearMisses, 1);
  assert.equal(s.best.source, 'volatility-proxy-v1');
  assert.deepEqual(s.checkpoint, {have: 100, need: 200});
  const [lo, hi] = wilson(60, 100); assert.ok(lo > .49 && lo < .51 && hi > .69 && hi < .70);
  assert.equal(computeStats({}).rows.length, 0);
});
