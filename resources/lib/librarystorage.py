# -*- coding: utf-8 -*-
# Wakanim - Watch videos from the german anime platform Wakanim.tv on Kodi.
# Copyright (C) 2018 MrKrabat, AkariDN
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import hashlib
import json
import os
import re
import sqlite3
from datetime import date

try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import StringIO

import xbmc

from .common import enc, dec, mkstr, mkimgurl, iteritems, get_episode_name, PLUGIN_LIBRARY_URL, log_error

# unicode encoding/decoding functions

def json_enc(d):
    """Object hook for json.loads to convert unicode to str for Python2"""
    if isinstance(d, dict):
        return {json_enc(k): json_enc(v) for k,v in iteritems(d)}
    elif isinstance(d, list):
        return [json_enc(i) for i in d]
    else:
        return enc(d)

# datetime related functions

def from_sqldate(s):
    """Convert SQL date format to datetime.date"""
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10])) if s else None

def to_sqldate(d):
    """Convert datetime.date to SQL date format"""
    return d.isoformat() if d else None

def to_sqldatetime(d):
    """Convert datetime.datetime to SQL datetime format"""
    return d.strftime('%Y-%m-%d %H:%M:%S') if d else None

# plugin url functions

def get_tvshow_url(sid):
    """Get plugin URL for TV Show"""
    return '{0}?sid={1}'.format(PLUGIN_LIBRARY_URL, sid)

def get_episode_url(eid):
    """Get plugin URL for episode"""
    return '{0}?eid={1}'.format(PLUGIN_LIBRARY_URL, eid)

# main class

class LibraryStorage(object):
    """Wakanim catalogue cache"""

    def __init__(self, args):
        self._fn = os.path.join(dec(xbmc.translatePath(args._addon.getAddonInfo('profile'))), u'library.sqlite')
        self._con = None
        self._new = True
        sids = args._addon.getSetting('sids')
        self._sids = [int(i) for i in sids.split(',')] if sids else None
        self._sids_filter = 'sid in (' + sids + ')' if sids else ''
        self._creators = args._addon.getSettingBool('include_creators')
        self._info_filter = dec(args._addon.getSetting('info_filter'))
        self._info_filter_sql = ' and info = ?' if self._info_filter else ''
        self._lang = args._country

    def __del__(self):
        self.closeLibrary()

    def openLibrary(self):
        """Open library database"""
        if self._con != None:
            return False
        try:
            self._new = os.path.getsize(self._fn) == 0
        except OSError:
            self._new = True
        self._con = sqlite3.connect(self._fn, check_same_thread=False)
        self._con.execute('pragma journal_mode=memory')
        if self._new:
            self._create_tables()
        return True

    def closeLibrary(self):
        """Close library database"""
        if self._con == None:
            return False
        self._con.commit()
        self._con.close()
        self._con = None
        return True

    def needInitialScan(self):
        """Is initial library scanning needed"""
        if self._new:
            return True
        return self.getAgendaLastCheck() == None

    def addCatalogue(self, data):
        """Add Wakanim catalogue to database"""
        if not data:
            return False
        with self._con as con:
            db = {row[0]: row[1:] for row in con.execute('select sid, info1, art from tvshows')}
            for d in data:
                sid = int(d[u'IdShowItem'])
                if self._sids and not sid in self._sids:
                    continue
                title = mkstr(d[u'Name'])
                added = mkstr(d[u'StartProduction']).replace(u'T', u' ')
                info = {
                    'originaltitle': mkstr(d[u'OriginalName']),
                    'plotoutline': mkstr(d[u'SmallSummary']),
                    'genre': [mkstr(g[u'Name']) for g in d[u'Genres']],
                    'dateadded': added
                }
                age_min = d[u'Classification'][u'AgeMin']
                if age_min:
                    info['mpaa'] = str(age_min) + u'+',
                info = json.dumps(info, ensure_ascii=False)
                art = mkimgurl(d[u'ImageUrl'])
                dbinfo = db.pop(sid, None)
                if not dbinfo:
                    con.execute('insert into tvshows (sid, title, info1, art, added, dirty) values (?, ?, ?, ?, ?, 1)', (sid, title, info, art, added[:10]))
                elif (info != dbinfo[0] or art != dbinfo[1]):
                    con.execute('update tvshows set info1 = ?, art = ? where sid = ?', (info, art, sid))
            ids = ','.join(str(i) for i in db)
            if ids:
                ids = 'delete from {0} where sid in (' + ids + ')'
                con.execute(ids.format('episodes'))
                con.execute(ids.format('tvshows'))
        return True

    def addTVShow(self, sid, info, creators, seasons, episodes):
        """Add TV Show to database"""
        with self._con as con:
            info = json.dumps(info, ensure_ascii=False)
            creators = json.dumps(creators, ensure_ascii=False)
            seasons = json.dumps(seasons, ensure_ascii=False)
            dbinfo = con.execute('select info2, creators, seasons, dirty from tvshows where sid = ?', (sid,)).fetchone()
            if not dbinfo:
                con.execute('insert into tvshows (sid, info2, creators, seasons, dirty) values (?, ?, ?, ?, 0)', (sid, info, creators, seasons))
            if (info != dbinfo[0] or creators != dbinfo[1] or seasons != dbinfo[2] or 0 != dbinfo[3]):
                con.execute('update tvshows set info2 = ?, creators = ?, seasons = ?, dirty = 0 where sid = ?', (info, creators, seasons, sid))
            con.execute('delete from episodes where sid = ?', (sid,))
            con.executemany('insert into episodes (eid, sid, season, episode, title, info, thumb) values (?, ?, ?, ?, ?, ?, ?)',
                ((info['id'], sid, info['season'], info['episode'], info.get('title', ''), info['info'], info['thumb']) for info in episodes))

    @classmethod
    def _add_agenda(cls, con, agenda, data, update):
        """[Internal] Add agenda to database: current week and VCALENDAR data"""
        if agenda:
            con.executemany('replace into agenda (eid, aired) values (?, ?)', ((k, to_sqldatetime(v[0])) for k,v in iteritems(agenda)))
        if not data:
            return
        try:
            data = StringIO(data)
        except TypeError:
            data = StringIO(data.decode('utf-8'))
        data.seek(0)
        event = False
        prem = False
        aired = ''
        eid = 0
        showtitle = u''
        if update:
            sids = {row[1]: row[0] for row in con.execute('select sid, title from tvshows')}
        for s in data:
            if event:
                if s.startswith('END:VEVENT'):
                    if prem and aired and eid:
                        con.execute('replace into agenda (eid, aired) values (?, ?)', (eid, aired))
                        if update:
                            sid = sids.get(showtitle)
                            if not sid:
                                try:
                                    sid = next(v for k,v in iteritems(sids) if any(i for i in map(unicode.strip, k.split(u'/', 1)) if i == showtitle))
                                except StopIteration:
                                    pass
                            log_error('get_sid() eid: {0}, title: {1}, sid: {2}'.format(eid, enc(showtitle), sid))
                            agenda[eid] = (aired, False, sid)
                    event = False
                    prem = False
                    aired = ''
                    eid = 0
                elif s.startswith('DTSTART:'):
                    aired = s[8:23]
                    aired = aired[:4]+'-'+aired[4:6]+'-'+aired[6:8]+' '+aired[9:11]+':'+aired[11:13]+':'+aired[13:15]
                elif s.startswith('CATEGORIES:Subscription,') or s.startswith('CATEGORIES:Abonnement,') or s.startswith('CATEGORIES:Abo,') or s.startswith('CATEGORIES:Подписка,'):
                    prem = True
                    if update:
                        showtitle = dec(s.split(',', 1)[1].strip())
                elif s.startswith('URL:'):
                    eid = int(re.search('episode/(\d+)', s).group(1))
            elif s.startswith('BEGIN:VEVENT'):
                event = True

    def addAgenda(self, agenda, data):
        """Add agenda to database (initial scan)"""
        with self._con as con:
            self._add_agenda(con, agenda, data, False)

    def updateAgenda(self, agenda, data=None):
        """Add agenda to database (subsequent scan) and mark changed tvshows"""
        with self._con as con:
            self._add_agenda(con, agenda, data, True)
            ids = {k: v[2] for k,v in iteritems(agenda) if not v[1] and (not self._sids or v[2] in self._sids)}
            if not ids:
                return False
            eids = [row[0] for row in con.execute('select eid from episodes where eid in (' + ','.join(str(i) for i in ids) + ')')]
            update_sids = set(v for k,v in iteritems(ids) if not k in eids)
            if not update_sids:
                return False
            ids = ','.join(str(i) for i in update_sids if i)
            if not ids:
                return True
            return con.execute('update tvshows set dirty = 1 where sid in ('+ids+')').rowcount != len(update_sids)

    def _get_settings(self, num):
        """Get value from settings #num"""
        ret = self._con.execute('select value from settings where id = ?', (num,)).fetchone()
        return ret[0] if ret else None

    def _set_settings(self, num, value):
        """Set value to settings #num"""
        with self._con as con:
            con.execute('replace into settings (id, value) values (?, ?)', (num, value))

    def getAgendaLastCheck(self):
        """Get last scanning date"""
        ret = self._get_settings(2)
        return from_sqldate(ret) if ret else None

    def setAgendaLastCheck(self, edt):
        """Set last scanning date"""
        self._set_settings(2, to_sqldate(edt))

    def getAgendaFirstCheck(self):
        """Get first scanning date"""
        ret = self._get_settings(3)
        return from_sqldate(ret) if ret else None

    def setAgendaFirstCheck(self, sdt):
        """Set first scanning date"""
        self._set_settings(3, to_sqldate(sdt))

    def getAgendaMinDate(self):
        """Get min date for agenda scanning"""
        ret = self._con.execute('select min(added) from tvshows').fetchone()
        return from_sqldate(ret[0]) if ret else None

    def _sids_filter_sql(self, prefix):
        """Get sql condition for sids filter"""
        return ' ' + prefix + ' ' + self._sids_filter if self._sids_filter else ''

    def _info_filter_param(self, param):
        """Get sql parameters for episode info filter"""
        return (param, self._info_filter) if self._info_filter else (param,)

    def getTVShowTitles(self):
        """Get list of TV Shows (id, title)"""
        return {row[0]: row[1] for row in self._con.execute('select sid, title from tvshows' + self._sids_filter_sql('where'))}

    def getIncompleteTVShows(self):
        """Get list of TV Shows with incomplete data"""
        return [row[0] for row in self._con.execute('select sid from tvshows where info2 is null' + self._sids_filter_sql('and'))]

    def _get_tvshow_info(self, sid, row):
        """[Internal] Get TV Show information frow database row"""
        if not sid or not row or not all(row[:7]):
            return None

        # calculate hash for tv show folder contents
        if row[7] != None and row[7] == 0:
            found = False
            pathhash = hashlib.md5()
            for r in self._con.execute('select eid from episodes where sid = ?' + self._info_filter_sql + ' order by eid', self._info_filter_param(sid)):
                found = True
                pathhash.update(get_episode_url(r[0]).encode('utf-8'))
            pathhash = pathhash.hexdigest().upper() if found else ''
        else:
            pathhash = None

        added = enc(row[6])
        info = {
            'title': enc(row[0]),
            'premiered': added,
            'aired': added,
            'tag': 'Wakanim',
            'path': get_tvshow_url(sid),
            'mediatype': 'tvshow'
        }
        info.update(json.loads(row[1], object_hook=json_enc))
        info.update(json.loads(row[2], object_hook=json_enc))
        if self._creators:
            info.update(json.loads(row[3], object_hook=json_enc))

        return {
            'id': sid,
            'info': info,
            'seasons': json.loads(row[4], object_hook=json_enc),
            'art': {'poster': enc(row[5])},
            'hash': pathhash
        }

    def getTVShows(self):
        """Get TV Shows list"""
        query = 'select title, info1, info2, creators, seasons, art, added, dirty, sid from tvshows' + self._sids_filter_sql('where') + ' order by sid'
        return filter(None, (self._get_tvshow_info(row[8], row) for row in self._con.execute(query)))

    def getTVShowInfo(self, sid):
        """Get TV Show information"""
        if self._sids != None and not sid in self._sids:
            return None
        return self._get_tvshow_info(sid, self._con.execute('select title, info1, info2, creators, seasons, art, added, dirty from tvshows where sid = ?', (sid,)).fetchone())

    def _get_tvshow_info_for_episode(self, row, idx=0):
        """[Internal] Get TV Show information for episode frow database row"""
        if not row:
            return None
        info = {'tvshowtitle': enc(row[idx])}
        info.update(json.loads(row[idx+1], object_hook=json_enc))
        info.update(json.loads(row[idx+2], object_hook=json_enc))
        if self._creators:
            info.update(json.loads(row[idx+3], object_hook=json_enc))
        return info

    def _get_episode_info(self, eid, tvshow, row, poster=None):
        """[Internal] Get episode information frow database row"""
        if not eid or not row or row[2] < 0 or row[3] < 0 or not row[5]:
            return None
        info = {
            'title': enc(row[0]) if row[0] else get_episode_name(self._lang, row[3], enc(row[1])),
            'season': row[2],
            'episode': row[3],
            'tag': 'Wakanim',
            'path': PLUGIN_LIBRARY_URL,
            'filenameandpath': get_episode_url(eid),
            'mediatype': 'episode'
        }
        info.update(tvshow)
        if row[4]:
            info['dateadded'] = aired = enc(row[4])
        else:
            aired = info['dateadded']
        aired = aired[:10]
        info.update({
            'premiered': aired,
            'aired': aired,
        })
        art = {'thumb': enc(row[5])}
        if poster:
            art['tvshow.poster'] = enc(poster)
        return {
            'id': eid,
            'info': info,
            'art': art
        }

    def getEpisodeInfo(self, eid, poster):
        """Get episode information"""
        query = 'select e.title, e.info, e.season, e.episode, a.aired, e.thumb, s.title, s.info1, s.info2, s.creators, s.art \
                 from episodes e join tvshows s on e.sid = s.sid left join agenda a on e.eid = a.eid \
                 where e.eid = ?' + self._info_filter_sql
        row = self._con.execute(query, self._info_filter_param(eid)).fetchone()
        if not row or not all(row[6:]):
            return None
        return self._get_episode_info(eid, self._get_tvshow_info_for_episode(row, 6), row, row[10] if poster else None)

    def getEpisodes(self, sid):
        """Get episodes list"""
        if self._sids != None and not sid in self._sids:
            return None
        tvshow = self._get_tvshow_info_for_episode(self._con.execute('select title, info1, info2, creators from tvshows where sid = ?', (sid,)).fetchone())
        if not tvshow or not all(tvshow):
            return None
        query = 'select e.title, e.info, e.season, e.episode, a.aired, e.thumb, e.eid \
                 from episodes e left join agenda a on e.eid = a.eid \
                 where e.sid = ?' + self._info_filter_sql + ' \
                 order by e.eid'
        return filter(None, (self._get_episode_info(row[6], tvshow, row) for row in self._con.execute(query, self._info_filter_param(sid))))

    def getTVShowID(self, eid):
        """Get TV Show id from episode id"""
        ret = self._con.execute('select sid from episodes where eid = ?', (eid,)).fetchone()
        return ret[0] if ret else None

    def isTVShowExists(self, sid):
        """Check if TV Show exists in database"""
        if self._sids != None and not sid in self._sids:
            return False
        ret = self._con.execute('select sid from tvshows where sid = ?', (sid,)).fetchone()
        return True if ret and ret[0] else False

    def isEpisodeExists(self, eid):
        """Check if episode exists in database"""
        query = 'select eid from episodes where eid = ?' + self._info_filter_sql +\
                'and exists (select sid from tvshows where sid = episodes.sid' + self._sids_filter_sql('and') + ')'
        ret = self._con.execute(query, self._info_filter_param(eid)).fetchone()
        return True if ret and ret[0] else False

    def isDirty(self, sid):
        """Check TV Show should be refreshed"""
        ret = self._con.execute('select dirty from tvshows where sid = ?', (sid,)).fetchone()
        return True if ret and (ret[0] == None or ret[0] != 0) else False

    def clearLibrary(self):
        """Delete library contents"""
        self._con.executescript('''
            delete from tvshows;
            delete from episodes;
            delete from agenda;
            delete from settings where id > 1;
            vacuum;''')

    def _create_tables(self):
        """Create database tables and indexes"""
        self._con.executescript('''
            pragma encoding="utf-8";
            create table tvshows (sid INTEGER primary key, title TEXT, info1 TEXT, info2 TEXT, creators TEXT, seasons TEXT, art TEXT, added TEXT, dirty INTEGER);
            create table episodes (eid INTEGER primary key, sid INTEGER, season INTEGER, episode INTEGER, title TEXT, info TEXT, thumb TEXT);
            create table agenda (eid INTEGER primary key, aired TEXT);
            create table settings (id INTEGER primary key, value TEXT);
            create index idx_episodes_sid on episodes (sid);
            insert into settings (id, value) values (1, '1');''')
