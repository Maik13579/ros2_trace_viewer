/** Executed timer callbacks, using the same normalized statistics as other views. */
import {$, el, api, state, duration, callbackLabel, callbackType, showError} from './shared.js';

export class Timers {
  constructor(onSelect){
    this.onSelect=onSelect;this.offset=0;this.limit=50;this.version=0;this.selected=null;
    $('timers-sort').onchange=()=>{this.offset=0;this.refresh();};
    $('timers-prev').onclick=()=>{this.offset=Math.max(0,this.offset-this.limit);this.refresh();};
    $('timers-next').onclick=()=>{this.offset+=this.limit;this.refresh();};
    state.events.addEventListener('filters',()=>{
      this.offset=0;this.selected=null;if(state.view==='timers')this.refresh();
    });
  }
  async refresh(){
    if(state.view!=='timers')return;
    const version=++this.version;
    try{
      const sort=$('timers-sort').value;
      const data=await api('timers',state.parameters({sort,descending:sort!=='name',offset:this.offset,limit:this.limit}));
      if(version!==this.version||state.view!=='timers')return;
      $('timers-summary').textContent=`${data.total_groups.toLocaleString()} timers · ${data.execution_count.toLocaleString()} executions`;
      const body=$('timers-table').tBodies[0];body.replaceChildren();
      for(const record of data.rows){
        const row=el('tr'),button=el('button',{title:record.name,onclick:()=>{
          this.selected=record.id;
          body.querySelectorAll('tr').forEach(item=>item.classList.toggle('selected',item.dataset.callbackId===record.id));
          this.onSelect(record);
        }},callbackLabel(record));
        row.dataset.callbackId=record.id;row.dataset.callbackType=record.type;
        row.classList.toggle('selected',this.selected===record.id);
        row.append(el('td',{},button,el('small',{class:'callback-type'},callbackType(record))),
          el('td',{title:record.timer_period_ns==null?'Not recorded':`${record.timer_period_ns} ns at initialization`},record.timer_period_ns==null?'—':duration(record.timer_period_ns)),
          el('td',{},record.count.toLocaleString()),el('td',{},duration(record.mean_ns)),el('td',{},duration(record.total_ns)));
        body.append(row);
      }
      if(!data.rows.length)body.append(el('tr',{},el('td',{colspan:5},'No timers in this selection.')));
      $('timers-page').textContent=`${data.total_groups?this.offset+1:0}–${Math.min(this.offset+this.limit,data.total_groups)} / ${data.total_groups}`;
      $('timers-prev').disabled=this.offset===0;
      $('timers-next').disabled=this.offset+this.limit>=data.total_groups;
    }catch(error){if(version===this.version)showError(error);}
  }
}
