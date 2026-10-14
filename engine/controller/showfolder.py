"""
Studio's writes to the show folder (F19j): drafts checked against this rig,
saves that quote the rev they read, renames that move every reference, and
deletes refused while anything still names the file. The rules are
`showfiles`'; every write runs on the worker, and the folder reloads after.

Also linking a guest's track to a prepped one, and prepping tracks from the
DJ's rekordbox collection.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .. import collection as collectionmod
from .. import program as programmod
from .. import routines as routinesmod
from .. import showfiles
from .. import showlibrary
from .. import timeline as timelinemod
from .. import tracks as tracksmod
from ..commands import CONFIGURE, command
from .common import DEFERRED, S, _BASE_REV, _DOC, _NAME


# The longest title, artist or album a typed `track_link` takes.
LINK_TEXT_MAX = 200


class ShowFolder:
    """Studio's edits to the show folder."""

    def _need_library(self):
        if self.show_library is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        return self.show_library

    @command("timeline_draft", tier=CONFIGURE, args={"doc": _DOC})
    def _cmd_timeline_draft(self, m: dict, now: float) -> object:
        """Check an unsaved timeline: the format's rules, then a compile
        against THIS rig. Answers with errors, warnings and problems. If the
        designer is driving the rig on that track, the draft is what plays."""
        library = self._need_library()
        doc = m["doc"]
        routines = library.folder.routines
        rigging = self._rigging()
        root, track = library.root, library.folder.tracks.get(doc.get("track"))

        def work():
            result = showfiles.validate("timeline", doc)
            if not result.ok:
                return result, None
            result.warnings += showfiles.param_lane_problems(doc, routines)
            audio, missing = showfiles.timeline_audio(root, doc, track)
            result.warnings += missing
            timeline = timelinemod.Timeline.from_doc(
                doc, showfiles.timeline_channels, audio)
            return result, programmod.compile(
                timeline, routines, rigging, f"draft of {doc.get('track')}")

        def then(value, respond):
            result, program = value
            preview = self.player.preview if self.player else None
            if (program is not None and preview is not None
                    and preview.track_id == doc.get("track")):
                preview.program, preview.draft = program, True
            respond(True, {"errors": result.errors, "warnings": result.warnings,
                           "problems": program.problems if program else []})

        return self._on_worker("checking a timeline draft", work, then)

    def _save(self, kind: str, m: dict) -> object:
        library = self._need_library()
        doc = m["doc"]
        path = showfiles.path_for(library.root, kind,
                                  showfiles.doc_ident(kind, doc) or "")
        base = m["base_rev"]

        # show.json is the one document at the folder's top, with no subfolder
        rel = path.name if kind == "show" else f"{showfiles.SUBDIR[kind]}/{path.name}"

        def then(rev, respond):
            respond(True, {"rev": rev, "path": rel})
            self.reload_library()

        return self._on_worker(f"saving {path.name}",
                               lambda: showfiles.write_doc(path, doc, kind, base),
                               then)

    @command("timeline_save", tier=CONFIGURE, args={"doc": _DOC, "base_rev": _BASE_REV})
    def _cmd_timeline_save(self, m: dict, now: float) -> object:
        """Write a timeline, refused if the file changed since `base_rev`.
        It applies to the track from its next play, like any folder change."""
        return self._save("timeline", m)

    @command("timeline_delete", tier=CONFIGURE, args={
        "track": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_timeline_delete(self, m: dict, now: float) -> object:
        """Delete a track's timeline and keep the track: the template set (or
        the operator's show) lights it again. Refused if the file changed
        since `base_rev`. Like a save, it applies from the track's next play:
        one playing now keeps the timeline it started with."""
        library = self._need_library()
        track, base = m["track"], m["base_rev"]
        root = library.root

        def work():
            path = showfiles.path_for(root, "timeline", track)
            if not path.is_file():
                raise ValueError(f"{track!r} has no timeline to delete")
            showfiles.delete_doc(path, base)
            return f"{showfiles.SUBDIR['timeline']}/{track}.json"

        def then(rel, respond):
            respond(True, {"deleted": rel})
            self.note(f"deleted {rel}")
            self.reload_library()

        return self._on_worker(f"deleting the timeline of {track}", work, then)

    def _on_stage(self, track_id: str, now: float) -> Optional[str]:
        """Who has this track on the rig right now, and what to do about it,
        in words: the deck it is loaded on, or Studio driving its timeline.
        None when nobody does."""
        sample = self.transport.sample(now)
        pinned = self.pinned
        if (pinned is not None and pinned.track_seq == sample.track_seq
                and pinned.match is not None and pinned.match.track_id == track_id):
            return ("it is the track on the deck now"
                    + (f" ({sample.source})" if sample.source else "")
                    + ". Remove it when the track has changed -- or take the "
                      "clock back first, if the deck has gone")
        preview = self.player.preview if self.player is not None else None
        if preview is not None and preview.track_id == track_id:
            return (f"Studio ({preview.name}) is driving the rig on it. "
                    f"Release the rig first")
        return None

    @command("track_delete", tier=CONFIGURE, args={
        "track": _NAME,
        "base_rev": _BASE_REV,
        "timeline_rev": S(str, fix="its timeline's rev as you read it, or \"\" "
                                   "if it had none")})
    def _cmd_track_delete(self, m: dict, now: float) -> object:
        """Take a track out of the show: tracks/<id>.json and, with it, that
        track's timeline and waveform (showfiles.remove_track -- the track's
        own file last). `base_rev` is the track's rev and `timeline_rev` its
        timeline's ("" for none), so neither goes unseen. Refused while the
        track is on the rig: the deck's, or Studio's preview. Answered with
        the files removed; the audio it names is never touched."""
        library = self._need_library()
        track, base = m["track"], m["base_rev"]
        timeline_rev = m.get("timeline_rev") or ""
        busy = self._on_stage(track, now)
        if busy is not None:
            raise ValueError(f"{track!r} cannot be removed while it is "
                             f"playing: {busy}")
        root = library.root

        def then(removed, respond):
            respond(True, {"deleted": removed})
            self.note(f"removed track {track!r} from the show: {', '.join(removed)}")
            self.reload_library()

        return self._on_worker(
            f"removing track {track}",
            lambda: showfiles.remove_track(root, track, base, timeline_rev), then)

    @command("routine_draft", tier=CONFIGURE, args={"doc": _DOC})
    def _cmd_routine_draft(self, m: dict, now: float) -> object:
        """Check an unsaved routine: the format's rules, then the routine bound
        to THIS rig as it is -- and in each of its variations -- for what will
        not work here (a role no fixture carries, a block with nothing to aim)."""
        self._need_library()
        doc = m["doc"]
        rigging = self._rigging()

        def work():
            result = showfiles.validate("routine", doc)
            if not result.ok:
                return result, []
            where = f"routine {doc.get('id')!r}"
            problems = routinesmod.instantiate(doc, {}, rigging, where).problems
            for name in sorted(doc.get("variations") or {}):
                label = f"{where} variation {name!r}"
                for p in routinesmod.instantiate(
                        doc, {"variation": name}, rigging, label).problems:
                    # only what the variation adds: the rest is said above
                    if p.replace(label, where, 1) not in problems:
                        problems.append(p)
            return result, problems

        def then(value, respond):
            result, problems = value
            respond(True, {"errors": result.errors, "warnings": result.warnings,
                           "problems": problems})

        return self._on_worker("checking a routine draft", work, then)

    @command("routine_save", tier=CONFIGURE, args={"doc": _DOC, "base_rev": _BASE_REV})
    def _cmd_routine_save(self, m: dict, now: float) -> object:
        return self._save("routine", m)

    @staticmethod
    def _on_disk(root: Path, folder: "showfiles.Folder") -> "showfiles.Folder":
        """The folder as it is on disk NOW, for a delete or a rename to judge
        what names what. The loaded folder can be a couple of seconds behind
        (the watcher waits for a change to hold still), and a timeline written
        in that time -- by MCP, another machine, a save just ahead in the
        queue -- must not be missed: deleting or renaming a routine it names
        would leave it naming one that is gone. Runs on the worker."""
        return showfiles.load_folder(root, previous=folder)

    @command("routine_rename", tier=CONFIGURE, args={
        "routine": _NAME,
        "to": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_routine_rename(self, m: dict, now: float) -> object:
        """Rename a routine and every reference to it (showfiles.rename_routine:
        new file first, references next, old file last). Answered with the
        files written; the folder reloads after."""
        library = self._need_library()
        # `routine`, not `id`: `id` is the request's own, for its reply.
        old, new, base = m["routine"], m["to"], m["base_rev"]
        root, folder = library.root, library.folder

        def then(written, respond):
            respond(True, {"written": written})
            self.note(f"renamed routine {old!r} to {new!r}: {len(written)} file(s)")
            self.reload_library()

        return self._on_worker(
            f"renaming routine {old}",
            lambda: showfiles.rename_routine(root, self._on_disk(root, folder), old, new, base),
            then)

    @command("template_draft", tier=CONFIGURE, args={"doc": _DOC})
    def _cmd_template_draft(self, m: dict, now: float) -> object:
        """Check an unsaved template set: the format's rules, then what it asks
        of the routines (one that is not there, a variation or parameter it
        lacks) -- the same checks a folder load makes, before the save."""
        library = self._need_library()
        doc = m["doc"]
        folder = library.folder

        def work():
            result = showfiles.validate("template_set", doc)
            problems = showfiles.template_set_problems(folder, doc) if result.ok else []
            return {"errors": result.errors, "warnings": result.warnings,
                    "problems": problems}

        return self._on_worker("checking a template set", work,
                               lambda value, respond: respond(True, value))

    @command("template_save", tier=CONFIGURE, args={"doc": _DOC, "base_rev": _BASE_REV})
    def _cmd_template_save(self, m: dict, now: float) -> object:
        """Write a template set, refused if the file changed since `base_rev`."""
        return self._save("template_set", m)

    @command("template_rename", tier=CONFIGURE, args={
        "template": _NAME,
        "to": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_template_rename(self, m: dict, now: float) -> object:
        """Rename a template set, and show.json with it if it is the show's."""
        library = self._need_library()
        old, new, base = m["template"], m["to"], m["base_rev"]
        root, folder = library.root, library.folder

        def then(written, respond):
            respond(True, {"written": written})
            self.note(f"renamed template set {old!r} to {new!r}")
            self.reload_library()

        return self._on_worker(f"renaming template set {old}",
                               lambda: showfiles.rename_template_set(
                                   root, self._on_disk(root, folder), old, new, base),
                               then)

    @command("template_delete", tier=CONFIGURE, args={
        "template": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_template_delete(self, m: dict, now: float) -> object:
        """Delete a template set that is not the show's."""
        library = self._need_library()
        tid, base = m["template"], m["base_rev"]
        root, folder = library.root, library.folder

        def work():
            uses = showfiles.template_set_uses(self._on_disk(root, folder), tid)
            if uses:
                raise ValueError(f"{tid!r} is {uses[0]}: make another set the "
                                 f"show's first")
            showfiles.delete_doc(showfiles.path_for(root, "template_set", tid), base)
            return f"{showfiles.SUBDIR['template_set']}/{tid}.json"

        def then(rel, respond):
            respond(True, {"deleted": rel})
            self.note(f"deleted {rel}")
            self.reload_library()

        return self._on_worker(f"deleting template set {tid}", work, then)

    @command("palette_save", tier=CONFIGURE, args={"doc": _DOC, "base_rev": _BASE_REV})
    def _cmd_palette_save(self, m: dict, now: float) -> object:
        """Write a library palette. Its copies in timelines and sets are not
        touched: `palette_sync` does that, on request. Its name must be its
        own: copies are found by name, so two library palettes of one name
        would both claim the same copies."""
        library = self._need_library()
        pid, name = m["doc"].get("id"), m["doc"].get("name")
        for other_id, other in library.folder.palettes.items():
            if other_id != pid and other.get("name") == name:
                raise ValueError(f"the library already has a palette called {name!r} "
                                 f"(palettes/{other_id}.json): copies are found by "
                                 f"name, so each needs its own")
        return self._save("palette", m)

    @command("palette_delete", tier=CONFIGURE, args={
        "palette": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_palette_delete(self, m: dict, now: float) -> object:
        """Delete a library palette. Its copies stay where they are -- each
        timeline and set keeps its own -- so nothing goes dark."""
        library = self._need_library()
        pid, base = m["palette"], m["base_rev"]
        root = library.root

        def work():
            showfiles.delete_doc(showfiles.path_for(root, "palette", pid), base)
            return f"{showfiles.SUBDIR['palette']}/{pid}.json"

        def then(rel, respond):
            respond(True, {"deleted": rel})
            self.note(f"deleted {rel}")
            self.reload_library()

        return self._on_worker(f"deleting palette {pid}", work, then)

    @command("palette_sync", tier=CONFIGURE, args={
        "palette": _NAME,
        "files": S(list, required=True, non_empty=True, each=S(str),
                   fix="the timelines and sets to update, as /api/palettes lists them")})
    def _cmd_palette_sync(self, m: dict, now: float) -> object:
        """Give copies of a library palette its colors: `files` are the
        timelines and sets to update, as /api/palettes lists them."""
        library = self._need_library()
        pid, files = m["palette"], m["files"]
        if len(files) > 1000:
            raise ValueError("palette_sync takes at most 1000 files at a time")
        root, folder = library.root, library.folder

        def then(written, respond):
            respond(True, {"written": written})
            self.note(f"palette {pid!r}: updated {len(written)} file(s)")
            self.reload_library()

        return self._on_worker(f"updating copies of palette {pid}",
                               lambda: showfiles.sync_palette(root, folder, pid, files), then)

    @command("show_save", tier=CONFIGURE, args={"doc": _DOC, "base_rev": _BASE_REV})
    def _cmd_show_save(self, m: dict, now: float) -> object:
        """Write show.json: the show's template set, the pause policy and idle
        routine, Follow's start. Refused if it changed since `base_rev` -- the
        phone's latency slider writes it too. Applies when the folder reloads,
        as any change to it does."""
        return self._save("show", m)

    @command("routine_delete", tier=CONFIGURE, args={
        "routine": _NAME,
        "base_rev": _BASE_REV})
    def _cmd_routine_delete(self, m: dict, now: float) -> object:
        """Delete a routine nothing uses. One that a timeline, a template set
        or show.json still names is refused with where, so a delete can never
        leave a show playing a routine that is gone."""
        library = self._need_library()
        rid, base = m["routine"], m["base_rev"]
        root, folder = library.root, library.folder

        def work():
            uses = showfiles.routine_uses(self._on_disk(root, folder), rid)
            if uses:
                raise ValueError(f"{rid!r} is still used by {'; '.join(uses)}: "
                                 f"take it out of those first")
            showfiles.delete_doc(showfiles.path_for(root, "routine", rid), base)
            return f"{showfiles.SUBDIR['routine']}/{rid}.json"

        def then(rel, respond):
            respond(True, {"deleted": rel})
            self.note(f"deleted {rel}")
            self.reload_library()

        return self._on_worker(f"deleting routine {rid}", work, then)

    @command("track_link", tier=CONFIGURE, args={
        "track_id": _NAME,
        "title": S(str),
        "artist": S(str),
        "album": S(str)})
    def _cmd_track_link(self, m: dict, now: float) -> dict:
        """This playing track IS that prepped track -- for a guest's copy the
        matcher could not place (a different tag, a different export).

        Recorded on the prepped track as an alias, plus the deck's signature
        when beat-link sent one, so every later play matches by itself. It does
        NOT re-match the track playing now: like any change to the show
        folder, it applies from the track's next play. The write is on the
        worker; the reply says it was queued and a notice says how it went.

        With a `title` (and `artist`, `album`) the description is the one
        given instead of the deck's -- Studio, before the night, with nothing
        playing. Typed, so it is held to more: one the track already answers
        to, or one that is another track's, is refused rather than queued."""
        library = self.show_library
        if library is None:
            raise ValueError("no show folder -- start the engine with "
                             "--show-dir to link tracks")
        track_id = m["track_id"]
        if track_id not in library.folder.tracks:
            raise ValueError(f"there is no prepped track {track_id!r} in "
                             f"{library.root}/tracks")
        if any(k in m for k in ("title", "artist", "album")):
            title, artist, album = self._typed_description(library, track_id, m)
            sig = None
        else:
            ident = self.transport.sample(now).identity
            if not ident.title:
                raise ValueError("nothing identified is playing -- a link needs "
                                 "a track title from the deck")
            title, artist, album, sig = (ident.title, ident.artist, ident.album,
                                         ident.signature)
        if sig:
            owner = [t for t in library.index.by_signature.get(sig, ())
                     if t != track_id]
            if owner:
                raise ValueError(
                    f"this deck's signature already belongs to {owner[0]!r}; "
                    f"remove it from tracks/{owner[0]}.json ids.blt_signatures "
                    f"first, or both tracks would claim it")
        root = library.root

        def done(what: str) -> None:
            if what == "already":
                self.note(f"{track_id} already answers to {title!r}")
            else:
                self.note(f"linked {title!r} to {track_id}; it applies from "
                          f"the track's next play")
            self.reload_library()

        self.worker.submit(
            lambda: showlibrary.link(root, track_id, title, artist, album, sig,
                                     showlibrary.default_added()),
            done=done, label=f"linking {title!r} to {track_id}")
        return {"queued": True, "track_id": track_id, "applies": "next_play"}

    @staticmethod
    def _typed_description(library, track_id: str, m: dict) -> tuple[str, str, str]:
        """A description typed for `track_link`, checked: (title, artist,
        album). Dictionary lookups on the loaded folder."""
        parts = []
        for name in ("title", "artist", "album"):
            value = m.get(name) or ""
            if len(value) > LINK_TEXT_MAX:
                raise ValueError(f"{name} must be at most {LINK_TEXT_MAX} characters")
            parts.append(value.strip())
        title, artist, album = parts
        alias = tracksmod.alias_for(title, artist, album)
        key = tracksmod.identity_key(alias)
        if not key[0]:
            raise ValueError("a link needs a title: what the deck will call "
                             "the track")
        if tracksmod.has_description(library.folder.tracks[track_id], alias):
            raise ValueError(f"{track_id} already answers to that description")
        index = library.index
        for table in (index.by_alias, index.by_title_artist_album):
            other = [t for t in table.get(key, ()) if t != track_id]
            if other:
                raise ValueError(
                    f"that description already leads to {other[0]!r} "
                    f"(tracks/{other[0]}.json): one description can lead to "
                    f"only one track")
        return title, artist, album

    @command("rekordbox_prep", tier=CONFIGURE, args={
        "ids": S(list, required=True, non_empty=True, each=S(int, min=0),
                 fix="rekordbox track ids")})
    def _cmd_rekordbox_prep(self, m: dict, now: float) -> object:
        """Prep tracks from the DJ's rekordbox collection into the show folder:
        what the designer's collection browser picked, by rekordbox id.

        The bridge does it, in a child process, on a thread of its own rather
        than the worker: it is a wait on another process, not CPU here, and a
        playlist's worth of tracks would otherwise hold up every compile the
        designer asks for behind it. Answered when it finishes, with the
        bridge's summary per track; the folder reloads after, and a track that
        is playing keeps the load it was matched against."""
        if self.show_dir is None:
            raise ValueError("no show folder -- start the engine with --show-dir "
                             "to prep tracks into one")
        ids = collectionmod.check_ids(m.get("ids"))
        root = self.show_dir
        collection = self.collection
        respond = self._deferred()

        def run() -> None:
            try:
                outcome = (True, collection.prep(ids, root))
            except Exception as exc:                        # noqa: BLE001
                outcome = (False, str(exc))

            def done() -> None:
                ok, value = outcome
                if not ok:
                    respond(False, error=value)
                    return
                results = value.get("results") or []
                fresh = sum(r.get("status") == "created" for r in results)
                self.note(f"prepped {len(results)} track(s) from rekordbox, "
                          f"{fresh} new" + (f"; {len(value.get('skipped') or [])} "
                                            f"skipped" if value.get("skipped") else ""))
                respond(True, value)
                self.reload_library()
            self.submit_call(done)

        threading.Thread(target=run, name="klights-rekordbox-prep",
                         daemon=True).start()
        return DEFERRED

    @command("show_reload", tier=CONFIGURE)
    def _cmd_show_reload(self, m: dict, now: float) -> None:
        """Read the show folder again now, rather than at the next poll."""
        if self.show_dir is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        self.reload_library()
