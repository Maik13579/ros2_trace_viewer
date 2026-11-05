import {$, el, api, state, duration, seconds, canvasSize, showError, debounce, callbackLabel} from './shared.js';

const basicColumns = [
  ['name','Callback'], ['count','Executions'], ['total_ns','Total duration'], ['share_percent','Share'],
];
const detailedColumns = [
  ['interval_percent','Duration / interval'], ['min_ns','Min'], ['max_ns','Max'], ['mean_ns','Mean'],
  ['median_ns','Median'], ['stddev_ns','Std dev'], ['p95_ns','P95'], ['p99_ns','P99'],
];
let columns = basicColumns;

export class Statistics {
  constructor(onSelect) {
    this.onSelect = onSelect; this.offset = 0; this.sort = 'total_ns'; this.descending = true;
    $('detailed-metrics').onchange = () => this.refresh();
    $('extra-charts').ontoggle = () => { if ($('extra-charts').open && this.chartData) this.renderCharts(this.chartData); };
    $('stats-level').onchange = () => { this.offset = 0; this.refresh(); };
    $('stats-prev').onclick = () => { this.offset = Math.max(0, this.offset - 50); this.refresh(); };
    $('stats-next').onclick = () => { this.offset += 50; this.refresh(); };
    state.events.addEventListener('filters', () => { this.offset = 0; if (state.view === 'statistics') this.refresh(); });
    new ResizeObserver(debounce(() => { if (this.chartData && state.view === 'statistics') this.renderCharts(this.chartData); }, 100)).observe($('statistics-view'));
  }
  async refresh() {
    if (state.view !== 'statistics') return;
    const version = this.version = (this.version || 0) + 1;
    const level = $('stats-level').value;
    columns = [...basicColumns, ...($('detailed-metrics').checked ? detailedColumns : [])];
    try {
      const [data, charts] = await Promise.all([
        api('statistics', state.parameters({level, sort: this.sort, descending: this.descending, offset: this.offset, limit: 50})),
        api('charts', state.parameters()),
      ]);
      if (this.version !== version) return;
      this.total = Number(data.total_callback_ns); this.versionData = version;
      $('stats-summary').textContent = `${data.execution_count.toLocaleString()} executions · ${duration(data.total_callback_ns)} total${data.crossing_count ? ` · ${data.crossing_count} boundary crossings excluded` : ''}`;

      const head = el('tr');
      for (const [key, label] of columns) head.append(el('th', {}, el('button', {onclick: () => {
        this.descending = this.sort === key ? !this.descending : key !== 'name'; this.sort = key; this.refresh();
      }}, (key === 'name' ? (level === 'callback' ? 'Callback' : level === 'node' ? 'Node' : 'Process') : label) + (key === this.sort ? (this.descending ? ' ↓' : ' ↑') : ''))));
      $('stats-table').tHead.replaceChildren(head);
      const body = $('stats-table').tBodies[0]; body.replaceChildren();
      for (const row of data.rows) body.append(this.row(row, level, 0, {}, version));
      if (!data.rows.length) body.append(el('tr', {}, el('td', {colspan: columns.length}, 'No complete executions match this interval and selection.')));
      $('stats-page').textContent = `${data.total_groups ? this.offset + 1 : 0}–${Math.min(this.offset + 50, data.total_groups)} of ${data.total_groups}`;
      $('stats-prev').disabled = this.offset === 0;
      $('stats-next').disabled = this.offset + 50 >= data.total_groups;
      this.chartData = charts; if ($('extra-charts').open) this.renderCharts(charts);
    } catch (error) { showError(error); }
  }
  row(data, level, depth, ancestry, version) {
    const row = el('tr'), identity = el('td'); identity.style.paddingLeft = `${10 + depth * 18}px`;
    let expanded = false, children = [];
    const title = level === 'callback' ? callbackLabel(data) : data.name;
    const button = el('button', {title: `${data.name} · ${data.id}`, onclick: async () => {
      if (level === 'callback') {
        $('stats-table').querySelectorAll('tr.selected').forEach(item => item.classList.remove('selected'));
        row.classList.add('selected'); return this.onSelect({...data,share_percent:share});
      }
      if (expanded) { children.forEach(child => child.remove()); children = []; button.textContent = `▸ ${title}`; expanded = false; return; }
      expanded = true; button.textContent = `▾ ${title}`;
      const childLevel = level === 'process' ? 'node' : 'callback';
      const scope = {...ancestry, ...(level === 'process' ? {processes: data.pid} : {nodes: data.id})};
      const load = async (offset = 0) => {
        try {
          const result = await api('statistics', state.parameters({...scope, level: childLevel,
            sort: this.sort, descending: this.descending, limit: 50, offset}));
          if (!expanded || this.version !== version) return;
          let anchor = children[children.length - 1] || row;
          for (const item of result.rows) {
            const child = this.row(item, childLevel, depth + 1, scope, version);
            anchor.after(child); children.push(child); anchor = child;
          }
          if (offset + result.rows.length < result.total_groups) {
            const more = el('tr', {}, el('td', {colspan: columns.length}, el('button', {onclick: () => {
              children = children.filter(item => item !== more); more.remove(); load(offset + result.rows.length);
            }}, 'Load more children…')));
            anchor.after(more); children.push(more);
          }
        } catch (error) { showError(error); }
      };
      await load();
    }}, `${level === 'callback' ? '' : '▸ '}${title}`);
    identity.append(button);
    if (level === 'callback') identity.append(el('small', {title: data.name}, data.callback_topic ? 'Subscription' : data.node_name || 'Unknown node'));
    row.append(identity);
    // Children retain the root denominator so all three hierarchy levels are
    // directly comparable, even when the server query is scoped to a parent.
    const share = this.total ? Number(data.total_ns) / this.total * 100 : 0;
    for (const [key] of columns.slice(1)) {
      const value = key === 'share_percent' ? share : data[key];
      const text = key === 'count' ? value.toLocaleString() : key.endsWith('percent')
        ? (value == null ? '—' : `${value.toFixed(2)}%`) : duration(value);
      const cell = el('td', {}, text);
      if (key === 'share_percent') {
        const fill = el('div',{class:'share-fill'}); fill.style.width = `${Math.min(100,share)}%`;
        cell.className='share-cell'; cell.replaceChildren(el('div',{class:'share-inner'},el('div',{class:'share-track'},fill),el('span',{},text)));
      }
      row.append(cell);
    }
    // Remove nested expansions when a parent is collapsed or replaced.
    const observer = new MutationObserver(() => {
      if (!row.isConnected) { children.forEach(child => child.remove()); observer.disconnect(); }
    });
    observer.observe($('stats-table').tBodies[0], {childList:true});
    return row;
  }
  renderCharts(data) {
    this.histogram(data.histogram); this.overTime(data.over_time);
    const top = $('top-callbacks'); top.replaceChildren();
    const maximum = Math.max(1,...data.top.map(row => row.total_ns));
    for (const row of data.top) {
      const fill = el('span'); fill.style.width = `${row.total_ns / maximum * 100}%`;
      top.append(el('div', {class: 'top-row'}, el('button', {title:row.name, onclick:() => this.onSelect({...row,share_percent:this.total?Number(row.total_ns)/this.total*100:0})}, callbackLabel(row)),
        el('div', {class:'bar'}, fill), el('span', {}, duration(row.total_ns))));
    }
    if (!data.top.length) top.textContent = 'No executions in this selection.';
  }
  chart(id) {
    const canvas = $(id), width = canvas.parentElement.clientWidth - 40, height = 180;
    const ctx = canvasSize(canvas, Math.max(100,width), height);
    ctx.fillStyle = '#8c9bb0'; ctx.font = '9px system-ui'; return {canvas,ctx,width,height};
  }
  histogram(rows) {
    const {canvas,ctx,width,height} = this.chart('histogram'), left = 37, right = width - 8, bottom = height - 25;
    const maximum = Math.max(1,...rows.map(row => row.count)), bins = 64;
    ctx.fillStyle = '#8c9bb0'; ctx.fillText(maximum.toLocaleString(),0,12);
    ctx.fillText('0',20,bottom);
    ctx.strokeStyle = '#314159'; ctx.beginPath(); ctx.moveTo(left,0);ctx.lineTo(left,bottom);ctx.lineTo(right,bottom);ctx.stroke();
    for (const row of rows) {
      const x = left + row.bin / bins * (right-left), w = (right-left) / bins, h = row.count / maximum * (bottom-10);
      ctx.fillStyle = '#57d4bc'; ctx.fillRect(x,bottom-h,Math.max(1,w-1),h);
    }
    if (rows.length) {
      ctx.fillStyle = '#8c9bb0'; ctx.fillText(duration(rows[0].start_ns),left,height-6);
      ctx.textAlign = 'right';ctx.fillText(duration(rows[rows.length - 1].end_ns),right,height-6);ctx.textAlign = 'left';
    }
    canvas.onmousemove = event => {
      const x = event.clientX - canvas.getBoundingClientRect().left;
      const index = Math.floor((x-left)/(right-left)*bins), row = rows.find(row => row.bin === index);
      canvas.title = row ? `${row.count.toLocaleString()} executions · ${duration(row.start_ns)} to ${duration(row.end_ns)}` : '';
    };
    canvas.onclick = null;
  }
  overTime(rows) {
    const {canvas,ctx,width,height} = this.chart('duration-chart'), left = 52, right = width-10, bottom = height-25;
    const max = Math.max(1,...rows.map(row => row.max_ns));
    ctx.fillText(duration(max),0,12);ctx.fillText('0',30,bottom);
    const start = Number(state.filters.start_time || 0), end = Number(state.filters.end_time || state.info.duration_seconds);
    for (const [key,stroke] of [['max_ns','#81a9fa'],['mean_ns','#57d4bc']]) {
      ctx.strokeStyle = stroke;ctx.beginPath();
      rows.forEach((row,index) => {
        const x = left + (seconds(row.start_ns)-start)/Math.max(1e-9,end-start)*(right-left), y = bottom-row[key]/max*(bottom-10);
        if(index)ctx.lineTo(x,y);else ctx.moveTo(x,y);
      });ctx.stroke();
    }
    ctx.fillStyle = '#8c9bb0';ctx.fillText(`${start.toFixed(3)} s`,left,height-6);ctx.textAlign='right';ctx.fillText(`${end.toFixed(3)} s`,right,height-6);ctx.textAlign='left';
    canvas.title = 'Green: mean duration. Blue: maximum duration. Time buckets contain fully selected executions.';
  }
}
