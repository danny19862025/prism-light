# SPDX-License-Identifier: GPL-3.0-only
"""Check GitHub for a newer Prism Monitor release, only when the user asks.

Prism Monitor releases live in the prism-light repository, tagged prism-monitor-vX.Y.Z,
next to Prism Light's own releases. The request carries no personal data: just the app
name and version in the User-Agent, as GitHub's API requires one.
"""
import json
import re

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

RELEASES_API = 'https://api.github.com/repos/danny19862025/prism-light/releases?per_page=30'
RELEASES_PAGE = 'https://github.com/danny19862025/prism-light/releases'
TAG = re.compile(r'^prism-monitor-v(\d+(?:\.\d+)*)$')


def parse_version(text):
    return tuple(int(part) for part in re.findall(r'\d+', text or ''))


def latest_release(releases):
    """(version text, page url) of the newest published Prism Monitor release, or None."""
    best = None
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
            continue
        match = TAG.match(str(release.get('tag_name', '')))
        if not match:
            continue
        version = parse_version(match.group(1))
        if best is None or version > best[0]:
            best = (version, match.group(1), release.get('html_url') or RELEASES_PAGE)
    return (best[1], best[2]) if best else None


def compare(current, releases):
    """('newer', version, url), ('current', version, url) or ('none', '', RELEASES_PAGE)."""
    found = latest_release(releases)
    if found is None:
        return 'none', '', RELEASES_PAGE
    version, url = found
    return ('newer' if parse_version(version) > parse_version(current) else 'current'), version, url


class UpdateChecker(QObject):
    """One check per click. finished(state, version, url) where state is newer, current,
    none or error (then version holds the error text)."""
    finished = Signal(str, str, str)

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.current = current
        self.net = QNetworkAccessManager(self)
        self.reply = None

    def check(self):
        if self.reply is not None:
            return
        request = QNetworkRequest(QUrl(RELEASES_API))
        request.setRawHeader(b'User-Agent', f'PrismMonitor/{self.current}'.encode())
        request.setRawHeader(b'Accept', b'application/vnd.github+json')
        request.setTransferTimeout(12000)
        self.reply = self.net.get(request)
        self.reply.finished.connect(self._done)

    def _done(self):
        reply, self.reply = self.reply, None
        try:
            if reply.error() != QNetworkReply.NoError:
                self.finished.emit('error', reply.errorString(), RELEASES_PAGE)
                return
            try:
                releases = json.loads(bytes(reply.readAll()).decode('utf-8'))
            except ValueError:
                self.finished.emit('error', 'GitHub sent an unreadable answer.', RELEASES_PAGE)
                return
            self.finished.emit(*compare(self.current, releases))
        finally:
            reply.deleteLater()
