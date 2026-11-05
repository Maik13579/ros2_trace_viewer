import {$, el, api, state, showError, callbackLabel, callbackType} from './shared.js';

/** Lazy, paged hierarchy. Exclusion filters preserve unloaded descendants. */
export class FilterTree {
  constructor() {
    this.root = $('tree'); this.boxes = [];
    state.events.addEventListener('draft', () => this.sync());
  }
  async init() {
    this.root.replaceChildren(); this.boxes = [];
    await this.page('processes', {}, this.root, process => {
      const title = `${process.name || 'Unknown process'} [${process.id}]`;
      return this.branch('processes', String(process.id), title, [], async target => {
        await this.page('nodes', {pid: process.id}, target, node => this.node(process, node));
      });
    });
  }
  node(process, node) {
    return this.branch('nodes', node.id, node.name || 'Unknown node', [String(process.id)], target =>
      this.page('callbacks', {pid: process.id, node: node.id}, target, callback =>
        this.branch('callbacks', callback.id, `${callbackLabel(callback)} · ${callbackType(callback)}`,
          [String(process.id), node.id])));
  }
  branch(kind, id, label, parents, load) {
    const wrapper = el('div'), children = el('div', {class: 'tree-children', hidden: true});
    const box = el('input', {type: 'checkbox', 'aria-label': label});
    const text = el('label', {title: label}, label);
    text.addEventListener('click', () => { box.checked = !box.checked; box.dispatchEvent(new Event('change')); });
    box.addEventListener('change', () => {
      const f=state.draft;
      if (f.none) f.none='';
      if (f.include_selection) {
        const key=`include_${kind}`,values=f[key]||[];
        f[key]=box.checked?[...new Set([...values,id])]:values.filter(x=>String(x)!==id);
      }
      const key = `exclude_${kind}`, values = state.draft[key] || [];
      state.draft[key] = box.checked ? values.filter(x => String(x) !== id) : [...new Set([...values, id])];
      // Re-enabling a child also re-enables its ancestors.
      if (box.checked) {
        state.draft.exclude_processes = state.draft.exclude_processes.filter(x => String(x) !== parents[0]);
        if (kind === 'callbacks') state.draft.exclude_nodes = state.draft.exclude_nodes.filter(x => x !== parents[1]);
      }
      if(box.checked && kind==='processes'){
        f.exclude_nodes=f.exclude_nodes.filter(x=>!x.startsWith(`${id}:`));
        f.exclude_callbacks=f.exclude_callbacks.filter(x=>!x.startsWith(`${id}:`));
      }else if(box.checked && kind==='nodes'){
        const descendants=this.boxes.filter(x=>x.parents[1]===id).map(x=>x.id);
        f.exclude_callbacks=f.exclude_callbacks.filter(x=>!descendants.includes(x));
      }
      state.events.dispatchEvent(new Event('draft'));
    });
    this.boxes.push({box, kind, id, parents});
    const expand = el('button', {class: 'expand', 'aria-label': `Expand ${label}`, 'aria-expanded': false}, load ? '▸' : '·');
    let loaded = false;
    if (load) expand.addEventListener('click', async () => {
      children.hidden = !children.hidden;
      expand.textContent = children.hidden ? '▸' : '▾';
      expand.setAttribute('aria-expanded', !children.hidden);
      if (!loaded) {
        loaded = true; children.textContent = 'Loading…';
        try { children.replaceChildren(); await load(children); this.sync(); }
        catch (error) { loaded = false; showError(error); }
      }
    });
    wrapper.append(el('div', {class: 'tree-row'}, expand, box, text), children);
    this.sync(); return wrapper;
  }
  async page(kind, parameters, target, create, offset = 0) {
    const result = await api(kind, {...parameters, offset, limit: 100});
    if (!result.items.length && !offset) target.append(el('div', {class: 'tree-empty'}, 'No matching callbacks'));
    result.items.forEach(item => target.append(create(item)));
    if (offset + result.items.length < result.total) {
      const more = el('button', {onclick: async () => {
        more.disabled = true;
        try { await this.page(kind, parameters, target, create, offset + result.items.length); more.remove(); }
        catch (error) { more.disabled = false; showError(error); }
      }}, `Load more (${result.total - offset - result.items.length})`);
      target.append(more);
    }
    this.sync();
  }
  sync() {
    for (const {box, kind, id, parents} of this.boxes) {
      const excludes = (state.draft[`exclude_${kind}`] || []).map(String);
      const ancestors = (state.draft.exclude_processes || []).map(String).includes(parents[0]) ||
        (kind === 'callbacks' && state.draft.exclude_nodes.includes(parents[1]));
      const f=state.draft;
      let included=true;
      if(f.include_selection){
        included=(f[`include_${kind}`]||[]).map(String).includes(id) ||
          (f.include_processes||[]).map(String).includes(parents[0]) ||
          (kind==='callbacks' && (f.include_nodes||[]).includes(parents[1]));
      }
      box.checked = included && !f.none && !excludes.includes(id) && !ancestors;
      if (state.draft.callbacks?.length && kind === 'callbacks') box.checked &&= state.draft.callbacks.includes(id);
      const descendant = kind === 'processes'
        ? state.draft.exclude_nodes.some(x => x.startsWith(`${id}:`)) || state.draft.exclude_callbacks.some(x => x.startsWith(`${id}:`))
        : kind === 'nodes' && this.boxes.some(x => x.parents[1] === id && state.draft.exclude_callbacks.includes(x.id));
      const selectedDescendants=f.include_selection && (kind==='processes'
        ? [...(f.include_nodes||[]),...(f.include_callbacks||[])].some(x=>x.startsWith(`${id}:`))
        : kind==='nodes' && this.boxes.some(x=>x.parents[1]===id && (f.include_callbacks||[]).includes(x.id)));
      box.indeterminate = (box.checked && !!descendant) || (!box.checked && !!selectedDescendants);
    }
  }
}
