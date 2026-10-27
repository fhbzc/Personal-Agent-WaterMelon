"""Privacy-filtered WeChat observations and conservative message alignment.

All identifiers are local. UI evidence can establish a provisional match,
not a real WeChat ID or proof of delivery. This store is called only after
Listener policy and credential filtering, in the same transaction as raw data.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from difflib import SequenceMatcher
from datetime import datetime, timezone

from ...common.events.models import new_id, json_dump


# Simplified and traditional compared alike only where OpenCC happens to be
# installed: it is no dependency (the user, 2026-10-26). WeChat's words on
# Windows are its own and never vary; a recognizer reading one character in
# the other script is rare beside its other mistakes, which the matching of
# messages already allows for (`_revision`).
try:
    from opencc import OpenCC
    _converter = OpenCC('t2s')
except ImportError:
    _converter = None


@lru_cache(maxsize=4096)
def _key(value):
    text = unicodedata.normalize('NFKC', str(value or ''))
    if _converter is not None:
        text = _converter.convert(text)
    return re.sub(r'\s+', '', text).casefold()


def _content_key(capture):
    """Reading identity ignores OCR confidence and script variants, not position."""
    messages = capture.get('messages') or []
    if capture.get('layout_status') != 'recognized' or not capture.get('peer_name') or not messages:
        return None
    try:
        content = [(_key(m.get('text')), m.get('sender_role'),
                    [round(float(v), 3) for v in m.get('bbox', [])]) for m in messages]
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(json_dump([_key(capture['peer_name']), content]).encode()).hexdigest()


def _enough_frame_evidence(capture):
    messages = capture.get('messages') or []
    boxes = {tuple(m.get('bbox') or []) for m in messages if len(m.get('bbox') or []) == 4}
    tokens = {(m.get('sender_role'), _key(m.get('text'))) for m in messages}
    return len(boxes) >= 2 and len(tokens) >= 2


def _avatar_matches(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        ah, ac = a.split(':')
        bh, bc = b.split(':')
        colors_a, colors_b = list(map(int, ac.split(','))), list(map(int, bc.split(',')))
        return ((int(ah, 16) ^ int(bh, 16)).bit_count() <= 5
                and len(colors_a) == len(colors_b) == 3
                and max(abs(x-y) for x,y in zip(colors_a,colors_b)) <= 1)
    except (ValueError, TypeError):
        return False


class WechatStore:
    def __init__(self, db):
        self.db = db

    def _account(self, conn):
        row = conn.execute("SELECT value FROM kv WHERE key='listener_wechat_local_account'").fetchone()
        if row:
            return row['value']
        account = new_id('account')
        conn.execute("INSERT INTO kv(key,value) VALUES('listener_wechat_local_account',?)", (account,))
        return account

    @staticmethod
    def _canonical(conn, ident):
        seen = set()
        while ident not in seen:
            seen.add(ident)
            row = conn.execute('SELECT canonical_id FROM listener_wechat_message_aliases WHERE id=?', (ident,)).fetchone()
            if not row:
                return ident
            ident = row['canonical_id']
        raise ValueError('Cyclic WeChat message alias')

    @staticmethod
    def _archive_link(conn, capture, position, old, chosen, reason):
        conn.execute('INSERT INTO listener_wechat_link_archive(capture_id,position,message_id,retained_message_id,reason,archived_at) VALUES(?,?,?,?,?,?)',
                     (capture,position,old,chosen,reason,datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _can_alias(conn, old, chosen):
        a=conn.execute('SELECT * FROM listener_wechat_messages WHERE id=?',(old,)).fetchone()
        b=conn.execute('SELECT * FROM listener_wechat_messages WHERE id=?',(chosen,)).fetchone()
        if not a or not b or a['conversation_id'] != b['conversation_id']:
            return False
        # Repair is lossless only for the same literal content and role.
        # Simplified/traditional equivalence is for future alignment, not
        # permission to rewrite uncertain historical authorship or wording.
        if (a['text'],a['sender_role']) != (b['text'],b['sender_role']):
            return False
        distinct=conn.execute('SELECT 1 FROM listener_wechat_message_captures a JOIN listener_wechat_message_captures b ON a.capture_id=b.capture_id WHERE a.message_id=? AND b.message_id=? AND a.position<>b.position LIMIT 1',(old,chosen)).fetchone()
        return not distinct

    @staticmethod
    def _alias_message(conn, old, chosen, capture, position):
        old=WechatStore._canonical(conn,old);chosen=WechatStore._canonical(conn,chosen)
        if old==chosen:
            return True
        if not WechatStore._can_alias(conn,old,chosen):
            return False
        original=conn.execute('SELECT * FROM listener_wechat_messages WHERE id=?',(old,)).fetchone()
        conn.execute('INSERT INTO listener_wechat_message_aliases(id,canonical_id,capture_id,position,reason,created_at) VALUES(?,?,?,?,?,?)',
                     (old,chosen,capture,position,'same_capture_position',datetime.now(timezone.utc).isoformat()))
        conn.execute('UPDATE listener_wechat_message_aliases SET canonical_id=? WHERE canonical_id=? AND id<>?',(chosen,old,old))
        old_links=conn.execute('SELECT * FROM listener_wechat_message_captures WHERE message_id=?',(old,)).fetchall()
        for link in old_links:
            WechatStore._archive_link(conn,link['capture_id'],link['position'],old,chosen,'message_alias')
        conn.execute('DELETE FROM listener_wechat_message_captures WHERE message_id=?',(old,))
        for link in old_links:
            occupied=conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? AND position=?',(link['capture_id'],link['position'])).fetchall()
            if not occupied:
                conn.execute('INSERT OR IGNORE INTO listener_wechat_message_captures(message_id,capture_id,position) VALUES(?,?,?)',(chosen,link['capture_id'],link['position']))
        conn.execute('UPDATE listener_wechat_messages SET first_seen_at=MIN(first_seen_at,?),last_seen_at=MAX(last_seen_at,?) WHERE id=?',
                     (original['first_seen_at'],original['last_seen_at'],chosen))
        return True

    @staticmethod
    def _capture_ids(conn, capture):
        return [r['message_id'] for r in conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? ORDER BY position',(capture,))]

    @staticmethod
    def _restore_views(conn):
        for table in ('listener_wechat_views','listener_wechat_segments'):
            for row in conn.execute(f'SELECT id,message_ids FROM {table}').fetchall():
                ids=[WechatStore._canonical(conn,i) for i in json.loads(row['message_ids'])]
                conn.execute(f'UPDATE {table} SET message_ids=? WHERE id=?',(json_dump(ids),row['id']))
        for segment in conn.execute('SELECT * FROM listener_wechat_segments').fetchall():
            latest=conn.execute('SELECT capture_id FROM listener_wechat_observations WHERE segment_id=? AND capture_id IS NOT NULL ORDER BY last_seen_at DESC,rowid DESC LIMIT 1',(segment['id'],)).fetchone()
            if not latest:
                continue
            ids=WechatStore._capture_ids(conn,latest['capture_id'])
            header=_key(segment['header_key'])
            scope=hashlib.sha256((segment['account_id']+'\n'+segment['id']+'\n'+header).encode()).hexdigest()
            conn.execute('UPDATE listener_wechat_segments SET header_key=?,message_ids=? WHERE id=?',(header,json_dump(ids),segment['id']))
            conn.execute('INSERT INTO listener_wechat_views(id,conversation_id,message_ids,last_seen_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET conversation_id=excluded.conversation_id,message_ids=excluded.message_ids,last_seen_at=excluded.last_seen_at',
                         (scope,segment['conversation_id'],json_dump(ids),segment['last_seen_at']))

    @staticmethod
    def repair_capture_links(conn):
        """One-time, audited repair. Historical messages and raw rows survive."""
        marker='listener_wechat_link_repair_v1'
        if conn.execute('SELECT value FROM kv WHERE key=?',(marker,)).fetchone():
            return
        conflicts=conn.execute('SELECT capture_id,position FROM listener_wechat_message_captures GROUP BY capture_id,position HAVING COUNT(*)>1').fetchall()
        aliases=0;uncertain=0
        for conflict in conflicts:
            cap,pos=conflict['capture_id'],conflict['position']
            rows=conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? AND position=? ORDER BY rowid',(cap,pos)).fetchall()
            if len(rows)<2:
                continue
            chosen=WechatStore._canonical(conn,rows[0]['message_id'])
            for row in rows[1:]:
                old=WechatStore._canonical(conn,row['message_id'])
                if old==chosen:
                    continue
                if WechatStore._alias_message(conn,old,chosen,cap,pos):
                    aliases+=1
                else:
                    # Preserve conflicting messages independently; archive the
                    # ambiguous extra association instead of guessing identity.
                    WechatStore._archive_link(conn,cap,pos,old,chosen,'unresolved_historical_association')
                    conn.execute('DELETE FROM listener_wechat_message_captures WHERE capture_id=? AND position=? AND message_id=?',(cap,pos,old))
                    uncertain+=1
        for row in conn.execute('SELECT id,peer_name FROM listener_wechat_conversations').fetchall():
            conn.execute('UPDATE listener_wechat_conversations SET header_key=? WHERE id=?',(_key(row['peer_name']),row['id']))
        for row in conn.execute('SELECT id,payload FROM listener_wechat_captures').fetchall():
            conn.execute('UPDATE listener_wechat_captures SET content_key=? WHERE id=?',(_content_key(json.loads(row['payload'])),row['id']))
        WechatStore._restore_views(conn)
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_capture_position ON listener_wechat_message_captures(capture_id,position)')
        conn.execute('INSERT INTO kv(key,value) VALUES(?,?)',(marker,json_dump({'aliased_messages':aliases,'uncertain_associations':uncertain,'conflicting_positions':len(conflicts),'script_comparison':_converter is not None})))

    @staticmethod
    def _merge(conn, old_id, new_id):
        for table in ('listener_wechat_messages','listener_wechat_views','listener_wechat_segments','listener_wechat_observations'):
            conn.execute(f'UPDATE {table} SET conversation_id=? WHERE conversation_id=?',(new_id,old_id))
        for capture in conn.execute('SELECT * FROM listener_wechat_captures WHERE conversation_id=?',(old_id,)).fetchall():
            existing=conn.execute('SELECT id FROM listener_wechat_captures WHERE conversation_id=? AND frame_hash=?',(new_id,capture['frame_hash'])).fetchone()
            if not existing:
                conn.execute('UPDATE listener_wechat_captures SET conversation_id=? WHERE id=?',(new_id,capture['id']))
                continue
            for link in conn.execute('SELECT * FROM listener_wechat_message_captures WHERE capture_id=? ORDER BY position',(capture['id'],)).fetchall():
                occupied=conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? AND position=?',(existing['id'],link['position'])).fetchone()
                if occupied:
                    if not WechatStore._alias_message(conn,link['message_id'],occupied['message_id'],existing['id'],link['position']):
                        WechatStore._archive_link(conn,existing['id'],link['position'],link['message_id'],occupied['message_id'],'unresolved_historical_association')
                else:
                    conn.execute('INSERT OR IGNORE INTO listener_wechat_message_captures(message_id,capture_id,position) VALUES(?,?,?)',(link['message_id'],existing['id'],link['position']))
            conn.execute('UPDATE listener_wechat_observations SET capture_id=? WHERE capture_id=?',(existing['id'],capture['id']))
            conn.execute('DELETE FROM listener_wechat_captures WHERE id=?',(capture['id'],))
        conn.execute('UPDATE listener_wechat_conversation_aliases SET conversation_id=? WHERE conversation_id=?',(new_id,old_id))
        conn.execute('INSERT OR REPLACE INTO listener_wechat_conversation_aliases(id,conversation_id) VALUES(?,?)',(old_id,new_id))
        conn.execute('DELETE FROM listener_wechat_conversations WHERE id=?',(old_id,))
        WechatStore._restore_views(conn)

    def _conversation(self, conn, account, capture, when):
        name = str(capture.get('peer_name') or '').strip()[:200] or None
        header = _key(name)
        avatar = capture.get('peer_avatar') if capture.get('conversation_type') == 'direct' else None
        scope = hashlib.sha256((account+'\n'+str(capture.get('scope_id') or '')+'\n'+header).encode()).hexdigest()
        visit = conn.execute('SELECT c.* FROM listener_wechat_views v JOIN listener_wechat_conversations c ON c.id=v.conversation_id WHERE v.id=?', (scope,)).fetchone()
        matched = []
        match_basis=['chat_header','avatar']
        if header and avatar:
            matched = [r for r in conn.execute('SELECT * FROM listener_wechat_conversations WHERE account_id=? AND header_key=?',
                                               (account,header)).fetchall() if _avatar_matches(r['peer_avatar'],avatar)]
        if not matched and header and _enough_frame_evidence(capture):
            matched=conn.execute('SELECT DISTINCT c.* FROM listener_wechat_conversations c JOIN listener_wechat_captures p ON p.conversation_id=c.id WHERE c.account_id=? AND c.header_key=? AND p.content_key=?',
                                 (account,header,_content_key(capture))).fetchall()
            matched=[r for r in matched if not avatar or not r['peer_avatar'] or _avatar_matches(r['peer_avatar'],avatar)]
            match_basis=['chat_header','visible_frame']
        if not matched and header:
            match_basis=['chat_header','visible_context']
            # Revisit matching uses distinct, substantive visible context.
            # A display name or repeated short reply alone is insufficient.
            matched=[r for r in conn.execute('SELECT * FROM listener_wechat_conversations WHERE account_id=? AND header_key=?',
                                              (account,header)).fetchall()
                     if (not avatar or not r['peer_avatar'] or _avatar_matches(r['peer_avatar'],avatar))
                     and self._context_matches(conn,r['id'],capture)]
        if len(matched) == 1:
            chosen = matched[0]
            if visit and visit['id'] != chosen['id'] and not visit['peer_avatar']:
                self._merge(conn, visit['id'], chosen['id'])
        elif visit and (not avatar or not visit['peer_avatar'] or _avatar_matches(visit['peer_avatar'], avatar)):
            match_basis=['continuous_segment']
            chosen = visit
            if avatar and not visit['peer_avatar']:
                conn.execute("UPDATE listener_wechat_conversations SET peer_avatar=?,identity_status='provisional',identity_basis=? WHERE id=?",
                             (avatar,json_dump(['chat_header','avatar']),visit['id']))
        else:
            ident = new_id('conv')
            kind = capture.get('conversation_type') if capture.get('conversation_type') in ('direct','group') else 'unknown'
            basis = ['chat_header','avatar'] if name and avatar else ['chat_header'] if name else []
            match_basis=['new_anonymous_conversation']+basis
            conn.execute('INSERT INTO listener_wechat_conversations(id,account_id,person_id,peer_name,header_key,peer_avatar,kind,identity_status,identity_basis,created_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (ident, account, None, name, header,
                          avatar, kind, 'anonymous' if name else 'unresolved',json_dump(basis),when,when))
            chosen = conn.execute('SELECT * FROM listener_wechat_conversations WHERE id=?', (ident,)).fetchone()
        conn.execute('UPDATE listener_wechat_conversations SET last_seen_at=? WHERE id=?', (when,chosen['id']))
        return chosen['id'], scope,match_basis

    @staticmethod
    def _context_matches(conn, conversation, capture):
        old=conn.execute('SELECT text FROM listener_wechat_messages m WHERE conversation_id=? AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id) ORDER BY rowid DESC LIMIT 100',
                         (conversation,)).fetchall()
        common={_key(r['text']) for r in old} & {_key(m.get('text')) for m in capture.get('messages',[]) if isinstance(m,dict)}
        return len(common)>=2 and sum(len(t) for t in common)>=12 and any(len(t)>=5 for t in common)

    def _segment(self, conn, account, capture, when):
        scope=hashlib.sha256((account+'\n'+str(capture.get('scope_id') or '')).encode()).hexdigest()
        conn.execute('UPDATE listener_wechat_segments SET closed_at=last_seen_at WHERE account_id=? AND scope_key<>? AND closed_at IS NULL',(account,scope))
        last=conn.execute('SELECT * FROM listener_wechat_segments WHERE account_id=? AND scope_key=? ORDER BY rowid DESC LIMIT 1',
                          (account,scope)).fetchone()
        header=_key(capture.get('peer_name'));avatar=capture.get('peer_avatar') if capture.get('conversation_type')=='direct' else None
        gap=999
        if last:
            try: gap=(datetime.fromisoformat(when)-datetime.fromisoformat(last['last_seen_at'])).total_seconds()
            except ValueError: pass
        compatible=last and not last['closed_at'] and 0<=gap<=90 and header==last['header_key']
        if compatible and avatar and last['peer_avatar'] and not _avatar_matches(avatar,last['peer_avatar']):
            compatible=False
        if compatible:
            return last,scope
        if last and not last['closed_at']:
            conn.execute('UPDATE listener_wechat_segments SET closed_at=last_seen_at WHERE id=?',(last['id'],))
        ident=new_id('seg')
        conn.execute('INSERT INTO listener_wechat_segments(id,account_id,scope_key,header_key,peer_avatar,started_at,last_seen_at) VALUES(?,?,?,?,?,?,?)',
                     (ident,account,scope,header,avatar,when,when))
        return conn.execute('SELECT * FROM listener_wechat_segments WHERE id=?',(ident,)).fetchone(),scope

    @staticmethod
    def _same_box(a,b):
        try:
            ax,ay,aw,ah=a;bx,by,bw,bh=b
            overlap=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
            return overlap/max(1e-9,aw*ah+bw*bh-overlap)>=.80
        except (ValueError,TypeError): return False

    @staticmethod
    def _revision(old, message, anchors, same_frame):
        data=json.loads(old['payload'])
        if message.get('text_truncated'):
            return None
        if not WechatStore._same_box(data.get('bbox'),message.get('bbox')): return None
        old_text,new_text=_key(old['text']),_key(message['text'])
        upgrade=old['sender_role']=='unknown' and message['sender_role'] in ('self','other')
        if old_text==new_text and upgrade and (anchors or same_frame): return 'sender_resolved'
        if old['sender_role']!=message['sender_role'] and not upgrade: return None
        # Geometry alone cannot distinguish a new message occupying an old
        # location after scrolling. Require unchanged neighbouring anchors,
        # a small OCR change and better recognizer confidence.
        if anchors and min(len(old_text),len(new_text))>=6 and abs(len(old_text)-len(new_text))<=3:
            score=SequenceMatcher(None,old_text,new_text,autojunk=False).ratio()
            if score>=.88 and float(message.get('ocr_confidence') or 0)>=float(data.get('ocr_confidence') or 0)+3:
                return 'ocr_corrected'
        return None

    @staticmethod
    def _valid_messages(capture):
        messages = []
        for source in (capture.get('messages') or [])[:200]:
            if not isinstance(source,dict) or source.get('content_status') != 'visible_chat_bubble':
                continue
            text = str(source.get('text') or '').strip()[:16000]
            if not text:
                continue
            role = source.get('sender_role')
            role = role if role in ('self','other') else 'unknown'
            # No short-line threshold: a single character can be a message.
            messages.append({**source,'text':text,'sender_role':role,'message_time':None})
        return messages

    @staticmethod
    def _tokens(rows):
        return [(r['sender_role'],_key(r['text'])) for r in rows]

    def ingest(self, conn, event):
        capture=event.data['wechat_capture']
        when=str(capture.get('captured_at') or event.timestamp.isoformat())
        try:
            parsed=datetime.fromisoformat(when)
            when=parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else event.timestamp.isoformat()
        except (ValueError,TypeError): when=event.timestamp.isoformat()
        account=self._account(conn)
        messages=self._valid_messages(capture)
        valid=capture.get('layout_status')=='recognized' and bool(capture.get('peer_name')) and bool(messages)
        segment,scope=self._segment(conn,account,capture,when) if valid else (None,hashlib.sha256((account+'\n'+str(capture.get('scope_id') or '')).encode()).hexdigest())
        if not valid:
            conn.execute('UPDATE listener_wechat_segments SET closed_at=last_seen_at WHERE account_id=? AND scope_key=? AND closed_at IS NULL',(account,scope))
        conversation=None
        if valid:
            segmented={**capture,'scope_id':segment['id']}
            conversation,view_scope,match_basis=self._conversation(conn,account,segmented,when)
            conn.execute('UPDATE listener_wechat_segments SET conversation_id=?,last_seen_at=?,peer_avatar=COALESCE(peer_avatar,?),match_basis=? WHERE id=?',
                         (conversation,when,capture.get('peer_avatar') if capture.get('conversation_type')=='direct' else None,json_dump(match_basis),segment['id']))
        semantic={k:capture.get(k) for k in ('peer_name','peer_avatar','layout_status','layout_reason','messages','unresolved_text','parser_version')}
        frame_hash=hashlib.sha256(json_dump(semantic).encode()).hexdigest()
        prior=conn.execute('SELECT * FROM listener_wechat_observations WHERE account_id=? AND scope_key=? AND semantic_hash=? AND segment_id IS ? ORDER BY rowid DESC LIMIT 1',
                           (account,scope,frame_hash,segment['id'] if segment else None)).fetchone()
        if prior:
            conn.execute('UPDATE listener_wechat_observations SET last_seen_at=?,observations=observations+1 WHERE id=?',(when,prior['id']))
            if prior['capture_id']:
                conn.execute('UPDATE listener_wechat_captures SET last_seen_at=?,observations=observations+1 WHERE id=?',(when,prior['capture_id']))
                conn.execute('UPDATE listener_wechat_messages SET last_seen_at=? WHERE id IN (SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=?)',(when,prior['capture_id']))
                if segment:
                    ids=[r['message_id'] for r in conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? ORDER BY position',(prior['capture_id'],))]
                    conn.execute('UPDATE listener_wechat_segments SET message_ids=? WHERE id=?',(json_dump(ids),segment['id']))
                    conn.execute('UPDATE listener_wechat_views SET message_ids=?,last_seen_at=? WHERE id=?',(json_dump(ids),when,view_scope))
            return None
        observation_id=new_id('obs')
        conn.execute('INSERT INTO listener_wechat_observations(id,account_id,scope_key,segment_id,conversation_id,semantic_hash,captured_at,last_seen_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                     (observation_id,account,scope,segment['id'] if segment else None,conversation,frame_hash,when,when,json_dump(capture)))
        ids=[];new_messages=[];updates=[];capture_id=None
        if valid:
            prior_capture=conn.execute('SELECT * FROM listener_wechat_captures WHERE conversation_id=? AND frame_hash=?',(conversation,frame_hash)).fetchone()
            reference_capture=prior_capture or conn.execute('SELECT * FROM listener_wechat_captures WHERE conversation_id=? AND content_key=? ORDER BY rowid LIMIT 1',(conversation,_content_key(capture))).fetchone()
            capture_id=prior_capture['id'] if prior_capture else observation_id
            if not prior_capture:
                conn.execute('INSERT INTO listener_wechat_captures(id,conversation_id,frame_hash,content_key,captured_at,last_seen_at,payload) VALUES(?,?,?,?,?,?,?)',
                             (capture_id,conversation,frame_hash,_content_key(capture),when,when,json_dump(capture)))
            else:
                conn.execute('UPDATE listener_wechat_captures SET last_seen_at=?,observations=observations+1 WHERE id=?',(when,capture_id))
            conn.execute('UPDATE listener_wechat_observations SET capture_id=? WHERE id=?',(capture_id,observation_id))
            view=conn.execute('SELECT * FROM listener_wechat_views WHERE id=? AND conversation_id=?',(view_scope,conversation)).fetchone()
            old_rows=[]
            for ident in json.loads(view['message_ids']) if view else []:
                row=conn.execute('SELECT * FROM listener_wechat_messages WHERE id=? AND conversation_id=?',(ident,conversation)).fetchone()
                if row: old_rows.append(row)
            if not old_rows:
                old_rows=list(reversed(conn.execute('SELECT * FROM listener_wechat_messages m WHERE conversation_id=? AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id) ORDER BY rowid DESC LIMIT 300',(conversation,)).fetchall()))
            tokens=self._tokens(messages);old_tokens=self._tokens(old_rows)
            blocks=SequenceMatcher(None,old_tokens,tokens,autojunk=False).get_matching_blocks()
            matched=sum(block.size for block in blocks)
            if view and matched<len(tokens):
                # Returning from a scrolled older frame can expose already
                # observed messages beyond the current view. Reuse a richer
                # known window only with two distinct matching anchors.
                for known in conn.execute('SELECT id FROM listener_wechat_captures WHERE conversation_id=? AND id<>? ORDER BY rowid DESC LIMIT 20',(conversation,capture_id)).fetchall():
                    candidates=conn.execute('SELECT m.* FROM listener_wechat_message_captures l JOIN listener_wechat_messages m ON m.id=l.message_id WHERE l.capture_id=? ORDER BY l.position',(known['id'],)).fetchall()
                    candidate_blocks=SequenceMatcher(None,self._tokens(candidates),tokens,autojunk=False).get_matching_blocks()
                    count=sum(b.size for b in candidate_blocks)
                    distinct={tokens[b.b+i] for b in candidate_blocks for i in range(b.size)}
                    if count>matched and len(distinct)>=2:
                        old_rows=candidates;old_tokens=self._tokens(candidates);blocks=candidate_blocks;matched=count
            reliable=bool(view) or (matched>=2 and len({tokens[b.b+i] for b in blocks for i in range(b.size)})>=2)
            mapping={b.b+i:old_rows[b.a+i] for b in blocks for i in range(b.size)} if reliable else {}
            if reference_capture:
                # This exact semantic capture already owns its positions. A
                # revisit must never realign them against append-order history.
                known={r['position']:r for r in conn.execute('SELECT m.*,l.position FROM listener_wechat_message_captures l JOIN listener_wechat_messages m ON m.id=l.message_id WHERE l.capture_id=? ORDER BY l.position',(reference_capture['id'],))}
                if prior_capture:
                    mapping.update(known)
                elif set(known)==set(range(len(messages))):
                    mapping=known
            used={row['id'] for row in mapping.values()}
            for position,message in enumerate(messages):
                old=mapping.get(position);reason=None
                if old is None and view and position<len(old_rows) and old_rows[position]['id'] not in used and len(messages)==len(old_rows):
                    candidate=old_rows[position]
                    anchors=sum(i in mapping and mapping[i]['id']==old_rows[i]['id'] for i in (position-1,position+1) if 0<=i<len(old_rows))
                    previous=conn.execute('SELECT payload FROM listener_wechat_captures WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1 OFFSET 1',(conversation,)).fetchone()
                    same_frame=bool(previous and capture.get('frame_hash') and json.loads(previous['payload']).get('frame_hash')==capture['frame_hash'])
                    reason=self._revision(candidate,message,anchors,same_frame)
                    if reason: old=candidate;used.add(old['id'])
                if old is None:
                    ident=new_id('msg')
                    conn.execute('INSERT INTO listener_wechat_messages(id,conversation_id,sender_role,text,content_status,message_time,first_seen_at,last_seen_at,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                                 (ident,conversation,message['sender_role'],message['text'],'visible_chat_bubble',None,when,when,json_dump(message)))
                    new_messages.append({'message_id':ident,**message,'first_seen_at':when,'revision':1})
                else:
                    ident=old['id']
                    if reason:
                        conn.execute('INSERT INTO listener_wechat_message_revisions(id,message_id,observation_id,revised_at,reason,before_payload,after_payload) VALUES(?,?,?,?,?,?,?)',
                                     (new_id('rev'),ident,observation_id,when,reason,old['payload'],json_dump(message)))
                        conn.execute('UPDATE listener_wechat_messages SET sender_role=?,text=?,payload=? WHERE id=?',
                                     (message['sender_role'],message['text'],json_dump(message),ident))
                        revision=1+conn.execute('SELECT COUNT(*) FROM listener_wechat_message_revisions WHERE message_id=?',(ident,)).fetchone()[0]
                        updates.append({'message_id':ident,**message,'revision':revision,'change_reason':reason})
                ids.append(ident)
                conn.execute('UPDATE listener_wechat_messages SET last_seen_at=? WHERE id=?',(when,ident))
                occupied=conn.execute('SELECT message_id FROM listener_wechat_message_captures WHERE capture_id=? AND position=?',(capture_id,position)).fetchone()
                if occupied and occupied['message_id']!=ident:
                    raise ValueError('WeChat capture position already belongs to another message')
                if not occupied:
                    conn.execute('INSERT INTO listener_wechat_message_captures(message_id,capture_id,position) VALUES(?,?,?)',(ident,capture_id,position))
            conn.execute('UPDATE listener_wechat_segments SET message_ids=? WHERE id=?',(json_dump(ids),segment['id']))
            conn.execute('INSERT INTO listener_wechat_views(id,conversation_id,message_ids,last_seen_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET conversation_id=excluded.conversation_id,message_ids=excluded.message_ids,last_seen_at=excluded.last_seen_at',
                         (view_scope,conversation,json_dump(ids),when))
        publish=bool(new_messages or updates or not valid)
        if not publish: return None
        labels={'self':'本人（界面推断）','other':'对方（界面推断）','unknown':'发言者未知'}
        body='\n'.join(f"[{labels[m['sender_role']]}] {m['text']}" for m in new_messages)
        if updates: body+='\n'+ '\n'.join(f"[消息修订 {m['message_id']}] {m['text']}" for m in updates)
        if not valid: body='[界面未完成结构识别，不作本人发言证据]\n'+str(capture.get('unresolved_text') or '')[:16000]
        event.data={k:v for k,v in event.data.items() if k!='wechat_capture'}
        event.data.update(text=body,characters=len(body),schema_version='2.0',wechat_conversation_id=conversation,
                          wechat_segment_id=segment['id'] if segment else None,wechat_observation_id=observation_id,
                          wechat_capture_id=capture_id,wechat_messages=new_messages,wechat_message_updates=updates,
                          layout_status=capture.get('layout_status','unrecognized'))
        return event,observation_id

    def list_conversations(self, limit=30):
        rows = self.db.query('''SELECT c.id,c.peer_name,c.kind,c.identity_status,c.created_at,c.last_seen_at,
                         (SELECT COUNT(*) FROM listener_wechat_messages m WHERE m.conversation_id=c.id AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id)) AS messages,
                         (SELECT COUNT(*) FROM listener_wechat_messages m WHERE m.conversation_id=c.id AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id) AND m.sender_role='self') AS self_messages,
                         (SELECT COUNT(*) FROM listener_wechat_messages m WHERE m.conversation_id=c.id AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id) AND m.sender_role='other') AS other_messages,
                         (SELECT COUNT(*) FROM listener_wechat_segments s WHERE s.conversation_id=c.id) AS segments,
                         (SELECT COUNT(*) FROM listener_wechat_captures p WHERE p.conversation_id=c.id) AS captures
                         FROM listener_wechat_conversations c
                         WHERE EXISTS(SELECT 1 FROM listener_wechat_messages m WHERE m.conversation_id=c.id AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id))
                         ORDER BY c.last_seen_at DESC LIMIT ?''', (max(1,min(100,int(limit))),))
        repair=self.db.query_one("SELECT value FROM kv WHERE key='listener_wechat_link_repair_v1'")
        return {'conversations':rows,'schema_version':'2.0','source':'wechat_screen_ocr','diagnostics':self.observations(),
                'association_repair':json.loads(repair['value']) if repair else {}}

    def observations(self, limit=10):
        rows=self.db.query('SELECT * FROM listener_wechat_observations ORDER BY last_seen_at DESC,rowid DESC LIMIT ?',(max(1,min(50,int(limit))),))
        result=[]
        for row in rows:
            payload=json.loads(row['payload'])
            result.append({'observation_id':row['id'],'segment_id':row['segment_id'],'conversation_id':row['conversation_id'],
                           'captured_at':row['captured_at'],'last_seen_at':row['last_seen_at'],'observations':row['observations'],
                           'layout_status':payload.get('layout_status'),'layout_reason':payload.get('layout_reason'),
                           'parser_version':payload.get('parser_version'),'diagnostics':payload.get('diagnostics',{})})
        return result

    def conversation(self, ident, limit=200, before=None):
        alias = self.db.query_one('SELECT conversation_id FROM listener_wechat_conversation_aliases WHERE id=?',(ident,))
        if alias:
            ident = alias['conversation_id']
        conversation = self.db.query_one('SELECT * FROM listener_wechat_conversations WHERE id=?',(ident,))
        if not conversation:
            return None
        limit = max(1,min(1000,int(limit)))
        low = self.db.query_one('SELECT rowid AS n FROM listener_wechat_messages WHERE id=? AND conversation_id=?',(before,ident)) if before else None
        if before and low is None:
            raise ValueError('Unknown message pagination cursor')
        rows = self.db.query('SELECT rowid AS n,* FROM listener_wechat_messages m WHERE conversation_id=? AND rowid<? AND NOT EXISTS(SELECT 1 FROM listener_wechat_message_aliases a WHERE a.id=m.id) ORDER BY rowid DESC LIMIT ?',
                             (ident,low['n'] if low else 2**63-1,limit+1))
        more = len(rows)>limit
        rows = list(reversed(rows[:limit]))
        output, capture_ids = [], set()
        for row in rows:
            data = json.loads(row['payload'])
            link_count=self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_message_captures WHERE message_id=?',(row['id'],))['n']
            links = self.db.query('SELECT capture_id FROM listener_wechat_message_captures WHERE message_id=? ORDER BY rowid DESC LIMIT 20',(row['id'],))
            ids = [link['capture_id'] for link in links]
            capture_ids.update(ids)
            output.append({'message_id':row['id'],'sender_role':row['sender_role'],'text':row['text'],
                           'content_status':row['content_status'],'message_time':row['message_time'],
                           'first_seen_at':row['first_seen_at'],'last_seen_at':row['last_seen_at'],
                           'sender_basis':data.get('sender_basis','unresolved_layout'),
                           'attribution_status':data.get('attribution_status','unresolved'),
                           'bbox':data.get('bbox'),'text_truncated':bool(data.get('text_truncated')),
                           'ocr_confidence':data.get('ocr_confidence'),'source_capture_ids':ids,
                           'association_status':'needs_review' if self.db.query_one("SELECT id FROM listener_wechat_link_archive WHERE reason='unresolved_historical_association' AND (message_id=? OR retained_message_id=?) LIMIT 1",(row['id'],row['id'])) else 'linked',
                           'evidence_links_truncated':link_count>len(ids),
                           'revision':1+self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_message_revisions WHERE message_id=?',(row['id'],))['n'],
                           'revisions':[{'revised_at':r['revised_at'],'reason':r['reason'],'observation_id':r['observation_id'],
                                         'before':json.loads(r['before_payload']),'after':json.loads(r['after_payload'])}
                                        for r in self.db.query('SELECT * FROM (SELECT rowid AS revision_order,* FROM listener_wechat_message_revisions WHERE message_id=? ORDER BY rowid DESC LIMIT 20) ORDER BY revision_order',(row['id'],))]})
        # Empty/unrecognized captures must remain inspectable too.
        if not output:
            capture_ids.update(r['id'] for r in self.db.query('SELECT id FROM listener_wechat_captures WHERE conversation_id=? ORDER BY captured_at DESC LIMIT 20',(ident,)))
        captures = []
        selected_caps=self.db.query('SELECT id FROM listener_wechat_captures WHERE id IN (SELECT value FROM json_each(?)) ORDER BY last_seen_at DESC,rowid DESC LIMIT 50',(json_dump(list(capture_ids)),))
        selected_ids={c['id'] for c in selected_caps}
        for cap_id in sorted(capture_ids & selected_ids):
            row = self.db.query_one('SELECT * FROM listener_wechat_captures WHERE id=?',(cap_id,))
            payload = json.loads(row['payload'])
            captures.append({'capture_id':cap_id,'captured_at':row['captured_at'],'last_seen_at':row['last_seen_at'],
                             'observations':row['observations'],'source_event_id':row['source_event_id'],
                             'layout_status':payload.get('layout_status'),'layout_reason':payload.get('layout_reason'),
                             'parser_version':payload.get('parser_version'),'diagnostics':payload.get('diagnostics',{}),
                             'message_positions':self.db.query('SELECT message_id,position FROM listener_wechat_message_captures WHERE capture_id=? ORDER BY position',(cap_id,)),
                             'regions':payload.get('regions'),'content_truncated':bool(payload.get('content_truncated')),
                             'unresolved_text':payload.get('unresolved_text','')})
        return {'schema_version':'2.0','source':'wechat_screen_ocr',
                'segments':[{**r,'match_basis':json.loads(r['match_basis']),'visible_message_ids':json.loads(r['message_ids'])}
                            for r in self.db.query('SELECT id AS segment_id,started_at,last_seen_at,closed_at,match_basis,message_ids FROM listener_wechat_segments WHERE conversation_id=? ORDER BY rowid DESC LIMIT 100',(ident,))],
                'message_order':'first_observed; use capture message_positions for visible order',
                'evidence_capture_limit':50,'evidence_captures_truncated':len(capture_ids)>len(captures),
                'account_id':conversation['account_id'],
                'account_identity_status':'local_profile_unverified',
                'conversation':{'conversation_id':ident,'label':'会话 '+ident[-8:],'type':conversation['kind'],
                    'connection_id':None,'association_status':'local_ui_evidence_only',
                    'peer':{'person_id':conversation['person_id'],'display_name':conversation['peer_name'],'wechat_id':None},
                    'identity':{'basis':json.loads(conversation['identity_basis']),'status':conversation['identity_status']}},
                'message_aliases':self.db.query('SELECT a.id AS old_message_id,a.canonical_id AS message_id,a.reason,a.created_at FROM listener_wechat_message_aliases a JOIN listener_wechat_messages m ON m.id=a.canonical_id WHERE m.conversation_id=?',(ident,)),
                'association_conflicts':self.db.query("SELECT capture_id,position,message_id,retained_message_id,reason FROM listener_wechat_link_archive WHERE reason='unresolved_historical_association' AND (message_id IN (SELECT id FROM listener_wechat_messages WHERE conversation_id=?) OR retained_message_id IN (SELECT id FROM listener_wechat_messages WHERE conversation_id=?))",(ident,ident)),
                'messages':output,'captures':captures,
                'pagination':{'has_more':more,'next_before':output[0]['message_id'] if more and output else None}}

    def prune(self, conn, cutoff):
        conn.execute('DELETE FROM listener_wechat_observations WHERE last_seen_at<?',(cutoff,))
        conn.execute('DELETE FROM listener_wechat_segments WHERE last_seen_at<?',(cutoff,))
        messages = conn.execute('DELETE FROM listener_wechat_messages WHERE last_seen_at<?',(cutoff,)).rowcount
        captures = conn.execute('DELETE FROM listener_wechat_captures WHERE last_seen_at<?',(cutoff,)).rowcount
        conn.execute('DELETE FROM listener_wechat_views WHERE last_seen_at<?',(cutoff,))
        conn.execute('DELETE FROM listener_wechat_conversations WHERE last_seen_at<? AND NOT EXISTS(SELECT 1 FROM listener_wechat_messages m WHERE m.conversation_id=listener_wechat_conversations.id)',(cutoff,))
        return {'wechat_messages':messages,'wechat_captures':captures}
