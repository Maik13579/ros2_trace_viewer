import {$, el, api, state, duration, exactSeconds, seconds, debounce, showError, callbackLabel, publicationLabel} from './shared.js';
import {FilterTree} from './tree.js';
import {Timeline} from './timeline.js';
import {Statistics} from './statistics.js';
import {BoxPlots} from './boxplots.js';
import {Topics} from './topics.js';
import {Services} from './services.js';
import {Timers} from './timers.js';
import {Actions, actionDetails} from './actions.js';

const timeline = new Timeline(selectExecution,selectPublication,selectReceive), statistics = new Statistics(inspectStatistics), tree = new FilterTree();
const boxplots = new BoxPlots(inspectStatistics),topics=new Topics(inspectTopic);
const services=new Services(inspectService);
const timers=new Timers(inspectStatistics);
const actions=new Actions(inspectAction);
let filterMode='callbacks',draftGrouping='node';
const resourceFilters=['topics','services','actions'];
const isDefaultService=name=>/\/(?:describe_parameters|get_parameter_types|get_parameters|list_parameters|set_parameters|set_parameters_atomically|get_type_description)$/.test(name);
let inspectorVersion=0;
function openInspector(title){
  $('details').hidden=false;document.body.classList.add('detail-open');
  document.querySelector('#details .drawer-heading h2').textContent=title;
}
let previousView='timeline';
function switchView(name){
  state.view=name;
  for(const item of ['timeline','statistics','boxplots','topics','services','timers','actions','filters']){
    $(`${item}-view`).hidden=item!==name;
    $(item==='filters'?'toggle-filters':`${item}-tab`).classList.toggle('active',item===name);
  }
  if(name==='filters'){$('details').hidden=true;document.body.classList.remove('detail-open');inspectorVersion++;}
  if(name==='timeline'){timeline.resize();timeline.refresh();}
  else if(name==='statistics')statistics.refresh();
  else if(name==='boxplots')boxplots.refresh();
  else if(name==='topics')topics.refresh();
  else if(name==='services')services.refresh();
  else if(name==='timers')timers.refresh();
  else if(name==='actions')actions.refresh();
}
function view(name){
  if(name==='filters'){
    if(state.view==='filters')return;
    previousView=state.view;
    state.draft=JSON.parse(JSON.stringify(state.filters));syncFilterForm();
    draftGrouping=state.grouping;filterCategory('callbacks');
    for(const kind of ['topic','service','action'])$(`${kind}-columns`).checked=draftGrouping===kind;
  }
  switchView(name);
}
$('timeline-tab').onclick=()=>view('timeline');
$('statistics-tab').onclick=()=>view('statistics');
$('boxplots-tab').onclick=()=>view('boxplots');
$('topics-tab').onclick=()=>view('topics');
$('services-tab').onclick=()=>view('services');
$('timers-tab').onclick=()=>view('timers');
$('actions-tab').onclick=()=>view('actions');
for(const kind of ['topic','service','action']){
  $(`${kind}s-timeline`).onclick=()=>{
    state.grouping=kind;$('grouping').value=kind;timeline.laneOffset=0;view('timeline');
    if(kind==='action')$('fit').click();timeline.overview();
  };
  $(`${kind}-columns`).onchange=()=>{
    draftGrouping=$(`${kind}-columns`).checked?kind:'node';
    for(const item of ['topic','service','action'])$(`${item}-columns`).checked=draftGrouping===item;
  };
}
$('toggle-filters').onclick=()=>view('filters');
$('close-filters').onclick=()=>switchView(previousView);
$('close-details').onclick=()=>{
  $('details').hidden=true;document.body.classList.remove('detail-open');inspectorVersion++;state.selected=null;state.selectedPublish=null;state.selectedReceive=null;timeline.draw();
};
$('grouping').onchange=()=>{state.grouping=$('grouping').value;timeline.laneOffset=0;if(state.grouping==='action')$('fit').click();timeline.refresh();timeline.overview();};
function filterCategory(name){
  filterMode=name;
  for(const [key,panel] of [['callbacks','callback-filter-tree'],['topics','topic-filter-panel'],['services','service-filter-panel'],['actions','action-filter-panel'],['options','filter-options-panel']]){
    $(panel).hidden=key!==name;$(`filter-${key}`).classList.toggle('active',key===name);
  }
  $('select-all').hidden=$('clear').hidden=name==='options';
  const label=resourceFilters.includes(name)?name:'callbacks';
  $('search').placeholder=`Search ${label}…`;
  $('search').setAttribute('aria-label',`Search ${label}`);
  searchResourceLists();
}
for(const key of ['callbacks',...resourceFilters,'options'])$(`filter-${key}`).onclick=()=>filterCategory(key);
$('search').oninput=()=>{state.draft.search=$('search').value.trim();searchResourceLists();};
$('hide-default-services').onchange=()=>{
  state.draft.exclude_default_services=$('hide-default-services').checked?'1':'';searchResourceLists();
};
function facetChoices(id,visible=false){
  return [...$(id).querySelectorAll('input')].filter(box=>box.value!=='__all__'&&(!visible||!box.parentElement.hidden));
}
function updateFacetSelection(id){
  const choices=facetChoices(id),selected=choices.filter(box=>box.checked).map(box=>box.value);
  state.draft[id]=selected.length===choices.length?[]:selected.length?selected:[['types',...resourceFilters].includes(id)?'__none__':'-1'];
  const visible=facetChoices(id,resourceFilters.includes(id)),all=$(id).querySelector('input');
  all.checked=visible.length>0&&visible.every(box=>box.checked);
  all.indeterminate=!all.checked&&visible.some(box=>box.checked);
}
function searchResourceLists(){
  const query=$('search').value.trim().toLowerCase();
  for(const id of resourceFilters){
    const choices=facetChoices(id),hideDefaults=id==='services'&&$('hide-default-services').checked;
    for(const box of choices)box.parentElement.hidden=!!(query&&!box.value.toLowerCase().includes(query))||(hideDefaults&&isDefaultService(box.value));
    const visible=facetChoices(id,true),all=$(id).querySelector('input'),empty=$(id).querySelector('.facet-empty');
    if(all){
      all.parentElement.hidden=visible.length===0;all.checked=visible.length>0&&visible.every(box=>box.checked);
      all.indeterminate=!all.checked&&visible.some(box=>box.checked);
      all.parentElement.lastChild.textContent=query?'All shown':'All';
    }
    if(empty){empty.hidden=visible.length>0;empty.textContent=choices.length?`No matching ${id}.`:'None recorded.';}
  }
  const resource=resourceFilters.includes(filterMode),shown=resource&&!!query;
  $('select-all').textContent=shown?'All shown':'All';$('clear').textContent=shown?'None shown':'None';
  $('select-all').disabled=$('clear').disabled=resource&&facetChoices(filterMode,true).length===0;
}
$('clear').onclick=()=>{
  if(resourceFilters.includes(filterMode)){facetChoices(filterMode,true).forEach(box=>box.checked=false);updateFacetSelection(filterMode);return;}
  Object.assign(state.draft,{none:'',include_selection:'1',include_processes:[],include_nodes:[],include_callbacks:[]});tree.sync();
};
$('select-all').onclick=()=>{
  if(resourceFilters.includes(filterMode)){facetChoices(filterMode,true).forEach(box=>box.checked=true);updateFacetSelection(filterMode);return;}
  Object.assign(state.draft,{exclude_processes:[],exclude_nodes:[],exclude_callbacks:[],callbacks:[],none:'',include_selection:'',include_processes:[],include_nodes:[],include_callbacks:[]});tree.sync();
};
$('reset-filters').onclick=()=>{
  state.draft={exclude_processes:[],exclude_nodes:[],exclude_callbacks:[]};syncFilterForm();
};
function syncFilterForm(){
  const f=state.draft;
  $('search').value=f.search||'';
  $('hide-default-services').checked=!!f.exclude_default_services;
  $('range-start').value=f.start_time||'0';$('range-end').value=f.end_time||state.info.duration_seconds.toFixed(9);
  $('min-duration').value=f.min_duration?Number(f.min_duration)*1000:'';
  $('max-duration').value=f.max_duration?Number(f.max_duration)*1000:'';
  for(const key of ['types','threads','cpus',...resourceFilters]){
    const selected=(f[key]||[]).map(String),all=selected.length===0;
    $(key).querySelectorAll('input').forEach(input=>input.checked=input.value==='__all__'?all:all||selected.includes(input.value));
  }
  searchResourceLists();
  tree.sync();
}
function facet(id,items){
  const target=$(id);target.replaceChildren();
  const add=(value,label)=>{
    const box=el('input',{type:'checkbox',value,checked:true});
    box.onchange=()=>{
      const choices=facetChoices(id,resourceFilters.includes(id));
      if(value==='__all__')choices.forEach(input=>input.checked=box.checked);
      updateFacetSelection(id);
    };
    target.append(el('label',{class:'choice'},box,label));
  };
  add('__all__','All');items.forEach(item=>add(String(item.value),item.label));
  if(resourceFilters.includes(id)||!items.length)target.append(el('p',{class:'muted facet-empty',hidden:items.length>0},'None recorded.'));
}
$('apply-filters').onclick=()=>{
  const start=Number($('range-start').value||0),end=Number($('range-end').value||state.info.duration_seconds);
  const min=$('min-duration').value,max=$('max-duration').value;
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>state.info.duration_seconds||start>=state.info.duration_seconds)return showError(Error('Choose a start and end inside the recording, with end after start.'));
  if((min!==''&&Number(min)<0)||(max!==''&&Number(max)<0)||(min!==''&&max!==''&&Number(max)<Number(min)))return showError(Error('Enter a valid duration range.'));
  Object.assign(state.draft,{exclude_default_services:$('hide-default-services').checked?'1':'',
    search:$('search').value.trim(),start_time:start===0?'':start.toFixed(9),end_time:end===state.info.duration_seconds?'':end.toFixed(9),
    min_duration:min===''?'':(Number(min)/1000).toFixed(9),max_duration:max===''?'':(Number(max)/1000).toFixed(9)});
  state.grouping=draftGrouping;$('grouping').value=draftGrouping;
  state.filters=JSON.parse(JSON.stringify(state.draft));state.change();switchView(previousView);
};
$('full-range').onclick=()=>{$('range-start').value=0;$('range-end').value=state.info.duration_seconds.toFixed(9);};
$('visible-range').onclick=()=>{$('range-start').value=timeline.start.toFixed(9);$('range-end').value=(timeline.start+timeline.span).toFixed(9);};

async function selectExecution(id){
  const version=++inspectorVersion;
  try{
    const record=await api(`executions/${id}`,state.parameters());if(version!==inspectorVersion)return;state.selected=record;state.selectedPublish=null;state.selectedReceive=null;
    openInspector('Callback');
    const details=$('selection');details.replaceChildren(el('h3',{},callbackLabel(record)),
      el('div',{class:'duration-large'},duration(record.duration_ns)),el('div',{class:'muted note'},'Wall-clock duration'));
    const dl=el('dl');
    for(const [title,value] of [['Node',record.node_name||'Unknown'],...(record.callback_topic?[['Topic',record.callback_topic]]:[]),...(record.callback_service?[['Service',record.callback_service]]:[]),...(record.timer_period_ns!=null?[['Recorded period',duration(record.timer_period_ns)]]:[]),['Start',exactSeconds(record.start_ns)],['End',exactSeconds(record.end_ns)]])
      dl.append(el('div',{},el('dt',{},title),el('dd',{},value)));
    details.append(dl,el('div',{class:'selection-actions'},
      el('button',{onclick:()=>adjacent('previous')},'‹ Previous'),el('button',{onclick:()=>adjacent('next')},'Next ›')));
    const metadata=el('dl');
    const fields=[['Symbol',record.callback_symbol||'Unknown'],['Process',`${record.process_name||'Unknown'} [${record.pid}]`],
      ['Thread',String(record.tid)],['Type',record.callback_type],['Identity',record.callback_id],
      ['Exact duration',`${record.duration_ns} ns`],['CPU at start / end',`${record.start_cpu??'Unknown'} / ${record.end_cpu??'Unknown'}`]];
    if(record.cpu_intervals.length){
      const total=record.cpu_intervals.reduce((sum,row)=>sum+BigInt(row.end_ns)-BigInt(row.start_ns),0n);
      fields.push(['Observed on-CPU duration',duration(total)]);
    }
    fields.forEach(([title,value])=>metadata.append(el('div',{},el('dt',{},title),el('dd',{class:'mono'},value))));
    details.append(metadata);timeline.draw();
    if(record.publish_count){
      const list=el('div',{class:'publish-list'});details.append(el('h3',{class:'publish-list-heading'},`Publishes (${record.publish_count})`),list);
      loadPublications(record.id,list,version);
    }
  }catch(error){showError(error);}
}
async function loadPublications(id,target,version,offset=0){
  try{
    const data=await api(`executions/${id}/publications`,state.parameters({offset,limit:20}));
    if(version!==inspectorVersion)return;
    target.replaceChildren(...data.items.map(item=>el('button',{onclick:()=>selectPublication(item.id)},
      publicationLabel(item),el('small',{},exactSeconds(item.timestamp_ns)))));
    if(data.total>20){
      const prev=el('button',{disabled:offset===0,onclick:()=>loadPublications(id,target,version,Math.max(0,offset-20))},'‹');
      const next=el('button',{disabled:offset+20>=data.total,onclick:()=>loadPublications(id,target,version,offset+20)},'›');
      target.append(el('div',{class:'publish-list-nav'},prev,`${offset+1}–${Math.min(offset+20,data.total)} / ${data.total}`,next));
    }
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function selectPublication(id){
  const version=++inspectorVersion;
  try{
    const record=await api(`publications/${id}`,state.parameters());if(version!==inspectorVersion)return;
    state.selected=record.execution;state.selectedPublish=record.id;state.selectedReceive=null;openInspector('Publish');
    const fields=[['Node',record.node_name||'Unknown'],['Topic',record.topic||'Unknown'],
      ['Time',exactSeconds(record.timestamp_ns)],['Process',`${record.process_name||'Unknown'} [${record.pid}]`],
      ['Thread',String(record.tid)],['Message address',record.message_pointer],
      ['Tracepoints',record.tracepoints.join(', ')]];
    if(record.source_timestamp!=null)fields.push(['Source timestamp',`${record.source_timestamp} ns`]);
    const dl=el('dl');fields.forEach(([title,value])=>dl.append(el('div',{},el('dt',{},title),el('dd',{},value))));
    const details=$('selection');details.replaceChildren(el('h3',{},publicationLabel(record)),dl);
    if(record.execution)details.append(el('button',{onclick:()=>selectExecution(record.execution_id)},'Callback details'));
    timeline.draw();
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function inspectTopic(name){
  const version=++inspectorVersion;
  try{
    const result=await api('topics',state.parameters({topic:name,limit:1}));
    if(version!==inspectorVersion||!result.items.length)return;
    const record=result.items[0];openInspector('Topic');
    const fields=[['Publishes',record.publish_count.toLocaleString()],
      ['Publish rate',record.publish_rate_hz==null?'—':`${record.publish_rate_hz.toFixed(2)} Hz`],
      ['Recorded receives',record.receive_count==null?'Not attributable under callback filters':record.receive_count.toLocaleString()],
      ['Subscription callbacks',String(record.callback_count)],['Callback executions',record.execution_count.toLocaleString()],
      ['Accumulated callback duration',duration(record.total_callback_ns)],
      ['Mean callback duration',record.mean_duration_ns==null?'—':duration(record.mean_duration_ns)],
      ['First activity',record.first_ns==null?'—':exactSeconds(record.first_ns)],
      ['Last activity',record.last_ns==null?'—':exactSeconds(record.last_ns)]];
    const dl=el('dl');fields.forEach(([title,value])=>dl.append(el('div',{},el('dt',{},title),el('dd',{},value))));
    for(const [key,label,total] of [['publishers','Publishers','publisher_count'],['subscriptions','Subscribers','subscription_count']]){
      const list=record[key]||[];
      dl.append(el('dt',{title:result.association},`${label} (${record[total]})`));
      if(!list.length)dl.append(el('dd',{},'None recorded'));
      for(const endpoint of list)dl.append(el('dd',{},`${endpoint.node_name||'Unknown node'} · process ${endpoint.pid}`,el('small',{class:'muted'},` · depth ${endpoint.queue_depth??'Unknown'}`)));
      if(list.length<record[total])dl.append(el('dd',{},`${record[total]-list.length} more recorded endpoints`));
    }
    $('selection').replaceChildren(el('h3',{},record.topic),dl);
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function selectReceive(id){
  const version=++inspectorVersion;
  try{
    const record=await api(`receives/${id}`,state.parameters());if(version!==inspectorVersion)return;
    state.selected=null;state.selectedPublish=null;state.selectedReceive=record.id;openInspector('Receive');
    const dl=el('dl');
    for(const [label,value] of [['Action',record.action],['Channel',record.action_role],
      ['Topic',record.topic],['Time',exactSeconds(record.timestamp_ns)],
      ['Process',`${record.process_name||'Unknown'} [${record.pid}]`],['Thread',String(record.tid)],
      ['Tracepoint','ros2:rmw_take (taken=true)'],['Callback','Not recorded']])
      dl.append(el('div',{},el('dt',{},label),el('dd',{},value)));
    $('selection').replaceChildren(el('h3',{},record.node_name||'Unknown node'),dl);timeline.draw();
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function inspectService(name){
  const version=++inspectorVersion;
  try{
    const result=await api('services',state.parameters({service:name,limit:1}));
    if(version!==inspectorVersion||!result.items.length)return;
    const record=result.items[0];openInspector('Service');
    const fields=[['Server callbacks',record.execution_count.toLocaleString()],
      ['Accumulated duration',duration(record.total_callback_ns)],
      ['Mean duration',record.mean_duration_ns==null?'—':duration(record.mean_duration_ns)],
      ['Minimum',record.min_duration_ns==null?'—':duration(record.min_duration_ns)],
      ['Maximum',record.max_duration_ns==null?'—':duration(record.max_duration_ns)],
      ['First callback',record.first_ns==null?'—':exactSeconds(record.first_ns)],
      ['Last callback',record.last_ns==null?'—':exactSeconds(record.last_ns)]];
    const dl=el('dl');fields.forEach(([title,value])=>dl.append(el('div',{},el('dt',{},title),el('dd',{},value))));
    for(const [key,label,total] of [['servers','Servers','server_count'],['clients','Clients','client_count']]){
      const list=record[key]||[];dl.append(el('dt',{title:key==='clients'?result.client_association:''},`${label} (${record[total]})`));
      if(!list.length)dl.append(el('dd',{},'None recorded'));
      for(const endpoint of list)dl.append(el('dd',{},`${endpoint.node_name||'Unknown node'} · process ${endpoint.pid}`));
      if(list.length<record[total])dl.append(el('dd',{},`${record[total]-list.length} more recorded endpoints`));
    }
    $('selection').replaceChildren(el('h3',{},record.service),dl,el('p',{class:'muted note'},'Calling callback and request/reply timing are unavailable.'));
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function inspectAction(name){
  const version=++inspectorVersion;
  try{
    const result=await api('actions',state.parameters({action:name,limit:1}));
    if(version!==inspectorVersion||!result.items.length)return;
    openInspector('Action');
    $('selection').replaceChildren(actionDetails(result.items[0],inspectTopic,inspectService));
  }catch(error){if(version===inspectorVersion)showError(error);}
}
async function adjacent(direction){
  if(!state.selected)return;
  try{
    const record=await api(`executions/${state.selected.id}/${direction}`,state.parameters());
    if(record){timeline.jump(seconds(record.start_ns)-timeline.span*.1);await selectExecution(record.id);}
    else $('activity').textContent=`No ${direction} execution`;
  }catch(error){showError(error);}
}
async function inspectStatistics(data){
  const version=++inspectorVersion;
  try{
    const selectedShare=data.share_percent;
    if(data.mean_ns==null){
      const result=await api('statistics',state.parameters({level:'callback',callbacks:data.id}));
      data=result.rows[0];if(!data)return;
      if(selectedShare!=null)data.share_percent=selectedShare;
    }
    if(version!==inspectorVersion)return;openInspector('Callback');
    const details=$('selection');details.replaceChildren(el('h3',{},callbackLabel(data)),
      el('div',{class:'duration-large'},duration(data.total_ns)),el('div',{class:'muted note'},'Total callback duration'));
    const fields=[['Executions',data.count.toLocaleString()],['Node',data.node_name||'Unknown'],
      ['Share of selected callback time',`${data.share_percent.toFixed(2)}%`],
      ...(data.callback_topic?[['Topic',data.callback_topic]]:[]),...(data.callback_service?[['Service',data.callback_service]]:[]),['Process',String(data.pid)],['Type',data.type],['Identity',data.id],
      ...(data.timer_period_ns!=null?[['Recorded period',duration(data.timer_period_ns)]]:[]),
      ['Minimum',duration(data.min_ns)],['Maximum',duration(data.max_ns)],
      ['Mean',duration(data.mean_ns)],['Median',duration(data.median_ns)],
      ['Standard deviation',duration(data.stddev_ns)],['P95',duration(data.p95_ns)],['P99',duration(data.p99_ns)],
      ['Symbol',data.name]];
    if(data.q1_ns!=null)fields.splice(fields.length-1,0,
      ['Q1 (25%)',duration(data.q1_ns)],['Q3 (75%)',duration(data.q3_ns)],
      ['Lower whisker',duration(data.lower_whisker_ns)],['Upper whisker',duration(data.upper_whisker_ns)],
      ['Outliers',data.outlier_count.toLocaleString()]);
    const dl=el('dl');fields.forEach(([title,value])=>dl.append(el('div',{},el('dt',{},title),el('dd',{},value))));
    details.append(el('div',{class:'selection-actions'},el('button',{onclick:()=>locate(data.id)},'Show on timeline')),dl);
  }catch(error){showError(error);}
}
async function locate(callback){
  view('timeline');
  try{
    const record=await api('locate',state.parameters({callbacks:callback}));
    if(record){timeline.jump(seconds(record.start_ns)-timeline.span*.1);await selectExecution(record.id);}
    else showError(Error('No matching callback execution.'));
  }catch(error){showError(error);}
}
async function boot(){
  try{
    const [info,dimensions]=await Promise.all([api('trace/info'),api('dimensions')]);state.info=info;
    document.title=`${info.name} · Trace Viewer`;
    $('trace-summary').textContent=`${info.name} · ${duration(info.duration_ns)}`;
    $('trace-summary').title=`${info.known_execution_count.toLocaleString()} initialized callback executions`;
    $('range-end').value=info.duration_seconds.toFixed(9);
    $('cpu-note').textContent=info.capabilities.cpu_reason;
    $('publish-legend').hidden=!info.capabilities.publish_markers;
    $('grouping').querySelector('[value=cpu]').disabled=!info.capabilities.cpu_execution;
    $('cpu-filter-card').hidden=!info.capabilities.cpu_execution;
    facet('types',dimensions.types.map(value=>({value,label:value[0].toUpperCase()+value.slice(1)})));
    facet('threads',dimensions.threads.map(row=>({value:row.tid,label:`Thread ${row.tid} · process ${row.pid}`})));
    facet('cpus',dimensions.cpus.map(value=>({value,label:`CPU ${value}`})));
    facet('topics',dimensions.topics.map(value=>({value,label:value})));
    facet('services',dimensions.services.map(value=>({value,label:value})));
    facet('actions',(dimensions.actions||[]).map(value=>({value,label:value})));
    await timeline.init();await tree.init();
    if(!info.known_execution_count&&info.action_count)view('actions');
  }catch(error){showError(error);$('activity').textContent='Unable to load';}
}
boot();
