"""SQLite authority, immutable revisions and recoverable content-addressed files."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from ..materials import now, cutoff_time
from .schemas import KnowledgeItem, digest


class VersionConflict(ValueError):
    pass


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def terms(text):
    """FTS5 unicode tokens plus Han unigrams/bigrams for Chinese short words."""
    import re
    tokens = re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+", text.lower())
    out = []
    for token in tokens:
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            out.extend(token)
            out.extend(token[i:i+2] for i in range(len(token)-1))
        else:
            out.append(token)
    return " ".join(out)


def search_expression(text):
    import re
    clauses=[]
    for token in re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+",text.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+",token) and len(token)>1:
            clauses.append("("+" AND ".join('"'+token[i:i+2]+'"' for i in range(len(token)-1))+")")
        else:
            clauses.append('"'+token+'"')
    return " OR ".join(clauses)


class KnowledgeStore:
    def __init__(self, root, check=lambda: None):
        self.root, self.check = Path(root).resolve(), check
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "knowledge.sqlite"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, name TEXT, version INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS securities(id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies(id), identity TEXT);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, hash TEXT, metadata TEXT, missing INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS acquisitions(id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id), session TEXT, metadata TEXT, acquired_at TEXT, removed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, company_id TEXT REFERENCES companies(id), kind TEXT, head TEXT, deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY, entry_id TEXT REFERENCES entries(id), body TEXT, hash TEXT, session TEXT, recorded_at TEXT, company_version INTEGER, supersedes TEXT REFERENCES revisions(id));
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, entry_id TEXT, company_id TEXT, body TEXT, hash TEXT, session TEXT, proposer TEXT, base INTEGER, status TEXT, revision_id TEXT);
            CREATE TABLE IF NOT EXISTS reviewers(task TEXT PRIMARY KEY, session TEXT, role TEXT);
            CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, proposal_id TEXT REFERENCES proposals(id), hash TEXT, task TEXT REFERENCES reviewers(task), passed INTEGER, reasons TEXT, recorded_at TEXT);
            CREATE TABLE IF NOT EXISTS pins(session TEXT, revision_id TEXT REFERENCES revisions(id), adopted INTEGER DEFAULT 0, PRIMARY KEY(session,revision_id));
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, kind TEXT, session TEXT, payload TEXT, recorded_at TEXT);
            CREATE TABLE IF NOT EXISTS file_commits(id TEXT PRIMARY KEY, hash TEXT, content BLOB, status TEXT);
            CREATE TABLE IF NOT EXISTS deletion_journal(id TEXT PRIMARY KEY, payload TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, session TEXT, company_id TEXT, body TEXT, hash TEXT, formed_at TEXT);
            CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY, snapshot_id TEXT REFERENCES snapshots(id), body TEXT, recorded_at TEXT);
            CREATE TABLE IF NOT EXISTS vectors(revision_id TEXT REFERENCES revisions(id), fingerprint TEXT, model TEXT, dimension INTEGER, index_version INTEGER, vector TEXT, PRIMARY KEY(revision_id,fingerprint));
            CREATE TABLE IF NOT EXISTS vector_cache(content_hash TEXT, fingerprint TEXT, dimension INTEGER, vector TEXT, index_version INTEGER, PRIMARY KEY(content_hash,fingerprint));
            CREATE TABLE IF NOT EXISTS preferences(id TEXT, version INTEGER, scope TEXT, original TEXT, meaning TEXT, parameters TEXT, source TEXT, active INTEGER, recorded_at TEXT, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS applications(session TEXT, preference_id TEXT, version INTEGER, status TEXT, detail TEXT, recorded_at TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(adapter TEXT, target TEXT, cursor TEXT, checkpoint TEXT, PRIMARY KEY(adapter,target));
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, directory TEXT, missing_sources TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS priorities(revision_id TEXT REFERENCES revisions(id), rule TEXT, reason TEXT, session TEXT, recorded_at TEXT, PRIMARY KEY(revision_id,rule,session));
            CREATE VIRTUAL TABLE IF NOT EXISTS fulltext USING fts5(revision_id UNINDEXED, tokens);
            """)
        self.recover()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=.1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def transaction(self):
        # Bounded waits are charged to the caller's existing deadline.
        with (self.root / "commit.lock").open("a") as lock:
            while True:
                self.check()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.02)
            db = self.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                self.check()
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()
                fcntl.flock(lock, fcntl.LOCK_UN)

    def log(self, kind, session, payload, db=None):
        row = (uuid4().hex, kind, session, json.dumps(payload, ensure_ascii=False), now())
        if db is not None:
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?)", row)
        else:
            with self.connect() as connection:
                connection.execute("INSERT INTO operations VALUES(?,?,?,?,?)", row)

    def identity(self, research, session):
        identity = {k: research.get(k) for k in ("market", "exchange", "security_code", "share_class")}
        security = "security-" + digest(identity)[:24] if identity["security_code"] and identity["market"] else None
        with self.transaction() as db:
            prior = db.execute("SELECT company_id FROM securities WHERE id=?", (security,)).fetchone()
            company = research.get("company_id") or (prior[0] if prior else "company-" + uuid4().hex)
            db.execute("INSERT OR IGNORE INTO companies(id,name) VALUES(?,?)", (company, research["company"]))
            if prior and prior[0] != company:
                raise ValueError("security already belongs to a different company identity")
            if security:
                db.execute("INSERT OR IGNORE INTO securities VALUES(?,?,?)", (security, company, json.dumps(identity)))
        return company, security

    def version(self, company):
        with self.connect() as db:
            row = db.execute("SELECT version FROM companies WHERE id=?", (company,)).fetchone()
        if not row:
            raise ValueError("unknown company identity")
        return row[0]

    def blob_path(self, source_hash):
        if len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
            raise ValueError("invalid source hash")
        return self.root / "objects" / source_hash

    def archive_source(self, entry, content, session):
        if hashlib.sha256(content).hexdigest() != entry["source_hash"]:
            raise ValueError("source hash mismatch")
        metadata = {k: v for k, v in entry.items() if k != "file_path"}
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO file_commits VALUES(?,?,?,?)", (entry["source_hash"], entry["source_hash"], content, "pending"))
            db.execute("INSERT OR IGNORE INTO sources(id,hash,metadata) VALUES(?,?,?)", (entry["id"], entry["source_hash"], json.dumps(metadata, ensure_ascii=False)))
            row = db.execute("SELECT * FROM sources WHERE id=?", (entry["id"],)).fetchone()
            if row["hash"] != entry["source_hash"] or row["missing"]:
                raise ValueError("source identity missing or inconsistent")
            acquisition = digest([session, entry["id"], entry.get("origin"), entry.get("url"), entry.get("acquired_at")])
            db.execute("INSERT OR IGNORE INTO acquisitions VALUES(?,?,?,?,?,0)", (acquisition, entry["id"], session, json.dumps(metadata, ensure_ascii=False), entry["acquired_at"]))
        self.recover()
        return entry["id"]

    def recover(self):
        # An interruption after SQLite commit is completed from the durable journal.
        with self.connect() as db:
            for row in db.execute("SELECT * FROM file_commits WHERE status='pending'").fetchall():
                atomic_bytes(self.blob_path(row["hash"]), row["content"])
                db.execute("UPDATE file_commits SET status='complete',content=NULL WHERE id=?", (row["id"],))
            for row in db.execute("SELECT * FROM deletion_journal WHERE status='pending'").fetchall():
                plan=json.loads(row["payload"])
                for path in plan["files"]:
                    Path(path).unlink(missing_ok=True)
                for session in plan["sessions"]:
                    directory=Path(session["directory"])
                    index_path=directory/"materials"/"index.json"
                    if index_path.exists():
                        index=json.loads(index_path.read_text())
                        for material in index["materials"]:
                            if material["id"]==plan["source_id"]:
                                material["chunks"]=[]
                                material["source_missing"]=True
                        atomic_bytes(index_path,json.dumps(index,ensure_ascii=False,indent=2).encode())
                    marker={"source_missing":True,"cannot_fully_reproduce":True,"sources":session["missing_sources"]}
                    atomic_bytes(directory/"source-missing.json",json.dumps(marker).encode())
                db.execute("UPDATE deletion_journal SET status='complete' WHERE id=?",(row["id"],))

    def source(self, identifier, allow_deleted=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sources WHERE id=?", (identifier,)).fetchone()
        if not row or row["missing"] or (row["deleted"] and not allow_deleted):
            raise ValueError("source missing/deleted; cannot reproduce")
        path = self.blob_path(row["hash"])
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != row["hash"]:
            raise ValueError("source missing/hash mismatch; cannot reproduce")
        return json.loads(row["metadata"]), path

    def validate_evidence(self, item):
        for ref in item.evidence + item.opposing_evidence:
            source, _ = self.source(ref.source_id)
            chunk = next((c for c in source["chunks"] if c["ref"] == ref.anchor_ref), None)
            if source["source_hash"] != ref.source_hash or not chunk or chunk["location"] != ref.locator:
                raise ValueError("evidence hash/locator does not resolve")

    def propose(self, item, session, proposer, base_version, entry_id=None):
        item = KnowledgeItem.model_validate(item)
        self.validate_evidence(item)
        identifier, entry = "proposal-" + uuid4().hex, entry_id or "knowledge-" + uuid4().hex
        with self.transaction() as db:
            existing = db.execute("SELECT * FROM entries WHERE id=?", (entry,)).fetchone()
            if existing and (existing["company_id"] != item.company_id or existing["kind"] != item.kind or existing["deleted"]):
                raise ValueError("entry identity/type changed or deleted")
            db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                       (identifier, entry, item.company_id, item.model_dump_json(), digest(item.model_dump()), session, proposer, base_version, "pending"))
            self.log("propose", session, {"proposal_id": identifier, "entry_id": entry}, db)
        return self.proposal(identifier)

    def proposal(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("unknown proposal")
        return dict(row) | {"item": json.loads(row["body"])}

    def register_reviewer(self, task, session):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO reviewers VALUES(?,?,?)", (task, session, "verification"))

    def review(self, review, task, session):
        from .schemas import KnowledgeReview
        review = KnowledgeReview.model_validate(review)
        proposal = self.proposal(review.proposal_id)
        if proposal["hash"] != review.content_hash:
            raise ValueError("review content hash mismatch")
        with self.transaction() as db:
            reviewer = db.execute("SELECT * FROM reviewers WHERE task=?", (task,)).fetchone()
            if not reviewer or reviewer["session"] != session or proposal["session"] != session or task == proposal["proposer"]:
                raise PermissionError("independent verification task required")
            if review.passed:
                item = KnowledgeItem.model_validate(proposal["item"])
                self.validate_evidence(item)
                if item.kind in {"fact", "thesis", "question"} and not item.evidence:
                    raise ValueError("cannot verify without original evidence")
                if item.kind == "fact" and (item.content.get("information_kind") or item.content.get("kind") or "fact") != "fact":
                    raise ValueError("prediction/assumption is not a verified fact")
            db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?)", (uuid4().hex, review.proposal_id, review.content_hash, task, int(review.passed), json.dumps(review.reasons, ensure_ascii=False), now()))
            self.log("review", session, review.model_dump() | {"task": task}, db)

    def verification(self, revision_id, db=None):
        if db is None:
            with self.connect() as connection:
                return self.verification(revision_id, connection)
        rows = db.execute("SELECT r.* FROM reviews r JOIN proposals p ON p.id=r.proposal_id JOIN revisions v ON v.id=p.revision_id WHERE v.id=? AND r.hash=v.hash ORDER BY r.recorded_at", (revision_id,)).fetchall()
        if any(not r["passed"] for r in rows):
            status = "rejected"
        else:
            status = "passed" if rows else "pending"
        return {"status": status, "records": [dict(r) | {"reasons": json.loads(r["reasons"])} for r in rows]}

    def commit(self, identifier, allow_pending=False, manual=False):
        proposal = self.proposal(identifier)
        item = KnowledgeItem.model_validate(proposal["item"])
        self.validate_evidence(item)
        with self.transaction() as db:
            current = db.execute("SELECT * FROM entries WHERE id=?", (proposal["entry_id"],)).fetchone()
            if proposal["revision_id"]:
                return self.read(proposal["entry_id"], proposal["revision_id"])
            version = db.execute("SELECT version FROM companies WHERE id=?", (item.company_id,)).fetchone()[0]
            if version != proposal["base"]:
                raise VersionConflict("company base version changed; reconcile proposal")
            reviews = db.execute("SELECT * FROM reviews WHERE proposal_id=? AND hash=?", (identifier, proposal["hash"])).fetchall()
            passed = bool(reviews) and all(r["passed"] for r in reviews)
            if not passed and not (manual or (allow_pending and not current)):
                raise PermissionError("controller requires independent verification before update")
            revision = "revision-" + uuid4().hex
            db.execute("INSERT OR IGNORE INTO entries VALUES(?,?,?,?,0)", (proposal["entry_id"], item.company_id, item.kind, None))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)", (revision, proposal["entry_id"], proposal["body"], proposal["hash"], proposal["session"], now(), version+1, current["head"] if current else None))
            db.execute("UPDATE entries SET head=? WHERE id=?", (revision, proposal["entry_id"]))
            db.execute("UPDATE companies SET version=version+1 WHERE id=?", (item.company_id,))
            db.execute("UPDATE proposals SET status='committed',revision_id=? WHERE id=?", (revision, identifier))
            self.log("commit", proposal["session"], {"proposal_id": identifier, "revision_id": revision, "base": version, "manual": manual}, db)
        try:
            self.index_revision(revision)
        except sqlite3.Error:
            self.log("index_degraded", proposal["session"], {"revision_id": revision})
        return self.read(proposal["entry_id"], revision)

    def read(self, entry_id, revision_id=None):
        with self.connect() as db:
            entry = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not entry:
                raise ValueError("unknown knowledge entry")
            row = db.execute("SELECT * FROM revisions WHERE entry_id=? AND id=?", (entry_id, revision_id or entry["head"])).fetchone()
            if not row:
                raise ValueError("unknown immutable revision")
            item=json.loads(row["body"])
            verification=self.verification(row["id"],db)
            if item["content"].get("source_missing"):
                verification=verification|{"status":"source_missing","historical_status":verification["status"]}
            return dict(row) | {"item": item, "deleted": bool(entry["deleted"]), "verification": verification,"content_erased":bool(item["content"].get("source_missing"))}

    def index_revision(self, revision):
        with self.connect() as db:
            row = db.execute("SELECT body FROM revisions WHERE id=?", (revision,)).fetchone()
            db.execute("DELETE FROM fulltext WHERE revision_id=?", (revision,))
            db.execute("INSERT INTO fulltext VALUES(?,?)", (revision, terms(row[0])))

    def rebuild_fts(self):
        with self.connect() as db:
            db.execute("DELETE FROM fulltext")
            for row in db.execute("SELECT id,body FROM revisions").fetchall():
                self.check()
                db.execute("INSERT INTO fulltext VALUES(?,?)", (row["id"], terms(row["body"])))

    def mark_priority(self,revision,rule,reason,session):
        if rule not in {"R1","R2","R3","R4"} or not reason.strip():
            raise ValueError("priority rule and concrete reason required")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO priorities VALUES(?,?,?,?,?)",(revision,rule,reason,session,now()))

    def priority(self,revision):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM priorities WHERE revision_id=?",(revision,))]

    def pin(self, session, revision, adopted=False):
        with self.connect() as db:
            db.execute("INSERT INTO pins VALUES(?,?,?) ON CONFLICT(session,revision_id) DO UPDATE SET adopted=MAX(adopted,excluded.adopted)", (session, revision, int(adopted)))
        self.log("adopt" if adopted else "read", session, {"revision_id": revision})

    def edit_fact(self, entry, changes, reason, session="cli"):
        old = self.read(entry)
        if old["item"]["kind"] != "fact" or not reason.strip():
            raise ValueError("fact edit requires a reason")
        item = old["item"]
        if not isinstance(changes,dict) or "verification" in changes or "passed" in changes:
            raise ValueError("manual input cannot set verification")
        item["content"].update(changes)
        if "period" in changes:
            item["period"] = changes["period"]
        if "disclosure_date" in changes:
            item["disclosed_at"] = changes["disclosure_date"]
        # Whitespace-only edits use the same structured semantic hash.
        if digest(item) == old["hash"]:
            self.log("format_only_edit", session, {"entry_id": entry, "reason": reason})
            return old
        item.update(manual=True, reason=reason)
        proposal = self.propose(item, session, "user", self.version(item["company_id"]), entry)
        return self.commit(proposal["id"], manual=True)

    def snapshot(self, session, company, judgment, evidence):
        identifier = "judgment-" + uuid4().hex
        body = {"judgment": judgment, "evidence": evidence}
        with self.connect() as db:
            db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (identifier, session, company, json.dumps(body, ensure_ascii=False), digest(body), now()))
        self.log("judgment_snapshot", session, {"snapshot_id": identifier, "hash": digest(body)})
        return identifier

    def compare(self, snapshot_id, comparisons):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM snapshots WHERE id=?", (snapshot_id,)).fetchone():
                raise ValueError("fix judgment snapshot before historical comparison")
            identifier = uuid4().hex
            db.execute("INSERT INTO comparisons VALUES(?,?,?,?)", (identifier, snapshot_id, json.dumps(comparisons, ensure_ascii=False), now()))
        return identifier

    def deletion_impact(self, source_id):
        with self.connect() as db:
            revisions = [r["id"] for r in db.execute("SELECT id,body FROM revisions") if any(e["source_id"] == source_id for e in json.loads(r["body"])["evidence"] + json.loads(r["body"])["opposing_evidence"])]
            sessions = sorted({r[0] for r in db.execute("SELECT session FROM acquisitions WHERE source_id=?", (source_id,))} | {r["session"] for r in db.execute("SELECT session,revision_id FROM pins") if r["revision_id"] in revisions})
        impact = {"source_id": source_id, "revision_ids": revisions, "sessions": sessions}
        return impact | {"confirmation": "DELETE:" + source_id + ":" + digest(impact)}

    def delete_source(self, source_id, permanent=False, confirmation=None, session=None):
        purge_reports=[]
        with self.transaction() as db:
            impact = self.deletion_impact(source_id)
            row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if not row:
                raise ValueError("unknown source")
            if session:
                db.execute("UPDATE acquisitions SET removed=1 WHERE source_id=? AND session=?", (source_id, session))
                for revision in impact["revision_ids"]:
                    db.execute("DELETE FROM pins WHERE session=? AND revision_id=?", (session, revision))
            elif permanent:
                if confirmation != impact["confirmation"]:
                    raise ValueError("display impact and explicitly confirm its token before permanent deletion")
                db.execute("UPDATE sources SET missing=1,deleted=1,metadata='{}' WHERE id=?", (source_id,))
                db.execute("UPDATE acquisitions SET metadata='{}' WHERE source_id=?", (source_id,))
                for revision in impact["revision_ids"]:
                    db.execute("DELETE FROM fulltext WHERE revision_id=?", (revision,))
                    db.execute("DELETE FROM vectors WHERE revision_id=?", (revision,))
                    # Preserve identifiers and missing-source provenance, remove affected text.
                    body = json.loads(db.execute("SELECT body FROM revisions WHERE id=?", (revision,)).fetchone()[0])
                    from .embedding import vector_text
                    db.execute("DELETE FROM vector_cache WHERE content_hash=?",(digest(vector_text(body)),))
                    if body["kind"]=="study":
                        purge_reports.extend(r["path"] for r in body["content"].get("reports",[]))
                    body["content"] = {"source_missing": True}
                    db.execute("UPDATE revisions SET body=? WHERE id=?", (json.dumps(body), revision))
                    db.execute("UPDATE proposals SET body=? WHERE revision_id=?",(json.dumps(body),revision))
                for pending in db.execute("SELECT id,body FROM proposals WHERE revision_id IS NULL").fetchall():
                    body=json.loads(pending["body"])
                    if any(e["source_id"]==source_id for e in body["evidence"]+body["opposing_evidence"]):
                        body["content"]={"source_missing":True}
                        db.execute("UPDATE proposals SET body=?,status='source_missing' WHERE id=?",(json.dumps(body),pending["id"]))
                for snapshot in db.execute("SELECT id,body FROM snapshots").fetchall():
                    body=json.loads(snapshot["body"])
                    if any(e["source_id"]==source_id for e in body["evidence"]):
                        body["judgment"]={"source_missing":True}
                        db.execute("UPDATE snapshots SET body=? WHERE id=?",(json.dumps(body),snapshot["id"]))
                for affected in impact["sessions"]:
                    session_row = db.execute("SELECT * FROM sessions WHERE id=?",(affected,)).fetchone()
                    if session_row:
                        missing = sorted(set(json.loads(session_row["missing_sources"])) | {source_id})
                        db.execute("UPDATE sessions SET missing_sources=? WHERE id=?",(json.dumps(missing),affected))
                db.execute("DELETE FROM file_commits WHERE hash=?", (row["hash"],))
                files=[]
                if not db.execute("SELECT 1 FROM sources WHERE hash=? AND missing=0",(row["hash"],)).fetchone():
                    files.append(str(self.blob_path(row["hash"])))
                for report in purge_reports:
                    path=(self.root/report).resolve()
                    if path.is_relative_to(self.root):
                        files.append(str(path))
                session_plans=[]
                for registered in db.execute("SELECT * FROM sessions").fetchall():
                    if registered["id"] not in impact["sessions"]:
                        continue
                    directory=Path(registered["directory"])
                    index_path=directory/"materials"/"index.json"
                    index=json.loads(index_path.read_text()) if index_path.exists() else {"materials":[]}
                    for material in index["materials"]:
                        if material["id"]==source_id and Path(material["file"]).name==material["file"]:
                            files.append(str(directory/"materials"/material["file"]))
                    files.extend(str(report) for report in directory.glob("report-v*.*"))
                    session_plans.append({"directory":str(directory),"missing_sources":json.loads(registered["missing_sources"])})
                plan={"source_id":source_id,"files":sorted(set(files)),"sessions":session_plans}
                db.execute("INSERT INTO deletion_journal VALUES(?,?,'pending')",(uuid4().hex,json.dumps(plan)))
                self.log("source_missing_cannot_reproduce", "cli", impact, db)
            else:
                db.execute("UPDATE sources SET deleted=1 WHERE id=?", (source_id,))
            self.log("remove_session_reference" if session else "permanent_delete" if permanent else "soft_delete", session or "cli", impact, db)
        if permanent and not session:
            self.recover()
        return impact

    def status(self):
        with self.connect() as db:
            return {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in ("companies", "securities", "sources", "entries", "revisions", "reviews", "vectors", "fulltext")} | {"root": str(self.root), "fts_version": "han-bigram-v1",
                "company_identities":[dict(r) for r in db.execute("SELECT * FROM companies")],
                "security_identities":[dict(r)|{"identity":json.loads(r["identity"])} for r in db.execute("SELECT * FROM securities")],
                "vector_indexes":[dict(r) for r in db.execute("SELECT fingerprint,model,dimension,index_version,count(*) AS rows FROM vectors GROUP BY fingerprint,model,dimension,index_version")],
                "pending_file_commits":db.execute("SELECT count(*) FROM file_commits WHERE status='pending'").fetchone()[0]}
