# -*- coding: utf-8 -*-
"""Baut kodi/dist/service.watchnplay-<version>.zip (oberster Ordner service.watchnplay/)."""

import os
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_ID = 'service.watchnplay'
SRC = os.path.join(HERE, ADDON_ID)
DIST = os.path.join(HERE, 'dist')
SKIP_DIRS = {'__pycache__', '.git', '.idea', '.vscode'}
SKIP_EXT = {'.pyc', '.pyo', '.tmp'}


def main():
    version = ET.parse(os.path.join(SRC, 'addon.xml')).getroot().get('version')
    os.makedirs(DIST, exist_ok=True)
    target = os.path.join(DIST, '%s-%s.zip' % (ADDON_ID, version))
    count = 0
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(SRC):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if os.path.splitext(name)[1] in SKIP_EXT:
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, SRC).replace(os.sep, '/')
                zf.write(full, '%s/%s' % (ADDON_ID, rel))
                count += 1
    print('%s (%d files)' % (target, count))


if __name__ == '__main__':
    main()
