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

import re
import json
from time import timezone
from functools import cmp_to_key
from datetime import date, time, datetime, timedelta
try:
    from urllib2 import urlopen
except ImportError:
    from urllib.request import urlopen
from bs4 import BeautifulSoup

from .api import getPage
from .librarystorage import LibraryStorage
from .streamparams import getStreamParams
from .common import log_debug, log_error, showdlg, get_wakanim_url, mkstr, mkimgurl, enc, iteritems, get_season_name, WAKANIM_URL_BASE

import xbmc
import xbmcgui
import xbmcplugin

# Auxiliary functions

def fiximgurl(url, html):
    """Get full image url from html"""
    j = html.find(url+u'"')
    if j < 0:
        return url
    i = j-1
    while i >= 0 and html[i] != u'"':
        i -= 1
    if i < 0:
        return url
    return html[i+1:j] + url

def mkseason(season, prev_season, lang):
    """Make correct season name"""
    m = re.match(u'^(.*?)(?:[-\(])?\s*(?:Arc|Арка)\s*\d+\)?(.*)$', season, re.IGNORECASE|re.UNICODE)
    if m:
        # remove arc from season name
        season = m.group(1).strip()
        if not season and prev_season:
            season = get_season_name(lang) + u' ' + str(prev_season)
        info = m.group(2).strip()
        if season and info:
            season += u' '
        season += info
    m = re.match(u'^(.*?)(?:Season|Staffel|Saison|Сезон)\s*(\d+)(.*)$', season, re.IGNORECASE|re.UNICODE)
    if m:
        # correct season name
        season_no = m.group(2)
        season = m.group(1) + get_season_name(lang) + u' ' + season_no + m.group(3)
        season_no = int(season_no)
    else:
        season_no = None
    return [season_no, season, []]

def merge_season(seasons, num, lang):
    """Merge season"""
    season_no = num or 1
    season = [season_no, get_season_name(lang) + u' ' + str(season_no), []]
    episodes = []
    for s in seasons:
        if s[0] == num:
            unique_episodes = set(e['episode'] for e in s[2])
            if not unique_episodes or any(i in episodes for i in unique_episodes):
                return None
            episodes.extend(unique_episodes)
            season[2].extend(s[2])
    return season

def merge_seasons(seasons, lang):
    """Merge seasons"""
    if lang == 'ru':
        return seasons
    cnt = {}
    for s in seasons:
        cnt[s[0]] = cnt.get(s[0], 0)+1
    # merge one season splitted to arcs (no season numbers)
    if len(cnt) == 1 and None in cnt and cnt[None] > 1:
        season = merge_season(seasons, None, lang)
        if season:
            return [season]
    # merge seasons with numbers
    ret = []
    nums = []
    for num,c in iteritems(cnt):
        if num > 0 and c > 1:
            season = merge_season(seasons, num, lang)
            if season:
                ret.append(season)
                nums.append(num)
    for s in seasons:
        if not s[0] in nums:
            ret.append(s)
    return ret or seasons

def compare_seasons(a, b):
    """Compare two seasons"""
    x = a[0]
    y = b[0]
    if x != y:
        return 1 if x == None or x > y else -1
    x = a[1].lower()
    y = b[1].lower()
    return (x > y) - (x < y)

def parse_creators(s, lang):
    """Parse studio, director, writer"""
    if not s:
        return {}
    params = {
        'sc': {
            'studio': u'(?:Studio|Production)',
            'director': u'Director',
            'credits': u'(?:Creator|Script|Scenario)'},
        'de': {
            'studio': u'(?:Studio|Produktion)',
            'director': u'Regie',
            'credits': u'Drehbuch'},
        'fr': {
            'studio': u'Studio',
            'director': u'Réalisat',
            'credits': u'(?:Scénar|Auteur|Créateur)'},
        'ru': {
            'studio': u'студия',
            'director': u'Режиссёр',
            'credits': u'(?:Автор|Сценари)'}}
    params = params.get(lang)
    if not params:
        return
    creators = {}
    for k,r in iteritems(params):
        # find creators with regex
        l = [i.group(1).strip() for i in re.finditer(r+u'.*?:(.+)', s, re.IGNORECASE|re.UNICODE)]
        if not l:
            continue
        # split creators
        l = [j.strip() for i in l for j in i.split(u',') if j.strip()]
        if lang == 'ru':
            l = [i.split(u'/', 1)[0].strip() for i in l]
        else:
            l = [j.strip() for i in l for j in i.split(u'/') if j.strip()]
        creators[k] = l
    return creators

def weekstart(d=None):
    """Get start of week date"""
    if not d:
        d = date.today()
    w = d.weekday()
    if w:
        d -= timedelta(days=w)
    return d

def get_id(args, name):
    """Get sid and eid from args object"""
    if not hasattr(args, name):
        return None
    arg_id = getattr(args, name)
    return int(arg_id) if arg_id.isdigit() else None

# Main functions

def loadCatalogue(args):
    """Load Wakanim catalogue"""
    html = getPage(args, get_wakanim_url(args, 'catalogue'))
    if not html:
        return
    i = html.find(u'var catalogItems = [')
    if i < 0:
        return
    j = html.find(u'];', i)
    if j < 0:
        return
    data = json.loads(html[i+19:j+1])
    for d in data:
        url = d[u'ImageUrl']
        if not u'/' in url:
            d[u'ImageUrl'] = fiximgurl(url, html)
    return data

def fetchCatalogue(args, lib):
    """Load Wakanim catalogue and save to library database"""
    log_debug('fetch_catalogue')
    return lib.addCatalogue(loadCatalogue(args))

def fetchTVShow(args, lib, sid):
    """Load TV Show with episodes and save to library database"""
    log_debug('fetch_tvshow({0})'.format(sid))
    if not sid:
        return False
    html = getPage(args, get_wakanim_url(args, 'catalogue/show/{0}/'.format(sid)))
    if not html:
        return False
    soup = BeautifulSoup(html, 'html.parser')
    # fetch tvshow info
    info = {}
    info['plot'] = mkstr(soup.find(u'div', {u'class': u'serie_description'}).get_text())
    trailer = soup.find(u'div', {u'class': u'TrailerEp-iframeWrapperRatio'})
    try:
        trailer = trailer.iframe[u'src']
        info['trailer'] = u'plugin://plugin.video.youtube/play/?video_id=' + re.search(u'(?:\.be/|/embed)/?([^&=%:/\?]{11})', trailer).group(1)
    except (KeyError, AttributeError):
        pass
    creators = soup.find(u'div', {u'class': u'serie_description_more'})
    creators = parse_creators(creators.p.get_text().strip(), args._country) if creators else {}
    # fetch seasons
    seasons = []
    episodes = []
    idx = 1
    fix_seasons = args._addon.getSettingBool('merge_seasons')
    for section in soup.find_all(u'section', {u'class': u'seasonSection'}):
        season_name = mkstr(section.find(u'h2', {u'class': u'slider-section_title'}).get_text().split(u'%')[1])
        # fetch episodes
        for li in section.find_all(u'li', {u'class': u'slider_item'}):
            try:
                episode = int(mkstr(li.find(u'span', {u'class': u'slider_item_number'}).string))
                ep_info = li.find(u'span', {u'class': u'slider_item_info_text'})
                if ep_info and ep_info.string:
                    ep_info = mkstr(ep_info.string)
                elif args._country == 'ru':
                    ep_info = u'рус. озв.'
                else:
                    ep_info = u''
                episodes.append({
                    'id': int(re.search(u'episode/(\d+)', mkimgurl(li.a[u'href'])).group(1)),
                    'info': ep_info,
                    'season': idx,
                    'episode': episode,
                    'thumb': mkimgurl(li.img[u'src']),
                })
            except AttributeError:
                pass
        # set episode title for specials and movies
        if len(episodes) == 1 and not re.search(u'(Season|Staffel|Saison|Сезон)\s*\d+', season_name, re.IGNORECASE|re.UNICODE):
            episodes[0]['title'] = season_name
        if fix_seasons:
            # remove arc from season name
            cur_season = mkseason(season_name, idx, args._country)
            try:
                season = next(s for s in seasons if s[1].lower() == cur_season[1].lower())
                if any(e1['episode'] == e2['episode'] for e1 in episodes for e2 in season[2]):
                    season = [None, season_name, []]
                    seasons.append(season)
            except StopIteration:
                season = cur_season
                seasons.append(season)
            season[2].extend(episodes)
            episodes = []
            if season[0]:
                idx = season[0]
        else:
            seasons.append((idx, season_name))
            idx += 1
    if fix_seasons:
        # merge and sort seasons
        seasons = merge_seasons(seasons, args._country)
        seasons.sort(key=cmp_to_key(compare_seasons))
        episodes = []
        season = 0
        for s in seasons:
            season = s[0] if s[0] and s[0] > season else season+1
            s[0] = season
            for e in s[2]:
                e['season'] = season
                episodes.append(e)
            del s[2]
    return lib.addTVShow(sid, info, creators, seasons, episodes)

def fetchFullAgenda(args, sdt, edt):
    """Load full agenda from VCALENDAR"""
    log_debug('fetch_full_agenda({0}, {1})'.format(sdt, edt))
    return urlopen(get_wakanim_url(args, 'agenda/downloadevents?start=' + sdt.isoformat() + '&end=' + edt.isoformat() + '&timezone=' + str(timezone//60))).read()

def fetchAgenda(args, agenda, sdt):
    """Load agenda"""
    log_debug('fetch_agenda({0})'.format(sdt))
    html = urlopen(get_wakanim_url(args, 'agenda/getevents?s=' + sdt.strftime('%d-%m-%Y') + '&e=' + (sdt+timedelta(days=6)).strftime('%d-%m-%Y') + '&free=false')).read()
    if not html:
        return False
    d = sdt
    n = datetime.today()
    soup = BeautifulSoup(html, 'html.parser')
    for col in soup.find_all('div', {'class': 'Calendar-col'}):
        for ep in col.find_all('div', {'class': 'Calendar-ep'}):
            t = mkstr(ep.find('span', {'class': 'Calendar-hourTxt'}).string).split(':')
            t = time(int(t[0]), int(t[1]))
            aired = datetime.combine(d, t)
            sid = int(re.search('show/(\d+)', mkimgurl(ep.find('a', {'class': 'Calendar-epTitle'})['href'])).group(1))
            m = re.search('episode/(\d+)', mkimgurl(ep.find('a', {'class': 'Calendar-linkImg'})['href']))
            if not m: continue
            eid = int(m.group(1))
            thumb = mkimgurl(ep.find('img', {'class': 'Calendar-image'})['src'])
            # episode unavailable if thumb is tvshow poster or not yet aired
            no_content = '/library/' in thumb or aired > n
            agenda[eid] = (aired, no_content, sid)
        d += timedelta(days=1)
    return True

def mkitem(data, isfolder):
    """Make ListItem from tvshow/episode data"""
    li = xbmcgui.ListItem(data['info']['title'])
    li.setInfo('video', data['info'])
    li.setArt(data['art'])
    li.setUniqueIDs({'wakanim': data['id']}, 'wakanim')
    if isfolder:
        url = data['info']['path']
        for s in data['seasons']:
            li.addSeason(s[0], s[1])
        if data['hash'] != None:
            li.setProperty('hash', data['hash'])
        li.setMimeType('x-directory/normal')
    else:
        url = data['info']['filenameandpath']
        li.setMimeType('application/mp4')
        li.setContentLookup(False)
        li.setProperty('IsPlayable', 'true')
    return (li, url)

def addItem(data, isfolder, handle):
    """Add ListItem to directory"""
    li, url = mkitem(data, isfolder)
    xbmcplugin.addDirectoryItem(handle, url, li, isfolder)

def listTVShows(args, lib):
    """List TV Shows from library database"""
    handle = int(args._argv[1])
    tvshows = lib.getTVShows()
    if tvshows != None:
        xbmcplugin.setContent(handle, 'tvshows')
        for data in tvshows:
            addItem(data, True, handle)
    xbmcplugin.endOfDirectory(handle, tvshows != None)

def listEpisodes(args, lib, sid):
    """List episodes from library database"""
    handle = int(args._argv[1])
    episodes = lib.getEpisodes(sid)
    if episodes != None:
        xbmcplugin.setContent(handle, 'episodes')
        for data in episodes:
            addItem(data, False, handle)
    xbmcplugin.endOfDirectory(handle, episodes != None)

def refreshTVShow(args, lib, sid):
    """Refresh TV Show details"""
    fetchTVShow(args, lib, sid)
    handle = int(args._argv[1])
    tvshow = lib.getTVShowInfo(sid)
    if tvshow != None:
        xbmcplugin.setContent(handle, 'tvshows')
        addItem(tvshow, True, handle)
    xbmcplugin.endOfDirectory(handle, tvshow != None)

def refreshEpisode(args, lib, eid):
    """Refresh episode details"""
    fetchTVShow(args, lib, lib.getTVShowID(eid))
    handle = int(args._argv[1])
    episode = lib.getEpisodeInfo(eid, False)
    if episode != None:
        xbmcplugin.setContent(handle, 'episodes')
        addItem(episode, False, handle)
    xbmcplugin.endOfDirectory(handle, episode != None)

def checkPlayable(args, url):
    """Check episode is playable and reactivate video if needed"""
    html = getPage(args, url)
    if not u'jwplayer-container' in html:
        log_error('this video is available for premium members only ({0})'.format(url))
        showdlg(args, 30043)
        return None
    if not u'reactivate' in html:
        return html
    # reactivate episode
    getPage(args, WAKANIM_URL_BASE + BeautifulSoup(html, 'html.parser').find('div', {'id': 'jwplayer-container'}).a['href'])
    html = getPage(args, url)
    if u'reactivate' in html:
        log_error('reactivation failed for {0}'.format(url))
        showdlg(args, 30042)
        return None
    return html

def play(args, lib, eid):
    """Play episode"""
    info = lib.getEpisodeInfo(eid, True)
    if not info:
        reply(args, False)
        return
    html = checkPlayable(args, get_wakanim_url(args, 'catalogue/episode/{0}/'.format(eid)))
    if not html:
        reply(args, False)
        return
    params = getStreamParams(args, html)
    if not params:
        reply(args, False)
        return
    li, url = mkitem(info, False)
    li.setPath(params['url'])
    if params['content-type']:
        li.setMimeType(params['content-type'])
    for k,v in iteritems(params['properties']):
        li.setProperty(k, v)
    li.setProperty('original_listitem_url', url)
    xbmcplugin.setResolvedUrl(int(args._argv[1]), True, li)

def reply(args, ret):
    xbmcplugin.setResolvedUrl(int(args._argv[1]), ret, xbmcgui.ListItem())

def doInitialScan(args):
    """Initial full scan"""
    lib = LibraryStorage(args)
    lib.openLibrary()
    if not lib.needInitialScan():
        lib.clearLibrary()
    dlg = xbmcgui.DialogProgress()
    dlg.create(xbmc.getLocalizedString(189), xbmc.getLocalizedString(314))
    xbmc.sleep(50)
    try:
        dlg.update(0)
        # fetch catalogue
        fetchCatalogue(args, lib)
        if (dlg.iscanceled()):
            return
        dlg.update(33)
        xbmc.sleep(50)
        # fetch current week agenda
        agenda = {}
        edt = weekstart()
        fetchAgenda(args, agenda, edt)
        if (dlg.iscanceled()):
            return
        dlg.update(66)
        xbmc.sleep(50)
        # fetch full agenda
        sdt = lib.getAgendaMinDate()
        if sdt:
            sdt = weekstart(sdt) - timedelta(days=7)
            data = fetchFullAgenda(args, sdt, edt)
        else:
            sdt = edt
            data = None
        if (dlg.iscanceled()):
            return
        dlg.update(100)
        xbmc.sleep(50)
        lib.addAgenda(agenda, data)
        lib.setAgendaLastCheck(edt)
        lib.setAgendaFirstCheck(sdt)
        # fetch tvshows
        sids = lib.getTVShowTitles()
        c = len(sids)
        i = 0
        for sid,title in iteritems(sids):
            dlg.update(100*i//c, title)
            xbmc.sleep(50)
            fetchTVShow(args, lib, sid)
            if (dlg.iscanceled()):
                return
            i += 1
        dlg.update(100)
    finally:
        lib.closeLibrary()
        dlg.close()

def fetch(args, lib):
    """Subsequent scan"""
    agenda = {}
    # fetch current week agenda
    edt = weekstart()
    fetchAgenda(args, agenda, edt)
    sdt = lib.getAgendaLastCheck()
    if not sdt:
        sdt = lib.getAgendaMinDate()
    sdt = weekstart(sdt) if sdt else edt
    data = None
    if sdt != edt:
        if (edt-sdt).days < 14:
            # fetch previous week agenda
            fetchAgenda(args, agenda, sdt)
        else:
            # fetch full agenda
            data = fetchFullAgenda(args, sdt, edt)
    update = lib.updateAgenda(agenda, data)
    lib.setAgendaLastCheck(edt)
    fdt = lib.getAgendaFirstCheck()
    if not fdt or sdt < fdt:
        lib.setAgendaFirstCheck(sdt)
    if not update:
        return
    # fetch new tvshows (not in catalogue)
    fetchCatalogue(args, lib)
    for sid in lib.getIncompleteTVShows():
        fetchTVShow(args, lib, sid)

def selectTVShows(args):
    """Display select tvshows dialog"""

    # make tvshows list from wakanim catalogue
    data = loadCatalogue(args)
    if not data:
        return False
    sids = []
    titles = {}
    tvshows = []
    for d in data:
        sid = int(d[u'IdShowItem'])
        sids.append(sid)
        titles[sid] = title1 = enc(mkstr(d[u'Name']))
        if args._country == 'ru':
            title1 = title1.split('/', 1)
            title2 = title1[1].strip() if len(title1) > 1 else ''
            title1 = title1[0].strip()
        else:
            title2 = enc(mkstr(d[u'OriginalName']))
        tvshows.append(xbmcgui.ListItem(title1, title2, enc(mkimgurl(d[u'ImageUrl']))))

    # make preselected indexes from settings
    sids_conf = args._addon.getSetting('sids')
    sids_conf = [int(i) for i in sids_conf.split(',')] if sids_conf else []
    idx = []
    for i in sids_conf:
        try:
            idx.append(sids.index(i))
        except ValueError:
            pass

    # show multiselect dialog
    idx = xbmcgui.Dialog().multiselect(xbmc.getLocalizedString(20343), tvshows, preselect=idx, useDetails=True)
    if idx == None:
        return False

    # save settings and determine new tvshows
    sids_new = [sids[i] for i in idx]
    args._addon.setSetting('sids', ','.join(str(i) for i in sids_new))
    if not sids_new:
        sids_new = sids
    sids_new = [i for i in sids_new if not i in sids_conf]
    if not sids_new:
        return True

    # fetch new tvshows and agenda if needed
    lib = LibraryStorage(args)
    lib.openLibrary()
    if lib.needInitialScan():
        lib.closeLibrary()
        return True
    dlg = xbmcgui.DialogProgress()
    dlg.create(xbmc.getLocalizedString(189), xbmc.getLocalizedString(314))
    xbmc.sleep(50)
    try:
        lib.addCatalogue(data)
        # fetch full agenda if we have tvshows aired earlier than first check
        sdt = lib.getAgendaMinDate()
        if sdt:
            sdt = weekstart(sdt) - timedelta(days=7)
            edt = lib.getAgendaFirstCheck()
            if edt and sdt < edt:
                data = fetchFullAgenda(args, sdt, edt)
                lib.addAgenda([], data)
                lib.setAgendaFirstCheck(sdt)
        if (dlg.iscanceled()):
            return
        # fetch new tvshows
        c = len(sids_new)
        i = 0
        for sid in sids_new:
            dlg.update(100*i//c, titles.get(sid, ''))
            xbmc.sleep(50)
            fetchTVShow(args, lib, sid)
            if (dlg.iscanceled()):
                return False
            i += 1
        dlg.update(100)
    finally:
        lib.closeLibrary()
        dlg.close()
    return True

def checkVersion(args):
    """Check Kodi version"""
    m = re.match(r'(\d+)\.(\d+).*? Git:(\d+)', xbmc.getInfoLabel('System.BuildVersion'))
    if not m:
        return True
    major = int(m.group(1))
    minor = int(m.group(2))
    build = int(m.group(3))
    ret = major > 18 or (major == 18 and minor > 0) or (major == 18 and minor == 0 and build >= 20180323)
    if not ret:
        log_error('Kodi 18.0-ALPHA2 Git:20180327 or higher is required for using this plugin as a video source')
        showdlg(args, xbmc.getLocalizedString(24152))
    return ret

def libraryMain(args):
    """Main function"""
    if not checkVersion(args):
        reply(args, False)
        return

    sid = get_id(args, 'sid')
    eid = get_id(args, 'eid')
    # if a user pressed "Refresh" button in video info dialog
    force_refresh = hasattr(args, 'kodi_action') and args.kodi_action == 'refresh_info'
    # check on cleaning media library
    check_exists = hasattr(args, 'kodi_action') and args.kodi_action == 'check_exists'
    log_debug('library_main({0}, {1}, {2})'.format(sid, eid, force_refresh))

    lib = LibraryStorage(args)
    lib.openLibrary()
    if lib.needInitialScan():
        log_error('You should first perform initial media library scanning in plugin settings dialog')
        showdlg(args, 30048)
        reply(args, False)
        return
    try:
        if eid:
            if force_refresh:
                refreshEpisode(args, lib, eid)
            elif check_exists:
                reply(args, lib.isEpisodeExists(eid))
            else:
                play(args, lib, eid)
        elif sid:
            if force_refresh:
                refreshTVShow(args, lib, sid)
            elif check_exists:
                reply(args, lib.isTVShowExists(sid))
            else:
                if lib.isDirty(sid):
                    fetchTVShow(args, lib, sid)
                listEpisodes(args, lib, sid)
        else:
            if check_exists:
                reply(args, True)
            else:
                fetch(args, lib)
                listTVShows(args, lib)
    finally:
        lib.closeLibrary()
