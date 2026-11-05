#!/usr/bin/env python3
"""Exercise action/channel navigation and explicit Apply in a real Chromium UI."""
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
    view.resize(1600, 1000); view.show(); view.load(QUrl(sys.argv[1]))

    def js(code):
        loop = QEventLoop(); result = []
        page.runJavaScript(code, lambda value: (result.append(value), loop.quit()))
        QTimer.singleShot(10000, loop.quit); loop.exec_()
        assert result, 'Browser JavaScript timed out'
        return result[0]

    def wait(condition):
        for _ in range(150):
            if js(condition): return
            loop = QEventLoop(); QTimer.singleShot(100, loop.quit); loop.exec_()
        raise AssertionError('Browser state did not become ready: '+condition)

    def capture(name):
        loop = QEventLoop(); QTimer.singleShot(200, loop.quit); loop.exec_()
        view.grab().save(str(output/name))

    try:
        wait("document.getElementById('trace-summary')?.title.includes('executions')")
        js("window.traceInfo=null;fetch('/api/trace/info').then(r=>r.json()).then(d=>window.traceInfo=d)")
        wait('window.traceInfo!==null')
        if js('window.traceInfo.known_execution_count===0'):
            wait("!document.getElementById('actions-view').hidden")
        js("document.getElementById('actions-tab').click()")
        wait("document.querySelectorAll('#actions-table tbody tr[data-action]').length===1")
        js("window.actionName=document.querySelector('#actions-table tbody tr').dataset.action;window.initialFilters=document.getElementById('filter-count').innerText")
        assert js("document.querySelector('#actions-view .transport-badge').innerText==='Transport only'")
        capture('actions.png')
        js("document.querySelector('#actions-table tbody button').click()")
        wait("document.querySelectorAll('#selection .action-channels button').length===5")
        assert js("document.getElementById('selection').innerText.includes('Goal IDs / timing')")
        assert js("document.getElementById('filter-count').innerText===window.initialFilters")
        assert js("document.querySelector('#actions-table tbody tr').classList.contains('selected')")
        assert js("document.querySelector('main').getBoundingClientRect().right<=document.getElementById('details').getBoundingClientRect().left+1")
        capture('action-details.png')
        js("[...document.querySelectorAll('#selection .action-channels button')].find(b=>b.innerText.startsWith('Feedback')).click()")
        wait("document.querySelector('#details h2').innerText==='Topic'")
        assert js("document.getElementById('selection').innerText.includes(window.actionName+'/_action/feedback')")
        js("document.getElementById('actions-tab').click();document.querySelector('#actions-table tbody button').click()")
        wait("document.querySelectorAll('#selection .action-channels button').length===5")
        js("[...document.querySelectorAll('#selection .action-channels button')].find(b=>b.innerText.startsWith('Send goal')).click()")
        wait("document.querySelector('#details h2').innerText==='Service'")
        assert js("document.getElementById('filter-count').innerText===window.initialFilters")
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click();document.querySelector('#topics input[value=__all__]').click();[...document.querySelectorAll('#topics input')].find(b=>b.value===window.actionName+'/_action/feedback').click()")
        assert js("document.getElementById('filter-count').innerText===window.initialFilters")
        js("document.getElementById('apply-filters').click()")
        wait("!document.getElementById('actions-view').hidden&&document.querySelector('#actions-table tbody tr').cells[2].innerText==='—'")
        js("document.querySelector('#actions-table tbody button').click()")
        wait("document.querySelectorAll('#selection .action-channels button').length===4")
        assert js("![...document.querySelectorAll('#selection .action-channels button')].some(b=>b.innerText.startsWith('Status'))")
        capture('action-filtered.png')
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click();document.getElementById('clear').click();document.getElementById('close-filters').click()")
        wait("!document.getElementById('actions-view').hidden")
        assert js("document.querySelector('#actions-table tbody tr').cells[1].innerText!=='—'"), 'Cancel must discard draft choices'
        js("document.getElementById('toggle-filters').click();document.getElementById('filter-topics').click();document.getElementById('clear').click();document.getElementById('filter-services').click();document.getElementById('clear').click();document.getElementById('apply-filters').click()")
        wait("document.getElementById('actions-summary').innerText==='0 actions'")
        assert js("document.querySelectorAll('#actions-table tbody tr[data-action]').length===0")
        js("document.getElementById('toggle-filters').click();document.getElementById('reset-filters').click();document.getElementById('apply-filters').click()")
        wait("document.getElementById('actions-summary').innerText==='1 action'")
        assert js("document.getElementById('error').hidden"), js("document.getElementById('error').innerText")
        assert not page.errors, page.errors
        print(json.dumps({'browser':'passed','action':js('window.actionName'),'screenshots':str(output)}))
    finally:
        view.close();page.deleteLater();view.deleteLater();profile.deleteLater();app.processEvents()


if __name__ == '__main__': main()
