#!/usr/bin/env python3
"""Inspect real action handlers, call markers and blocking waits in Chromium."""
from pathlib import Path
import sys
from browser_smoke import Page
from PyQt5.QtCore import QEventLoop,QTimer,QUrl
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebEngineWidgets import QWebEngineProfile,QWebEngineView


def main():
    app=QApplication([]);output=Path(sys.argv[2]);output.mkdir(parents=True,exist_ok=True)
    profile=QWebEngineProfile();view=QWebEngineView();page=Page(profile,view);view.setPage(page)
    view.resize(1600,1000);view.show();view.load(QUrl(sys.argv[1]))
    def js(code):
        result=[];loop=QEventLoop()
        page.runJavaScript(code,lambda value:(result.append(value),loop.quit()))
        QTimer.singleShot(10000,loop.quit);loop.exec_();assert result;return result[0]
    def wait(condition):
        for _ in range(150):
            if js(condition):return
            loop=QEventLoop();QTimer.singleShot(100,loop.quit);loop.exec_()
        raise AssertionError(condition)
    def click(identity):js(f"document.getElementById('{identity}').click()")
    def capture(name):
        loop=QEventLoop();QTimer.singleShot(200,loop.quit);loop.exec_();view.grab().save(str(output/name))
    try:
        wait("document.getElementById('timeline')?.dataset.callMarkers>0")
        assert js("document.getElementById('grouping').value==='node'")
        capture('timeline.png')
        click('statistics-tab')
        wait("document.querySelectorAll('#stats-table tbody tr[data-callback-type=action]').length===7")
        assert js("[...document.querySelectorAll('#stats-table tbody tr[data-callback-type=action]')].every(r=>r.innerText.includes('/viewer_demo/fibonacci')&&r.querySelector('.callback-type').innerText.startsWith('Action · '))")
        capture('action-statistics.png')
        click('boxplots-tab')
        wait("document.querySelectorAll('#boxplot-rows [data-callback-type=action]').length===7")
        assert js("[...document.querySelectorAll('#boxplot-rows [data-callback-type=action] canvas')].every(c=>c.width>0)")
        capture('action-boxplots.png')
        click('statistics-tab')
        js("[...document.querySelectorAll('#stats-table tbody tr[data-callback-type=action]')].find(r=>r.innerText.includes('Action · Execute')).querySelector('button').click()")
        wait("document.getElementById('selection').innerText.includes('Handler')")
        js("document.querySelector('#selection .selection-actions button').click()")
        wait("document.getElementById('selection').innerText.includes('Wall-clock duration')")
        click('reset-zoom');wait("document.getElementById('activity').innerText==='Ready'")
        capture('action-execution.png')
        click('statistics-tab')
        js("[...document.querySelectorAll('#stats-table tbody tr[data-callback-type=timer]')].find(r=>r.innerText.includes('/viewer_demo/action_client')).querySelector('button').click()")
        wait("document.getElementById('selection').innerText.includes('Total callback duration')")
        js("document.querySelector('#selection .selection-actions button').click()")
        wait("document.getElementById('selection').innerText.includes('Calls (')")
        click('reset-zoom');wait("document.getElementById('activity').innerText==='Ready'&&Number(document.getElementById('timeline').dataset.callMarkers)>=2")
        capture('callback-calls.png')
        js("[...document.querySelectorAll('#selection .publish-list button')].find(b=>b.innerText.includes('/add_two_ints')).click()")
        wait("document.querySelector('#details h2').innerText==='Service call'")
        assert js("document.getElementById('selection').innerText.includes('Recorded wait')")
        capture('blocking-service-call.png')
        js("document.querySelector('#selection>button').click()")
        wait("document.getElementById('selection').innerText.includes('Calls (')")
        js("[...document.querySelectorAll('#selection .publish-list button')].find(b=>b.innerText.includes('fibonacci')).click()")
        wait("document.querySelector('#details h2').innerText==='Action call'")
        capture('action-call.png')
        click('services-tab');wait("document.querySelector('#services-table tbody button')!==null")
        assert js("document.getElementById('services-table').innerText.includes('/viewer_demo/add_two_ints')")
        assert js("document.getElementById('error').hidden"),js("document.getElementById('error').innerText")
        assert not page.errors,page.errors
        print('Full demonstration browser checks passed')
    finally:
        view.close();page.deleteLater();view.deleteLater();profile.deleteLater();app.processEvents()


if __name__=='__main__':main()
