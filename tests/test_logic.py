# -*- coding: utf-8 -*-
"""Offline-Tests der Add-on-Logik (ohne Kodi). Start: python kodi/tests/test_logic.py"""

import datetime
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.path.join(os.path.dirname(HERE), 'service.watchnplay')


# --- Kodi-Module stubben ---
def _install_stubs():
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 0, 1, 2, 3
    xbmc.logged = []
    xbmc.log = lambda msg, level=1: xbmc.logged.append(msg)
    xbmc.getInfoLabel = lambda label: ''
    xbmc.getCondVisibility = lambda cond: False
    xbmc.executeJSONRPC = lambda payload: '{"result":{}}'
    xbmc.executebuiltin = lambda cmd: None

    class Player(object):
        def __init__(self):
            pass

    class Monitor(object):
        def __init__(self):
            pass

        def abortRequested(self):
            return False

        def waitForAbort(self, t=0):
            return False

    xbmc.Player = Player
    xbmc.Monitor = Monitor

    xbmcaddon = types.ModuleType('xbmcaddon')

    class Addon(object):
        settings = {}

        def __init__(self, addon_id=None):
            pass

        def getAddonInfo(self, key):
            return {'version': '0.1.0', 'path': ADDON_DIR, 'icon': ''}.get(key, '')

        def getLocalizedString(self, sid):
            return {32006: 'Connected as %s'}.get(sid, 'S%d' % sid)

        def getSetting(self, key):
            return Addon.settings.get(key, '')

        def setSetting(self, key, value):
            Addon.settings[key] = value

        def setSettingBool(self, key, value):
            Addon.settings[key] = value

    xbmcaddon.Addon = Addon

    xbmcgui = types.ModuleType('xbmcgui')

    class Dialog(object):
        def notification(self, *a, **k):
            pass

    xbmcgui.Dialog = Dialog
    xbmcgui.WindowDialog = object

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.exists = lambda p: False

    for name, mod in (('xbmc', xbmc), ('xbmcaddon', xbmcaddon), ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs)):
        sys.modules[name] = mod


_install_stubs()
sys.path.insert(0, ADDON_DIR)

from resources.lib import library, player, store as store_mod, util  # noqa: E402
from resources.lib.api import Api, ProRequired, Retryable, Unauthorized  # noqa: E402
from resources.lib import sync  # noqa: E402


class Clock(object):
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeUi(object):
    def __init__(self):
        self.notes = []
        self.states = []
        self.logs = []
        self.warns = []

    def notify(self, sid, *args):
        self.notes.append(sid)

    def status(self, paired, user, device, paused):
        self.states.append((paired, paused))

    def log(self, msg):
        self.logs.append(msg)

    def warn(self, msg):
        self.warns.append(msg)


class FakeLib(object):
    chunks = staticmethod(library.chunks)

    def __init__(self, items=None):
        self.items = items or []
        self.set_calls = []

    def collect_all(self):
        return [dict(i) for i in self.items]

    def get_item(self, kid):
        for i in self.items:
            if i['kid'] == kid:
                return dict(i)
        return None

    def set_watched(self, kid, watched, has_lp):
        self.set_calls.append((kid, watched, has_lp))
        return True


class FakeApi(object):
    def __init__(self):
        self.status_res = {'userName': 'Jens', 'deviceName': 'TV', 'pro': True, 'backSync': True, 'version': 'v1'}
        self.library_res = {'mark': [], 'unmark': [], 'backSync': True}
        self.raise_on = {}
        # Fehler erst beim n-ten Aufruf (1-basiert), z. B. {'library': (2, Retryable(429))}
        self.raise_at = {}
        self.calls = []
        self.counts = {}
        self.min_percents = []

    def _maybe(self, name):
        self.calls.append(name)
        self.counts[name] = self.counts.get(name, 0) + 1
        at = self.raise_at.get(name)
        if at and at[0] == self.counts[name]:
            raise at[1]
        exc = self.raise_on.get(name)
        if exc:
            raise exc

    def status(self):
        self._maybe('status')
        return dict(self.status_res)

    def library(self, items, full):
        self._maybe('library')
        self.calls.append(('library', len(items), full))
        return dict(self.library_res)

    def plays(self, plays, min_percent=None):
        self._maybe('plays')
        self.min_percents.append(min_percent)
        return {'accepted': len(plays)}


class Waiter(object):
    """Ersatz fuer Monitor.waitForAbort: merkt sich Pausen, bricht auf Wunsch ab."""

    def __init__(self):
        self.waits = []
        self.abort_after = None

    def __call__(self, seconds):
        self.waits.append(seconds)
        return self.abort_after is not None and len(self.waits) >= self.abort_after


def make_engine(tmp, items=None, min_percent=None):
    st = store_mod.Store(tmp)
    st.set_auth('secret-token', 'Jens', True)
    api = FakeApi()
    ui = FakeUi()
    lib = FakeLib(items)
    clock = Clock()
    eng = sync.SyncEngine(st, lib, ui, lambda token: api, clock=clock, wait=Waiter(), min_percent=min_percent)
    return eng, st, api, ui, lib, clock


class LibraryTests(unittest.TestCase):
    def test_lastplayed_local_to_epoch_ms(self):
        expected = int(datetime.datetime(2026, 10, 4, 20, 15, 30).timestamp() * 1000)
        self.assertEqual(library.lastplayed_to_ms('2026-10-04 20:15:30'), expected)
        self.assertIsNone(library.lastplayed_to_ms(''))
        self.assertIsNone(library.lastplayed_to_ms(None))
        self.assertIsNone(library.lastplayed_to_ms('kaputt'))

    def test_movie_item_ids_from_uniqueid(self):
        m = {'movieid': 12, 'title': 'The Matrix', 'year': 1999, 'playcount': 2,
             'lastplayed': '2026-01-02 03:04:05', 'uniqueid': {'tmdb': '603', 'imdb': 'tt0133093'}, 'imdbnumber': ''}
        item = library.build_movie_item(m)
        self.assertEqual(item['kid'], 'movie:12')
        self.assertEqual(item['ids'], {'tmdb': 603, 'imdb': 'tt0133093'})
        self.assertTrue(item['watched'])
        self.assertEqual(item['year'], 1999)
        self.assertIn('lastPlayed', item)

    def test_movie_imdbnumber_fallback_and_junk(self):
        m = {'movieid': 1, 'title': 'X', 'year': 0, 'playcount': 0, 'lastplayed': '',
             'uniqueid': {'tmdb': ''}, 'imdbnumber': 'tt1234567'}
        item = library.build_movie_item(m)
        self.assertEqual(item['ids'], {'imdb': 'tt1234567'})
        self.assertFalse(item['watched'])
        self.assertNotIn('year', item)
        self.assertNotIn('lastPlayed', item)
        # imdbnumber ohne tt (alte Scraper: tvdb-Id) wird nicht als IMDb gesendet
        self.assertEqual(library.parse_ids({}, '81189'), {})

    def test_episode_item_with_show(self):
        shows = {7: library.build_show({'tvshowid': 7, 'title': 'Breaking Bad', 'year': 2008,
                                        'uniqueid': {'tvdb': '81189', 'tmdb': '1396'}, 'imdbnumber': 'tt0903747'})}
        e = {'episodeid': 99, 'tvshowid': 7, 'title': 'Pilot', 'season': 1, 'episode': 1, 'playcount': 1,
             'lastplayed': '', 'uniqueid': {'tvdb': '349232'}}
        item = library.build_episode_item(e, shows)
        self.assertEqual(item['kid'], 'episode:99')
        self.assertEqual(item['ids'], {'tvdb': 349232})
        self.assertEqual(item['show'], {'title': 'Breaking Bad', 'year': 2008,
                                        'ids': {'tvdb': 81189, 'tmdb': 1396, 'imdb': 'tt0903747'}})
        self.assertEqual((item['season'], item['episode']), (1, 1))

    def test_chunking_at_500(self):
        sizes = [len(c) for c in library.chunks(list(range(1201)))]
        self.assertEqual(sizes, [500, 500, 201])
        self.assertEqual(list(library.chunks([])), [])

    def test_parse_kid(self):
        self.assertEqual(library.parse_kid('movie:5'), ('movie', 5))
        self.assertEqual(library.parse_kid('episode:0'), (None, None))
        self.assertEqual(library.parse_kid('song:5'), (None, None))


class PlayerTests(unittest.TestCase):
    def test_percent_threshold(self):
        self.assertTrue(player.reached(player.percent(4860, 5400), 90))
        self.assertFalse(player.reached(player.percent(4850, 5400), 90))
        self.assertEqual(player.percent(10, 0), 0.0)
        self.assertEqual(util.parse_threshold('<advancedsettings><video><playcountminimumpercent>85'
                                              '</playcountminimumpercent></video></advancedsettings>'), 85.0)
        self.assertIsNone(util.parse_threshold('<advancedsettings/>'))

    def test_library_items_are_ignored(self):
        self.assertTrue(player.is_library_item({'id': 4, 'type': 'movie'}))
        self.assertFalse(player.is_library_item({'id': -1, 'type': 'movie'}))
        self.assertFalse(player.is_library_item({'type': 'unknown'}))

    def test_build_play_from_plugin(self):
        info = {'type': 'episode', 'title': 'Ozymandias', 'showtitle': 'Breaking Bad', 'season': 5, 'episode': 14,
                'year': 0, 'uniqueid': {'tmdb': '62161', 'tvshow.tmdb': '1396'},
                'file': 'plugin://plugin.video.netflix/play/show/1/season/2/episode/3/'}
        play = player.build_play(info, 1_700_000_000_000, 93.24)
        self.assertEqual(play['type'], 'episode')
        self.assertEqual(play['plugin'], 'plugin.video.netflix')
        self.assertEqual(play['ids'], {'tmdb': 62161})
        self.assertEqual(play['show'], {'title': 'Breaking Bad', 'ids': {'tmdb': 1396}})
        self.assertEqual(play['percent'], 93.2)
        self.assertNotIn('year', play)
        self.assertIsNone(player.build_play({'title': '', 'showtitle': ''}, 0, 99))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_queue_roundtrip(self):
        s = store_mod.Store(self.tmp)
        s.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        s.add_delta('movie:3', 123.0)
        s.save_if_dirty()  # Deltas werden gesammelt geschrieben
        s.set_auth('tok', 'Jens', False)
        s.state['version'] = 'v9'
        s.save_state()
        s2 = store_mod.Store(self.tmp)
        self.assertEqual(s2.plays[0]['title'], 'A')
        self.assertEqual(s2.deltas, {'movie:3': 123.0})
        self.assertEqual(s2.token, 'tok')
        self.assertEqual(s2.state['version'], 'v9')
        s2.clear_auth()
        self.assertIsNone(store_mod.Store(self.tmp).token)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_reconcile_chunks_and_applies(self):
        items = [{'kid': 'movie:%d' % i, 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': False}
                 for i in range(1, 1102)]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        api.library_res = {'mark': ['movie:1'], 'unmark': ['movie:2'], 'backSync': True}
        eng.tick()
        lib_calls = [c for c in api.calls if isinstance(c, tuple)]
        self.assertEqual(lib_calls, [('library', 500, True), ('library', 500, True), ('library', 101, True)])
        # movie:2 ist nicht gesehen -> unmark ueberflüssig; movie:1 wird einmal markiert (erster Chunk)
        self.assertEqual(lib.set_calls, [('movie:1', True, False)])
        self.assertFalse(eng.full_pending)

    def test_loop_guard_suppresses_own_marks(self):
        items = [{'kid': 'movie:1', 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': False}]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        api.library_res = {'mark': ['movie:1'], 'unmark': []}
        eng.tick()
        self.assertEqual(lib.set_calls, [('movie:1', True, False)])
        # Kodi meldet unsere eigene Aenderung per OnUpdate: nicht zurueckmelden
        self.assertFalse(eng.on_library_update('movie:1'))
        self.assertEqual(st.deltas, {})
        # andere Titel und spaeter wieder normal
        self.assertTrue(eng.on_library_update('movie:2'))
        clock.t += sync.GUARD_TTL + 1
        self.assertTrue(eng.on_library_update('movie:1'))

    def test_delta_debounce(self):
        items = [{'kid': 'movie:1', 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': True}]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        eng.tick()
        api.calls[:] = []
        clock.t += 1
        eng.on_library_update('movie:1')
        eng.tick()
        self.assertNotIn(('library', 1, False), api.calls)
        clock.t += sync.DEBOUNCE
        eng.tick()
        self.assertIn(('library', 1, False), api.calls)
        self.assertEqual(st.deltas, {})

    def test_401_clears_token_and_notifies_once(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        api.raise_on['status'] = Unauthorized(401)
        eng.tick()
        self.assertIsNone(st.token)
        self.assertFalse(eng.paired)
        self.assertEqual(st.plays, [])
        self.assertEqual(ui.notes, [32018])
        eng.tick()
        self.assertEqual(ui.notes, [32018])
        self.assertEqual(api.calls.count('status'), 1)

    def test_402_pauses_notifies_daily_and_retries_every_6h(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        api.raise_on['status'] = ProRequired(402, {'code': 'PRO_REQUIRED'})
        eng.tick()
        self.assertTrue(eng.pro_paused)
        self.assertEqual(ui.notes, [32019])
        # waehrend der Pause keine Anfragen
        clock.t += 3600
        eng.tick()
        self.assertEqual(api.calls.count('status'), 1)
        # nach 6 h erneut, weiter 402: kein zweiter Hinweis am selben Tag
        clock.t += 5 * 3600 + 1
        st.state['proNotifiedDay'] = util.time.strftime('%Y-%m-%d', util.time.localtime(clock.t))
        eng.tick()
        self.assertEqual(api.calls.count('status'), 2)
        self.assertEqual(ui.notes, [32019])
        # wieder Pro: Pause endet, voller Abgleich folgt
        clock.t += 6 * 3600 + 1
        del api.raise_on['status']
        eng.tick()
        self.assertFalse(eng.pro_paused)
        eng.tick()
        self.assertIn(('library', 0, True), api.calls)
        # Wiedergaben werden waehrend der Pause weiter gesammelt
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        self.assertEqual(len(st.plays), 1)

    def test_retryable_backoff_keeps_queue(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        eng.full_pending = False
        st.state['lastFull'] = clock.t
        eng.last_status = clock.t
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        api.raise_on['plays'] = Retryable(503)
        delays = []
        for _ in range(5):
            eng.tick()
            delays.append(eng.next_retry - clock.t)
            clock.t = eng.next_retry
            eng.last_status = clock.t
        self.assertEqual(delays, [30, 120, 600, 1800, 1800])
        self.assertEqual(len(st.plays), 1)
        del api.raise_on['plays']
        eng.tick()
        self.assertEqual(st.plays, [])
        self.assertEqual(eng.fails, 0)

    def test_version_change_triggers_full(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        eng.tick()
        self.assertFalse(eng.full_pending)
        n_full = sum(1 for c in api.calls if c == ('library', 0, True))
        api.status_res['version'] = 'v2'
        clock.t += sync.STATUS_EVERY
        eng.tick()
        self.assertEqual(sum(1 for c in api.calls if c == ('library', 0, True)), n_full + 1)



# --- Audit-Fixes 0.1.1 ---

NETFLIX_EP = {'type': 'episode', 'title': 'Pilot', 'showtitle': 'Dark', 'season': 1, 'episode': 1,
              'uniqueid': {'tvshow.tmdb': '70523'}, 'file': 'plugin://plugin.video.netflix/play/1/'}
NETFLIX_MOVIE = {'type': 'movie', 'title': 'Roma', 'uniqueid': {'tmdb': '426426'},
                 'file': 'plugin://plugin.video.netflix/play/movie/2/'}


class ReportFilterTests(unittest.TestCase):
    """Fix 1: nur Filme/Folgen aus Video-Add-ons, keine Trailer, keine Plattformen."""

    def test_movie_and_episode_from_addon_are_reported(self):
        self.assertTrue(player.should_report(NETFLIX_MOVIE, 20 * 60)[0])
        self.assertTrue(player.should_report(NETFLIX_EP, 5 * 60)[0])

    def test_local_file_is_not_reported(self):
        self.assertFalse(player.should_report(dict(NETFLIX_MOVIE, file='/storage/videos/Roma.mkv'), 7200)[0])
        self.assertFalse(player.should_report(dict(NETFLIX_MOVIE, file='smb://nas/Roma.mkv'), 7200)[0])
        self.assertFalse(player.should_report(dict(NETFLIX_MOVIE, file=''), 7200)[0])

    def test_type_must_be_explicit(self):
        for mt in (None, '', 'unknown', 'video', 'musicvideo', 'tvshow'):
            info = dict(NETFLIX_MOVIE, type=mt)
            self.assertFalse(player.should_report(info, 7200)[0], mt)
            self.assertIsNone(player.build_play(info, 0, 95))

    def test_trailers_are_too_short(self):
        self.assertFalse(player.should_report(NETFLIX_MOVIE, 20 * 60 - 1)[0])
        self.assertFalse(player.should_report(NETFLIX_MOVIE, 150)[0])
        self.assertFalse(player.should_report(NETFLIX_EP, 5 * 60 - 1)[0])
        self.assertFalse(player.should_report(NETFLIX_EP, 0)[0])

    def test_video_platforms_are_denied(self):
        for pid in ('plugin.video.youtube', 'plugin.video.vimeo', 'plugin.video.dailymotion_com',
                    'plugin.video.twitch', 'plugin.video.invidious', 'plugin.video.tubed'):
            info = dict(NETFLIX_MOVIE, file='plugin://%s/play/?video_id=x' % pid)
            self.assertFalse(player.should_report(info, 7200)[0], pid)

    def test_library_index_matches_movies_and_episodes(self):
        shows = {7: library.build_show({'tvshowid': 7, 'title': 'Dark', 'uniqueid': {'tmdb': '70523'}})}
        items = [
            library.build_movie_item({'movieid': 1, 'title': 'Roma', 'uniqueid': {'imdb': 'tt6155172'},
                                      'playcount': 0}),
            library.build_movie_item({'movieid': 2, 'title': 'Other', 'uniqueid': {'tmdb': '426426'},
                                      'playcount': 0}),
            library.build_episode_item({'episodeid': 9, 'tvshowid': 7, 'season': 1, 'episode': 1,
                                        'playcount': 0}, shows),
        ]
        index = library.build_index(items)
        movie = player.build_play(NETFLIX_MOVIE, 0, 95)
        self.assertTrue(library.in_index(index, movie))
        self.assertTrue(library.in_index(index, {'type': 'movie', 'ids': {'imdb': 'tt6155172'}}))
        self.assertFalse(library.in_index(index, {'type': 'movie', 'ids': {'tmdb': 1}}))
        ep = player.build_play(NETFLIX_EP, 0, 95)
        self.assertTrue(library.in_index(index, ep))
        self.assertFalse(library.in_index(index, dict(ep, episode=2)))
        self.assertFalse(library.in_index(index, {'type': 'episode', 'show': {'ids': {'tmdb': 70523}}}))
        self.assertFalse(library.in_index(None, movie))


class EngineLibraryDropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_play_of_library_item_is_dropped(self):
        items = [{'kid': 'movie:2', 'type': 'movie', 'title': 'Roma', 'ids': {'tmdb': 426426}, 'watched': False}]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        # vor dem ersten Lesen gemerkt, beim Senden aussortiert
        self.assertTrue(eng.queue_play(player.build_play(NETFLIX_MOVIE, 0, 95)))
        eng.tick()
        self.assertEqual(st.plays, [])
        self.assertNotIn('plays', api.calls)
        # danach gar nicht erst gemerkt; fremde Titel weiter
        self.assertFalse(eng.queue_play(player.build_play(NETFLIX_MOVIE, 0, 95)))
        self.assertTrue(eng.queue_play(player.build_play(NETFLIX_EP, 0, 95)))
        eng.tick()
        self.assertIn('plays', api.calls)
        self.assertEqual(st.plays, [])


class FakeTag(object):
    def __getattr__(self, name):
        return lambda *a: ''


class TrackerTests(unittest.TestCase):
    """Fix 3: 'ended' zaehlt nie pauschal 100 %."""

    def make(self, info=None):
        played = []
        t = player.Tracker(played.append)
        t.state = {'playing': True, 'time': 0.0, 'total': 0.0, 'file': 'https://cdn/x.mpd'}
        t.isPlayingVideo = lambda: t.state['playing']
        t.getTime = lambda: t.state['time']
        t.getTotalTime = lambda: t.state['total']
        t.getPlayingFile = lambda: t.state['file']
        t.getVideoInfoTag = lambda: FakeTag()
        if info is not None:
            t._read_item = lambda: dict(info)
        return t, played

    def test_ended_uses_tracked_position(self):
        t, played = self.make(NETFLIX_MOVIE)
        t.state.update(time=10.0, total=6000.0)
        t.onAVStarted()
        self.assertIsNotNone(t.current)
        t.state['time'] = 3000.0
        t.update()
        t.state['playing'] = False
        t.onPlayBackEnded()  # HTTP-Quelle bricht bei 50 % ab
        self.assertEqual(played, [])

    def test_ended_near_end_is_reported_with_real_percent(self):
        t, played = self.make(NETFLIX_MOVIE)
        t.state.update(time=10.0, total=6000.0)
        t.onAVStarted()
        t.state['time'] = 5700.0
        t.update()
        t.onPlayBackEnded()
        self.assertEqual(len(played), 1)
        self.assertEqual(played[0]['percent'], 95.0)

    def test_local_file_is_not_tracked(self):
        t, played = self.make(dict(NETFLIX_MOVIE, file='/media/Roma.mkv'))
        t.state.update(time=10.0, total=6000.0, file='/media/Roma.mkv')
        t.onAVStarted()
        self.assertIsNone(t.current)

    def test_trailer_is_not_reported(self):
        t, played = self.make(NETFLIX_MOVIE)
        t.state.update(time=10.0, total=150.0)
        t.onAVStarted()
        t.state['time'] = 150.0
        t.update()
        t.onPlayBackEnded()
        self.assertEqual(played, [])

    def test_next_playlist_item_does_not_overwrite_position(self):
        t, played = self.make(NETFLIX_MOVIE)
        t.state.update(time=10.0, total=6000.0)
        t.onAVStarted()
        t.state['time'] = 5800.0
        t.update()
        # Folgetitel laeuft schon, onAVStarted fuer ihn kommt erst gleich
        t.state.update(time=1.0, total=60.0, file='https://cdn/next.mpd')
        t.update()
        self.assertEqual((t.position, t.total), (5800.0, 6000.0))

    def test_plugin_path_preferred_over_resolved_stream(self):
        t, played = self.make()
        old_rpc = library.rpc

        def fake_rpc(method, params=None):
            if method == 'Player.GetActivePlayers':
                return [{'type': 'video', 'playerid': 1}]
            return {'item': {'type': 'movie', 'id': -1, 'title': 'Roma', 'file': 'https://cdn/x.mpd'}}
        library.rpc = fake_rpc
        old_label = player.xbmc.getInfoLabel
        player.xbmc.getInfoLabel = lambda label: 'plugin://plugin.video.netflix/play/movie/2/'
        try:
            info = t._read_item()
        finally:
            library.rpc = old_rpc
            player.xbmc.getInfoLabel = old_label
        self.assertEqual(info['file'], 'plugin://plugin.video.netflix/play/movie/2/')


class SyncNowTests(unittest.TestCase):
    """Fix 2: 'Jetzt synchronisieren' in der Pro-Pause prueft sofort."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sync_now_rechecks_status_immediately(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        api.raise_on['status'] = ProRequired(402, {'code': 'PRO_REQUIRED'})
        eng.tick()
        self.assertTrue(eng.pro_paused)
        del api.raise_on['status']
        clock.t += 60
        eng.tick()
        self.assertEqual(api.calls.count('status'), 1)
        eng.sync_now()
        self.assertNotIn('proCheckAt', store_mod.Store(self.tmp).state)
        eng.tick()
        self.assertEqual(api.calls.count('status'), 2)
        self.assertFalse(eng.pro_paused)


class VersionTests(unittest.TestCase):
    """Fix 4: Version aus /library uebernehmen, voller Abgleich nur mit Rueck-Sync."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_library_version_is_adopted_without_status_call(self):
        items = [{'kid': 'movie:1', 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': True}]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        api.library_res = {'mark': [], 'unmark': [], 'backSync': True, 'version': 'v5'}
        eng.tick()
        self.assertEqual(api.calls.count('status'), 1)  # nur der regulaere vorab
        self.assertEqual(st.state['version'], 'v5')
        # Delta: Version NICHT uebernehmen (Review 08.10.2026). v6 kann eine Aenderung in
        # der App an einem anderen Titel enthalten, die mark/unmark dieser Antwort nicht kennt.
        api.library_res = dict(api.library_res, version='v6')
        clock.t += 1
        eng.on_library_update('movie:1')
        clock.t += sync.DEBOUNCE
        eng.tick()
        self.assertEqual(api.calls.count('status'), 1)
        self.assertEqual(st.state['version'], 'v5')
        # /status meldet v6: voller Abgleich holt die App-Aenderung nach Kodi
        api.status_res['version'] = 'v6'
        n_full = sum(1 for c in api.calls if c == ('library', 1, True))
        clock.t += sync.STATUS_EVERY
        eng.tick()
        self.assertEqual(sum(1 for c in api.calls if c == ('library', 1, True)), n_full + 1)
        self.assertEqual(st.state['version'], 'v6')

    def test_change_during_flush_is_not_swallowed(self):
        """Server-Stand v5 nach unserem /library, danach in der App geaendert (v7): voller Abgleich."""
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        api.library_res = {'mark': [], 'unmark': [], 'backSync': True, 'version': 'v5'}
        eng.tick()
        api.status_res['version'] = 'v7'
        clock.t += sync.STATUS_EVERY
        n_full = sum(1 for c in api.calls if c == ('library', 0, True))
        eng.tick()
        self.assertEqual(sum(1 for c in api.calls if c == ('library', 0, True)), n_full + 1)

    def test_plays_do_not_adopt_version(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        eng.tick()
        st.add_play(player.build_play(NETFLIX_EP, 1, 95))
        api.calls[:] = []
        eng.tick()
        self.assertEqual(api.calls, ['plays'])

    def test_version_change_without_backsync_needs_no_full(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        api.status_res['backSync'] = False
        eng.tick()
        n_full = sum(1 for c in api.calls if c == ('library', 0, True))
        api.status_res['version'] = 'v2'
        clock.t += sync.STATUS_EVERY
        eng.tick()
        self.assertEqual(sum(1 for c in api.calls if c == ('library', 0, True)), n_full)
        self.assertEqual(st.state['version'], 'v2')


class PacingTests(unittest.TestCase):
    """Fix 5: Pause zwischen Haeppchen, nach 429 weiter beim naechsten Haeppchen."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self, n):
        return [{'kid': 'movie:%d' % i, 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': False}
                for i in range(1, n + 1)]

    def lib_calls(self, api):
        return [c for c in api.calls if isinstance(c, tuple)]

    def test_pause_between_chunks(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, self.items(1201))
        eng.tick()
        self.assertEqual(eng.wait.waits, [sync.CHUNK_PAUSE, sync.CHUNK_PAUSE])
        self.assertNotIn('fullPass', st.state)

    def test_429_resumes_at_failed_chunk(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, self.items(1201))
        api.raise_at['library'] = (2, Retryable(429))
        eng.tick()
        self.assertEqual(self.lib_calls(api), [('library', 500, True)])
        self.assertEqual(store_mod.Store(self.tmp).state['fullPass']['next'], 1)
        self.assertTrue(eng.full_pending)
        clock.t = eng.next_retry
        api.calls[:] = []
        eng.last_status = clock.t
        eng.tick()
        self.assertEqual(self.lib_calls(api), [('library', 500, True), ('library', 201, True)])
        self.assertFalse(eng.full_pending)
        self.assertNotIn('fullPass', st.state)

    def test_count_change_restarts_pass(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, self.items(1201))
        api.raise_at['library'] = (2, Retryable(429))
        eng.tick()
        lib.items = self.items(1202)
        clock.t = eng.next_retry
        api.calls[:] = []
        eng.last_status = clock.t
        eng.tick()
        self.assertEqual(self.lib_calls(api),
                         [('library', 500, True), ('library', 500, True), ('library', 202, True)])

    def test_abort_keeps_position(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, self.items(1201))
        eng.wait.abort_after = 1
        eng.tick()
        self.assertEqual(self.lib_calls(api), [('library', 500, True)])
        self.assertEqual(store_mod.Store(self.tmp).state['fullPass']['next'], 1)


class ThresholdTests(unittest.TestCase):
    """Fix 6: Schwelle aus dem Master-Profil, 50..100, als minPercent an /plays."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clamp(self):
        self.assertEqual(util.clamp_threshold(None), 90.0)
        self.assertEqual(util.clamp_threshold(30), 50.0)
        self.assertEqual(util.clamp_threshold(150), 100.0)
        self.assertEqual(util.clamp_threshold(85), 85.0)
        self.assertEqual(util.parse_threshold('<advancedsettings><video><playcountminimumpercent>5'
                                              '</playcountminimumpercent></video></advancedsettings>'), 5.0)

    def test_reads_masterprofile(self):
        seen = []
        xbmcvfs = util.xbmcvfs

        class File(object):
            def __init__(self, path):
                pass

            def read(self):
                return ('<advancedsettings><video><playcountminimumpercent>40'
                        '</playcountminimumpercent></video></advancedsettings>')

            def close(self):
                pass

        old_exists = xbmcvfs.exists
        xbmcvfs.exists = lambda p: seen.append(p) or True
        xbmcvfs.File = File
        try:
            self.assertEqual(util.watched_threshold(), 50.0)
        finally:
            xbmcvfs.exists = old_exists
            del xbmcvfs.File
        self.assertEqual(seen, ['special://masterprofile/advancedsettings.xml'])
        self.assertEqual(util.watched_threshold(), 90.0)

    def test_min_percent_sent_with_plays(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, min_percent=lambda: 80.0)
        eng.tick()
        st.add_play(player.build_play(NETFLIX_EP, 1, 95))
        eng.tick()
        self.assertEqual(api.min_percents, [80.0])

    def test_api_body_contains_min_percent(self):
        import json
        sent = []

        class Resp(object):
            def getcode(self):
                return 200

            def read(self):
                return b'{"accepted":1}'

            def close(self):
                pass

        def opener(req, timeout=None):
            sent.append(req.data)
            return Resp()
        Api('https://x/api/kodi', 'ua', 'tok', opener=opener).plays([{'title': 'A'}], min_percent=85.0)
        Api('https://x/api/kodi', 'ua', 'tok', opener=opener).plays([{'title': 'A'}])
        self.assertEqual(json.loads(sent[0].decode('utf-8'))['minPercent'], 85.0)
        self.assertNotIn('minPercent', json.loads(sent[1].decode('utf-8')))


class SetWatchedTests(unittest.TestCase):
    """Fix 7: Markieren per Rueck-Sync setzt auch den Fortsetzen-Punkt zurueck."""

    def test_mark_clears_resume(self):
        import json
        sent = []
        old = library.xbmc.executeJSONRPC
        library.xbmc.executeJSONRPC = lambda payload: sent.append(json.loads(payload)) or '{"result":"OK"}'
        try:
            self.assertTrue(library.set_watched('movie:5', True, True))
            self.assertTrue(library.set_watched('episode:7', True, False))
            self.assertTrue(library.set_watched('episode:8', False, True))
        finally:
            library.xbmc.executeJSONRPC = old
        self.assertEqual(sent[0]['method'], 'VideoLibrary.SetMovieDetails')
        self.assertEqual(sent[0]['params']['resume'], {'position': 0, 'total': 0})
        self.assertEqual(sent[1]['params']['resume'], {'position': 0, 'total': 0})
        self.assertIn('lastplayed', sent[1]['params'])
        self.assertNotIn('resume', sent[2]['params'])
        self.assertEqual(sent[2]['params']['playcount'], 0)


class LibraryReadFailureTests(unittest.TestCase):
    """Fix 8: Lesefehler startet den Backoff statt sekuendlich neu."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backoff_on_read_failure(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        reads = []

        def broken():
            reads.append(1)
            raise RuntimeError('rpc down')
        lib.collect_all = broken
        eng.tick()
        self.assertEqual(eng.next_retry - clock.t, 30)
        clock.t += 1
        eng.tick()
        self.assertEqual(len(reads), 1)
        clock.t = eng.next_retry
        eng.tick()
        self.assertEqual(len(reads), 2)
        self.assertEqual(eng.next_retry - clock.t, 120)


class KodiDisabledTests(unittest.TestCase):
    """503 KODI_DISABLED: warten wie bei Retryable, Warteschlange bleibt, kein Hinweis."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_backs_off_quietly(self):
        from resources.lib import api as api_mod
        exc = api_mod.classify(503, {'code': 'KODI_DISABLED'})
        self.assertIsInstance(exc, Retryable)
        self.assertTrue(exc.kodi_disabled)
        self.assertFalse(api_mod.classify(503, {}).kodi_disabled)
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        st.add_play(player.build_play(NETFLIX_EP, 1, 95))
        api.raise_on['status'] = exc
        eng.tick()
        self.assertEqual(eng.next_retry - clock.t, 30)
        self.assertEqual(len(st.plays), 1)
        self.assertEqual(ui.notes, [])
        self.assertEqual(ui.warns, [])
        self.assertTrue(any('KODI_DISABLED' in m for m in ui.logs))
        self.assertTrue(eng.paired)


def movies(n, watched=False):
    return [{'kid': 'movie:%d' % i, 'type': 'movie', 'title': 'T', 'ids': {}, 'watched': watched}
            for i in range(1, n + 1)]


class ReviewTests(unittest.TestCase):
    """Befunde aus dem Review von xbmc/repo-scripts#2904 (08.10.2026)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lib_calls(self, api):
        return [c for c in api.calls if isinstance(c, tuple)]

    # 1: Korrektur des Nutzers direkt nach unserer Markierung geht nicht verloren
    def test_guard_only_swallows_our_own_state(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1))
        api.library_res = {'mark': ['movie:1'], 'unmark': []}
        eng.tick()
        self.assertFalse(eng.on_library_update('movie:1', 1))   # unser eigenes Markieren
        self.assertTrue(eng.on_library_update('movie:1', 0))    # Nutzer nimmt es zurueck
        self.assertIn('movie:1', st.deltas)

    # 2: endgueltig abgelehntes Haeppchen verwirft nicht die folgenden
    def test_rejected_chunk_does_not_drop_the_rest(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1201))
        st.add_delta('movie:700', clock.t - 100)   # liegt im abgelehnten 2. Haeppchen
        st.add_delta('movie:1100', clock.t - 100)  # liegt im 3. Haeppchen
        api.raise_at['library'] = (2, sync.ApiError(400))
        eng.tick()
        # 2. Haeppchen abgelehnt (der Fake zaehlt es nur in counts), 3. trotzdem gesendet,
        # danach die Aenderung aus dem abgelehnten Haeppchen einzeln
        self.assertEqual(api.counts['library'], 4)
        self.assertEqual(self.lib_calls(api),
                         [('library', 500, True), ('library', 201, True), ('library', 1, False)])
        self.assertFalse(eng.full_pending)
        self.assertEqual(st.deltas, {})

    def test_deltas_kept_on_retryable(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(150))
        eng.tick()
        for i in range(1, 151):
            st.add_delta('movie:%d' % i, clock.t)
        clock.t += sync.DEBOUNCE
        api.raise_at['library'] = (api.counts['library'] + 1, Retryable(503))
        eng.tick()
        self.assertEqual(len(st.deltas), 150)
        self.assertEqual(len(store_mod.Store(self.tmp).deltas), 150)

    # 3: Fortsetzen nur, wenn es dieselben Eintraege sind, nicht nur dieselbe Anzahl
    def test_same_count_other_items_restarts_pass(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1201))
        api.raise_at['library'] = (2, Retryable(429))
        eng.tick()
        items = movies(1201)
        items[0] = {'kid': 'movie:9999', 'type': 'movie', 'title': 'Neu', 'ids': {}, 'watched': True}
        lib.items = items
        clock.t = eng.next_retry
        api.calls[:] = []
        eng.last_status = clock.t
        eng.tick()
        self.assertEqual(self.lib_calls(api),
                         [('library', 500, True), ('library', 500, True), ('library', 201, True)])

    # 4: Position wird unabhaengig vom Abgleich gemessen
    def test_sampler_updates_position_on_its_own(self):
        from resources.lib import service as service_mod
        calls = []

        class Fake(object):
            tracker = types.SimpleNamespace(update=lambda: calls.append(1))
            waits = 0

            def abortRequested(self):
                return False

            def waitForAbort(self, t):
                self.waits += 1
                return self.waits >= 3

        service_mod.Service._sample(Fake())
        self.assertEqual(len(calls), 3)
        self.assertEqual(service_mod.SAMPLE_EVERY, 1)

    # 5: 401 fuer ein altes Token loescht keine neue Kopplung
    def test_stale_401_keeps_new_pairing(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        exc = Unauthorized(401)
        exc.token = 'old-token'
        api.raise_on['status'] = exc
        eng.tick()
        self.assertEqual(st.token, 'secret-token')
        self.assertEqual(ui.notes, [])
        exc.token = 'secret-token'
        eng.last_status = 0
        eng.next_retry = 0
        eng.tick()
        self.assertIsNone(st.token)

    def test_api_marks_401_with_request_token(self):
        from urllib.error import HTTPError
        import io

        def opener(req, timeout=None):
            raise HTTPError(req.full_url, 401, 'x', {}, io.BytesIO(b'{}'))

        try:
            Api('https://x/api/kodi', 'UA', 'tok-1', opener=opener).status()
        except Unauthorized as exc:
            self.assertEqual(exc.token, 'tok-1')
        else:
            self.fail('no 401')

    # 6: Trennen leert die Warteschlange auch ohne laufenden Dienst; Kopplung startet leer
    def test_pairing_drops_queue_of_previous_account(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        st.add_delta('movie:1', 1)
        st.set_auth('other-account', 'Bob', False)
        eng.on_paired()
        self.assertEqual(st.plays, [])
        self.assertEqual(st.deltas, {})
        self.assertEqual(store_mod.Store(self.tmp).plays, [])

    def test_disconnect_clears_queue_file(self):
        from resources.lib import script
        st = store_mod.Store(self.tmp)
        st.set_auth('tok', 'Jens', True)
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        orig = script.Api

        class NoNet(object):
            def __init__(self, *a, **k):
                pass

            def unpair(self):
                return {}

        script.Api = NoNet
        try:
            script.disconnect(st, confirm=False)
        finally:
            script.Api = orig
        fresh = store_mod.Store(self.tmp)
        self.assertIsNone(fresh.token)
        self.assertEqual(fresh.plays, [])

    # 7: fehlgeschlagenes Schreiben in Kodi wird nach einer Minute wiederholt
    def test_failed_write_is_retried(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1))
        results = [False, True]
        lib.set_watched = lambda kid, w, lp: lib.set_calls.append((kid, w)) or results.pop(0)
        api.library_res = {'mark': ['movie:1'], 'unmark': []}
        eng.tick()
        self.assertIn('movie:1', eng.write_retry)
        eng.tick()
        self.assertEqual(len(lib.set_calls), 1)  # noch nicht faellig
        clock.t += sync.WRITE_RETRY
        eng.tick()
        self.assertEqual(lib.set_calls, [('movie:1', True), ('movie:1', True)])
        self.assertNotIn('movie:1', eng.write_retry)

    def test_failed_write_gives_up_after_limit(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1))
        lib.set_watched = lambda kid, w, lp: lib.set_calls.append(kid) or False
        api.library_res = {'mark': ['movie:1'], 'unmark': []}
        eng.tick()
        for _ in range(sync.WRITE_TRIES + 2):
            clock.t += sync.WRITE_RETRY
            eng.tick()
        self.assertEqual(len(lib.set_calls), sync.WRITE_TRIES)
        self.assertEqual(eng.write_retry, {})

    # 8: Abbrechen waehrend der Abfrage koppelt nicht und meldet das Geraet wieder ab
    def test_cancel_during_poll_does_not_pair(self):
        from resources.lib import pairing
        st = store_mod.Store(self.tmp)
        unpaired = []
        holder = {}

        class Dlg(object):
            def __init__(self):
                self.closed = False
                holder['dlg'] = self

            def set_code(self, res):
                pass

            def set_countdown(self, s):
                pass

            def set_hint(self, t):
                pass

            def show(self):
                pass

            def close_dialog(self):
                self.closed = True

        class PairApi(object):
            def __init__(self, base, ua, token=None):
                self.token = token

            def pair_start(self, *a):
                return {'code': 'ABC123', 'pollToken': 'p', 'expiresIn': 600, 'interval': 2}

            def pair_poll(self, token):
                holder['dlg'].closed = True  # Nutzer bricht ab, waehrend die Anfrage laeuft
                return {'status': 'confirmed', 'token': 'new-token', 'userName': 'Jens'}

            def unpair(self):
                unpaired.append(self.token)
                return {}

        class FakeTime(object):
            t = 1000.0

            def time(self):
                FakeTime.t += 1
                return FakeTime.t

        saved = (pairing.PairingDialog, pairing.Api, pairing.time)
        pairing.PairingDialog, pairing.Api, pairing.time = Dlg, PairApi, FakeTime()
        try:
            self.assertFalse(pairing.run(st))
        finally:
            pairing.PairingDialog, pairing.Api, pairing.time = saved
        self.assertIsNone(st.token)
        self.assertEqual(unpaired, ['new-token'])


class SecondReviewTests(unittest.TestCase):
    """Eigenes Review 08.10.2026 (0.1.5)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def full_calls(self, api):
        return [c for c in api.calls if isinstance(c, tuple) and c[2]]

    def test_full_request_during_pass_is_kept(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1201))
        # Mediathek-Scan endet waehrend der Pause zwischen zwei Haeppchen
        eng.wait = lambda s: eng.request_full() or False
        eng.tick()
        self.assertTrue(eng.full_pending)

    def test_version_of_first_chunk_is_adopted(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1201))
        versions = iter(['v1', 'v2', 'v3'])
        orig = api.library

        def library(items, full):
            res = orig(items, full)
            res['version'] = next(versions)
            return res
        api.library = library
        eng.tick()
        self.assertEqual(st.state['version'], 'v1')

    def test_unexpected_4xx_is_retried_not_dropped(self):
        from resources.lib import api as api_mod
        self.assertIsInstance(api_mod.classify(403, {}), Retryable)
        self.assertIsInstance(api_mod.classify(404, {}), Retryable)
        self.assertIsInstance(api_mod.classify(400, {}), Retryable)
        dropped = api_mod.classify(400, {'code': 'KODI_BAD_PLAYS'})
        self.assertNotIsInstance(dropped, sync.KEEP_ERRORS)
        eng, st, api, ui, lib, clock = make_engine(self.tmp)
        eng.tick()
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        api.raise_on['plays'] = api_mod.classify(403, {})
        eng.tick()
        self.assertEqual(len(st.plays), 1)

    def test_read_error_keeps_delta_missing_item_drops_it(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(2))
        eng.tick()

        def get_item(kid):
            if kid == 'movie:1':
                raise library.RpcError('busy', -32100)
            return None
        lib.get_item = get_item
        st.add_delta('movie:1', clock.t)
        st.add_delta('movie:9', clock.t)
        clock.t += sync.DEBOUNCE
        eng.tick()
        self.assertIn('movie:1', st.deltas)
        orig = library.rpc
        library.rpc = lambda *a, **k: (_ for _ in ()).throw(library.RpcError('x', library.RPC_INVALID_PARAMS))
        try:
            self.assertIsNone(library.get_item('movie:5'))
            library.rpc = lambda *a, **k: (_ for _ in ()).throw(library.RpcError('x', -32100))
            with self.assertRaises(library.RpcError):
                library.get_item('movie:5')
        finally:
            library.rpc = orig

    def test_status_without_pro_keeps_pause(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1))
        api.status_res['pro'] = False
        eng.tick()
        self.assertTrue(eng.pro_paused)
        self.assertEqual(self.full_calls(api), [])
        clock.t += sync.PRO_RETRY
        eng.tick()
        self.assertTrue(eng.pro_paused)
        self.assertEqual(self.full_calls(api), [])

    def test_unpair_during_pass_stops_it(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1201))
        def wait(s):
            st.clear_auth()
            eng.on_unpaired()
            return False
        eng.wait = wait
        eng.tick()
        self.assertEqual(len(self.full_calls(api)), 1)
        # nach dem Trennen kein Haeppchen mehr und kein Durchgangs-Stand im frischen Zustand
        self.assertNotIn('fullPass', st.state)

    def test_without_backsync_only_watched_items_are_sent(self):
        items = movies(3) + [{'kid': 'movie:9', 'type': 'movie', 'title': 'S', 'ids': {}, 'watched': True}]
        eng, st, api, ui, lib, clock = make_engine(self.tmp, items)
        api.status_res['backSync'] = False
        api.library_res = {'mark': [], 'unmark': [], 'backSync': False}
        eng.tick()
        self.assertEqual(self.full_calls(api), [('library', 1, True)])
        # Rueck-Sync eingeschaltet: ganze Bibliothek
        api.status_res['backSync'] = True
        clock.t += sync.STATUS_EVERY
        eng.tick()
        self.assertEqual(self.full_calls(api)[-1], ('library', 4, True))

    def test_backsync_switched_on_seen_in_library_response(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(3))
        api.status_res['backSync'] = False
        api.library_res = {'mark': [], 'unmark': [], 'backSync': False}
        eng.tick()
        st.add_delta('movie:1', clock.t)
        api.library_res = {'mark': [], 'unmark': [], 'backSync': True}
        clock.t += sync.DEBOUNCE
        eng.tick()
        self.assertTrue(eng.full_pending)
        eng.tick()
        self.assertEqual(self.full_calls(api)[-1], ('library', 3, True))

    def test_mass_changes_trigger_full_instead_of_single_reads(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(300))
        eng.tick()
        reads = []
        lib.get_item = lambda kid: reads.append(kid)
        for i in range(1, 301):
            st.add_delta('movie:%d' % i, clock.t)
        clock.t += sync.DEBOUNCE
        eng.tick()   # fordert den vollen Abgleich an
        eng.tick()   # fuehrt ihn aus, er deckt die Aenderungen ab
        self.assertEqual(reads, [])
        self.assertEqual(st.deltas, {})
        self.assertEqual(self.full_calls(api)[-1], ('library', 300, True))

    def test_full_waits_for_playback_unless_sync_now(self):
        eng, st, api, ui, lib, clock = make_engine(self.tmp, movies(1))
        eng.is_playing = lambda: True
        eng.tick()
        self.assertEqual(self.full_calls(api), [])
        eng.sync_now()
        eng.tick()
        self.assertEqual(len(self.full_calls(api)), 1)
        self.assertFalse(eng.full_forced)

    def test_settings_written_only_on_change(self):
        import xbmcaddon
        writes = []
        orig = xbmcaddon.Addon.setSetting

        def counting(self_, key, value):
            writes.append(key)
            orig(self_, key, value)
        xbmcaddon.Addon.setSetting = counting
        try:
            util.set_status(True, 'Jens', 'TV')
            first = len(writes)
            util.set_status(True, 'Jens', 'TV')
            self.assertEqual(len(writes), first)
            util.set_status(True, 'Jens', 'Box')
            self.assertEqual(writes[-1], 'device_label')
        finally:
            xbmcaddon.Addon.setSetting = orig

    def test_api_base_requires_https_outside_lan(self):
        import xbmcaddon
        for value, expected in (('http://evil.example/api', util.DEFAULT_API_BASE),
                                ('http://192.168.0.10:3011/api/kodi', 'http://192.168.0.10:3011/api/kodi'),
                                ('https://staging.example/api/kodi', 'https://staging.example/api/kodi')):
            xbmcaddon.Addon.settings['api_base'] = value
            self.assertEqual(util.api_base(), expected)
        xbmcaddon.Addon.settings['api_base'] = ''

    def test_store_temp_file_is_per_process_and_cleaned(self):
        st = store_mod.Store(self.tmp)
        st.add_play({'type': 'movie', 'title': 'A', 'ids': {}, 'watchedAt': 1, 'percent': 95})
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith('.tmp')], [])

    def test_infotag_reads_independently(self):
        class PartialTag(object):
            def getUniqueID(self, key):
                raise AttributeError('old Kodi')

            def getTitle(self):
                return 'Dune'

            def getMediaType(self):
                return 'movie'

        t = player.Tracker(lambda p: None)
        t.getVideoInfoTag = lambda: PartialTag()
        t._playing_file = lambda: 'plugin://plugin.video.netflix/play/1'
        orig = library.rpc
        library.rpc = lambda m, p=None: ([{'type': 'video', 'playerid': 1}] if m == 'Player.GetActivePlayers'
                                          else {'item': {'file': 'plugin://plugin.video.netflix/play/1'}})
        try:
            info = t._read_item()
        finally:
            library.rpc = orig
        self.assertEqual(info['title'], 'Dune')
        self.assertEqual(info['type'], 'movie')


class MetadataTests(unittest.TestCase):
    """Fix 9/10: Pro immer noetig, schwedisch einheitlich 'Koppla', keine Gedankenstriche."""

    def test_disclaimer_and_version(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(os.path.join(ADDON_DIR, 'addon.xml')).getroot()
        self.assertEqual(root.get('version'), '0.1.5')
        meta = root.find("./extension[@point='xbmc.addon.metadata']")
        disclaimers = dict((d.get('lang'), d.text) for d in meta.findall('disclaimer'))
        self.assertEqual(len(disclaimers), 12)
        for lang, text in disclaimers.items():
            self.assertIn('WatchNPlay Pro', text, lang)
        for word in ('may require', 'kann WatchNPlay', 'peut', 'puede', 'può', 'kan WatchNPlay',
                     'może', 'pode', 'kan kräva', 'kan kreve', 'kan kræve'):
            self.assertFalse(any(word in t for t in disclaimers.values()), word)
        self.assertIn('v0.1.5', meta.find('news').text)

    def test_swedish_uses_koppla(self):
        path = os.path.join(ADDON_DIR, 'resources', 'language', 'resource.language.sv_se', 'strings.po')
        with open(path, encoding='utf-8') as f:
            po = f.read()
        msgstrs = [line for line in po.splitlines() if line.startswith('msgstr') and line != 'msgstr ""']
        self.assertFalse([m for m in msgstrs if 'nslut' in m.replace('internetanslutning', '')])
        with open(os.path.join(ADDON_DIR, 'addon.xml'), encoding='utf-8') as f:
            self.assertNotIn('Anslut', f.read())

    def test_no_em_dash(self):
        dash = chr(0x2014)
        for root, dirs, files in os.walk(os.path.dirname(HERE)):
            dirs[:] = [d for d in dirs if d not in ('dist', '__pycache__')]
            for name in files:
                if os.path.splitext(name)[1] in ('.py', '.po', '.xml', '.md'):
                    with open(os.path.join(root, name), encoding='utf-8') as f:
                        self.assertNotIn(dash, f.read(), name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
