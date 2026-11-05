/** Recorded topic activity and same-topic endpoints; no delivery inference. */
import {$, el, api, state, showError} from './shared.js';

export class Topics {
  constructor(onSelect) {
    this.onSelect=onSelect;this.offset=0;this.limit=50;this.version=0;this.selected=null;
    $('topics-prev').onclick=()=>{this.offset=Math.max(0,this.offset-this.limit);this.refresh();};
    $('topics-next').onclick=()=>{this.offset+=this.limit;this.refresh();};
    state.events.addEventListener('filters',()=>{
      this.offset=0;this.selected=null;if(state.view==='topics')this.refresh();
    });
  }
  async refresh() {
    if(state.view!=='topics')return;
    const version=++this.version;
    try {
      const data=await api('topics',state.parameters({offset:this.offset,limit:this.limit}));
      if(version!==this.version||state.view!=='topics')return;
      $('topics-summary').textContent=`${data.total.toLocaleString()} topics`;
      const body=$('topics-table').tBodies[0];body.replaceChildren();
      for(const record of data.items){
        const row=el('tr'),button=el('button',{title:record.topic,onclick:()=>{
          this.selected=record.topic;
          body.querySelectorAll('tr').forEach(item=>item.classList.toggle('selected',item.dataset.topic===record.topic));
          this.onSelect(record.topic);
        }},record.topic);
        row.dataset.topic=record.topic;row.classList.toggle('selected',this.selected===record.topic);
        row.append(el('td',{},button),el('td',{},record.publish_count.toLocaleString()),
          el('td',{title:record.receive_count==null?'Receives cannot be assigned to the selected callbacks.':'Recorded rmw_take events with taken=true; not a unique-message or delivery count.'},record.receive_count==null?'—':record.receive_count.toLocaleString()),
          el('td',{},record.publish_rate_hz==null?'—':`${record.publish_rate_hz.toFixed(2)} Hz`));
        for(const [key,count] of [['publisher_nodes','publisher_count'],['subscriber_nodes','subscription_count']]){
          const names=record[key].join(', ');
          row.append(el('td',{class:'topic-owners',title:`${names}\n${record[count]} recorded endpoints. ${data.association}`},names||'—'));
        }
        body.append(row);
      }
      if(!data.items.length)body.append(el('tr',{},el('td',{colspan:6},'No topics in this selection.')));
      $('topics-page').textContent=`${data.total?this.offset+1:0}–${Math.min(this.offset+this.limit,data.total)} / ${data.total}`;
      $('topics-prev').disabled=this.offset===0;
      $('topics-next').disabled=this.offset+this.limit>=data.total;
    }catch(error){if(version===this.version)showError(error);}
  }
}
