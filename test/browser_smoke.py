#!/usr/bin/env python3
"""Exercise real ES modules and Canvas in Qt's Chromium engine.

Run with xvfb-run -a python3 test/browser_smoke.py URL OUTPUT_DIRECTORY.
The browser is an optional development/test dependency, never a runtime need.
"""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu --disable-dev-shm-usage')
from PyQt5.QtCore import QEventLoop, QTimer, QUrl
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineView


class Page(QWebEnginePage):
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self.errors = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.ErrorMessageLevel:
            self.errors.append(f'{source}:{line}: {message}')
            print(self.errors[-1], flush=True)


def main():
    app = QApplication([])
    output = Path(sys.argv[2]); output.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile()  # off-the-record: no browser data written to HOME
    view = QWebEngineView(); page = Page(profile, view); view.setPage(page)
    view.resize(1600, 1000); view.show()
    view.load(QUrl(sys.argv[1]))

    def js(code):
        loop = QEventLoop(); result = []
        page.runJavaScript(code, lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(10000, loop.quit); loop.exec_()
        if not result:
            raise AssertionError('Browser JavaScript timed out')
        return result[0]

    def wait(condition):
        attempts = 0
        while attempts < 150:
            if js(condition):
                return
            loop = QEventLoop(); QTimer.singleShot(100, loop.quit); loop.exec_(); attempts += 1
        raise AssertionError(f'Browser state did not become ready: {condition}')

    def capture(name):
        # Let Chromium paint the most recent DOM/Canvas frame before grabbing.
        loop=QEventLoop();QTimer.singleShot(200,loop.quit);loop.exec_()
        view.grab().save(str(output/name))

    def check_resource_search(kind):
        js(f"window.searchListId='{kind}';")
        js("{const boxes=[...document.querySelectorAll('#'+window.searchListId+' input:not([value=__all__])')];window.searchSelections=boxes.map(b=>b.checked);window.searchName=boxes.find(b=>!b.parentElement.hidden).value;window.filterCountBefore=document.getElementById('filter-count').innerText;}")
        js("document.getElementById('search').value=window.searchName.toUpperCase();document.getElementById('search').dispatchEvent(new Event('input'))")
        assert js("[...document.querySelectorAll('#'+window.searchListId+' label.choice')].filter(r=>!r.hidden).every(r=>r.querySelector('input').value==='__all__'||r.innerText.toLowerCase().includes(window.searchName.toLowerCase()))")
        assert js("JSON.stringify([...document.querySelectorAll('#'+window.searchListId+' input:not([value=__all__])')].map(b=>b.checked))===JSON.stringify(window.searchSelections)"), 'Search must preserve selections'
        js("document.getElementById('clear').click()")
        assert js("[...document.querySelectorAll('#'+window.searchListId+' input:not([value=__all__])')].every((b,i)=>!b.parentElement.hidden?!b.checked:b.checked===window.searchSelections[i])"), 'None shown must preserve hidden selections'
        js("document.getElementById('select-all').click();document.getElementById('search').value='NO_RESOURCE_MATCH_91';document.getElementById('search').dispatchEvent(new Event('input'))")
        assert js("[...document.querySelectorAll('#'+window.searchListId+' label.choice')].every(r=>r.hidden)")
        assert js("!document.querySelector('#'+window.searchListId+' .facet-empty').hidden")
        assert js("document.getElementById('filter-count').innerText===window.filterCountBefore"), 'Search is a draft until Apply'
        js("document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input'))")
        assert js("[...document.querySelectorAll('#'+window.searchListId+' input:not([value=__all__])')].every(b=>b.parentElement.hidden===(window.searchListId==='services'&&document.getElementById('hide-default-services').checked&&/\\/(?:describe_parameters|get_parameter_types|get_parameters|list_parameters|set_parameters|set_parameters_atomically|get_type_description)$/.test(b.value)||window.searchListId==='topics'&&document.getElementById('hide-parameter-events').checked&&b.value.endsWith('/parameter_events')))")

    def check_callback_labels(kind):
        selector='#stats-table tbody tr[data-callback-id]' if kind=='statistics' else '#boxplot-rows .boxplot-row'
        route='statistics?level=callback&limit=100' if kind=='statistics' else 'boxplots?limit=100'
        js(f"window.labelSelector='{selector}';window.callbackLabelData=null;fetch('/api/{route}&exclude_default_services=1&exclude_parameter_events=1').then(r=>r.json()).then(data=>window.callbackLabelData=data);")
        wait("window.callbackLabelData!==null")
        limit=50 if kind=='statistics' else 25
        assert js(f"document.querySelectorAll(window.labelSelector).length===Math.min({limit},window.callbackLabelData.total_groups)")
        assert js("[...document.querySelectorAll(window.labelSelector)].every(element=>{const row=window.callbackLabelData.rows.find(r=>r.id===element.dataset.callbackId);if(!row)return false;const endpoint=row.callback_topic||row.callback_service;const primary=(row.node_name||'Unknown node')+(endpoint?' · '+endpoint:'');return element.querySelector('button').innerText===primary&&element.querySelector('.callback-type').innerText===row.type[0].toUpperCase()+row.type.slice(1);})"), 'Each callback must show node/resource first and callback type second'
        for kind_name in ['service','subscription','timer']:
            if js(f"window.callbackLabelData.rows.slice(0,{limit}).some(r=>r.type==='{kind_name}')"):
                assert js(f"[...document.querySelectorAll(window.labelSelector)].some(r=>r.dataset.callbackType==='{kind_name}')")

    def check_default_parameter_visibility():
        js("document.getElementById('topics-tab').click()")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert js("[...document.querySelectorAll('#topics-table tbody button')].every(b=>!b.innerText.endsWith('/parameter_events'))")
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click()")
        assert js("document.getElementById('hide-parameter-events').checked")
        assert js("[...document.querySelectorAll('#topics input')].filter(b=>b.value.endsWith('/parameter_events')).every(b=>b.parentElement.hidden)")
        js("document.getElementById('hide-parameter-events').click()")
        assert js("[...document.querySelectorAll('#topics input')].filter(b=>b.value.endsWith('/parameter_events')).every(b=>!b.parentElement.hidden)")
        assert js("document.getElementById('filter-count').innerText===''")
        js("document.getElementById('close-filters').click();document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click()")
        assert js("document.getElementById('hide-parameter-events').checked"), 'Cancel must preserve default hiding'
        js("document.getElementById('hide-parameter-events').checked=false;document.getElementById('apply-filters').click()")
        wait("[...document.querySelectorAll('#topics-table tbody button')].some(b=>b.innerText.endsWith('/parameter_events'))")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click()")
        assert js("document.getElementById('hide-parameter-events').checked&&document.getElementById('hide-default-services').checked")
        js("document.getElementById('apply-filters').click()")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert js("[...document.querySelectorAll('#topics-table tbody button')].every(b=>!b.innerText.endsWith('/parameter_events'))")
        js("document.getElementById('timeline-tab').click()")

    try:
        wait("document.getElementById('trace-summary')?.title.includes('executions')")
        wait("document.getElementById('lane-head').children.length > 0")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert not js("!document.getElementById('error').hidden"), js("document.getElementById('error').innerText")
        check_default_parameter_visibility()
        loop=QEventLoop();QTimer.singleShot(300,loop.quit);loop.exec_()
        view.grab().save(str(output / 'timeline.png'))
        js("document.getElementById('statistics-tab').click()")
        wait("document.querySelectorAll('#stats-table tbody tr').length > 0")
        assert js("document.getElementById('stats-table').innerText.includes('%')")
        assert js("document.querySelectorAll('#stats-table th').length===4")
        check_callback_labels('statistics')
        view.grab().save(str(output / 'statistics.png'))
        js("document.getElementById('boxplots-tab').click()")
        wait("document.querySelectorAll('#boxplot-rows .boxplot-row').length>0")
        assert js("document.querySelectorAll('#boxplot-rows .boxplot-row').length<=25")
        assert js("document.querySelector('#boxplot-rows canvas').width>0")
        assert js("document.querySelector('#boxplot-rows canvas').title.includes('Whiskers')")
        check_callback_labels('boxplots')
        js("document.querySelector('#boxplot-rows button').click()")
        wait("document.getElementById('selection').innerText.includes('Q1 (25%)')")
        assert js("!document.getElementById('boxplots-view').hidden"), 'Selecting a box plot must keep its view'
        assert js("document.getElementById('filter-count').innerText===''")
        assert js("document.querySelector('#boxplot-rows .selected')!==null")
        js("document.getElementById('close-details').click();document.getElementById('boxplot-scale').value='log';document.getElementById('boxplot-scale').dispatchEvent(new Event('change'))")
        assert not js("!document.getElementById('error').hidden")
        loop=QEventLoop();QTimer.singleShot(200,loop.quit);loop.exec_()
        view.grab().save(str(output/'boxplots.png'))
        js("document.getElementById('boxplot-zoom-in').click();document.getElementById('boxplot-zoom-in').click()")
        assert js("document.getElementById('boxplot-zoom-label').innerText==='225%'")
        assert js("!document.getElementById('boxplot-pan').hidden")
        js("document.getElementById('boxplot-pan').value=250;document.getElementById('boxplot-pan').dispatchEvent(new Event('input'))")
        assert js("document.getElementById('filter-count').innerText===''")
        view.grab().save(str(output/'boxplots_zoomed.png'))
        js("document.getElementById('boxplot-fit').click()")
        assert js("document.getElementById('boxplot-zoom-label').innerText==='100%'")
        js("document.getElementById('timers-tab').click()")
        wait("document.querySelector('#timers-table tbody button')!==null")
        assert js("[...document.querySelectorAll('#timers-table tbody tr')].every(row=>row.dataset.callbackType==='timer'&&row.querySelector('.callback-type').innerText==='Timer')")
        assert js("document.getElementById('filter-count').innerText===''")
        js("document.querySelector('#timers-table tbody button').click()")
        wait("document.getElementById('selection').innerText.includes('Recorded period')")
        assert js("!document.getElementById('timers-view').hidden"), 'Selecting a timer must keep the timer list open'
        assert js("document.getElementById('filter-count').innerText===''")
        capture('timers.png')
        js("document.querySelector('#selection .selection-actions button').click()")
        wait("document.getElementById('selection').innerText.includes('Wall-clock duration')")
        assert js("document.getElementById('selection').innerText.includes('Recorded period')")
        js("document.getElementById('close-details').click();document.getElementById('timers-tab').click();document.getElementById('timers-sort').value='name';document.getElementById('timers-sort').dispatchEvent(new Event('change'))")
        wait("document.getElementById('activity').innerText==='Ready'")
        js("document.getElementById('toggle-filters').click();document.getElementById('search').value='NO_MATCH_TIMERS_81';document.getElementById('search').dispatchEvent(new Event('input'));document.getElementById('apply-filters').click()")
        wait("document.querySelector('#timers-table tbody')?.innerText.includes('No timers')")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click();document.getElementById('apply-filters').click()")
        wait("document.querySelector('#timers-table tbody button')!==null")
        js("document.getElementById('topics-tab').click()")
        wait("document.querySelectorAll('#topics-table tbody button').length>0")
        js("document.querySelector('#topics-table tbody button').click()")
        wait("document.querySelector('#details .drawer-heading h2').innerText==='Topic'")
        assert js("document.getElementById('selection').innerText.includes('Publish rate')")
        assert js("document.querySelector('main').getBoundingClientRect().right<=document.getElementById('details').getBoundingClientRect().left+1"), 'Inspector must have its own column'
        view.grab().save(str(output/'topics.png'))
        js("document.getElementById('close-details').click();document.getElementById('topics-timeline').click();document.getElementById('fit').click()")
        wait("document.getElementById('timeline').dataset.publishPoints>0")
        assert js("document.getElementById('grouping').value==='topic'")
        assert js("document.getElementById('filter-count').innerText===''")
        js("fetch('/api/timeline?grouping=topic&budget=20000').then(r=>r.json()).then(data=>{window.topicCallbacks=data.records;})")
        wait("window.topicCallbacks!==undefined")
        assert js("window.topicCallbacks.some(x=>x.topic_publish_count>0)"), 'Topic columns must contain the publishing callback, not only a publish point'
        capture('topic_timeline.png')
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click()")
        assert js("document.getElementById('topic-filter-panel').getBoundingClientRect().width>document.querySelector('main').clientWidth*.9")
        assert js("document.querySelector('#topics input:not([value=__all__])')!==null")
        check_resource_search('topics')
        js("document.getElementById('clear').click();[...document.querySelectorAll('#topics input:not([value=__all__])')].find(b=>!b.parentElement.hidden).click();window.chosenTopic=[...document.querySelectorAll('#topics input:checked:not([value=__all__])')].find(b=>!b.parentElement.hidden).value")
        assert js("document.getElementById('filter-count').innerText===''")
        sizes=js("[...document.querySelectorAll('#topics input')].filter(x=>!x.parentElement.hidden).map(x=>[x.getBoundingClientRect().width,x.getBoundingClientRect().height])")
        assert all(size==[18,18] for size in sizes),sizes
        capture('topic_filters.png')
        js("document.getElementById('apply-filters').click()")
        wait("document.getElementById('lane-head').children.length===1&&document.querySelector('#lane-head>div').innerText===window.chosenTopic")
        assert js("document.querySelectorAll('#topics input:not([value=__all__])').length===1||document.getElementById('filter-count').innerText!==''")
        js("fetch('/api/timeline?grouping=topic&topics='+encodeURIComponent(window.chosenTopic)).then(r=>r.json()).then(data=>{window.filteredPublish=data.publications.find(p=>p.execution_id!=null);})")
        wait("window.filteredPublish!==undefined")
        js("{const c=document.getElementById('timeline'),r=c.getBoundingClientRect(),y=(Number(window.filteredPublish.timestamp_ns)/1e9-Number(c.dataset.startTime))/Number(c.dataset.timeSpan)*c.clientHeight;c.dispatchEvent(new MouseEvent('click',{clientX:r.left+102,clientY:r.top+y,bubbles:true}));}")
        wait("document.querySelector('#details .drawer-heading h2').innerText==='Publish'")
        js("document.querySelector('#selection>button').click()")
        wait("document.querySelector('#selection .publish-list button')!==null")
        assert js("[...document.querySelectorAll('#selection .publish-list>button')].every(b=>b.innerText.includes(window.chosenTopic))"), 'Callback details must hide rejected publish topics too'
        capture('filtered_callback_publishes.png')
        js("document.getElementById('close-details').click()")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click();document.getElementById('apply-filters').click();document.getElementById('services-tab').click()")
        wait("document.querySelectorAll('#services-table tbody tr').length>0")
        if js("document.querySelector('#services-table tbody button')!==null"):
            js("window.applicationServiceName=document.querySelector('#services-table tbody button').innerText;document.querySelector('#services-table tbody button').click()")
            wait("document.querySelector('#details .drawer-heading h2').innerText==='Service'")
            assert js("document.getElementById('selection').innerText.includes('Mean duration')")
            view.grab().save(str(output/'services.png'))
            js("document.getElementById('close-details').click();document.getElementById('services-timeline').click();document.getElementById('fit').click()")
            wait("document.getElementById('activity').innerText==='Ready'")
            assert js("document.getElementById('grouping').value==='service'")
            capture('service_timeline.png')
            js("document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            assert js("document.getElementById('service-filter-panel').getBoundingClientRect().width>document.querySelector('main').clientWidth*.9")
            check_resource_search('services')
            js("fetch('/api/dimensions').then(r=>r.json()).then(data=>{const suffixes=['describe_parameters','get_parameter_types','get_parameters','list_parameters','set_parameters','set_parameters_atomically','get_type_description'];window.defaultServiceNames=data.services.filter(n=>suffixes.includes(n.slice(n.lastIndexOf('/')+1)));window.nonDefaultServices=data.services.filter(n=>!window.defaultServiceNames.includes(n));})")
            wait("window.defaultServiceNames!==undefined")
            assert js("window.defaultServiceNames.length>0")
            assert js("document.getElementById('hide-default-services').checked")
            assert js("[...document.querySelectorAll('#services input:not([value=__all__])')].every(b=>b.parentElement.hidden===window.defaultServiceNames.includes(b.value))"), 'Default services must disappear immediately'
            assert js("document.getElementById('filter-count').innerText===''")
            capture('service_filters_hidden_defaults.png')
            js("document.getElementById('hide-default-services').click()")
            assert js("[...document.querySelectorAll('#services label.choice')].every(r=>!r.hidden)")
            js("document.getElementById('close-filters').click();document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            assert js("document.getElementById('hide-default-services').checked"), 'Cancel must preserve default hiding'
            js("document.getElementById('hide-default-services').checked=false;document.getElementById('apply-filters').click();document.getElementById('services-tab').click()")
            wait("document.getElementById('services-summary').innerText===(window.nonDefaultServices.length+window.defaultServiceNames.length)+' services'")
            js("document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            # Apply reads the actual control, without relying on a prior change event.
            js("document.getElementById('hide-default-services').checked=true")
            js("document.getElementById('apply-filters').click();document.getElementById('services-tab').click()")
            wait("document.getElementById('services-summary').innerText===window.nonDefaultServices.length+' services'")
            assert js("[...document.querySelectorAll('#services-table tbody button')].every(b=>!window.defaultServiceNames.includes(b.innerText))")
            js("document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            assert js("document.getElementById('hide-default-services').checked")
            assert js("[...document.querySelectorAll('#services input:not([value=__all__])')].filter(b=>window.defaultServiceNames.includes(b.value)).every(b=>b.parentElement.hidden)")
            js("document.getElementById('filter-topics').click();document.getElementById('clear').click();[...document.querySelectorAll('#topics input:not([value=__all__])')].find(b=>!b.parentElement.hidden).click();document.getElementById('apply-filters').click()")
            wait("document.getElementById('activity').innerText==='Ready'")
            assert js("[...document.querySelectorAll('#services-table tbody button')].some(b=>b.innerText===window.applicationServiceName)"), 'Topic selection must not hide service rows'
            js("document.getElementById('services-timeline').click()")
            wait("[...document.querySelectorAll('#lane-head>div')].some(lane=>lane.innerText===window.applicationServiceName)")
            capture('service_with_topic_filter.png')
            js("document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            js("document.getElementById('reset-filters').click()")
            assert js("document.getElementById('hide-default-services').checked")
            assert js("[...document.querySelectorAll('#services input:not([value=__all__])')].every(b=>b.parentElement.hidden===window.defaultServiceNames.includes(b.value))")
            js("document.getElementById('apply-filters').click();document.getElementById('services-timeline').click();document.getElementById('toggle-filters').click();document.getElementById('filter-services').click()")
            js("document.getElementById('clear').click();[...document.querySelectorAll('#services input:not([value=__all__])')].find(b=>!b.parentElement.hidden).click();window.chosenService=[...document.querySelectorAll('#services input:checked:not([value=__all__])')].find(b=>!b.parentElement.hidden).value;document.getElementById('apply-filters').click()")
            wait("document.getElementById('lane-head').children.length===1&&document.querySelector('#lane-head>div').innerText===window.chosenService")
            js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click();document.getElementById('apply-filters').click()")
        js("document.getElementById('timeline-tab').click();document.getElementById('grouping').value='node';document.getElementById('grouping').dispatchEvent(new Event('change'));document.getElementById('boxplots-tab').click()")

        js("document.getElementById('toggle-filters').click();document.getElementById('search').value='NO_MATCH_BOX_PLOTS';document.getElementById('search').dispatchEvent(new Event('input'));document.getElementById('apply-filters').click()")
        wait("!document.getElementById('boxplot-empty').hidden")
        assert js("document.querySelectorAll('#boxplot-rows .boxplot-row').length===0")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click();document.getElementById('apply-filters').click()")
        wait("document.querySelectorAll('#boxplot-rows .boxplot-row').length>0")
        js("document.getElementById('statistics-tab').click()")
        js("window.originalStatistics=document.getElementById('stats-summary').innerText;document.getElementById('toggle-filters').click()")
        assert js("!document.getElementById('filters-view').hidden")
        assert js("document.getElementById('callback-filter-tree').getBoundingClientRect().width > document.querySelector('main').clientWidth*.9"), 'The tree must use the full page width'
        js("document.querySelector('#tree .expand').click()")
        wait("document.querySelector('#tree .tree-children .expand')!==null")
        js("document.querySelector('#tree .tree-children .expand').click()")
        wait("document.querySelectorAll('#tree input[type=checkbox]').length>3")
        sizes=js("[...document.querySelectorAll('#tree input[type=checkbox]')].filter(x=>x.getBoundingClientRect().width).map(x=>[x.getBoundingClientRect().width,x.getBoundingClientRect().height])")
        assert all(size==[18,18] for size in sizes),sizes
        assert js("document.documentElement.scrollWidth<=window.innerWidth")
        loop=QEventLoop();QTimer.singleShot(200,loop.quit);loop.exec_()
        view.grab().save(str(output/'filters.png'))
        js("document.getElementById('search').value='NO_MATCH_123xyz';document.getElementById('search').dispatchEvent(new Event('input'))")
        assert js("document.getElementById('stats-summary').innerText===window.originalStatistics"), 'Editing filters must not apply them'
        js("document.getElementById('apply-filters').click()")
        wait("document.getElementById('stats-summary').innerText.startsWith('0 executions')")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click()")
        assert js("document.getElementById('stats-summary').innerText.startsWith('0 executions')"), 'Reset is a draft until Apply'
        js("document.getElementById('apply-filters').click()")
        wait("!document.getElementById('stats-summary').innerText.startsWith('0 executions')")
        js("document.querySelector('#stats-table tbody button').click()")
        wait("!document.getElementById('details').hidden")
        assert js("document.getElementById('selection').innerText.includes('Total callback duration')")
        assert js("document.getElementById('filter-count').innerText===''")
        assert js("!document.getElementById('statistics-view').hidden"), 'Selection must not navigate'
        js("document.querySelector('#selection .selection-actions button').click()")
        wait("document.getElementById('selection').innerText.includes('Wall-clock duration')")
        assert js("document.getElementById('filter-count').innerText===''")
        assert js("!document.getElementById('selection').innerText.includes('More details')")
        assert js("document.getElementById('selection').innerText.includes('Exact duration')")
        assert js("document.querySelector('main').getBoundingClientRect().right<=document.getElementById('details').getBoundingClientRect().left+1")
        js("window.selectedCenter=(parseFloat([...document.querySelectorAll('#selection dt')].find(x=>x.innerText==='Start').nextElementSibling.innerText)+parseFloat([...document.querySelectorAll('#selection dt')].find(x=>x.innerText==='End').nextElementSibling.innerText))/2;document.getElementById('zoom-in').click();document.getElementById('zoom-in').click()")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert js("Math.abs(Number(document.getElementById('timeline').dataset.startTime)-Math.max(0,Math.min(Number(window.traceEnd||document.getElementById('range-end').value)-Number(document.getElementById('timeline').dataset.timeSpan),window.selectedCenter-Number(document.getElementById('timeline').dataset.timeSpan)/2)))<1e-8"), 'Zoom must center the selected callback within the trace bounds'
        js("document.getElementById('fit').click()")
        wait("document.getElementById('activity').innerText==='Ready'")
        js("fetch('/api/timeline?grouping=node&budget=20000').then(r=>r.json()).then(data=>{window.allDurations=(data.records||[]).map(x=>Number(x.duration_ns)/1e9);})")
        wait("window.allDurations!==undefined")
        if js("Math.max(...window.allDurations)/Number(document.getElementById('timeline').dataset.timeSpan)*document.getElementById('timeline').clientHeight<12"):
            wait("document.getElementById('timeline').dataset.publishTicks==='0'")
        # All publish markers carry a topic and a callback association. Selecting
        # from the callback's list exercises the same inspector as a Canvas tick.
        js("fetch('/api/timeline?grouping=node').then(r=>r.json()).then(data=>{window.markerExecution=(data.records||[]).find(x=>x.publish_count>0)?.id;})")
        wait("window.markerExecution!==undefined")
        js("fetch('/api/executions/'+window.markerExecution+'/publications').then(r=>r.json()).then(data=>{window.markerPublication=data.items[0];})")
        wait("window.markerPublication!==undefined")
        assert js("typeof window.markerPublication.topic==='string'")
        # The callback inspector lists recorded publishes with node/topic labels.
        if js("document.querySelector('#selection .publish-list button')!==null"):
            js("document.querySelector('#selection .publish-list button').click()")
            wait("document.querySelector('#details .drawer-heading h2').innerText==='Publish'")
            assert js("document.getElementById('selection').innerText.includes('Topic')")
            assert js("document.getElementById('selection').innerText.includes('Message address')")
            assert js("document.getElementById('filter-count').innerText===''")
            js("window.publishTime=parseFloat([...document.querySelectorAll('#selection dt')].find(x=>x.innerText==='Time').nextElementSibling.innerText);document.querySelector('#selection>button').click()")
            wait("document.querySelector('#details .drawer-heading h2').innerText==='Callback'")
            js("document.getElementById('reset-zoom').click()")
            wait("document.getElementById('activity').innerText==='Ready'")
            js("window.selectedNode=[...document.querySelectorAll('#selection dt')].find(x=>x.innerText==='Node').nextElementSibling.innerText;window.laneIndex=[...document.querySelectorAll('#lane-head>div')].findIndex(x=>x.innerText===window.selectedNode);const canvas=document.getElementById('timeline'),box=canvas.getBoundingClientRect();window.markerX=90+(canvas.clientWidth-90)/document.getElementById('lane-head').children.length*(window.laneIndex+.5);window.markerY=(window.publishTime-Number(canvas.dataset.startTime))/Number(canvas.dataset.timeSpan)*canvas.clientHeight;canvas.dispatchEvent(new MouseEvent('mousemove',{clientX:box.left+window.markerX,clientY:box.top+window.markerY,bubbles:true}));")
            assert js("window.laneIndex>=0")
            assert js("!document.getElementById('tooltip').hidden")
            js("{const canvas=document.getElementById('timeline'),box=canvas.getBoundingClientRect();canvas.dispatchEvent(new MouseEvent('click',{clientX:box.left+window.markerX,clientY:box.top+window.markerY,bubbles:true}));}")
            wait("document.querySelector('#details .drawer-heading h2').innerText==='Publish'")
            assert js("document.getElementById('filter-count').innerText===''")
            loop=QEventLoop();QTimer.singleShot(200,loop.quit);loop.exec_()
            view.grab().save(str(output/'publish_marker.png'))



        js("document.getElementById('close-details').click();document.getElementById('statistics-tab').click();document.getElementById('stats-level').value='process';document.getElementById('stats-level').dispatchEvent(new Event('change'))")
        wait("document.querySelector('#stats-table tbody button')?.innerText.startsWith('▸')")
        js("document.querySelector('#stats-table tbody button').click()")
        wait("document.querySelector('#stats-table tbody button')?.innerText.startsWith('▾')")
        js("document.getElementById('toggle-filters').click();document.getElementById('clear').click();document.getElementById('close-filters').click()")
        assert js("document.getElementById('filter-count').innerText===''")
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-options').click();window.traceEnd=Number(document.getElementById('range-end').value);document.getElementById('range-start').value=(window.traceEnd*.25).toFixed(9);document.getElementById('range-end').value=(window.traceEnd*.75).toFixed(9);document.getElementById('apply-filters').click();document.getElementById('timeline-tab').click()")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert js("!document.getElementById('error').hidden") is False, js("document.getElementById('error').innerText")
        # Fitting the already selected interval must preserve its scale, not
        # restore the whole recording. Both overview and scroll use that extent.
        scale=js("document.getElementById('zoom-label').innerText")
        js("document.getElementById('fit').click()")
        assert js("document.getElementById('zoom-label').innerText") == scale
        assert not page.errors, page.errors
        print(json.dumps({'browser':'Qt WebEngine Chromium','status':'passed',
                          'screenshots':[str(output/'timeline.png'),str(output/'statistics.png')]}))
    finally:
        from PyQt5 import sip
        view.close(); sip.delete(view); sip.delete(profile)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
