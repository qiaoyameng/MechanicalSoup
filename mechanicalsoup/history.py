"""Bounded back/forward page history for :class:`StatefulBrowser`.

The history stores *snapshots* of visited pages: their final URL, a deep copy
of the parsed page and the currently selected form. Restoring a snapshot
never performs any network access, so going back to the result of a POST does
not re-send the original request. Only the browser's view changes; the
requests session (cookies included) is left untouched.

Capacity is bounded in two ways: by the number of retained pages and by the
sum of the original response body sizes in bytes.
"""

import copy

from .form import Form


class HistoryEntry:
    """An immutable-ish snapshot of one visited page.

    :param url: Final URL of the page (after any redirect).
    :param page: Deep copy of the parsed page, or ``None`` for responses
        that were not parsed as HTML.
    :param form: Deep copy of the selected :class:`Form`, belonging to the
        copied page. ``None`` when no form was selected.
    :param body_size: Size in bytes of the original response body, used for
        capacity accounting even though the body itself is not retained.
    """

    def __init__(self, url, page, form, body_size):
        self.url = url
        self.page = page
        self.form = form
        self.body_size = body_size

    def __repr__(self):
        return f"HistoryEntry(url={self.url!r}, body_size={self.body_size})"


class PageHistory:
    """A bounded, cursor-based back/forward history.

    The history is a list of :class:`HistoryEntry` together with a cursor
    pointing at the entry backing the currently displayed page. Entries after
    the cursor form the *forward branch*; appending a new entry discards
    them, mirroring the behavior of a web browser.

    :param max_pages: Maximum number of entries kept. ``None`` means
        unlimited. When the limit is exceeded, the oldest entries are
        evicted from the front.
    :param max_bytes: Maximum total of the original response body sizes
        (in bytes) retained across all entries. ``None`` means unlimited.
        Entries are evicted from the front until the budget is satisfied.
    """

    def __init__(self, max_pages=10, max_bytes=2 * 1024 * 1024):
        self.max_pages = max_pages
        self.max_bytes = max_bytes
        self._entries = []
        self._index = -1
        # Whether the live page is backed by the entry at ``_index``. It is
        # False right after a navigation whose entry was refused (oversize or
        # non-HTML page), in which case the history must not be snapshotted
        # from that unrelated live page.
        self._live_in_history = False
        # Number of entries refused because a single page exceeded the
        # byte budget by itself.
        self.oversize_skips = 0

    @staticmethod
    def snapshot_state(page, form):
        """Return deep copies of ``page`` and ``form``.

        BeautifulSoup's :meth:`Tag.__deepcopy__` deliberately ignores the
        copy memo, so copying the page and the form tag together would not
        preserve the fact that the form lives inside the page. The page is
        therefore copied first and the form is re-attached to its copy by
        its position among the page's forms. All user edits made to the form
        are carried by the copied tag tree.
        """
        if page is None:
            return None, None

        page_copy = copy.deepcopy(page)
        if form is None:
            return page_copy, None

        forms = page.find_all('form')
        if form.form not in forms:
            # The selected form belongs to a different page.
            return page_copy, None
        form_index = forms.index(form.form)
        form_copy = Form(page_copy.find_all('form')[form_index])

        if form.submit_chosen is False:
            form_copy._submit_chosen = False
        elif form.submit_chosen is not None:
            controls = list(form.form.find_all(True))
            chosen_index = controls.index(form.submit_chosen)
            form_copy._submit_chosen = list(
                form_copy.form.find_all(True))[chosen_index]
        return page_copy, form_copy

    def _total_bytes(self):
        return sum(entry.body_size for entry in self._entries)

    def update_current(self, page, form):
        """Re-snapshot the live page and form into the current entry.

        Called before the cursor moves away from an entry so that edits made
        after the navigation (filling fields, selecting another form) are
        carried by the history. Has no effect on an empty history.
        """
        entry = self.current
        if entry is None or not self._live_in_history:
            return
        if form is not None and form.form not in page.find_all('form'):
            # The form belongs to a previous page (e.g. the current page is
            # the result of a form submission): keep the entry formless.
            form = None
        entry.page, entry.form = self.snapshot_state(page, form)

    @property
    def index(self):
        """Zero-based position of the current entry, or ``-1`` if empty."""
        return self._index

    @property
    def current(self):
        """The :class:`HistoryEntry` backing the current page, or ``None``."""
        if not self._entries:
            return None
        return self._entries[self._index]

    def entries(self):
        """Return a list of all retained entries, oldest first."""
        return list(self._entries)

    def __len__(self):
        return len(self._entries)

    @property
    def total_bytes(self):
        """Sum of the original body sizes of the retained entries."""
        return self._total_bytes()

    @property
    def can_go_back(self):
        """Whether :meth:`back` has an entry to restore."""
        return self._index > 0

    @property
    def can_go_forward(self):
        """Whether :meth:`forward` has an entry to restore."""
        return 0 <= self._index < len(self._entries) - 1

    @property
    def live_in_history(self):
        """Whether the currently displayed page is backed by the entry at
        :attr:`index`. ``False`` after a successful navigation that created
        no entry, such as a non-HTML response or an oversize page."""
        return self._live_in_history

    def push(self, url, page, form, body_size):
        """Append a snapshot for a new successful top-level navigation.

        Any forward branch is discarded first. The entry is then subjected to
        the capacity policy:

        * An entry whose ``body_size`` alone exceeds ``max_bytes`` is never
          stored (the navigation itself still succeeds);
          :attr:`oversize_skips` is incremented.
        * Otherwise the entry is appended and the oldest entries are evicted
          until both the page-count and the byte budgets are satisfied.

        :return: The appended :class:`HistoryEntry`, or ``None`` if the
            entry was refused because it was over the byte budget.
        """
        # A new navigation truncates every entry after the current one.
        if self._entries:
            del self._entries[self._index + 1:]

        if self.max_bytes is not None and body_size > self.max_bytes:
            # A single page larger than the whole budget cannot be retained
            # alongside anything, and evicting other pages would still leave
            # the budget violated: skip it explicitly instead.
            self.oversize_skips += 1
            self._live_in_history = False
            return None

        page_copy, form_copy = self.snapshot_state(page, form)
        entry = HistoryEntry(url, page_copy, form_copy, body_size)
        self._entries.append(entry)
        self._index = len(self._entries) - 1
        self._enforce_budget()
        return entry

    def mark_live_detached(self):
        """Mark the current live page as not represented by a history entry.

        Used for successful navigations that update the browser state without
        creating an entry, such as non-HTML responses.
        """
        self._live_in_history = False

    def replace_current(self, url, page, form, body_size):
        """Replace the current entry in place (e.g. after :meth:`refresh`).

        The cursor position and the forward branch are preserved. The same
        byte-budget policy as :meth:`push` applies; an oversize replacement
        drops the current entry rather than violating the budget.

        :return: The replacement :class:`HistoryEntry`, or ``None`` when the
            replacement was refused or the history was empty.
        """
        if not self._entries:
            return None

        position = self._index
        del self._entries[position]
        # With the current slot removed, the entry directly before it becomes
        # the cursor target; if the head was removed the history is empty.
        self._index = position - 1

        if self.max_bytes is not None and body_size > self.max_bytes:
            self.oversize_skips += 1
            self._live_in_history = False
            return None

        page_copy, form_copy = self.snapshot_state(page, form)
        self._entries.insert(
            position, HistoryEntry(url, page_copy, form_copy, body_size))
        self._index = position
        self._enforce_budget()
        return self._entries[self._index]

    def _enforce_budget(self):
        """Evict oldest entries until both capacity budgets hold, keeping
        the newest entry reachable."""
        if self.max_pages is not None:
            overflow = len(self._entries) - self.max_pages
            if overflow > 0:
                del self._entries[:overflow]

        if self.max_bytes is not None:
            while (len(self._entries) > 1 and
                   self._total_bytes() > self.max_bytes):
                self._entries.pop(0)

        self._index = len(self._entries) - 1
        self._live_in_history = bool(self._entries)

    def back(self, steps=1):
        """Move the cursor backwards and return the restored entry.

        :raise IndexError: if there is no entry to go back to.
        """
        target = self._index - steps
        if target < 0 or not self._entries:
            raise IndexError("No page to go back to in the history.")
        self._index = target
        self._live_in_history = True
        return self.current

    def restore_current(self):
        """Return the entry at the cursor and mark the live page as backed by
        the history again. Used to re-attach after an entry-less navigation
        (such as an oversize page)."""
        if not self._entries:
            raise IndexError("No page to go back to in the history.")
        self._live_in_history = True
        return self.current

    def forward(self, steps=1):
        """Move the cursor forwards and return the restored entry.

        :raise IndexError: if there is no entry to go forward to.
        """
        target = self._index + steps
        if target >= len(self._entries):
            raise IndexError("No page to go forward to in the history.")
        self._index = target
        self._live_in_history = True
        return self.current

    def clear(self):
        """Drop every entry and release the stored page and form objects."""
        self._entries = []
        self._index = -1
        self._live_in_history = False
