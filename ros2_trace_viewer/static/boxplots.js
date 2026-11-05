/** Paged callback duration distributions. Selection never changes filters. */
import {$, el, api, state, duration, color, canvasSize, callbackLabel, debounce, showError} from './shared.js';

export class BoxPlots {
  constructor(onSelect) {
    this.onSelect = onSelect;
    this.offset = 0;
    this.limit = 25;
    this.selected = null;
    this.version = 0;this.zoomFactor=1;this.pan=0;
    $('boxplot-sort').onchange = () => { this.offset = 0; this.refresh(); };
    $('boxplot-scale').onchange = () => {this.pan=0;this.draw();};
    $('boxplot-zoom-in').onclick=()=>this.zoom(1.5);
    $('boxplot-zoom-out').onclick=()=>this.zoom(1/1.5);
    $('boxplot-fit').onclick=()=>{this.zoomFactor=1;this.pan=0;this.draw();};
    $('boxplot-pan').oninput=()=>{this.pan=Number($('boxplot-pan').value)/1000;this.draw();};
    document.querySelector('#boxplots-view .boxplot-scroll').addEventListener('wheel',event=>{
      if(event.ctrlKey||event.metaKey){event.preventDefault();this.zoom(event.deltaY>0?1/1.25:1.25);}
    },{passive:false});
    $('boxplot-prev').onclick = () => {
      this.offset = Math.max(0, this.offset - this.limit); this.refresh();
    };
    $('boxplot-next').onclick = () => { this.offset += this.limit; this.refresh(); };
    state.events.addEventListener('filters', () => {
      this.offset = 0;
      this.selected = null;this.zoomFactor=1;this.pan=0;
      if (state.view === 'boxplots') this.refresh();
    });
    new ResizeObserver(debounce(() => this.draw(), 80)).observe($('boxplots-view'));
  }

  async refresh() {
    if (state.view !== 'boxplots') return;
    const version = ++this.version;
    try {
      const data = await api('boxplots', state.parameters({
        sort: $('boxplot-sort').value,
        descending: $('boxplot-sort').value !== 'name',
        offset: this.offset, limit: this.limit,
      }));
      if (version !== this.version || state.view !== 'boxplots') return;
      this.data = data;
      const crossings = data.crossing_count ? ` · ${data.crossing_count.toLocaleString()} boundary crossings excluded` : '';
      $('boxplot-summary').textContent = `${data.total_groups.toLocaleString()} callbacks · ${data.execution_count.toLocaleString()} executions${crossings}`;
      $('boxplot-page').textContent = `${data.total_groups ? this.offset + 1 : 0}–${Math.min(this.offset + this.limit, data.total_groups)} / ${data.total_groups}`;
      $('boxplot-prev').disabled = this.offset === 0;
      $('boxplot-next').disabled = this.offset + data.rows.length >= data.total_groups;
      $('boxplot-empty').hidden = data.rows.length !== 0;
      $('boxplot-axis-row').hidden = data.rows.length === 0;
      const target = $('boxplot-rows'); target.replaceChildren();
      this.plots = data.rows.map(record => {
        const title = callbackLabel(record);
        const button = el('button', {title: `${record.name} · ${record.id}`}, title);
        const canvas = el('canvas', {role: 'img', 'aria-label':
          `${title}: ${record.count} executions, median ${duration(record.median_ns)}`});
        const row = el('div', {class: 'boxplot-row'}, el('div', {class: 'boxplot-label'},
          button, el('small', {}, `${record.callback_topic ? 'Subscription' : record.node_name || 'Unknown node'} · n=${record.count.toLocaleString()}`)), canvas);
        row.dataset.callbackId = record.id;
        const select = () => {
          this.selected = record.id;
          target.querySelectorAll('.boxplot-row').forEach(item => {
            item.classList.toggle('selected', item.dataset.callbackId === record.id);
          });
          this.draw();
          this.onSelect(record);
        };
        button.onclick = select;
        let dragged=false,drag=null;
        canvas.onclick=()=>{if(!dragged)select();};
        canvas.onpointerdown=event=>{
          dragged=false;
          if(this.zoomFactor>1){drag={x:event.clientX,pan:this.pan};canvas.setPointerCapture(event.pointerId);}
        };
        canvas.onpointermove=event=>{
          if(!drag||!canvas.hasPointerCapture(event.pointerId))return;
          const delta=event.clientX-drag.x;if(Math.abs(delta)>4)dragged=true;
          if(dragged){const extra=Math.max(1,(canvas.clientWidth-56)*(this.zoomFactor-1));this.pan=Math.max(0,Math.min(1,drag.pan-delta/extra));this.draw();}
        };
        canvas.onpointerup=event=>{drag=null;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);setTimeout(()=>{dragged=false;},0);};
        const sampled = record.outliers_sampled ? ` (${record.outliers_ns.length} shown)` : '';
        canvas.title = `Median ${duration(record.median_ns)}\nQ1–Q3 ${duration(record.q1_ns)}–${duration(record.q3_ns)}\nWhiskers ${duration(record.lower_whisker_ns)}–${duration(record.upper_whisker_ns)}\n${record.outlier_count} outliers${sampled}`;
        row.classList.toggle('selected', record.id === this.selected);
        target.append(row);
        return {record, canvas};
      });
      this.draw();
    } catch (error) { if (version === this.version) showError(error); }
  }

  zoom(factor) {
    const before=this.zoomFactor,after=Math.max(1,Math.min(64,before*factor));
    const selected=this.data?.rows.find(row=>row.id===this.selected);
    let center=(this.pan*(before-1)+.5)/before;
    if(selected){const scale=this.scale();center=scale.transform(selected.median_ns)/scale.transform(scale.maximum);}
    this.zoomFactor=after;
    this.pan=after===1?0:Math.max(0,Math.min(1,(center*after-.5)/(after-1)));
    this.draw();
  }

  scale() {
    const maximum = Math.max(1, Number(this.data.axis_max_ns));
    if ($('boxplot-scale').value === 'log') {
      const end = Math.ceil(Math.log10(maximum + 1));
      const ticks = [0];
      for (let exponent = 0; exponent <= end; exponent++){
        ticks.push(10**exponent);
        if(this.zoomFactor>2 && exponent<end)ticks.push(2*10**exponent,5*10**exponent);
      }
      ticks.sort((a,b)=>a-b);
      return {
        maximum: ticks[ticks.length - 1], ticks,
        transform: value => Math.log1p(Number(value)),
      };
    }
    const power = 10 ** Math.floor(Math.log10(maximum / 4));
    const step = [1, 2, 5, 10].find(value => value * power >= maximum / 4) * power;
    const end = Math.ceil(maximum / step) * step;
    const ticks=[];
    const target=step/this.zoomFactor,finePower=10**Math.floor(Math.log10(target));
    const fineStep=[1,2,5,10].find(n=>n*finePower>=target)*finePower;
    for(let value=0;value<=end+fineStep/2;value+=fineStep)ticks.push(value);
    return {maximum: end, ticks, transform: Number};
  }

  draw() {
    if(state.view!=='boxplots')return;
    $('boxplot-zoom-label').textContent=`${Math.round(this.zoomFactor*100)}%`;
    $('boxplot-pan').hidden=this.zoomFactor===1;
    $('boxplot-pan').value=String(Math.round(this.pan*1000));
    $('boxplot-zoom-out').disabled=this.zoomFactor<=1;
    $('boxplot-zoom-in').disabled=this.zoomFactor>=64;
    if (!this.data || !this.data.rows.length) return;
    const scale = this.scale();
    const coordinates = canvas => {
      const width = canvas.parentElement.getBoundingClientRect().width -
        canvas.parentElement.firstElementChild.getBoundingClientRect().width;
      const ctx = canvasSize(canvas, Math.max(1, width), canvas.id === 'boxplot-axis' ? 38 : 64);
      const left = 14, right = Math.max(left + 1, width - 42);
      const x = value => left + scale.transform(value) / scale.transform(scale.maximum) * (right-left)*this.zoomFactor-this.pan*(right-left)*(this.zoomFactor-1);
      return {ctx, width, x};
    };
    const axis = coordinates($('boxplot-axis'));
    axis.ctx.font = '11px system-ui';
    axis.ctx.fillStyle = '#8e9aab';
    axis.ctx.strokeStyle = '#364252';
    for (const tick of scale.ticks) {
      const x = axis.x(tick);if(x<4||x>axis.width-14)continue;
      axis.ctx.beginPath(); axis.ctx.moveTo(x, 24); axis.ctx.lineTo(x, 33); axis.ctx.stroke();
      axis.ctx.textAlign = tick === 0 ? 'left' : 'center';
      axis.ctx.fillText(tick < 1000 ? `${Number(tick.toPrecision(6))} ns` : duration(tick), x, 16);
    }
    for (const {record, canvas} of this.plots) {
      const {ctx, width, x} = coordinates(canvas), center = 32;
      ctx.strokeStyle = '#25303e'; ctx.lineWidth = 1;
      for (const tick of scale.ticks) {
        ctx.beginPath(); ctx.moveTo(x(tick), 0); ctx.lineTo(x(tick), 64); ctx.stroke();
      }
      const accent = record.id === this.selected ? '#ffd877' : color(record.id);
      const lo = x(record.lower_whisker_ns), hi = x(record.upper_whisker_ns);
      const q1 = x(record.q1_ns), q3 = x(record.q3_ns), median = x(record.median_ns);
      ctx.strokeStyle = accent; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(lo, center); ctx.lineTo(hi, center);
      ctx.moveTo(lo, center - 8); ctx.lineTo(lo, center + 8);
      ctx.moveTo(hi, center - 8); ctx.lineTo(hi, center + 8); ctx.stroke();
      ctx.fillStyle = '#171e29'; ctx.fillRect(q1, center - 12, Math.max(1, q3 - q1), 24);
      ctx.globalAlpha = .3; ctx.fillStyle = accent;
      ctx.fillRect(q1, center - 12, Math.max(1, q3 - q1), 24); ctx.globalAlpha = 1;
      ctx.strokeRect(q1, center - 12, Math.max(1, q3 - q1), 24);
      ctx.strokeStyle = '#eef4fb'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(median, center - 12); ctx.lineTo(median, center + 12); ctx.stroke();
      ctx.fillStyle = accent;
      for (const value of record.outliers_ns) {
        ctx.beginPath(); ctx.arc(x(value), center, 2.8, 0, Math.PI * 2); ctx.fill();
      }
      if (record.id === this.selected) {
        ctx.strokeStyle = '#ffd877'; ctx.lineWidth = 2;
        ctx.strokeRect(2, 2, Math.max(0, width - 4), 60);
      }
    }
  }
}
