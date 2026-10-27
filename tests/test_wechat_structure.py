"""Synthetic layout and transactional conversation tests; no personal chat data."""
import copy
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.data_manage.storage.raw_store import RawStore
from personalized_agent.platform_layer.wechat_layout import parse_wechat_frame
from personalized_agent.platform_layer.window_ocr import LocalWindowOcr


def frame(dark=False, scale=1, avatars=True):
    bg = (35,35,35) if dark else (237,237,237)
    image=Image.new('RGB',(1000,700),bg)
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,299,699),fill=(55,55,55) if dark else (220,220,220))
    draw.rectangle((300,0,999,69),fill=(48,48,48) if dark else (250,250,250))
    draw.rectangle((300,520,999,699),fill=(48,48,48) if dark else (250,250,250))
    # Two incoming messages (one wrapped), two identical outgoing short ones.
    boxes=[(360,120,560,164),(650,200,940,244),(650,275,940,319),(360,350,560,420)]
    for i,b in enumerate(boxes):
        draw.rounded_rectangle(b,radius=5,fill=(85,190,40) if i in (1,2) else (65,65,65) if dark else (255,255,255))
        if avatars:
            left=b[0]-47 if i in (0,3) else b[2]+5
            for x in range(40):
                for y in range(40):
                    image.putpixel((left+x,b[1]+y),((x*7+y*2)%220,(y*9+x)%220,(x*3+y*5)%220))
    words=[]
    entries=[('小陈',(330,26,70,16)),('sidebar private',(90,130,170,16)),
             ('你好',(375,133,50,16)),('好',(670,213,20,16)),('好',(670,288,20,16)),
             ('第一行',(375,363,65,16)),('第二行',(375,389,65,16)),
             ('13:50',(610,460,50,16)),('unsent draft',(340,560,150,16))]
    for i,(text,box) in enumerate(entries):
        words.append({'text':text,'bbox':[v*scale for v in box],'line':(i,0,0),'confidence':90})
    if scale!=1:
        image=image.resize((1000*scale,700*scale))
    out=io.BytesIO();image.save(out,format='PNG')
    return out.getvalue(),{'text':'\n'.join(t for t,b in entries),'words':words}


class LayoutTests(unittest.TestCase):
    def test_standard_light_dark_and_retina_exclude_sidebar_time_and_draft(self):
        for dark,scale in ((False,1),(True,1),(False,2)):
            with self.subTest(dark=dark,scale=scale):
                png,ocr=frame(dark,scale)
                result=parse_wechat_frame(png,ocr)
                self.assertEqual(result['layout_status'],'recognized',result['layout_reason'])
                self.assertEqual(result['peer_name'],'小陈')
                self.assertEqual([m['sender_role'] for m in result['messages']],['other','self','self','other'])
                self.assertEqual([m['text'] for m in result['messages']],['你好','好','好','第一行\n第二行'])
                self.assertIsNotNone(result['peer_avatar'])
                self.assertEqual(result['unresolved_text'],'13:50')
                self.assertNotIn('draft',json.dumps(result,ensure_ascii=False))

    def test_unknown_layout_and_missing_avatar_do_not_guess_sender(self):
        png,ocr=frame(avatars=False)
        parsed=parse_wechat_frame(png,ocr)
        self.assertEqual(parsed['messages'][0]['sender_role'],'other')
        blank=io.BytesIO();Image.new('RGB',(1000,700),'white').save(blank,format='PNG')
        parsed=parse_wechat_frame(blank.getvalue(),ocr)
        self.assertEqual(parsed['layout_status'],'unrecognized')
        self.assertEqual(parsed['messages'],[])


def message(text,role='self'):
    return {'text':text,'sender_role':role,'content_status':'visible_chat_bubble',
            'sender_basis':'right_bubble' if role=='self' else 'left_bubble',
            'attribution_status':'inferred_from_ui','message_time':None,'bbox':[.6,.2,.2,.05]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'db.sqlite');self.raw=RawStore(self.db)
        self.now=datetime.now(timezone.utc)
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def capture(self,texts=('A','B'),scope='visit1',name='小陈',avatar='0123456789abcdef:4,6,9',seconds=0,**kwargs):
        capture={'peer_name':name,'peer_avatar':avatar,'scope_id':scope,'conversation_type':'direct',
                 'layout_status':'recognized','layout_reason':'','frame_hash':'pixels'+str(seconds),
                 'captured_at':(self.now+timedelta(seconds=seconds)).isoformat(),
                 'messages':[message(t) for t in texts],'unresolved_text':'',**kwargs}
        event=ListenerEvent(event_type='screen.text',source='screen',application='WeChat',data={'text':'raw input','wechat_capture':capture})
        saved=self.raw.add_event(event)
        return event,saved
    def only(self):
        rows=self.raw.wechat.list_conversations()['conversations'];self.assertEqual(len(rows),1)
        return self.raw.wechat.conversation(rows[0]['id'])
    def test_repeat_observation_updates_without_duplicate_raw_and_new_repeat_is_kept(self):
        self.capture();e,saved=self.capture(seconds=5)
        self.assertIsNone(saved);data=self.only();self.assertEqual(len(data['messages']),2);self.assertEqual(len(data['captures']),1)
        self.assertEqual(data['captures'][0]['observations'],2);self.assertEqual(self.raw.count_events(),1)
        self.capture(('A','B','B'),seconds=10)
        data=self.only();self.assertEqual([m['text'] for m in data['messages']],['A','B','B'])
        self.assertEqual(len({m['message_id'] for m in data['messages']}),3)
        self.assertEqual(data['messages'][0]['last_seen_at'],(self.now+timedelta(seconds=10)).isoformat())
        self.assertEqual(len(data['messages'][0]['source_capture_ids']),2)
    def test_cross_visit_needs_avatar_and_context_not_name_alone(self):
        self.capture();self.capture(scope='visit2',seconds=5)
        self.assertEqual(len(self.only()['messages']),2)
        self.capture(scope='visit3',avatar='ffffffffffffffff:14,1,1',seconds=10)
        self.assertEqual(len(self.raw.wechat.list_conversations()['conversations']),2)
        self.capture(scope='visit4',avatar=None);self.capture(scope='visit5',avatar=None)
        self.assertEqual(len(self.raw.wechat.list_conversations()['conversations']),4)
    def test_continuous_name_only_visit_can_be_promoted_and_merged(self):
        self.capture(scope='known');self.capture(scope='new',avatar=None,texts=('C',),seconds=5)
        self.capture(scope='new',texts=('C','D'),seconds=10)
        data=self.only();self.assertEqual([m['text'] for m in data['messages']],['A','B','C','D'])
        self.assertEqual(data['conversation']['identity']['basis'],['chat_header','avatar'])
    def test_short_and_unknown_messages_and_pagination(self):
        self.capture(('好','嗯','好','?'))
        ident=self.only()['conversation']['conversation_id']
        latest=self.raw.wechat.conversation(ident,limit=2)
        self.assertEqual([m['text'] for m in latest['messages']],['好','?']);self.assertTrue(latest['pagination']['has_more'])
        older=self.raw.wechat.conversation(ident,limit=2,before=latest['pagination']['next_before'])
        self.assertEqual([m['text'] for m in older['messages']],['好','嗯']);self.assertFalse(older['pagination']['has_more'])
        with self.assertRaises(ValueError):self.raw.wechat.conversation(ident,before='wrong')
    def test_unrecognized_is_evidence_not_self_and_same_observation_is_not_republished(self):
        self.capture((),name=None,avatar=None,layout_status='unrecognized',unresolved_text='unattributed fragment')
        self.capture((),name=None,avatar=None,seconds=5,layout_status='unrecognized',unresolved_text='unattributed fragment')
        self.assertEqual(self.raw.wechat.list_conversations()['conversations'],[])
        observations=self.raw.wechat.observations();self.assertEqual(len(observations),1)
        self.assertIsNone(observations[0]['conversation_id']);self.assertEqual(observations[0]['observations'],2)
        self.assertIn('不作本人发言证据',self.raw.recent_events()[0].data['text']);self.assertEqual(self.raw.count_events(),1)
    def test_raw_failure_rolls_back_normalized_records(self):
        with patch.object(self.raw,'_insert_event',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):self.capture()
        self.assertEqual(self.raw.wechat.list_conversations()['conversations'],[])
    def test_restart_and_retention_preserve_legacy_records(self):
        legacy=ListenerEvent(event_type='screen.text',source='screen',data={'text':'legacy'})
        self.raw.add_event(legacy);self.capture();before=self.only()
        self.db.close();self.db=Database(Path(self.temp.name)/'db.sqlite');self.raw=RawStore(self.db)
        self.assertEqual(self.only(),before);self.assertEqual(self.raw.count_events(),2)
        self.assertEqual(self.raw.prune_older_than(0),{'events':0,'edits':0})
        self.db.execute("UPDATE listener_wechat_messages SET last_seen_at='2000-01-01'")
        self.db.execute("UPDATE listener_wechat_captures SET last_seen_at='2000-01-01'")
        self.db.execute("UPDATE listener_wechat_conversations SET last_seen_at='2000-01-01'")
        self.db.execute("UPDATE listener_wechat_views SET last_seen_at='2000-01-01'")
        self.raw.prune_older_than(1)
        self.assertEqual(self.raw.wechat.list_conversations()['conversations'],[])
        self.assertEqual(self.raw.count_events(),2)

    def test_contact_switch_cuts_segment_and_return_joins_anonymous_conversation(self):
        self.capture(('hello friend','answer message'),avatar=None)
        self.capture(('different chat','different reply'),name='另一个聊天',avatar=None,seconds=5)
        self.capture(('hello friend','answer message'),avatar=None,seconds=10)
        listed=self.raw.wechat.list_conversations()['conversations']
        self.assertEqual(len(listed),2)
        first=next(c for c in listed if c['peer_name']=='小陈')
        detail=self.raw.wechat.conversation(first['id'])
        self.assertEqual(len(detail['segments']),2)
        self.assertNotEqual(detail['segments'][0]['segment_id'],detail['segments'][1]['segment_id'])
        self.assertEqual(len(detail['messages']),2)
        self.assertIsNone(detail['conversation']['peer']['person_id'])
        self.assertEqual(detail['conversation']['identity']['status'],'anonymous')

    def test_same_header_short_context_and_avatar_conflict_stay_separate(self):
        self.capture(('好','嗯'),avatar=None)
        self.capture(('好','嗯'),scope='new',avatar=None,seconds=5)
        self.assertEqual(len(self.raw.wechat.list_conversations()['conversations']),2)
        self.capture(('long context text','another long line'),avatar='0123456789abcdef:4,6,9',scope='three',seconds=10)
        self.capture(('long context text','another long line'),avatar='ffffffffffffffff:14,1,1',scope='four',seconds=15)
        self.assertEqual(len(self.raw.wechat.list_conversations()['conversations']),4)

    def test_ocr_and_unknown_sender_revision_preserve_id_and_evidence(self):
        first=[message('stable before'),{**message('I am feeling tird','unknown'),'ocr_confidence':40},message('stable after')]
        self.capture(messages=first)
        original=self.only()['messages'][1]['message_id']
        corrected=[first[0],{**message('I am feeling tired','unknown'),'ocr_confidence':90},first[2]]
        event,saved=self.capture(messages=corrected,seconds=5)
        self.assertIsNotNone(saved);self.assertEqual(event.data['wechat_messages'],[])
        self.assertEqual(event.data['wechat_message_updates'][0]['message_id'],original)
        resolved=[corrected[0],{**corrected[1],'sender_role':'self','sender_basis':'right_bubble'},corrected[2]]
        self.capture(messages=resolved,seconds=10)
        detail=self.only();self.assertEqual(len(detail['messages']),3)
        current=detail['messages'][1]
        self.assertEqual(current['message_id'],original);self.assertEqual(current['sender_role'],'self')
        self.assertEqual(current['text'],'I am feeling tired');self.assertEqual(current['revision'],3)
        self.assertEqual([r['reason'] for r in current['revisions']],['ocr_corrected','sender_resolved'])
        self.assertEqual(len(current['source_capture_ids']),3)
        self.assertIsNone(self.capture(messages=resolved,seconds=15)[1])

    def test_same_position_without_context_is_new_message_not_correction(self):
        self.capture(messages=[{**message('I am feeling tired'),'ocr_confidence':40}])
        self.capture(messages=[{**message('I am feeling wired'),'ocr_confidence':95}],seconds=5)
        self.assertEqual(len(self.only()['messages']),2)
        self.assertEqual(self.db.query('SELECT * FROM listener_wechat_message_revisions'),[])

    def test_old_frame_revisit_restores_alignment_and_keeps_repeat_occurrences(self):
        self.capture(('A','B'))
        self.capture(('A','B','B'),seconds=5)
        self.capture(('A','B'),seconds=10)
        self.capture(('A','B','B','C'),seconds=15)
        self.assertEqual([m['text'] for m in self.only()['messages']],['A','B','B','C'])

    def test_failed_reading_ends_segment_without_creating_a_conversation(self):
        self.capture(('long first message','long second message'))
        first=self.only()['segments'][0]['segment_id']
        self.capture((),name=None,avatar=None,layout_status='unrecognized',seconds=5)
        self.capture(('long first message','long second message'),seconds=10)
        data=self.only();self.assertEqual(len(data['segments']),2)
        self.assertNotEqual(data['segments'][0]['segment_id'],first)
        self.assertEqual(len(data['messages']),2)
        self.assertIsNone(next(o for o in self.raw.wechat.observations() if o['layout_status']=='unrecognized')['segment_id'])

    def test_ambiguous_same_header_and_context_does_not_choose_an_arbitrary_person(self):
        texts=('long shared message','second shared message')
        self.capture(texts,scope='one')
        self.capture(texts,scope='two',avatar='ffffffffffffffff:14,1,1',seconds=5)
        self.capture(texts,scope='three',avatar=None,seconds=10)
        self.assertEqual(len(self.raw.wechat.list_conversations()['conversations']),3)

    def test_transaction_failure_leaves_no_observation_or_revision(self):
        first=[message('stable before'),{**message('feeling very tird'),'ocr_confidence':40}]
        self.capture(messages=first)
        with patch.object(self.raw,'_insert_event',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.capture(messages=[first[0],{**message('feeling very tired'),'ocr_confidence':95}],seconds=5)
        self.assertEqual(len(self.raw.wechat.observations()),1)
        self.assertEqual(self.only()['messages'][1]['revision'],1)


    def test_cross_segment_exact_frame_reuses_its_position_ids_despite_history_order(self):
        self.capture(('long first context','long second context'))
        self.capture(('好','long first context','long second context'),seconds=5)
        before=self.only();ids=[m['message_id'] for m in before['messages']]
        event,saved=self.capture(('好','long first context','long second context'),scope='back-again',seconds=10)
        self.assertIsNone(saved)
        after=self.only();self.assertEqual([m['message_id'] for m in after['messages']],ids)
        self.assertEqual(len(after['segments']),2)
        self.assertEqual(self.db.query('SELECT capture_id,position FROM listener_wechat_message_captures GROUP BY capture_id,position HAVING COUNT(*)>1'),[])

    def test_same_bounded_short_frame_can_reconnect_without_identifying_contact(self):
        mm=[{**message('好'),'bbox':[.6,.2,.2,.05]},{**message('嗯','other'),'bbox':[.4,.3,.2,.05]}]
        self.capture(messages=mm,avatar=None)
        before=self.only()
        self.capture(messages=mm,avatar=None,scope='new-visit',seconds=5)
        after=self.only();self.assertEqual(len(after['messages']),2)
        self.assertEqual(before['conversation']['conversation_id'],after['conversation']['conversation_id'])
        self.assertEqual(len(after['segments']),2)

    def test_script_variants_match_without_replacing_original_message_text(self):
        from personalized_agent.data_manage.storage import wechat_store
        if wechat_store._converter is None:
            self.skipTest("OpenCC is no dependency: only where it happens to be installed")
        first=[{**message('今天這個工作讓我很難過'),'bbox':[.6,.2,.2,.05]},
               {**message('我們明天再討論這件事','other'),'bbox':[.4,.3,.2,.05]}]
        self.capture(messages=first,name='測試聊天',avatar=None)
        before=self.only()
        newer=[{**first[0],'text':'今天这个工作让我很难过'},{**first[1],'text':'我们明天再讨论这件事'}]
        self.capture(messages=newer,name='测试聊天',avatar=None,scope='return',seconds=5)
        after=self.only()
        self.assertEqual([m['message_id'] for m in before['messages']],[m['message_id'] for m in after['messages']])
        self.assertEqual(after['messages'][0]['text'],first[0]['text'])

    def _inject_legacy_link(self, duplicate_text=None, second_position=False):
        self.capture(('好','好') if second_position else ('hello first context','hello second context'))
        data=self.only();first=data['messages'][0]['message_id'];cap=data['captures'][0]['capture_id']
        if second_position:
            self.capture(('好',),seconds=2)
            cap=self.db.query_one('SELECT id FROM listener_wechat_captures ORDER BY rowid DESC LIMIT 1')['id']
        self.db.execute('DROP INDEX idx_wechat_capture_position')
        self.db.execute("DELETE FROM kv WHERE key='listener_wechat_link_repair_v1'")
        if second_position:
            duplicate=data['messages'][1]['message_id']
        else:
            duplicate='msg_historical_duplicate'
            self.db.execute('INSERT INTO listener_wechat_messages(id,conversation_id,sender_role,text,content_status,message_time,first_seen_at,last_seen_at,payload) SELECT ?,conversation_id,sender_role,COALESCE(?,text),content_status,message_time,first_seen_at,last_seen_at,payload FROM listener_wechat_messages WHERE id=?',(duplicate,duplicate_text,first))
        self.db.execute('INSERT INTO listener_wechat_message_captures(message_id,capture_id,position) VALUES(?,?,0)',(duplicate,cap))
        return first,duplicate,cap

    def _reopen(self):
        path=self.db.path;self.db.close();self.db=Database(path);self.raw=RawStore(self.db)

    def test_historical_duplicate_is_aliased_with_original_rows_and_raw_history_kept(self):
        first,duplicate,cap=self._inject_legacy_link()
        physical=self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_messages')['n']
        raw_before=self.db.query('SELECT * FROM raw_events')
        self._reopen()
        self.assertEqual(self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_messages')['n'],physical)
        self.assertEqual(self.db.query('SELECT * FROM raw_events'),raw_before)
        alias=self.db.query_one('SELECT * FROM listener_wechat_message_aliases WHERE id=?',(duplicate,))
        self.assertEqual(alias['canonical_id'],first)
        data=self.only();self.assertEqual(len(data['messages']),2)
        self.assertEqual(data['message_aliases'][0]['old_message_id'],duplicate)
        self.assertEqual(self.db.query('PRAGMA foreign_key_check'),[])
        with self.assertRaises(__import__('sqlite3').IntegrityError):
            self.db.execute('INSERT INTO listener_wechat_message_captures(message_id,capture_id,position) VALUES(?,?,0)',(duplicate,cap))
        archived=self.db.query('SELECT * FROM listener_wechat_link_archive');self._reopen()
        self.assertEqual(self.db.query('SELECT * FROM listener_wechat_link_archive'),archived)

    def test_unequal_historical_content_is_archived_as_uncertain_not_merged(self):
        first,duplicate,cap=self._inject_legacy_link(duplicate_text='a different old statement')
        self._reopen()
        self.assertEqual(self.db.query('SELECT * FROM listener_wechat_message_aliases'),[])
        data=self.only();self.assertEqual(len(data['messages']),3)
        self.assertEqual(len(data['association_conflicts']),1)
        self.assertEqual(data['association_conflicts'][0]['message_id'],duplicate)

    def test_two_repeated_bubbles_seen_together_are_never_aliased(self):
        first,second,cap=self._inject_legacy_link(second_position=True)
        self._reopen()
        self.assertEqual(self.db.query('SELECT * FROM listener_wechat_message_aliases'),[])
        self.assertEqual(len(self.only()['messages']),2)
        self.assertEqual(self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_message_captures WHERE capture_id=?',(cap,))['n'],1)

    def test_promoting_conversations_reconciles_identical_capture_positions(self):
        self.capture(('好','嗯'),avatar=None)
        self.capture(('好','嗯'),avatar=None,scope='another-visit',seconds=5)
        conversations=self.db.query('SELECT id FROM listener_wechat_conversations ORDER BY rowid')
        self.assertEqual(len(conversations),2)
        with self.db.write() as conn:
            self.raw.wechat._merge(conn,conversations[1]['id'],conversations[0]['id'])
        data=self.only()
        self.assertEqual(len(data['messages']),2)
        self.assertEqual(len(data['captures']),1)
        self.assertEqual(len(data['message_aliases']),2)
        self.assertEqual(self.db.query_one('SELECT COUNT(*) AS n FROM listener_wechat_messages')['n'],4)
        self.assertEqual(self.db.query('PRAGMA foreign_key_check'),[])
        canonical={m['message_id'] for m in data['messages']}
        for segment in data['segments']:
            self.assertEqual(set(segment['visible_message_ids']),canonical)



class RoutingTests(unittest.TestCase):
    def setUp(self):
        from support import TemporaryAgent
        from personalized_agent.listener.providers.screen import ScreenProvider
        self.fixture=TemporaryAgent();self.app=self.fixture.app
        self.app.config.set('listener_enabled',True);self.app.config.set('listener_screen_enabled',True)
        self.provider=ScreenProvider(self.app.config,self.app.platform)
        self.app.listener.register(self.provider)
    def tearDown(self):self.fixture.cleanup()
    def emit(self,text='visible',**overrides):
        capture={'peer_name':'小陈','scope_id':'visit','conversation_type':'direct','layout_status':'recognized',
                 'messages':[message(text)],'unresolved_text':'',**overrides}
        event=ListenerEvent(source='screen',event_type='screen.text',application='WeChat',data={'text':text,'wechat_capture':capture})
        self.provider.emit(event)
    def test_the_denylist_writes_neither_normalized_nor_raw(self):
        self.app.config.set('listener_application_denylist','WeChat');self.emit()
        self.assertEqual(self.app.raw_store.count_events(),0)
        self.assertEqual(self.app.raw_store.wechat.list_conversations()['conversations'],[])
    def test_secrets_filtered_before_every_storage_and_duplicates_not_published(self):
        self.emit('password: example-private-password');self.emit('password: example-private-password')
        rows=self.app.db.query('SELECT payload FROM listener_wechat_messages')
        self.assertEqual(len(rows),1);self.assertNotIn('example-private-password',str(rows))
        self.assertEqual(self.app.raw_store.count_events(),1)
        self.assertEqual(self.app.listener.health()['providers'][0]['routing']['unchanged'],1)
    def test_untrusted_source_string_cannot_create_structured_evidence(self):
        event=ListenerEvent(source='screen',event_type='screen.text',application='WeChat',data={'text':'spoof','wechat_capture':{'messages':[message('spoof')]}})
        self.app.listener._on_event(event,owner=None)
        self.assertEqual(self.app.raw_store.wechat.list_conversations()['conversations'],[])
        self.assertNotIn('wechat_capture',self.app.raw_store.recent_events()[0].data)
    def test_export_routes_are_read_only_and_do_not_collect(self):
        from personalized_agent.api.routes import build_routes
        from personalized_agent.api.server import Request
        from personalized_agent.api.errors import ApiError
        self.emit('好')
        routes=build_routes(self.app);path='/api/debugger/listener/wechat/conversations'
        listed=routes[('GET',path)](Request('GET',path,{}, {}, {}))
        ident=listed['conversations'][0]['id'];path='/api/debugger/listener/wechat/conversation'
        before=self.app.raw_store.count_events()
        data=routes[('GET',path)](Request('GET',path,{'id':[ident]}, {}, {}))
        self.assertEqual(data['messages'][0]['text'],'好');self.assertEqual(data['schema_version'],'2.0')
        self.assertEqual(before,self.app.raw_store.count_events())
        with self.assertRaises(ApiError):routes[('GET',path)](Request('GET',path,{'id':["' OR 1=1 --"]}, {}, {}))
        self.assertNotIn(('POST',path),routes)


class NativeMetadataTests(unittest.TestCase):
    def test_cached_frame_has_fresh_observation_time_and_preserves_geometry(self):
        from unittest.mock import Mock
        from personalized_agent.platform_layer.macos_window_text import NativeReader,FrontWindow
        png,ocr=frame();native=NativeReader.__new__(NativeReader);native.q=Mock();native._encode_png=Mock(return_value=png)
        native._ocr_engine=Mock();native._ocr_engine.read_layout.return_value=ocr;native._ocr_engine.method='macos_tesseract_ocr'
        native.q.CGDataProviderCopyData.return_value=b'framepixels'
        window=FrontWindow('WeChat','Weixin',1,2,(0,0,1000,700));native.front=Mock(return_value=window)
        native.ocr(window);first=copy.deepcopy(native.ocr_wechat_capture)
        native.ocr_wechat_capture['peer_name']='mutated downstream'
        native.ocr(window);second=native.ocr_wechat_capture
        self.assertEqual(second['peer_name'],'小陈');self.assertEqual(len(second['messages']),4)
        self.assertNotEqual(first['captured_at'],second['captured_at']);self.assertEqual(native._ocr_engine.read_layout.call_count,1)
    def test_failed_ocr_does_not_reuse_previous_conversation_metadata(self):
        from test_macos_window_text import Backend
        from personalized_agent.platform_layer.macos_window_text import MacWindowTextReader
        backend=Backend();backend.supports_wechat_layout=True
        backend.ocr_wechat_capture={'peer_name':'old contact','messages':[message('old private text')]}
        backend.ocr.side_effect=RuntimeError('Local OCR failure')
        backend.accessible_text.return_value=('Only a safe partial accessible fragment remains.',3,False,True)
        reading=MacWindowTextReader(backend).read(lambda front:True)
        self.assertEqual(reading.wechat_capture['layout_status'],'unrecognized')
        self.assertNotIn('old',str(reading.wechat_capture))


class CaptureBudgetTests(unittest.TestCase):
    setUp = RoutingTests.setUp
    tearDown = RoutingTests.tearDown
    def test_configured_capture_budget_is_kept_and_truncation_is_explicit(self):
        from types import SimpleNamespace
        from personalized_agent.platform_layer.window_text import WindowText
        self.app.config.set('listener_screen_max_chars',200)
        reading=WindowText(application='WeChat',title='Weixin',text='nonempty',pid=1,handle=2,how='macos_tesseract_ocr',
            wechat_capture={'peer_name':'小陈','conversation_type':'direct','layout_status':'recognized',
                            'messages':[message('A'*150),message('B'*150)],'unresolved_text':'timestamp'})
        self.provider.platform=SimpleNamespace(name='macos',activity=SimpleNamespace(text_in_front_allowed=lambda allowed:reading))
        self.provider._look()
        listed=self.app.raw_store.wechat.list_conversations()['conversations']
        data=self.app.raw_store.wechat.conversation(listed[0]['id'])
        self.assertEqual(sum(len(m['text']) for m in data['messages']),200)
        self.assertTrue(data['messages'][0]['text_truncated'])
        self.assertTrue(data['captures'][0]['content_truncated'])

class RealShapedLayoutTests(unittest.TestCase):
    """Sanitized geometry reproduces the actual macOS 2026 window, not its text."""
    def sample(self,scale=1,dark=False):
        w,h=1396,1340
        bg=(35,35,35) if dark else (250,250,250)
        image=Image.new('RGB',(w,h),bg);draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,531,h),fill=(55,55,55) if dark else (230,230,232))
        draw.line((532,102,w-10,102),fill=(65,65,65) if dark else (242,242,242),width=2)
        draw.rounded_rectangle((548,1004,1370,1316),radius=20,outline=(85,85,85) if dark else (220,220,220),width=2)
        boxes=[(862,220,1244,294),(672,334,804,408),(672,448,916,522),(672,562,832,636),(672,676,1210,750)]
        words=[{'text':'示例聊天','bbox':[564,42,110,26],'line':(1,1,1),'confidence':90},
               {'text':'toolbar','bbox':[1140,39,180,30],'line':(1,1,1),'confidence':80},
               {'text':'draft','bbox':[575,1040,100,28],'line':(8,1,1),'confidence':90}]
        for i,(a,b,c,d) in enumerate(boxes):
            draw.rounded_rectangle((a,b,c,d),radius=12,fill=(177,241,167) if i==0 else (65,65,65) if dark else (238,238,240))
            words.append({'text':'sample '+str(i),'bbox':[a+24,b+24,75,26],'line':(i+2,1,1),'confidence':90})
        # Rounded window borders contain composited desktop pixels.
        draw.rectangle((w-8,0,w,h),fill=(235,235,235))
        if scale!=1:
            image=image.resize((round(w*scale),round(h*scale)))
            for word in words:word['bbox']=[round(v*scale) for v in word['bbox']]
        output=io.BytesIO();image.save(output,format='PNG')
        return output.getvalue(),{'words':words,'text':'synthetic only'}
    def test_shallow_grey_bubbles_rounded_composer_and_no_avatar_requirement(self):
        for scale,dark in ((1,False),(.7,False),(1,True)):
            with self.subTest(scale=scale,dark=dark):
                png,ocr=self.sample(scale,dark);parsed=parse_wechat_frame(png,ocr)
                self.assertEqual(parsed['layout_status'],'recognized',parsed['layout_reason'])
                self.assertEqual(parsed['peer_name'],'示例聊天')
                self.assertEqual([m['sender_role'] for m in parsed['messages']],['self','other','other','other','other'])
                self.assertNotIn('draft',str(parsed));self.assertNotIn('toolbar',str(parsed))
    def test_header_and_bubble_words_merged_by_ocr_are_separated_by_geometry(self):
        png,ocr=self.sample()
        for w in ocr['words']:w['line']=(1,1,1)
        parsed=parse_wechat_frame(png,ocr)
        self.assertEqual(len(parsed['messages']),5)
        self.assertEqual([m['text'] for m in parsed['messages']],['sample '+str(i) for i in range(5)])
    def test_unknown_page_does_not_retain_draft_or_manufacture_chat(self):
        png,ocr=self.sample();image=Image.open(io.BytesIO(png));ImageDraw.Draw(image).rectangle((533,980,1395,1339),fill=(250,250,250))
        out=io.BytesIO();image.save(out,format='PNG');parsed=parse_wechat_frame(out.getvalue(),ocr)
        self.assertEqual(parsed['messages'],[]);self.assertEqual(parsed['unresolved_text'],'')
