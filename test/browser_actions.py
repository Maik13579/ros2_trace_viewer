#!/usr/bin/env python3
"""Exercise action filters, timeline transport markers and channel navigation."""
import json
import sys
from pathlib import Path

from browser_smoke import Page
from PyQt5.QtCore import QEventLoop, QTimer, QUrl
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebEngineWidgets import QWebEngineProfile, QWebEngineView


def main():
    app = QApplication([])
    output = Path(sys.argv[2]); output.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile()
    view = QWebEngineView(); page = Page(profile, view); view.setPage(page)
    view.resize(1600,1000); view.show(); view.load(QUrl(sys.argv[1]))

    def js(code):
        loop = QEventLoop(); result = []
        page.runJavaScript(code,lambda value:(result.append(value),loop.quit()))
        QTimer.singleShot(10000,loop.quit); loop.exec_()
        assert result, 'Browser JavaScript timed out'
        return result[0]

    def wait(condition):
        for _ in range(150):
            if js(condition): return
            loop = QEventLoop(); QTimer.singleShot(100,loop.quit); loop.exec_()
        raise AssertionError('Browser state did not become ready: '+condition)

    def click(identifier): js(f"document.getElementById('{identifier}').click()")

    def capture(name):
        loop = QEventLoop(); QTimer.singleShot(200,loop.quit); loop.exec_()
        view.grab().save(str(output/name))

    def point_event(kind, event_type):
        js(f"window.pointKind='{kind}';window.pointEvent='{event_type}'")
        js("{const c=document.getElementById('timeline'),r=c.getBoundingClientRect(),point=window.actionTimeline[window.pointKind][0],x=r.left+90+(window.pointKind==='receives'?28:12),y=r.top+(Number(point.timestamp_ns)/1e9-Number(c.dataset.startTime))/Number(c.dataset.timeSpan)*r.height;c.dispatchEvent(new MouseEvent(window.pointEvent,{clientX:x,clientY:y,bubbles:true}));}")

    try:
        wait("document.getElementById('trace-summary')?.title.includes('executions')")
        wait("!document.getElementById('timeline-view').hidden&&document.getElementById('grouping').value==='node'&&Number(document.getElementById('timeline').dataset.publishPoints)>0&&Number(document.getElementById('timeline').dataset.receivePoints)>0")
        capture('default-node-actions.png')
        for grouping in ['process','thread','node']:
            js(f"document.getElementById('grouping').value='{grouping}';document.getElementById('grouping').dispatchEvent(new Event('change'))")
            wait("document.getElementById('activity').innerText==='Ready'&&Number(document.getElementById('timeline').dataset.publishPoints)>0&&Number(document.getElementById('timeline').dataset.receivePoints)>0")
        assert js("[...document.querySelectorAll('nav button')].filter(b=>['timers-tab','topics-tab','services-tab','actions-tab'].includes(b.id)).map(b=>b.innerText).join(',')==='Timers,Topics,Services,Actions'")
        click('actions-tab')
        wait("document.querySelectorAll('#actions-table tbody tr[data-action]').length>0")
        js("window.initialActionTotal=document.querySelectorAll('#actions-table tbody tr[data-action]').length;window.actionName=document.querySelector('#actions-table tbody tr').dataset.action;window.initialFilters=document.getElementById('filter-count').innerText")
        capture('actions.png')
        js("document.querySelector('#actions-table tbody button').click()")
        wait("document.querySelectorAll('#selection .action-channels button').length===5")
        assert js("document.querySelector('main').getBoundingClientRect().right<=document.getElementById('details').getBoundingClientRect().left+1")
        assert js("document.getElementById('filter-count').innerText===window.initialFilters")
        capture('action-details.png')
        js("[...document.querySelectorAll('#selection .action-channels button')].find(b=>b.innerText.startsWith('Feedback')).click()")
        wait("document.querySelector('#details h2').innerText==='Topic'")
        assert js("document.getElementById('selection').innerText.includes(window.actionName+'/_action/feedback')")
        click('toggle-filters');click('filter-topics')
        assert js("document.getElementById('hide-parameter-events').checked")
        assert js("[...document.querySelectorAll('#topics input')].every(b=>!b.value.includes('/_action/'))")
        click('filter-services')
        assert js("document.getElementById('hide-default-services').checked")
        assert js("[...document.querySelectorAll('#services input')].every(b=>!b.value.includes('/_action/'))")
        click('filter-actions')
        assert js("[...document.querySelectorAll('#actions input')].some(b=>b.value===window.actionName)")
        js("document.getElementById('search').value=window.actionName.toUpperCase();document.getElementById('search').dispatchEvent(new Event('input'))")
        assert js("[...document.querySelectorAll('#actions input')].filter(b=>!b.parentElement.hidden&&b.value!=='__all__').every(b=>b.value===window.actionName)")
        click('clear')
        assert js("document.getElementById('filter-count').innerText===window.initialFilters")
        click('close-filters')
        wait("!document.getElementById('actions-view').hidden")
        assert js("document.querySelectorAll('#actions-table tbody tr[data-action]').length===window.initialActionTotal"), 'Cancel must discard draft choices'
        click('toggle-filters');click('filter-actions');click('clear')
        js("[...document.querySelectorAll('#actions input')].find(b=>b.value===window.actionName).click()")
        click('action-columns');click('apply-filters')
        wait("document.getElementById('actions-summary').innerText==='1 action'")
        click('actions-timeline')
        wait("!document.getElementById('timeline-view').hidden&&Number(document.getElementById('timeline').dataset.publishPoints)>0&&Number(document.getElementById('timeline').dataset.receivePoints)>0")
        wait("document.getElementById('activity').innerText==='Ready'")
        assert js("document.getElementById('grouping').value==='action'")
        assert js("document.getElementById('lane-head').innerText===window.actionName")
        js("window.actionTimeline=null;fetch('/api/timeline?grouping=action&actions='+encodeURIComponent(window.actionName)).then(r=>r.json()).then(d=>window.actionTimeline=d)")
        wait('window.actionTimeline!==null')
        assert js("Number(document.getElementById('timeline').dataset.publishPoints)===window.actionTimeline.publications.length")
        assert js("Number(document.getElementById('timeline').dataset.receivePoints)===window.actionTimeline.receives.length")
        capture('action-timeline.png')
        point_event('receives','mousemove')
        assert js("!document.getElementById('tooltip').hidden&&document.getElementById('tooltip').innerText.includes('Receive')")
        point_event('receives','click')
        wait("document.querySelector('#details h2').innerText==='Receive'")
        assert js("document.getElementById('selection').innerText.includes('rmw_take')&&document.getElementById('selection').innerText.includes(window.actionName)")
        capture('action-receive.png')
        click('close-details');wait("document.getElementById('activity').innerText==='Ready'")
        point_event('publications','click')
        wait("document.querySelector('#details h2').innerText==='Publish'")
        assert js("document.getElementById('selection').innerText.includes('/_action/')")
        click('toggle-filters');click('filter-actions');click('clear');click('apply-filters')
        wait("document.getElementById('lane-page').innerText==='No columns'")
        assert js("Number(document.getElementById('timeline').dataset.publishPoints)===0&&Number(document.getElementById('timeline').dataset.receivePoints)===0")
        click('actions-tab');wait("document.getElementById('actions-summary').innerText==='0 actions'")
        click('toggle-filters');click('reset-filters');click('apply-filters')
        wait("document.querySelectorAll('#actions-table tbody tr[data-action]').length===window.initialActionTotal")
        for resource in ['topics','services']:
            click(resource+'-tab');wait("document.getElementById('activity').innerText==='Ready'")
            assert js(f"!document.getElementById('{resource}-table').innerText.includes('/_action/')")
        assert js("document.getElementById('error').hidden"),js("document.getElementById('error').innerText")
        assert not page.errors,page.errors
        print(json.dumps({'browser':'passed','action':js('window.actionName'),'screenshots':str(output)}))
    finally:
        view.close();page.deleteLater();view.deleteLater();profile.deleteLater();app.processEvents()


if __name__ == '__main__': main()
