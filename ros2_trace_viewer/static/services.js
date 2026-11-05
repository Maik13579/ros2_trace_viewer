/** Service metadata and recorded server callback durations. */
import {$, el, api, state, duration, showError} from './shared.js';

export class Services {
  constructor(onSelect) {
    this.onSelect=onSelect;this.offset=0;this.limit=50;this.version=0;this.selected=null;
    $('services-prev').onclick=()=>{this.offset=Math.max(0,this.offset-this.limit);this.refresh();};
    $('services-next').onclick=()=>{this.offset+=this.limit;this.refresh();};
    state.events.addEventListener('filters',()=>{
      this.offset=0;this.selected=null;if(state.view==='services')this.refresh();
    });
  }
  async refresh() {
    if(state.view!=='services')return;
    const version=++this.version;
    try {
      const data=await api('services',state.parameters({offset:this.offset,limit:this.limit}));
      if(version!==this.version||state.view!=='services')return;
      $('services-summary').textContent=`${data.total.toLocaleString()} services`;
      const body=$('services-table').tBodies[0];body.replaceChildren();
      for(const record of data.items){
        const row=el('tr'),button=el('button',{title:record.service,onclick:()=>{
          this.selected=record.service;
          body.querySelectorAll('tr').forEach(item=>item.classList.toggle('selected',item.dataset.service===record.service));
          this.onSelect(record.service);
        }},record.service);
        row.dataset.service=record.service;row.classList.toggle('selected',this.selected===record.service);
        row.append(el('td',{},button),el('td',{},record.execution_count.toLocaleString()),
          el('td',{},record.mean_duration_ns==null?'—':duration(record.mean_duration_ns)),
          el('td',{},duration(record.total_callback_ns)));
        for(const key of ['server_nodes','client_nodes']){
          const names=record[key].join(', ');
          row.append(el('td',{class:'topic-owners',title:names},names||'—'));
        }
        body.append(row);
      }
      if(!data.items.length)body.append(el('tr',{},el('td',{colspan:6},'No services in this selection.')));
      $('services-page').textContent=`${data.total?this.offset+1:0}–${Math.min(this.offset+this.limit,data.total)} / ${data.total}`;
      $('services-prev').disabled=this.offset===0;
      $('services-next').disabled=this.offset+this.limit>=data.total;
    }catch(error){if(version===this.version)showError(error);}
  }
}
