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

import xbmc
import xbmcgui

# global definitions

WAKANIM_URL_BASE = 'https://www.wakanim.tv'
PLUGIN_LIBRARY_URL = 'plugin://plugin.video.wakanim/play/'

# Kodi interface functions

def log(msg, lvl=xbmc.LOGNOTICE):
    """Log msg to Kodi journal"""
    xbmc.log('[WAKANIM] {0}'.format(enc(msg)), lvl)

def log_debug(msg):
    """Log debug message"""
    log(msg, xbmc.LOGDEBUG)

def log_error(msg):
    """Log error message"""
    log(msg, xbmc.LOGERROR)

def showdlg(args, msg):
    """Display dialog with message msg"""
    if isinstance(msg, int):
        msg = args._addon.getLocalizedString(msg)
    xbmcgui.Dialog().ok(args._addonname, msg)

# url related functions

def get_wakanim_url(args, suffix):
    """Returns Wakanim URL with language and suffix"""
    return '{0}/{1}/v2/{2}'.format(WAKANIM_URL_BASE, args._country, suffix)

# unicode encoding/decoding functions

def enc(s):
    """Convert unicode to str for Python2, do nothing for Python3"""
    try:
        return s.encode('utf-8') if isinstance(s, unicode) else s
    except NameError:
        return s

def dec(s):
    """Convert str to unicode for Python2, do nothing for Python3"""
    try:
        return s.decode('utf-8') if isinstance(s, str) else s
    except AttributeError:
        return s

# cleaning string/url functions

def mkstr(s):
    """Strip string and remove newline characters"""
    return s.strip().replace(u'\n', u'').replace(u'\r', u'')

def mkimgurl(s):
    """Make correct image url"""
    s = s.strip().replace(u' ', u'%20')
    if s:
        if s[:2] == u'//':
            return u'http:' + s
        if s[:4] != u'http':
            if not s.startswith(u'/'):
                s = u'/' + s
            return WAKANIM_URL_BASE + s
    return s

# dict related functions

def iteritems(d):
    """Make work dict.iteritems() on Python3"""
    try:
        return d.iteritems()
    except AttributeError:
        return d.items()

# language related functions

def get_season_name(lang):
    """Get localized season name"""
    if lang == 'de':
        return u'Staffel'
    elif lang == 'fr':
        return u'Saison'
    elif lang == 'ru':
        return u'Сезон'
    else:
        return u'Season'

def get_episode_name(lang, episode, info):
    """Get localized episode name"""
    if lang == 'de':
        name = 'Folge'
    elif lang == 'ru':
        name = 'Серия'
    else:
        name = 'Episode'
    ret = name + ' ' + str(episode)
    if info:
        sep = ' ' if info.startswith('(') else ' - '
        ret += sep + info
    return ret
