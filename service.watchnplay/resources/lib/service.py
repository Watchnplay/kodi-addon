# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""WatchNPlay-Dienst: laeuft ab Kodi-Start im Hintergrund."""

import json

import xbmc

from resources.lib import library, player, util
from resources.lib.api import Api
from resources.lib.store import Store
from resources.lib.sync import SyncEngine

STARTUP_DELAY = 30
TRACK_EVERY = 5


class Ui(object):
    def notify(self, string_id, *args):
        text = util.lang(string_id)
        if args:
            text = text % args
        util.notify(text)

    def status(self, paired, user, device, pro_paused):
        util.set_status(paired, user, device, pro_paused)

    def log(self, msg):
        util.log(msg)

    def warn(self, msg):
        util.warn(msg)


def make_api(token):
    return Api(util.api_base(), util.user_agent(), token)


class Service(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        self.store = Store(util.profile_dir())
        self.tracker = None
        self.engine = SyncEngine(self.store, library, Ui(), make_api,
                                 wait=self._wait, min_percent=util.watched_threshold)
        self.tracker = player.Tracker(self.engine.queue_play)

    def _wait(self, seconds):
        """Pause zwischen Abgleich-Haeppchen: Position der Wiedergabe weiter mitschreiben."""
        if self.tracker is not None:
            try:
                self.tracker.update()
            except Exception:
                pass
        return self.waitForAbort(seconds)

    def onNotification(self, sender, method, data):
        try:
            if method == 'VideoLibrary.OnScanFinished':
                self.engine.request_full()
            elif method == 'VideoLibrary.OnUpdate':
                payload = json.loads(data or '{}')
                if 'playcount' not in payload:
                    return
                item = payload.get('item') or {}
                if item.get('type') in ('movie', 'episode') and (item.get('id') or 0) > 0:
                    self.engine.on_library_update('%s:%d' % (item['type'], item['id']))
            elif sender == util.ADDON_ID:
                if method == 'Other.paired':
                    self.engine.on_paired()
                elif method == 'Other.unpaired':
                    self.engine.on_unpaired()
                elif method == 'Other.syncnow':
                    self.engine.sync_now()
        except Exception as exc:
            util.warn('notification %s not handled: %s' % (method, exc))

    def run(self):
        util.log('service started')
        if self.waitForAbort(STARTUP_DELAY):
            return
        self.engine.refresh_ui()
        ticks = 0
        while not self.abortRequested():
            try:
                if ticks % TRACK_EVERY == 0:
                    self.tracker.update()
                self.engine.tick()
            except Exception as exc:
                util.warn('sync tick failed: %s' % exc)
            ticks += 1
            if self.waitForAbort(1):
                break
        util.log('service stopped')
