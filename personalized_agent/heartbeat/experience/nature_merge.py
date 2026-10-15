"""ONCE: the natures that are the same trait, merged into one.

Until a stage's nature could be added to the nature it showed again
(`stage_natures.NatureWriter`), every lesson wrote its own - so the same
trait seen in ten lessons is ten natures, each with one example, grouped
only afterwards into one pattern. This finds them, within each pattern,
and proposes one nature for each set, whose examples are all of theirs.

A DRY RUN FIRST, ALWAYS: `preview` says what would be merged and changes
nothing. `apply` merges what a preview proposed, carefully: the earliest
nature of each set is kept - with all the set's examples, the stages they
were seen in, and in its history what was merged into it, the merged
natures' own text included - and the others' links, pattern places and
kinds move to it before their files go. The database changes are one
transaction, and the files touched are put back as they were if any step
fails.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .store import ExperienceStore, count_use, model_task, now
from ...common.languages import in_english

log = logging.getLogger(__name__)

#: Natures read in one call: a pattern larger than this is read in parts,
#: and a trait split across two parts is not seen as one.
MERGE_BATCH = 40

#: Without the model: two natures whose meanings are at least this close
#: (cosine of the vectors the patterns keep) are taken as one trait.
#: Measured on the user's 357 natures, all-MiniLM-L6-v2: 0.85 and above
#: found no set - one trait said twice scores 0.75 to 0.8 - 0.8 found
#: three, 0.75 ten (by reading, about two in three truly one trait), 0.7
#: twenty-two with more that were not. A pattern is already natures about
#: one way of working, so a cut is a rough first look; the model reads
#: what makes two of them one trait.
SAME_CUT = 0.75

MERGE_PROMPT = """WHICH OF THESE NATURES OF THE USER ARE THE SAME TRAIT?

Each item below is one nature - one stable trait of how the user judges a
result or what output they want - with its id and the cases it was seen
in, each with the user's own words where they were kept. They are listed
together because they are about one way of working; most of them are
still DIFFERENT traits of it.

Find the ones that are THE SAME TRAIT SAID MORE THAN ONCE: an agent would
check the same thing, or make the same thing, because of each of them -
seen in different cases, written in different words. Two natures on one
subject that ask for different things are different traits: leave them
apart. Leave out every nature that is its own trait.

For each set, word the one nature it becomes, from all its examples: the
same trait, as specific as they allow. The user's own words stay as they
wrote them.

Return ONLY this JSON object (an empty list when none are the same):
{"same": [{"ids": ["the ids of one set, two or more"],
           "title": "ONE sentence, the user as its subject",
           "content": "a short paragraph: what it is, how it shows, and what
                       it means for doing their work",
           "why": "what makes them one trait"}]}"""


def _as_json(text: str) -> Dict[str, Any]:
    from .stage_natures import _as_json as parsed
    return parsed(text)


class NatureMerger:
    """Finds the natures that are one trait, within each pattern, and merges
    them - in a dry run first."""

    def __init__(self, db: Any, library: Any, models: Any = None,
                 scope: str = "live", embedder: Any = None,
                 language: str = "English") -> None:
        self.db = db
        self.store = ExperienceStore(db, scope)
        self.library = library
        self.models = models
        self.embedder = embedder
        # English whatever is passed, as every nature is kept.
        from .stage_natures import NATURES_LANGUAGE
        self.language = NATURES_LANGUAGE
        self.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}

    # -- the dry run ------------------------------------------------------------
    def preview(self, mode: str = "similarity", patterns: Sequence[str] = (),
                budget: int = 200, cut: float = SAME_CUT,
                going: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """WHAT WOULD BE MERGED - nothing is changed.

        `mode` "model" asks the model, a pattern (or MERGE_BATCH of its
        natures) to a call, at most `budget` calls; "similarity" takes as one
        trait the natures of a pattern within `cut` of each other, by the
        vectors kept for them, and costs nothing. `patterns` limits it to
        those pattern ids."""
        from .patterns import folder_records
        going = going or (lambda: True)
        records = {r.id: r for r in folder_records(self.library, "user_nature")}
        chosen = [p for p in self.store.patterns("pattern")
                  if not patterns or p["id"] in patterns]
        report: Dict[str, Any] = {"mode": mode, "natures": len(records),
                                  "patterns": 0, "sets": [], "calls": 0,
                                  "unread": [], "at": now()}
        used = set()
        vectors = self._vectors(records) if mode != "model" else {}
        if mode != "model":
            report["compared_by"] = "vectors" if vectors else "words"
        for pattern in chosen:
            members = [one for one in pattern["members"]
                       if one in records and one not in used]
            if len(members) < 2:
                continue
            report["patterns"] += 1
            if mode == "model":
                found = []
                for start in range(0, len(members), MERGE_BATCH):
                    if report["calls"] >= budget or not going() or self.models is None:
                        report["unread"].append(pattern["id"])
                        break
                    part = members[start:start + MERGE_BATCH]
                    found += self._ask([records[one] for one in part])
                    report["calls"] += 1
            else:
                found = self._alike(members, records, vectors, cut)
            for one in found:
                ids = [i for i in dict.fromkeys(one.get("ids") or [])
                       if i in members and i not in used]
                if len(ids) < 2:
                    continue
                used.update(ids)
                report["sets"].append(self._set(pattern, ids, records, one))
        report["merged_away"] = sum(len(s["merge"]) for s in report["sets"])
        report["after"] = report["natures"] - report["merged_away"]
        report.update({k: v for k, v in self.used.items() if k != "calls"})
        return report

    def _set(self, pattern: Dict[str, Any], ids: Sequence[str],
             records: Dict[str, Any], said: Dict[str, Any]) -> Dict[str, Any]:
        from .stage_natures import examples_of
        ordered = sorted(ids, key=lambda one: (str(records[one].created_at), one))
        return {"pattern": pattern["id"], "statement": pattern.get("statement") or "",
                "keep": ordered[0], "merge": ordered[1:],
                "natures": [{"id": one, "title": records[one].title,
                             "examples": len(examples_of(records[one]))}
                            for one in ordered],
                "examples": sum(len(examples_of(records[one])) for one in ordered),
                "title": str(said.get("title") or "")[:200],
                "content": str(said.get("content") or "")[:1500],
                "why": str(said.get("why") or "")[:600]}

    def _ask(self, natures: Sequence[Any]) -> List[Dict[str, Any]]:
        from ...models.base import ModelRequest
        from .stage_natures import examples_of, examples_said
        items = []
        for record in natures:
            items.append(f"[{record.id}] {record.title}\n  {(record.summary or '')[:400]}\n"
                         + "\n".join(f"  {line}" for line in
                                     examples_said(examples_of(record)[-4:])))
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": "\n\n".join(items)}],
                             system=MERGE_PROMPT,
                             max_tokens=4000, task=model_task(self.store.scope, "merge"),
                             language=in_english("the natures"),
                             metadata={"kind": "experience natures merged"}),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not read natures for the same trait: %s", exc)
            return []
        count_use(self.used, response)
        said = _as_json(getattr(response, "text", "") or "").get("same")
        return [one for one in said or [] if isinstance(one, dict)]

    def _summarise(self, together: Sequence[Any]) -> Dict[str, str]:
        """One nature's wording, from the examples of every nature in a set:
        {"title", "content"}, or {} when the model could not be asked."""
        from ...models.base import ModelRequest
        from .stage_natures import (ASPECT_SAID, REWORD_PROMPT, examples_of,
                                    examples_said)
        keep = sorted(together, key=lambda r: str(r.created_at))[0]
        examples = sorted((e for r in together for e in examples_of(r)),
                          key=lambda e: str(e.get("at") or ""))
        aspect = str((getattr(keep, "metadata", None) or {}).get("aspect") or "")
        system = (REWORD_PROMPT.replace("{aspect}", ASPECT_SAID.get(aspect, aspect))
                  .replace("{worded}", "1").replace("{count}", str(len(examples))))
        asked = "\n".join(["THE NATURES FOUND TO BE ONE TRAIT:"]
                          + [f"- {r.title}" for r in together] + [""]
                          + examples_said(examples))
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=system, max_tokens=1200,
                             task=model_task(self.store.scope, "merge"),
                             language=in_english("the nature"),
                             metadata={"kind": "experience natures merged and worded"}),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not word a merged nature: %s", exc)
            return {}
        count_use(self.used, response)
        self.used["calls"] = self.used.get("calls", 0) + 1
        said = _as_json(getattr(response, "text", "") or "")
        title = str(said.get("title") or "").strip()
        content = str(said.get("content") or said.get("summary") or "").strip()
        return {"title": title, "content": content,
                "why": "the same trait, found by clustering, worded from all "
                       "its examples"} if title and content else {}

    def _vectors(self, records: Dict[str, Any]) -> Dict[str, List[float]]:
        """The vectors the patterns keep for the natures - read, not made:
        a dry run does not start the embedding model."""
        if self.embedder is None:
            return {}
        from .patterns import _Text
        try:
            kept = self.embedder._kept({r.id: f"{r.title} {r.summary or ''}".strip()
                                        for r in records.values()})
        except Exception:                 # pragma: no cover - words instead
            log.debug("could not read the natures' vectors", exc_info=True)
            return {}
        return dict(kept or {})

    def _alike(self, members: Sequence[str], records: Dict[str, Any],
               vectors: Dict[str, List[float]], cut: float) -> List[Dict[str, Any]]:
        """Sets of natures that are one trait: THE SAME CLUSTERING the
        patterns are found by (`patterns.groups`), cut much closer - a
        pattern is natures alike, at PATTERN_CUT; one nature is natures the
        same, within `cut` of each other in meaning. Words stand in where
        the vectors are not kept."""
        from .patterns import _dot, groups
        from .stage_natures import _by_terms
        if self.embedder is not None and all(one in vectors for one in members):
            return [{"ids": group} for group in groups(
                members, vectors, self.embedder, 1.0 - cut, 2)]
        if all(one in vectors for one in members):
            close = lambda a, b: _dot(vectors[a], vectors[b])  # noqa: E731
        else:
            texts = {one: f"{records[one].title} {records[one].summary or ''}"
                     for one in members}

            def close(a: str, b: str) -> float:
                return _by_terms([records[a]], [texts[b]]).get(a, 0.0)
        sets: List[List[str]] = []
        for one in members:
            home = next((s for s in sets if all(close(one, o) >= cut for o in s)), None)
            if home is None:
                sets.append([one])
            else:
                home.append(one)
        return [{"ids": s} for s in sets if len(s) >= 2]

    # -- merging ------------------------------------------------------------------
    def apply(self, sets: Sequence[Dict[str, Any]],
              backup: Optional[Path] = None, run: str = "") -> Dict[str, Any]:
        """MERGE WHAT A PREVIEW PROPOSED. Each set's first nature (`keep`) is
        kept and the others (`merge`) are merged into it: their examples
        and stages become its, their links and places in patterns move to
        it, and their files go - kept whole in its history, and copied to
        `backup` first when one is given. All of it or none: the database
        in one transaction, and every file put back if any step fails."""
        from .patterns import folder_records
        from .stage_natures import (examples_of, nature_body, revise_nature,
                                    stages_of, worded_from, _confidence_in,
                                    _with_examples)
        records = {r.id: r for r in folder_records(self.library, "user_nature")}
        # THEN SAID AGAIN: a set found by clustering has no wording of its
        # own, so the model words the nature from every example the set
        # holds - before anything is changed, outside the transaction.
        sets = [dict(one) for one in sets]
        for one in sets:
            if str(one.get("title") or "").strip() or self.models is None:
                continue
            ids = [str(one.get("keep") or "")] + [str(m) for m in one.get("merge") or []]
            together = [records[i] for i in ids if i in records]
            if len(together) >= 2:
                one.update(self._summarise(together))
        plan, seen = [], set()
        for one in sets:
            keep = str(one.get("keep") or "")
            merge = [str(m) for m in one.get("merge") or [] if str(m) != keep]
            ids = [keep, *merge]
            if not keep or not merge or any(i not in records for i in ids) \
                    or seen & set(ids):
                raise ValueError(f"not a set that can be merged: {ids}")
            seen.update(ids)
            plan.append((one, records[keep], [records[m] for m in merge]))
        if not plan:
            return {"merged": 0, "sets": 0}
        # What every file touched said before - to put back on failure.
        before: Dict[str, str] = {}
        for _one, keep, merged in plan:
            for record in (keep, *merged):
                before[record.path] = Path(record.path).read_text(encoding="utf-8")
        if backup is not None:
            backup.mkdir(parents=True, exist_ok=True)
            for path in before:
                shutil.copy2(path, backup / Path(path).name)
        away = {m.id: keep.id for _one, keep, merged in plan for m in merged}
        written: List[str] = []
        try:
            with self.db.write() as conn:
                self._move_links(conn, away)
                for one, keep, merged in plan:
                    every = sorted([keep, *merged], key=lambda r: str(r.created_at))
                    examples = sorted((e for r in every for e in examples_of(r)),
                                      key=lambda e: str(e.get("at") or ""))
                    stages = list(dict.fromkeys(s for r in every for s in stages_of(r)))
                    title = str(one.get("title") or "").strip()
                    content = str(one.get("content") or "").strip()
                    meta = getattr(keep, "metadata", None) or {}
                    metadata = {"examples": json.dumps(examples, ensure_ascii=False),
                                "worded_examples": worded_from(keep)}
                    if title and content:
                        # Worded from all of the set's examples just now.
                        metadata["worded_examples"] = len(examples)
                        body = nature_body(str(meta.get("aspect") or ""), content,
                                           examples, _confidence_in(keep.body), stages)
                    else:
                        title, content = keep.title, keep.summary or ""
                        body = _with_examples(keep.body, examples, stages)
                    revise_nature(self.library, keep, title=title,
                                  summary=content[:500], body=body,
                                  metadata=metadata, stages=stages, run=run,
                                  why=str(one.get("why") or "")
                                  or "the same trait as the natures merged into it",
                                  by="merge of the same natures",
                                  merged_from=[m.id for m in merged],
                                  merged_records=merged)
                    written.append(keep.path)
                for _one, _keep, merged in plan:
                    for record in merged:
                        os.remove(record.path)
                        written.append(record.path)
        except Exception:
            for path, text in before.items():
                try:
                    Path(path).write_text(text, encoding="utf-8")
                except OSError:           # pragma: no cover - said, not hidden
                    log.error("could not put back %s after a failed merge", path)
            raise
        return {"merged": len(away), "sets": len(plan),
                "kept": [keep.id for _one, keep, _m in plan],
                "backup": str(backup) if backup is not None else ""}

    def _move_links(self, conn: Any, away: Dict[str, str]) -> None:
        """Everything that names a merged nature names the one it went into
        instead: the lessons and states linked to it, the patterns it is a
        member of (and the members their words were written from, so they
        are not worded again for it). Its kind goes with it."""
        for gone, kept in away.items():
            conn.execute("INSERT OR IGNORE INTO stage_natures (stage_id, record_id, "
                         "created_at) SELECT stage_id, ?, created_at FROM "
                         "stage_natures WHERE record_id = ?", (kept, gone))
            conn.execute("DELETE FROM stage_natures WHERE record_id = ?", (gone,))
            conn.execute("INSERT OR IGNORE INTO nature_links (state_id, record_id, "
                         "run_id, created_at) SELECT state_id, ?, run_id, created_at "
                         "FROM nature_links WHERE record_id = ?", (kept, gone))
            conn.execute("DELETE FROM nature_links WHERE record_id = ?", (gone,))
            conn.execute("DELETE FROM nature_kinds WHERE record_id = ?", (gone,))
        rows = conn.execute("SELECT id, layer, members, written_by FROM "
                            "experience_patterns WHERE scope = ?",
                            (self.store.scope,)).fetchall()
        sizes: Dict[str, int] = {}
        for row in rows:
            if row["layer"] != "pattern":
                continue
            members = json.loads(row["members"] or "[]")
            moved = list(dict.fromkeys(away.get(one, one) for one in members))
            sizes[row["id"]] = len(moved)
            if moved == members:
                continue
            written_by = json.loads(row["written_by"] or "{}")
            if isinstance(written_by, dict) and written_by.get("summarised"):
                written_by["summarised"] = list(dict.fromkeys(
                    away.get(one, one) for one in written_by["summarised"]))
                if written_by.get("named_size"):
                    written_by["named_size"] = len(written_by["summarised"])
            conn.execute("UPDATE experience_patterns SET members = ?, support = ?, "
                         "written_by = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(moved), len(moved), json.dumps(written_by),
                          now(), row["id"]))
        for row in rows:
            if row["layer"] != "principle":
                continue
            members = json.loads(row["members"] or "[]")
            conn.execute("UPDATE experience_patterns SET support = ? WHERE id = ?",
                         (sum(sizes.get(one, 0) for one in members), row["id"]))
