import {$, el, api, state, duration, seconds, color, canvasSize, debounce, showError, callbackLabel, publicationLabel} from './shared.js';

/** Small binary heap for interval partitioning, O(n log n) even under overlap. */
class Heap {
  constructor(compare) { this.values = []; this.compare = compare; }
  peek() { return this.values[0]; }
  push(value) {
    const a = this.values; a.push(value); let i = a.length - 1;
    while (i && this.compare(a[i], a[(i - 1) >> 1]) < 0) {
      const p = (i - 1) >> 1; [a[i], a[p]] = [a[p], a[i]]; i = p;
    }
  }
  pop() {
    const a = this.values, result = a[0], last = a.pop();
    if (!a.length) return result;
    a[0] = last; let i = 0;
    while (true) {
      let j = i, left = 2 * i + 1, right = left + 1;
      if (left < a.length && this.compare(a[left], a[j]) < 0) j = left;
      if (right < a.length && this.compare(a[right], a[j]) < 0) j = right;
      if (i === j) break;
      [a[i], a[j]] = [a[j], a[i]]; i = j;
    }
    return result;
  }
}

export class Timeline {
  constructor(onSelect, onPublish) {
    this.onSelect = onSelect; this.onPublish=onPublish; this.canvas = $('timeline'); this.scroll = $('timeline-scroll');
    this.start = 0; this.span = 1; this.laneOffset = 0; this.data = null; this.hits = [];
    this.refreshSoon = debounce(() => this.refresh(), 70);
    this.scroll.addEventListener('scroll', () => {
      if (Math.abs(this.scroll.scrollTop - this.expectedScroll) < .5) return;
      const max = this.scroll.scrollHeight - this.height;
      const range=this.bounds();
      this.start = max > 0 ? range.start + (range.duration - this.span) * this.scroll.scrollTop / max : range.start;
      this.expectedScroll = this.scroll.scrollTop; this.draw(); this.refreshSoon();
    });
    this.canvas.addEventListener('wheel', event => {
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        const fraction = (event.clientY - this.canvas.getBoundingClientRect().top) / this.height;
        this.zoom(event.deltaY > 0 ? 1.3 : 1 / 1.3, fraction);
      } else this.setViewport(this.start + event.deltaY * this.span / this.height, this.span);
    }, {passive: false});
    this.canvas.addEventListener('mousemove', event => this.hover(event));
    this.canvas.addEventListener('mouseleave', () => { $('tooltip').hidden = true; });
    this.canvas.addEventListener('click', event => {
      const hit = this.hit(event);
      if(hit)state.selectedLane=hit.record?.lane??hit.publication?.lane;
      if (hit?.publication?.id) this.onPublish(hit.publication.id);
      else if (hit?.record) this.onSelect(hit.record.id);

    });
    $('zoom-in').onclick = () => this.zoom(.5);
    $('zoom-out').onclick = () => this.zoom(2);
    $('fit').onclick = () => {const range=this.bounds();this.setViewport(range.start,range.duration);};
    $('reset-zoom').onclick = () => {
      const selected=state.selected;
      if(selected){const start=seconds(selected.start_ns),end=seconds(selected.end_ns);const span=Math.min(this.bounds().duration,Math.max(1e-8,(end-start)*1.4));this.setViewport((start+end-span)/2,span);}
      else this.setViewport(this.start,Math.min(this.bounds().duration,.1));
    };
    $('jump').onclick = () => this.jump(Number($('jump-time').value));
    $('jump-time').onkeydown = event => { if (event.key === 'Enter') $('jump').click(); };
    $('lanes-prev').onclick = () => { this.laneOffset = Math.max(0, this.laneOffset - this.laneLimit); this.refresh(); };
    $('lanes-next').onclick = () => { this.laneOffset += this.laneLimit; this.refresh(); };
    state.events.addEventListener('filters', () => {
      state.selected=null;state.selectedPublish=null;
      this.laneOffset = 0;
      const range=this.bounds(),key=`${range.start}:${range.end}`;
      if(key!==this.rangeKey){this.rangeKey=key;this.setViewport(range.start,range.duration);}
      else this.refresh();
      this.overview();
    });
    new ResizeObserver(debounce(() => this.resize(), 80)).observe($('timeline-panel'));
    const overview = $('overview');
    overview.addEventListener('pointerdown', event => { overview.setPointerCapture(event.pointerId); this.overviewJump(event); });
    overview.addEventListener('pointermove', event => { if (overview.hasPointerCapture(event.pointerId)) this.overviewJump(event); });
    overview.addEventListener('pointerup', event => overview.releasePointerCapture(event.pointerId));
  }
  bounds() {
    const start=Math.min(state.info.duration_seconds,Number(state.filters.start_time||0));
    const end=Math.min(state.info.duration_seconds,Number(state.filters.end_time||state.info.duration_seconds));
    return {start,end,duration:Math.max(1e-12,end-start)};
  }
  async init() {
    this.span = Math.max(1e-8, Math.min(state.info.duration_seconds, Math.max(.1,Math.min(5,50*state.info.duration_seconds/state.info.known_execution_count))));
    try { const first = await api('locate'); if (first) this.start = Math.max(0, seconds(first.start_ns) - this.span * .1); } catch(error) { showError(error); }
    const range=this.bounds();this.rangeKey=`${range.start}:${range.end}`;
    this.resize(); this.overview();
  }
  resize() {
    if (!state.info || state.view !== 'timeline') return;
    this.width = this.scroll.clientWidth; this.height = this.scroll.clientHeight;
    if (!this.width || !this.height) return;
    this.ctx = canvasSize(this.canvas, this.width, this.height);
    const previousLimit=this.laneLimit;
    const selected=this.data?.records?.find(record=>record.id===state.selected?.id&&(!state.selectedLane||record.lane===state.selectedLane));
    const selectedIndex=selected?this.data.lanes.findIndex(lane=>lane.id===selected.lane):-1;
    const absoluteLane=this.laneOffset+selectedIndex;
    this.laneLimit = Math.max(1, Math.min(12, Math.floor((this.width - 86) / 150)));
    if(selectedIndex>=0 && previousLimit!==this.laneLimit)this.laneOffset=Math.floor(absoluteLane/this.laneLimit)*this.laneLimit;
    this.syncScroll(); this.draw(); this.refreshSoon(); this.drawOverview();
  }
  syncScroll() {
    const range=this.bounds(),total=range.duration,height=this.height;
    const extent = Math.min(4000000, Math.max(height, total / this.span * height));
    $('spacer').style.height = `${Math.max(0, extent - height)}px`;
    this.scroll.scrollTop = total <= this.span ? 0 : (this.start-range.start) / (total - this.span) * (extent - height);
    this.expectedScroll = this.scroll.scrollTop;
  }
  setViewport(start, span) {
    const range=this.bounds(),total=range.duration;
    this.span = Math.max(Math.min(1e-8,total), Math.min(total, span));
    this.start = Math.max(range.start, Math.min(range.end - this.span, start));
    this.syncScroll(); this.draw(); this.refreshSoon();
  }
  zoom(factor, anchor = .5) {
    const selected=state.selected,range=this.bounds();
    const span=Math.min(range.duration,Math.max(Math.min(1e-8,range.duration),this.span*factor));
    const center=selected?(seconds(selected.start_ns)+seconds(selected.end_ns))/2:null;
    if(center!=null && center>=range.start && center<=range.end)this.setViewport(center-span/2,span);
    else this.setViewport(this.start+this.span*anchor*(1-factor),span);
  }
  jump(time) { if (Number.isFinite(time)) this.setViewport(time, this.span); }
  async refresh() {
    if (!state.info || state.view !== 'timeline' || !this.height) return;
    this.controller?.abort(); this.controller = new AbortController();
    try {
      const data = await api('timeline', state.parameters({grouping: state.grouping,
        view_start: this.start.toFixed(9), view_end: (this.start + this.span).toFixed(9),
        lane_offset: this.laneOffset, lane_limit: this.laneLimit,
        budget: Math.min(12000, Math.max(1000, Math.round(this.height * this.laneLimit)))}), this.controller.signal);
      this.data = data;
      $('publish-legend').hidden=state.grouping==='service'||!state.info.capabilities.publish_markers;
      $('publish-legend').textContent=state.grouping==='topic'?'◆ Publish · ▬ Callback':'─ Publish (zoom in)';
      $('lane-head').replaceChildren(...data.lanes.map(lane => el('div', {title: lane.label}, state.grouping === 'node' ? lane.label.replace(/ \[\d+\]$/, '') : lane.label)));
      $('lane-page').textContent = data.total_lanes ? `${this.laneOffset + 1}–${this.laneOffset + data.lanes.length} / ${data.total_lanes}` : 'No columns';
      $('lanes-prev').disabled = this.laneOffset === 0;
      $('lanes-next').disabled = this.laneOffset + data.lanes.length >= data.total_lanes;
      $('timeline-message').textContent = data.mode === 'density' ? 'Overview · Zoom in to see individual callbacks' : 'One bar = one callback · Time ↓';
      this.draw();
    } catch (error) { if (error.name !== 'AbortError') showError(error); }
  }
  draw() {
    if (!this.ctx || !state.info) return;
    const ctx = this.ctx, w = this.width, h = this.height, axis = 90;
    ctx.clearRect(0, 0, w, h); ctx.fillStyle = '#10151e'; ctx.fillRect(0, 0, w, h);
    this.hits = []; const selectedHits=[],publishHits=[];this.barRects=[];
    const target = this.span / Math.max(2, h / 65), power = 10 ** Math.floor(Math.log10(target));
    const step = [1, 2, 5, 10].find(n => n * power >= target) * power;
    ctx.font = '12px system-ui';
    for (let t = Math.ceil(this.start / step) * step; t <= this.start + this.span; t += step) {
      const y = (t - this.start) / this.span * h;
      ctx.strokeStyle = '#26303e'; ctx.beginPath(); ctx.moveTo(axis, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.fillStyle = '#899bb5'; ctx.fillText(`${t.toFixed(Math.min(9, Math.max(0, -Math.floor(Math.log10(step)))))} s`, 7, y + 4);
    }
    const data = this.data;
    if (data?.lanes.length) {
      const laneWidth = (w - axis) / data.lanes.length;
      data.lanes.forEach((lane, index) => {
        const x = axis + index * laneWidth;
        ctx.strokeStyle = '#29364a'; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
        if (data.mode === 'density') {
          const cells = data.density.filter(d => d.lane === lane.id), max = Math.max(1, ...cells.map(c => c.count));
          for (const cell of cells) {
            const start = seconds(data.start_ns) + cell.bin * data.step_ns / 1e9;
            const y = (start - this.start) / this.span * h, height = data.step_ns / 1e9 / this.span * h;
            ctx.fillStyle = `rgba(87,212,188,${.18 + .8 * Math.log1p(cell.count) / Math.log1p(max)})`;
            ctx.fillRect(x + 4, y, laneWidth - 8, Math.max(1, height));
            this.hits.push({x: x + 4, y, width: laneWidth - 8, height: Math.max(1, height), density: true, start, count: cell.count});
          }
          return;
        }
        const records = data.records.filter(r => r.lane === lane.id);
        const active = new Heap((a,b) => a.end - b.end), free = new Heap((a,b) => a - b);
        let slots = 0;
        const positioned = records.map(record => {
          const start = seconds(record.segment_start_ns ?? record.start_ns), end = seconds(record.segment_end_ns ?? record.end_ns);
          while (active.peek() && active.peek().end <= start) free.push(active.pop().slot);
          const slot = free.peek() === undefined ? slots++ : free.pop(); active.push({end, slot});
          return {record, start, end, slot};
        });
        const gutter=state.grouping==='topic'?20:0;
        const slotWidth = (laneWidth - 12-gutter) / Math.max(slots, 1);
        for (const item of positioned) {
          const {record, start, end, slot} = item;
          const y = Math.max(0, (start - this.start) / this.span * h);
          const bottom = Math.min(h, (end - this.start) / this.span * h);
          if (bottom < 0 || y > h) continue;
          const height = Math.max(3, bottom - y), width = Math.max(.5, Math.min(100, slotWidth - 4));
          const rx = x + 6+gutter + slot * slotWidth + (slotWidth - width) / 2;
          ctx.fillStyle = color(record.callback_id); ctx.globalAlpha = .85;
          ctx.fillRect(rx, y, width, height); ctx.globalAlpha = 1;
          if (state.selected?.id === record.id) selectedHits.push({x:rx,y,width,height,record});
          const rect={x:rx,y,width,height,record,laneRight:x+laneWidth};
          this.hits.push(rect);this.barRects.push(rect);
        }
      });
    } else { ctx.fillStyle = '#8c9bb0'; ctx.fillText('No callback executions match this selection.', axis + 15, 45); }
    for(const r of selectedHits){
      const hh=Math.max(8,r.height),yy=r.y-(hh-r.height)/2;
      ctx.save();ctx.shadowColor='#ffd877';ctx.shadowBlur=12;ctx.fillStyle='#ffd877';
      ctx.fillRect(r.x,r.y,r.width,Math.max(3,r.height));ctx.shadowBlur=0;
      ctx.strokeStyle='#fff3d2';ctx.lineWidth=2;ctx.strokeRect(r.x-5,yy-3,r.width+10,hh+6);
      ctx.fillStyle='#ffd877';ctx.beginPath();ctx.moveTo(r.x-18,yy+hh/2-6);ctx.lineTo(r.x-9,yy+hh/2);ctx.lineTo(r.x-18,yy+hh/2+6);ctx.fill();ctx.restore();
      this.hits.push(r);
    }
    // Draw publication ticks after selection fill so every marker stays visible.
    for(const rect of this.barRects){
      const record=rect.record,segmentStart=seconds(record.segment_start_ns??record.start_ns),segmentEnd=seconds(record.segment_end_ns??record.end_ns);
      if((segmentEnd-segmentStart)/this.span*h<12)continue;
      for(const publication of record.publishes||[]){
        if(state.grouping==='topic'&&publication.topic!==record.lane)continue;
        const time=seconds(publication.timestamp_ns);
        if(time<segmentStart||time>segmentEnd||time<this.start||time>this.start+this.span)continue;
        const py=Math.max(rect.y,Math.min(rect.y+rect.height,(time-this.start)/this.span*h));
        ctx.save();ctx.strokeStyle=state.selectedPublish===publication.id?'#ffbd58':'#f5f9ff';ctx.lineWidth=2;
        ctx.beginPath();ctx.moveTo(rect.x+2,py);ctx.lineTo(rect.x+rect.width-2,py);ctx.stroke();
        ctx.fillStyle=ctx.strokeStyle;ctx.fillRect(rect.x+rect.width-3,py-2,3,4);
        ctx.restore();
        publishHits.push({x:rect.x,y:py-3,width:rect.width,height:6,record,publication});
      }
    }
    // Publish observations live in the topic gutter, separate from subscription bars.
    for(const publication of data?.publications||[]){
      const index=data.lanes.findIndex(lane=>lane.id===publication.lane);
      if(index<0)continue;
      const laneWidth=(w-axis)/data.lanes.length,px=axis+index*laneWidth+12;
      const py=(seconds(publication.timestamp_ns)-this.start)/this.span*h;
      if(py<0||py>h)continue;
      ctx.save();ctx.fillStyle=state.selectedPublish===publication.id?'#ffbd58':'#a2bfff';
      if(publication.count){
        const bottom=(seconds(publication.end_ns)-this.start)/this.span*h;
        ctx.globalAlpha=.75;ctx.fillRect(px-3,py,6,Math.max(3,bottom-py));
      }else{
        ctx.beginPath();ctx.moveTo(px,py-4);ctx.lineTo(px+4,py);ctx.lineTo(px,py+4);ctx.lineTo(px-4,py);ctx.closePath();ctx.fill();
      }
      ctx.restore();
      publishHits.push({x:px-6,y:py-4,width:12,height:Math.max(8,publication.count?(seconds(publication.end_ns)-seconds(publication.timestamp_ns))/this.span*h:8),publication});
    }
    this.hits.push(...publishHits);this.publishHits=publishHits;
    this.canvas.dataset.publishTicks=String(publishHits.filter(hit=>hit.record).length);
    this.canvas.dataset.publishPoints=String(publishHits.filter(hit=>!hit.record).length);
    $('zoom-label').textContent = duration(this.span * 1e9);
    this.canvas.dataset.startTime=this.start.toFixed(12);this.canvas.dataset.timeSpan=this.span.toFixed(12);
    $('viewport-label').textContent = `${this.start.toFixed(6)} → ${(this.start + this.span).toFixed(6)} s`;
    this.drawOverview();
  }
  hit(event) {
    const rect = this.canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
    return [...this.hits].reverse().find(r => x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height);
  }
  hover(event) {
    const item = this.hit(event), tooltip = $('tooltip');
    tooltip.hidden = !item; if (!item) return;
    const record=item.record;
    const role=record&&state.grouping==='topic'?`${record.topic_subscription?'Subscribes':''}${record.topic_subscription&&record.topic_publish_count?' · ':''}${record.topic_publish_count?`Publishes (${record.topic_publish_count})`:''} · ${record.lane}`:record?.publish_count?`${record.publish_count} publishes`:'';
    const text=item.publication?[item.publication.count?item.publication.topic:publicationLabel(item.publication),item.publication.count?`${item.publication.count} publishes`:'Publish',`${seconds(item.publication.timestamp_ns).toFixed(9)} s`]:record?[callbackLabel(record),duration(record.duration_ns),`${record.node_name||'Unknown node'}${role?' · '+role:''}`]:[`${item.count.toLocaleString()} callbacks`,'Use + to zoom in'];
    tooltip.replaceChildren(...text.map((t,index) => el(index === 2 ? 'small' : 'div', {}, t)));
    tooltip.style.left = `${Math.max(5, Math.min(window.innerWidth - 390, event.clientX + 14))}px`;
    tooltip.style.top = `${Math.min(window.innerHeight - 100, event.clientY + 15)}px`;
  }
  async overview() {
    if (!state.info) return;
    const version = this.overviewVersion = (this.overviewVersion || 0) + 1;
    try { const result = await api('charts', state.parameters()); if (version === this.overviewVersion) { this.overviewData = result.over_time; this.drawOverview(); } }
    catch (error) { showError(error); }
  }
  drawOverview() {
    const canvas = $('overview'), box = canvas.getBoundingClientRect();
    if (!box.width || !box.height || !state.info) return;
    const ctx = canvasSize(canvas, box.width, box.height),range=this.bounds(),total=range.duration;
    ctx.fillStyle = '#111d2b'; ctx.fillRect(0,0,box.width,box.height);
    const maximum = Math.max(1,...(this.overviewData || []).map(r => r.count));
    ctx.fillStyle = '#3c887f';
    for (const row of this.overviewData || []) ctx.fillRect(0,(seconds(row.start_ns)-range.start) / total * box.height, row.count / maximum * box.width, Math.max(2,box.height / 64));
    const y = (this.start-range.start) / total * box.height, height = Math.max(4, this.span / total * box.height);
    ctx.fillStyle = '#81a9fa33'; ctx.fillRect(0,y,box.width,height); ctx.strokeStyle = '#81a9fa'; ctx.strokeRect(.5,y,box.width - 1,height);
  }
  overviewJump(event) {
    const rect = $('overview').getBoundingClientRect();
    const range=this.bounds();
    this.setViewport(range.start+(event.clientY-rect.top)/rect.height*range.duration-this.span/2,this.span);
  }
}
