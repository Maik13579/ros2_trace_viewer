/** Action transport groups. Goal payloads and action handler spans are unknown. */
import {$, el, api, state, exactSeconds, duration, showError} from './shared.js';

const labels={send_goal:'Send goal',cancel_goal:'Cancel goal',get_result:'Get result',feedback:'Feedback',status:'Status'};

function activity(channel) {
  if(!channel)return '—';
  if(channel.transport==='service')return `${channel.execution_count.toLocaleString()} recorded callbacks`;
  return `${channel.publish_count.toLocaleString()} pub · ${channel.receive_count==null?'—':channel.receive_count.toLocaleString()} recv`;
}

/** Render only accepted channels; opening details never changes filter state. */
export function actionDetails(record,onTopic,onService) {
  const content=el('div',{},el('h3',{},record.action)),dl=el('dl');
  for(const [title,value] of [['Servers',record.server_nodes.join(', ')||'Unknown'],
    ['Clients',record.client_nodes.join(', ')||'Unknown']])
    dl.append(el('div',{},el('dt',{},title),el('dd',{},value)));
  const metadata=el('dl'),fields=[['Recorded channels',`${record.recorded_roles.length} / 5`],
    ['Visible channels',String(record.channels.length)],
    ['Action type',record.action_type||'Not recorded'],
    ['Goal IDs / timing','Not recorded'],
    ['Measured callback executions',record.callback_timing_available?record.execution_count.toLocaleString():'Not recorded'],
    ['Accumulated callback duration',record.callback_timing_available?duration(record.total_callback_ns):'Not recorded'],
    ['First transport activity',record.first_ns==null?'—':exactSeconds(record.first_ns)],
    ['Last transport activity',record.last_ns==null?'—':exactSeconds(record.last_ns)]];
  fields.forEach(([title,value])=>metadata.append(el('div',{},el('dt',{},title),el('dd',{},value))));
  const channels=el('div',{class:'action-channels'});
  for(const channel of record.channels){
    const name=channel[channel.transport];
    channels.append(el('button',{title:name,onclick:()=>channel.transport==='topic'?onTopic(name):onService(name)},
      el('span',{},labels[channel.role]),el('small',{},name),
      el('small',{class:'muted'},activity(channel))));
  }
  content.append(dl,el('h3',{},'Channels'),channels,metadata);
  return content;
}

export class Actions {
  constructor(onSelect) {
    this.onSelect=onSelect;this.offset=0;this.limit=50;this.version=0;this.selected=null;
    $('actions-prev').onclick=()=>{this.offset=Math.max(0,this.offset-this.limit);this.refresh();};
    $('actions-next').onclick=()=>{this.offset+=this.limit;this.refresh();};
    state.events.addEventListener('filters',()=>{
      this.offset=0;this.selected=null;if(state.view==='actions')this.refresh();
    });
  }
  async refresh() {
    if(state.view!=='actions')return;
    const version=++this.version;
    try {
      const data=await api('actions',state.parameters({offset:this.offset,limit:this.limit}));
      if(version!==this.version||state.view!=='actions')return;
      $('actions-summary').textContent=`${data.total.toLocaleString()} ${data.total===1?'action':'actions'}`;
      const body=$('actions-table').tBodies[0];body.replaceChildren();
      for(const record of data.items){
        const row=el('tr'),name=el('button',{title:data.association,onclick:()=>{
          this.selected=record.action;
          body.querySelectorAll('tr').forEach(item=>item.classList.toggle('selected',item.dataset.action===record.action));
          this.onSelect(record.action);
        }},record.action),cell=el('td',{},name);
        if(record.recorded_roles.length<5)cell.append(el('small',{class:'muted'},`${record.recorded_roles.length}/5 channels recorded`));
        row.dataset.action=record.action;row.classList.toggle('selected',this.selected===record.action);row.append(cell);
        for(const role of ['feedback','status'])row.append(el('td',{title:'Observed publish paths and successful take events; not unique messages or proven deliveries.'},activity(record.channels.find(item=>item.role===role))));
        for(const key of ['server_nodes','client_nodes']){
          const names=record[key].join(', ');
          row.append(el('td',{class:'topic-owners',title:names},names||'—'));
        }
        body.append(row);
      }
      if(!data.items.length)body.append(el('tr',{},el('td',{colspan:5},'No action channels in this selection.')));
      $('actions-page').textContent=`${data.total?this.offset+1:0}–${Math.min(this.offset+this.limit,data.total)} / ${data.total}`;
      $('actions-prev').disabled=this.offset===0;
      $('actions-next').disabled=this.offset+this.limit>=data.total;
    }catch(error){if(version===this.version)showError(error);}
  }
}
