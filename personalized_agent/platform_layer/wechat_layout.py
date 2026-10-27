"""Conservative local parsing of the standard WeChat desktop chat layout.

Only identified chat bubbles become message candidates. Sidebar text, drafts
and unreadable layouts remain evidence, never attributed messages. Images
are used in memory and never returned or saved here.
"""
from __future__ import annotations

import hashlib
import io
import re
from collections import defaultdict
from statistics import median


def _distance(a, b):
    return max(abs(int(x) - int(y)) for x, y in zip(a, b))


def _middle(colors):
    return tuple(int(median(c[i] for c in colors)) for i in range(3))


def _join(parts):
    text = ' '.join(parts).strip()
    return re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff，。！？、：；])', '', text)


def _lines(words, scale):
    grouped = defaultdict(list)
    for i, word in enumerate(words):
        box = word.get('bbox') or []
        if len(box) != 4:
            continue
        key = tuple(word.get('line') or (i,))
        grouped[key].append(word)
    result = []
    for members in grouped.values():
        members.sort(key=lambda w: w['bbox'][0])
        left = min(w['bbox'][0] for w in members) * scale
        top = min(w['bbox'][1] for w in members) * scale
        right = max(w['bbox'][0] + w['bbox'][2] for w in members) * scale
        bottom = max(w['bbox'][1] + w['bbox'][3] for w in members) * scale
        result.append({'text': _join(w['text'] for w in members),
                       'bbox': [int(left), int(top), max(1, int(right-left)), max(1, int(bottom-top))],
                       'confidence': round(sum(w.get('confidence', 0) for w in members) / len(members), 1)})
    return sorted(result, key=lambda line: (line['bbox'][1], line['bbox'][0]))


def _geometry(image):
    """Boundaries must be present; never infer a chat from its window name."""
    w, h = image.size
    left = _divider(image)
    if left is None or w-left < 160:
        return None
    header = _horizontal(image, left, .025, .2)
    composer = _horizontal(image, left, .50, .94)
    if header is None or composer is None or composer-header < 100:
        return None
    return left, header, composer


def _background(image, region):
    # The window's rounded outer edge is composited with the desktop. Do
    # not use that edge as the chat background (it can differ by >10 RGB).
    from collections import Counter
    left, top, right, bottom = region
    colors = [image.getpixel((x,y)) for x in range(left+8,right-8,max(1,(right-left)//30))
              for y in range(top+8,bottom-8,max(1,(bottom-top)//30))]
    bins = Counter(tuple(c//4 for c in p) for p in colors)
    common = bins.most_common(1)[0][0]
    return _middle([p for p in colors if tuple(c//4 for c in p) == common])


def _bubble_regions(image, region, background):
    """Connected flat fill, independent of OCR glyph/line grouping.

    Avatars and photographs cannot supply a sufficiently uniform fill.
    Numpy is already a runtime dependency; images stay in memory only.
    """
    import numpy as np
    left, top, right, bottom = region
    pixels = np.asarray(image, dtype=np.int16)
    rgb = pixels[top:bottom,left:right]
    neutral = rgb.max(axis=2)-rgb.min(axis=2) <= 8
    green = (rgb[:,:,1] > rgb[:,:,0]+20) & (rgb[:,:,1] > rgb[:,:,2]+20)
    distance = np.max(np.abs(rgb-np.array(background)),axis=2)
    # Dark glyphs must not connect otherwise separate flat regions.
    light = rgb.mean(axis=2) > 145 if sum(background)>360 else rgb.mean(axis=2)>42
    mask = ((neutral & light) | green) & (distance >= 6)
    components, previous = [], []
    for row in range(mask.shape[0]):
        changes=np.diff(np.r_[False,mask[row],False].astype(np.int8))
        runs=[(int(a),int(b)) for a,b in zip(np.where(changes==1)[0],np.where(changes==-1)[0]) if b-a>=12]
        current=[]
        for a,b in runs:
            hits={ident for c,d,ident in previous if a<d and c<b}
            if hits:
                ident=min(hits)
                for old in hits-{ident}:
                    if components[old] is not None:
                        box=components[old]; dest=components[ident]
                        dest[:4]=[min(dest[0],box[0]),min(dest[1],box[1]),max(dest[2],box[2]),max(dest[3],box[3])]
                        dest[4]+=box[4];components[old]=None
                        previous=[(c,d,ident if i==old else i) for c,d,i in previous]
                        current=[(c,d,ident if i==old else i) for c,d,i in current]
                box=components[ident];box[0]=min(box[0],a);box[2]=max(box[2],b);box[3]=row+1;box[4]+=b-a
            else:
                ident=len(components);components.append([a,row,b,row+1,b-a])
            current.append((a,b,ident))
        previous=current
    output=[]
    for box in components:
        if box is None: continue
        a,b,c,d,area=box; bw,bh=c-a,d-b
        if bw<18 or bh<14 or bw>.92*(right-left) or bh>.65*(bottom-top) or area/(bw*bh)<.52:
            continue
        # Robust fill color, not a glyph pixel from the centre.
        colors=rgb[b:d,a:c][mask[b:d,a:c]]
        dominant=np.median(colors,axis=0)
        same=np.max(np.abs(colors-dominant),axis=1)<=12
        if same.mean()<.70: continue
        color=tuple(int(v) for v in np.median(colors[same],axis=0))
        output.append((a+left,b+top,c+left,d+top,color))
    return output


def _pixels(image):
    """The picture as numbers, once a picture: it is not drawn on after."""
    import numpy as np
    got = getattr(image, '_wechat_pixels', None)
    if got is None:
        got = np.asarray(image.convert('RGB'), dtype=np.int16)
        image._wechat_pixels = got
    return got


def _divider(image):
    """Find a persistent list/chat boundary, rather than guessing a column."""
    import numpy as np
    w, h = image.size
    rows = [int(h * (.2 + .42 * i / 19)) for i in range(20)]
    xs = np.arange(max(4, int(w*.13)), min(w-4, int(w*.65)))
    if not xs.size:
        return None
    sampled = _pixels(image)[rows]
    # Each column's contrast two pixels either side, at every sampled row.
    contrasts = np.abs(sampled[:, xs-2] - sampled[:, xs+2]).max(axis=2)
    support = (contrasts >= 3).sum(axis=0) / len(rows)
    middle = np.median(contrasts, axis=0)
    scores = [(float(support[i]) * min(30, float(middle[i])), int(xs[i]))
              for i in np.where(support >= .8)[0]]
    if not scores:
        return None
    best = max(score for score, _ in scores)
    # Neighbouring columns describe the same separator; prefer its centre.
    candidates = [x for score, x in scores if score >= best * .95]
    return int(median(candidates))


def _horizontal(image, left, low, high):
    import numpy as np
    w, h = image.size
    xs = [int(left + (w-left)*(.06 + .88*i/24)) for i in range(25)]
    ys = np.arange(max(3, int(h*low)), min(h-3, int(h*high)))
    if not ys.size:
        return None
    sampled = _pixels(image)[:, xs]
    contrasts = np.abs(sampled[ys-2] - sampled[ys+2]).max(axis=2)
    support = (contrasts >= 3).sum(axis=1) / len(xs)
    middle = np.median(contrasts, axis=1)
    scored = [(float(support[i]) * min(30, float(middle[i])), int(ys[i]))
              for i in np.where(support >= .88)[0]]
    if not scored:
        return None
    peak = max(score for score, _ in scored)
    return min(y for score, y in scored if score >= peak*.95)


def _avatar(image, box):
    from PIL import Image
    x, y, w, h = map(int, box)
    iw, ih = image.size
    if x < 0 or y < 0 or w < 12 or h < 12 or x+w > iw or y+h > ih:
        return None
    crop = image.crop((x, y, x+w, y+h)).resize((9, 8), Image.Resampling.BILINEAR)
    colors = list(crop.getdata())
    if len({tuple(c//24 for c in p) for p in colors}) < 5:
        return None
    grey = list(crop.convert('L').getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (grey[row*9+col] > grey[row*9+col+1])
    avg = tuple(int(sum(c[i] for c in colors)/len(colors))//16 for i in range(3))
    return f'{bits:016x}:' + ','.join(map(str, avg))


def _gutter_avatar(image, box, background):
    """Locate the avatar itself; OCR glyph height is not an avatar size."""
    import numpy as np
    left,top,right,bottom = map(int,box)
    if right-left < 15 or bottom-top < 15:
        return None
    region=_pixels(image)[top:bottom,left:right]
    far=np.abs(region-np.array(background)).max(axis=2) > 24
    columns=np.where(far.sum(axis=0) >= 12)[0]
    if not columns.size:
        return None
    a,c=left+int(columns[0]),left+int(columns[-1])+1
    rows=np.where(far[:,a-left:c-left].sum(axis=1) >= max(6,(c-a)*.4))[0]
    if not rows.size:
        return None
    b,d=top+int(rows[0]),top+int(rows[-1])+1
    if not .7 <= (c-a)/max(1,d-b) <= 1.4:
        return None
    return _avatar(image,[a,b,c-a,d-b])


def prepare_wechat_ocr_cut(png: bytes) -> tuple:
    """`prepare_wechat_ocr`, cut to where its words are - the name and the
    bubbles - so the recognizer reads only that much of the window (the
    sidebar, the box being typed in and the empty space are never read
    anyway): the picture, and where its top left is in the window. A
    window whose layout is not found is given whole."""
    from PIL import Image
    prepared = prepare_wechat_ocr(png)
    if prepared is png:
        return png, (0, 0)
    mono = Image.open(io.BytesIO(prepared))
    inverted = mono.point(lambda value: 255 - value)
    box = inverted.getbbox()
    if box is None:
        return prepared, (0, 0)
    # A margin, so that a recognizer sees each word's edges whole.
    left, top = max(0, box[0]-12), max(0, box[1]-12)
    right, bottom = min(mono.width, box[2]+12), min(mono.height, box[3]+12)
    out = io.BytesIO()
    mono.crop((left, top, right, bottom)).save(out, format='PNG')
    return out.getvalue(), (left, top)


def shift_layout(layout: dict, offset) -> dict:
    """A cut picture's reading, put back where it was in the window."""
    dx, dy = offset
    if not dx and not dy:
        return layout
    words = []
    for word in layout.get('words') or []:
        box = list(word.get('bbox') or [])
        if len(box) == 4:
            box = [box[0]+dx, box[1]+dy, box[2], box[3]]
        words.append({**word, 'bbox': box})
    return {**layout, 'words': words}


def prepare_wechat_ocr(png: bytes) -> bytes:
    """Keep chat/header geometry while removing avatar and composer noise.

    The original image still supplies layout and avatar evidence. This
    separate in-memory monochrome image goes only to the local recognizer.
    """
    from PIL import Image, ImageDraw, ImageOps
    image = Image.open(io.BytesIO(png)).convert('RGB')
    w,h = image.size
    scale = min(1.0,1100/w,950/h)
    small = image.resize((round(w*scale),round(h*scale))) if scale < 1 else image
    geometry = _geometry(small)
    if geometry is None:
        return png
    left,header,composer = geometry
    region=(left+3,header+3,small.width-12,composer-3)
    background=_background(small,region)
    bubbles=_bubble_regions(small,region,background)
    grey = image.convert('L')
    if sum(background) < 360:
        grey = ImageOps.invert(grey)
    source = grey.point(lambda value: 0 if value < 125 else 255)
    # Copy only the header and detected bubbles. Fixed avatar margins cut
    # long bubbles and left pieces of real avatars in the old OCR image.
    mono = Image.new('L',image.size,255)
    header_box=(round((left+5)/scale),round(3/scale),round((small.width-30)/scale),round((header-2)/scale))
    mono.paste(source.crop(header_box),header_box[:2])
    for a,b,c,d,color in bubbles:
        box=tuple(round(v/scale) for v in (a,b,c,d))
        crop=image.crop(box).convert('L')
        green=color[1]>color[0]+20 and color[1]>color[2]+20
        if sum(background)<360 and not green:
            crop=ImageOps.invert(crop)
        crop=crop.point(lambda value:0 if value<125 else 255)
        mono.paste(crop,box[:2])
    out=io.BytesIO();mono.save(out,format='PNG')
    return out.getvalue()


def parse_wechat_frame(png: bytes, ocr: dict) -> dict:
    """Return UI evidence, including uncertainty, without inventing identities."""
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert('RGB')
    original_width, original_height = image.size
    scale = min(1.0, 1100/original_width, 950/original_height)
    if scale < 1:
        image = image.resize((round(original_width*scale), round(original_height*scale)), Image.Resampling.BILINEAR)
    w, h = image.size
    result = {'schema_version': '1.0', 'source': 'wechat_screen_ocr',
              'frame_hash': hashlib.blake2b(png, digest_size=16).hexdigest(),
              'layout_status': 'unrecognized', 'layout_reason': 'chat_boundary_not_found',
              'peer_name': None, 'peer_avatar': None, 'self_avatar': None,
              'conversation_type': 'unknown', 'messages': [],
              'capture_scope':'visible_text_only','completeness_status':'partial',
              'unresolved_text': ''}
    result['schema_version']='2.0'
    result['parser_version']='wechat_local_layout_v2'
    geometry=_geometry(image)
    result['diagnostics']={'ocr_words':len(ocr.get('words') or []),'bubble_candidates':0}
    if geometry is None:
        result['layout_reason']='header_or_input_boundary_not_found'
        # Without the input boundary, OCR text could be an unsent draft.
        result['unresolved_text']=''
        return result
    left,header_end,composer_top=geometry
    header_words=[word for word in (ocr.get('words') or []) if len(word.get('bbox') or [])==4
                  and left+5 < word['bbox'][0]*scale < left+(w-left)*.65
                  and (word['bbox'][1]+word['bbox'][3])*scale < header_end]
    header_lines = [line for line in _lines(header_words,scale) if line['bbox'][0] > left+5
                    and line['bbox'][1] > 3
                    and line['bbox'][1]+line['bbox'][3] < header_end
                    and line['text'].strip().lower() not in ('weixin', 'wechat', '微信', '...', '…')]
    if not header_lines:
        result['layout_reason'] = 'chat_header_not_readable'
        return result
    # In the standard layout the name is the first readable header label.
    name = header_lines[0]['text'].strip()[:200]
    result['peer_name'] = name
    if name.casefold() in ('discover','moments','contacts','search','发现','朋友圈','通讯录','搜索'):
        result.update(layout_status='not_chat',layout_reason='non_chat_page',unresolved_text='')
        return result
    result['conversation_type'] = 'group' if re.search(r'[（(]\s*\d+\s*[)）]\s*$', name) else 'unknown'
    result['layout_status'] = 'recognized'
    result['layout_reason'] = ''
    result['regions'] = {'chat': [left/w, header_end/h, (w-left)/w, (composer_top-header_end)/h],
                         'input_top': composer_top/h}
    region = (left+3, header_end+3, w-3, composer_top-3)
    background = _background(image,region)
    bubbles = _bubble_regions(image,region,background)
    result['diagnostics']['bubble_candidates']=len(bubbles)
    # OCR may put several columns on one line. Restrict each word
    # to its bubble before reconstructing text lines.
    body_words=[word for word in (ocr.get('words') or []) if len(word.get('bbox') or [])==4
                and region[0] <= (word['bbox'][0]+word['bbox'][2]/2)*scale < region[2]
                and region[1] <= (word['bbox'][1]+word['bbox'][3]/2)*scale < region[3]]
    body=_lines(body_words,scale)
    unresolved = []
    for line in body:
        x, y, lw, lh = line['bbox']
        if not any(a-3 <= x+lw/2 <= c+3 and b-3 <= y+lh/2 <= d+3 for a,b,c,d,_ in bubbles):
            unresolved.append(line['text'])
    for a,b,c,d,color in sorted(bubbles, key=lambda item: item[1]):
        words=[word for word in body_words if a-2 <= (word['bbox'][0]+word['bbox'][2]/2)*scale <= c+2
               and b-2 <= (word['bbox'][1]+word['bbox'][3]/2)*scale <= d+2]
        members=_lines(words,scale)
        if not members:
            continue
        extent = min(90,int((w-left)*.14))
        other_avatar = _gutter_avatar(image,[max(left+6,a-extent),b,a-4,min(composer_top-3,b+extent)],background)
        self_avatar = _gutter_avatar(image,[c+4,b,min(w-4,c+extent),min(composer_top-3,b+extent)],background)
        near_left = a-left < .2*(w-left)
        near_right = w-c < .2*(w-left)
        green = color[1] > color[0]+20 and color[1] > color[2]+20
        role, basis = 'unknown', 'unresolved_layout'
        if near_right and (green or self_avatar):
            role, basis = 'self', 'right_bubble'
        elif near_left and not green and (not near_right or other_avatar):
            role, basis = 'other', 'left_anchored_flat_bubble'
        if role == 'other' and other_avatar:
            result['peer_avatar'] = result['peer_avatar'] or other_avatar
        if role == 'self' and self_avatar:
            result['self_avatar'] = result['self_avatar'] or self_avatar
        result['messages'].append({'text': '\n'.join(line['text'] for line in members)[:16000],
                                   'sender_role': role, 'sender_basis': basis,
                                   'attribution_status': 'inferred_from_ui' if role != 'unknown' else 'unresolved',
                                   'content_status': 'visible_chat_bubble', 'message_time': None,
                                   'bbox': [round(a/w, 5), round(b/h, 5), round((c-a)/w, 5), round((d-b)/h, 5)],
                                   'ocr_confidence': round(sum(line['confidence'] for line in members)/len(members), 1)})
    result['unresolved_text'] = '\n'.join(unresolved)[:16000]
    if not result['messages']:
        result['layout_status'] = 'unrecognized'
        result['layout_reason'] = 'no_readable_chat_bubbles'
    result['diagnostics']['assigned_messages']=len(result['messages'])
    result['diagnostics']['unknown_senders']=sum(m['sender_role']=='unknown' for m in result['messages'])
    return result
