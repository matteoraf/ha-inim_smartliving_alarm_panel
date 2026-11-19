"""Laying a report out as one self-contained HTML page.

A header with the panel and the report, four numbers at a glance, a side
menu of sections, each a list with the selected item's details beside it,
and a reader for the raw blocks with several ways to decode their bytes.

The page carries its own style and a small script, and a policy that lets it
load nothing else. Every value is escaped; the script reads the page's text
and data attributes only and builds elements with textContent, never markup
from report text. With scripts off every section is shown one after the
other, each item's details open on a click, and each block is shown in its
default format.
"""

from html import escape

from . import raw
from .view import Cell, Page, Section, State

POLICY = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"

# Side menu order: section title -> menu name; sections not listed follow
MENU = {
    "Findings": "Findings",
    "Areas": "Areas",
    "Zones": "Zones",
    "Scenarios": "Scenarios",
    "Codes": "Codes",
    "Outputs": "Outputs",
    "Devices": "Devices",
    "Areas now": "Areas now",
    "Status": "Status",
    "Events": "Events",
    "Connection": "Transport",
    "Last exchanges": "Last exchanges",
    "Integration": "Integration",
}
# Zone chips: (tag, label); "All" is added
ZONE_CHIPS = (("alarm", "Alarm"), ("bypassed", "Bypassed"), ("not-ready", "Not ready"))
# Drop-down filters built by the script from the rows' tags: (prefix, label)
SELECT_FILTERS = {"Events": (("area-", "Area"), ("code-", "Code"))}

# Badge colours by state name; anything else is neutral
_BADGES = {
    "b-ok": {"standby", "disarmed", "public", "ok", "no"},
    "b-bad": {"alarm", "tamper", "short", "secret"},
    "b-warn": {"bypassed", "not-ready", "personal", "partial", "instant"},
    "b-info": {"total", "armed", "away", "stay"},
}

_ICON = (
    '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">'
    '<rect x="3" y="3" width="18" height="18" rx="4" fill="none" '
    'stroke="currentColor" stroke-width="1.6"/><rect x="7" y="7" width="10" '
    'height="5" rx="1" fill="currentColor" opacity=".25"/><circle cx="8.5" '
    'cy="16" r="1.2" fill="currentColor"/><circle cx="12" cy="16" r="1.2" '
    'fill="currentColor"/><circle cx="15.5" cy="16" r="1.2" '
    'fill="currentColor"/></svg>'
)

_STYLE = """
:root{--bg:#f4f6fb;--panel:#fff;--text:#1c2030;--muted:#5f6678;--line:#e2e6ef;
--stripe:#f8f9fc;--accent:#3557d4;--head:#eef1f8;--sel:#e6ecff;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--ok:#1d7a46;--ok-bg:#e3f5ea;--warn:#8a5a00;--warn-bg:#fff2d6;--bad:#b00020;
--bad-bg:#fde7ea;--info:#2c4fb8;--info-bg:#e6ecff;--dim:#8b90a0;--dim-bg:#eceef3;
--shadow:0 1px 2px rgba(20,25,40,.06),0 2px 8px rgba(20,25,40,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#101217;--panel:#191c23;--text:#e6e8ee;
--muted:#9aa1b2;--line:#2a2f3a;--stripe:#1d2129;--accent:#8aa2ff;--head:#222733;
--sel:#1f2a4d;--ok:#6fd49a;--ok-bg:#14301f;--warn:#f0c060;--warn-bg:#352a10;
--bad:#ff8095;--bad-bg:#3a1820;--info:#9fb4ff;--info-bg:#1c2547;--dim:#7d8394;
--dim-bg:#252a33;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
.top{display:flex;align-items:center;gap:.8em;padding:.9em 1.3em;background:var(--panel);
border-bottom:1px solid var(--line)}
.top svg{color:var(--accent);flex:none}
.top .t{flex:1;min-width:0}.top h1{font-size:1.15em;margin:0}
.top .sub{color:var(--muted);font-size:.9em;overflow-wrap:anywhere}
.fbadge{flex:none;text-decoration:none;padding:.3em .8em;border-radius:99px;
background:var(--ok-bg);color:var(--ok);font-weight:600;font-size:.9em}
.fbadge.warn{background:var(--warn-bg);color:var(--warn)}
.numbers{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8em;
padding:1em 1.3em 0}
.num{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:.7em 1em;box-shadow:var(--shadow)}
.num .l{color:var(--muted);font-size:.8em;text-transform:uppercase;letter-spacing:.05em}
.num .v{font-size:1.5em;font-weight:650;font-variant-numeric:tabular-nums}
.num.bad .v{color:var(--bad)}.num.warn .v{color:var(--warn)}.num.dim .v{color:var(--dim)}
.lead{color:var(--muted);margin:.6em 1.3em 0}
.app{display:grid;grid-template-columns:230px minmax(0,1fr);gap:1em;padding:1em 1.3em}
.side{position:sticky;top:1em;align-self:start;background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:.6em;box-shadow:var(--shadow);
max-height:calc(100vh - 2em);overflow:auto}
.side input{width:100%;margin-bottom:.5em}
.side a{display:flex;justify-content:space-between;gap:.5em;padding:.35em .6em;
border-radius:7px;color:var(--text);text-decoration:none}
.side a:hover{background:var(--head)}.side a[aria-current=true]{background:var(--sel);
color:var(--info);font-weight:600}
.side .c{color:var(--muted);font-variant-numeric:tabular-nums}
.pane{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:.8em 1em 1em;margin-bottom:1em;box-shadow:var(--shadow);scroll-margin-top:1em}
.pane h2{font-size:1.05em;margin:.1em 0 .5em}
.note{color:var(--muted);margin:.2em 0 .6em}
.split{display:grid;grid-template-columns:minmax(0,3fr) minmax(220px,2fr);gap:1em}
.split.single{grid-template-columns:minmax(0,1fr)}
.detail{border:1px solid var(--line);border-radius:8px;padding:.7em .9em;align-self:start;
position:sticky;top:1em;max-height:80vh;overflow:auto}
.detail h3{margin:0 0 .4em;font-size:1em}
.detail .hint{color:var(--muted);margin:0}
.detail dl,.more dl{display:grid;grid-template-columns:auto 1fr;gap:.2em .8em;margin:.3em 0}
.detail dt,.more dt{color:var(--muted)}.detail dd,.more dd{margin:0;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:.4em;margin:.1em 0 .7em}
.tw{overflow:auto;max-height:72vh;border:1px solid var(--line);border-radius:8px}
table{border-collapse:separate;border-spacing:0;width:100%}
th,td{padding:.35em .7em;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{position:sticky;top:0;background:var(--head);font-weight:600;white-space:nowrap;z-index:1}
tbody tr:nth-child(even) td{background:var(--stripe)}
tbody tr:hover td{background:var(--head)}
tbody tr[aria-selected=true] td{background:var(--sel)}
tr[data-title]{cursor:pointer}
td{overflow-wrap:break-word}
.badge{display:inline-block;padding:.05em .55em;border-radius:99px;font-size:.85em;
white-space:nowrap;background:var(--dim-bg);color:var(--text)}
.b-ok{background:var(--ok-bg);color:var(--ok)}.b-bad{background:var(--bad-bg);color:var(--bad)}
.b-warn{background:var(--warn-bg);color:var(--warn)}.b-info{background:var(--info-bg);color:var(--info)}
.st-masked{background:var(--warn-bg);color:var(--warn)}
.st-withheld{background:var(--bad-bg);color:var(--bad);font-style:italic}
.st-missing{background:transparent;color:var(--dim)}
button,select,input{font:inherit;color:var(--text);background:var(--panel);
border:1px solid var(--line);border-radius:7px;padding:.3em .7em}
button{cursor:pointer}button[aria-pressed=true]{background:var(--info-bg);
border-color:var(--accent);color:var(--info)}
th[data-sort]{cursor:pointer}th[aria-sort=ascending]::after{content:" \\2191"}
th[aria-sort=descending]::after{content:" \\2193"}
.reader{display:grid;grid-template-columns:240px minmax(0,1fr);gap:1em}
.blist{max-height:75vh;overflow:auto;border:1px solid var(--line);border-radius:8px}
.blist a{display:flex;justify-content:space-between;gap:.4em;padding:.3em .6em;
color:var(--text);text-decoration:none;border-bottom:1px solid var(--line);font-size:.92em}
.blist a[aria-current=true]{background:var(--sel);color:var(--info);font-weight:600}
.block{margin-bottom:1.2em}.block h3{margin:0;font:600 1em var(--mono)}
.block .meta{color:var(--muted);font-size:.9em;margin:.1em 0 .5em}
.ctl{flex-wrap:wrap;align-items:center;gap:.6em;margin:.3em 0 .6em}
.ctl output{font-variant-numeric:tabular-nums;min-width:2ch;display:inline-block}
table.bytes td{font:12px/1.5 var(--mono);white-space:pre;border-bottom:none;padding:.05em .7em}
table.bytes tr:nth-child(even) td{background:transparent}
table.bytes td.d{color:var(--muted)}
.bytes .hit{background:var(--warn-bg);color:var(--warn);border-radius:3px}
.bytes .lit{background:var(--info-bg);color:var(--info);border-radius:3px;outline:1px solid var(--accent)}
.bytes [data-i]{cursor:default}
.m{color:var(--dim)}
.js-only,.ctl,.detail{display:none}
html.js .js-only,html.js .ctl{display:flex}html.js .detail{display:block}
html.js .more-cell{display:none}
html.js .pane{display:none}html.js .pane.on{display:block}
html.js .block{display:none}html.js .block.on{display:block}
@media (max-width:600px){.numbers{grid-template-columns:repeat(2,minmax(0,1fr));padding:.7em .7em 0}
.app{grid-template-columns:1fr;padding:.7em}
.side{position:static;max-height:none;display:flex;flex-wrap:wrap;gap:.3em}
.side input{flex:1 0 100%;margin:0 0 .3em}.side a{flex:none}
.split,.reader{grid-template-columns:1fr}.detail{position:static}
.blist{max-height:30vh}.top{flex-wrap:wrap}}
@media print{.side,.ctl,.chips,.js-only,.detail{display:none!important}.app{display:block}
html.js .pane,html.js .block{display:block}.tw,.blist{max-height:none;overflow:visible}}
"""

_SCRIPT = """
(function(){
var doc=document,root=doc.documentElement;root.classList.add('js');
function all(sel,base){return Array.prototype.slice.call((base||doc).querySelectorAll(sel));}
function clear(el){while(el.firstChild){el.removeChild(el.firstChild);}}
/* sections */
function show(key){
  all('.pane').forEach(function(p){p.classList.toggle('on',p.getAttribute('data-pane')===key);});
  all('.side a').forEach(function(a){a.setAttribute('aria-current',String(a.getAttribute('data-pane')===key));});
}
all('.side a, a.fbadge').forEach(function(a){a.addEventListener('click',function(e){
  e.preventDefault();show(a.getAttribute('data-pane'));});});
var first=doc.querySelector('.side a');if(first){show(first.getAttribute('data-pane'));}
/* item details */
function select(tr){
  var pane=tr.closest('.pane'),panel=pane.querySelector('.detail');
  all('tbody tr',pane).forEach(function(r){r.setAttribute('aria-selected',String(r===tr));});
  if(!panel){return;}
  clear(panel);
  var h=doc.createElement('h3');h.textContent=tr.getAttribute('data-title')||'';panel.appendChild(h);
  var dl=tr.querySelector('.more dl');if(dl){panel.appendChild(dl.cloneNode(true));}
  var link=tr.querySelector('a.rawlink');
  if(link){var a=link.cloneNode(true);a.addEventListener('click',openRaw);panel.appendChild(a);}
}
all('tr[data-title]').forEach(function(tr){
  tr.tabIndex=0;
  tr.addEventListener('click',function(){select(tr);});
  tr.addEventListener('keydown',function(e){if(e.key==='Enter'){select(tr);}});
});
/* search, chips and drop-downs */
var q=doc.getElementById('q');
function apply(){
  var term=(q.value||'').trim().toLowerCase();
  all('.pane').forEach(function(p){
    var key=p.getAttribute('data-pane');if(key==='raw-blocks'){return;}
    var tag=p.getAttribute('data-chip')||'';
    var picks=all('select[data-prefix]',p).map(function(s){return s.value;}).filter(Boolean);
    var shown=0;
    all('tbody tr',p).forEach(function(tr){
      var tags=(tr.getAttribute('data-tags')||'').split(' ');
      var ok=(!term||tr.textContent.toLowerCase().indexOf(term)>=0)&&(!tag||tags.indexOf(tag)>=0)&&
        picks.every(function(x){return tags.indexOf(x)>=0;});
      tr.hidden=!ok;if(ok){shown++;}
    });
    var c=doc.querySelector('.side a[data-pane="'+key+'"] .c');
    if(c&&(term||tag||picks.length)){c.textContent=String(shown);}
    else if(c){c.textContent=c.getAttribute('data-total')||'';}
  });
  all('.blist a').forEach(function(a){a.hidden=!!term&&a.textContent.toLowerCase().indexOf(term)<0;});
}
all('.side .c').forEach(function(c){c.setAttribute('data-total',c.textContent);});
q.addEventListener('input',apply);
all('button[data-chip]').forEach(function(b){b.addEventListener('click',function(){
  var p=b.closest('.pane');p.setAttribute('data-chip',b.getAttribute('data-chip'));
  all('button[data-chip]',p).forEach(function(x){x.setAttribute('aria-pressed',String(x===b));});apply();});});
all('select[data-prefix]').forEach(function(sel){
  var prefix=sel.getAttribute('data-prefix'),seen={};
  all('tbody tr',sel.closest('.pane')).forEach(function(tr){
    (tr.getAttribute('data-tags')||'').split(' ').forEach(function(t){if(t.indexOf(prefix)===0){seen[t]=1;}});});
  Object.keys(seen).sort(function(a,b){return Number(a.slice(prefix.length))-Number(b.slice(prefix.length));})
    .forEach(function(t){var o=doc.createElement('option');o.value=t;o.textContent=t.slice(prefix.length);sel.appendChild(o);});
  sel.addEventListener('change',apply);});
/* sorting */
all('th[data-sort]').forEach(function(th){th.addEventListener('click',function(){
  var table=th.closest('table'),body=table.tBodies[0],col=th.cellIndex,up=th.getAttribute('aria-sort')!=='ascending';
  all('th',table).forEach(function(x){x.removeAttribute('aria-sort');});
  th.setAttribute('aria-sort',up?'ascending':'descending');
  all('tr',body).sort(function(a,b){
    var x=a.cells[col]?a.cells[col].textContent.trim():'',y=b.cells[col]?b.cells[col].textContent.trim():'';
    var r=x.localeCompare(y,undefined,{numeric:true});return up?r:-r;})
    .forEach(function(r){body.appendChild(r);});});});
/* raw block reader */
var MASK='\\u00b7';
function bytesOf(block){
  var hex=block.getAttribute('data-hex')||'',out=[];
  for(var i=0;i<hex.length;i+=2){var b=hex.substr(i,2);out.push(b==='xx'?null:parseInt(b,16));}
  return out;}
function pad(s,n){s=String(s);while(s.length<n){s=' '+s;}return s;}
function hx(b,n){var s=b.toString(16);while(s.length<n){s='0'+s;}return s;}
/* each format: units of [text, byte indexes] with a separator */
function unit(t,ix,sep){return [t,ix,sep];}
var FORMATS={
  text:{sep:'',f:function(r,o){return r.map(function(b,i){return unit(b!==null&&b>=32&&b<127?String.fromCharCode(b):MASK,[o+i]);});}},
  decimal:{sep:' ',f:function(r,o){return r.map(function(b,i){return unit(b===null?pad(MASK,3):pad(b,3),[o+i]);});}},
  bits:{sep:' ',f:function(r,o){var units=r.map(function(b,i){
      if(b===null){return unit(MASK+MASK+MASK+MASK+MASK+MASK+MASK+MASK,[o+i]);}
      var s=b.toString(2);while(s.length<8){s='0'+s;}return unit(s,[o+i]);});
    var set=[];r.forEach(function(b,i){if(b===null){return;}
      for(var bit=0;bit<8;bit++){if(b>>bit&1){set.push(unit(String(i*8+bit+1),[o+i],set.length?', ':' '));}}});
    units.push(unit('set:',[],'  '));
    if(set.length){set.forEach(function(u){units.push(u);});}else{units.push(unit('none',[],' '));}
    return units;}},
  nibbles:{sep:' ',f:function(r,o){return r.map(function(b,i){return unit(b===null?MASK:(b&15)+'/'+(b>>4),[o+i]);});}},
  digits:{sep:'',f:function(r,o){return r.map(function(b,i){return unit(b===null?MASK:b===255?'_':b<10?String(b):'?',[o+i]);});}},
  u16:{sep:'  ',f:function(r,o,big){var w=[];for(var i=0;i+1<r.length;i+=2){var a=r[i],b=r[i+1];
      if(a===null||b===null){w.push(unit(MASK,[o+i,o+i+1]));continue;}
      var v=big?(a<<8|b):(b<<8|a);w.push(unit(v+' (0x'+hx(v,4)+')',[o+i,o+i+1]));}
    if(r.length%2){w.push(unit(MASK,[o+r.length-1]));}return w;}}
};
function span(text,ix){var s=doc.createElement('span');s.textContent=text;
  if(ix.length){s.setAttribute('data-i',ix.join(' '));}return s;}
function render(block,from,length){
  var sel=block.querySelector('select.fmt');if(!sel){return;}
  var range=block.querySelector('input.len'),big=block.querySelector('input.big');
  var fmt=sel.value,len=Number(range.value),data=bytesOf(block),start=Number(block.getAttribute('data-start'));
  var record=Number(block.getAttribute('data-record')||0);
  if(fmt==='fields'&&record){len=record;range.value=String(record);}
  range.disabled=fmt==='fields';block.querySelector('output').textContent=String(len);
  block.querySelector('.bigsw').hidden=fmt!=='u16';
  var fields=all('ol.fields li',block).map(function(li){return li.textContent;});
  var body=block.querySelector('table.bytes tbody');clear(body);
  var hitRow=null,end=from===undefined?-1:from+(length||1);
  for(var off=0,row=0;off<data.length;off+=len,row++){
    var part=data.slice(off,off+len),tr=doc.createElement('tr'),h=doc.createElement('td'),d=doc.createElement('td');
    h.appendChild(doc.createTextNode(hx(start+off,5)+': '));
    part.forEach(function(b,i){if(i){h.appendChild(doc.createTextNode(' '));}
      var s=span(b===null?'xx':hx(b,2),[off+i]);if(b===null){s.className='m';}h.appendChild(s);});
    d.className='d';
    if(fmt==='fields'){var ix=[];for(var k=0;k<part.length;k++){ix.push(off+k);}d.appendChild(span(fields[row]||'',ix));}
    else{FORMATS[fmt].f(part,off,big.checked).forEach(function(u,n){
      if(n){d.appendChild(doc.createTextNode(u[2]!==undefined?u[2]:FORMATS[fmt].sep));}
      d.appendChild(span(u[0],u[1]));});}
    tr.appendChild(h);tr.appendChild(d);body.appendChild(tr);
    if(from!==undefined&&from<off+len&&end>off&&!hitRow){hitRow=tr;}
  }
  all('[data-i]',body).forEach(function(el){
    if(from===undefined){return;}
    var ix=el.getAttribute('data-i').split(' ').map(Number);
    if(ix.some(function(n){return n>=from&&n<end;})){el.classList.add('hit');}});
  if(hitRow){hitRow.scrollIntoView({block:'center'});}
}
/* hovering or tapping a byte lights up its counterparts */
function light(body,el,on){
  var ix=(el.getAttribute('data-i')||'').split(' ');
  all('[data-i]',body).forEach(function(x){
    var other=x.getAttribute('data-i').split(' ');
    if(on&&other.some(function(n){return ix.indexOf(n)>=0;})){x.classList.add('lit');}
    else if(!on){x.classList.remove('lit');}});
}
all('table.bytes tbody').forEach(function(body){
  body.addEventListener('mouseover',function(e){var el=e.target.closest('[data-i]');if(el){light(body,el,false);light(body,el,true);}});
  body.addEventListener('mouseout',function(e){var el=e.target.closest('[data-i]');if(el){light(body,el,false);}});
  body.addEventListener('click',function(e){var el=e.target.closest('[data-i]');if(el){light(body,el,false);light(body,el,true);}});
});
function openBlock(name,from,length){
  var block=doc.getElementById('raw-'+name);if(!block){return;}
  all('.block').forEach(function(b){b.classList.toggle('on',b===block);});
  all('.blist a').forEach(function(a){a.setAttribute('aria-current',String(a.getAttribute('data-block')===name));});
  var range=block.querySelector('input.len'),sel=block.querySelector('select.fmt');
  if(length&&range&&!(sel&&sel.value==='fields')){range.value=String(Math.max(2,Math.min(32,length)));}
  render(block,from,length);
}
all('.block').forEach(function(block){
  all('select.fmt,input.len,input.big',block).forEach(function(c){
    c.addEventListener('input',function(){render(block);});c.addEventListener('change',function(){render(block);});});
});
all('.blist a').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();openBlock(a.getAttribute('data-block'));});});
var firstBlock=doc.querySelector('.blist a:not([data-empty])')||doc.querySelector('.blist a');
if(firstBlock){openBlock(firstBlock.getAttribute('data-block'));}
function openRaw(e){
  e.preventDefault();var a=e.currentTarget;show('raw-blocks');
  openBlock(a.getAttribute('data-block'),Number(a.getAttribute('data-offset')),Number(a.getAttribute('data-length')));
}
all('a.rawlink').forEach(function(a){a.addEventListener('click',openRaw);});
})();
"""


def _badge_class(name: str) -> str:
    for css, names in _BADGES.items():
        if name in names:
            return css
    return ""


def _value(cell: Cell) -> str:
    """A cell's content: text or a badge."""
    text = escape(cell.text)
    if cell.state is not State.SHOWN:
        return f'<span class="badge st-{cell.state}">{text}</span>'
    if cell.badge:
        return f'<span class="badge {_badge_class(cell.badge)}">{text}</span>'
    return text


def _chips(section: Section) -> str:
    if section.title != "Zones" or not section.tags:
        return ""
    counts = {tag: sum(1 for t in section.tags if tag in t) for tag, _ in ZONE_CHIPS}
    chips = [
        f'<button type="button" data-chip="{escape(tag)}" aria-pressed="false">'
        f"{escape(label)} {counts[tag]}</button>"
        for tag, label in ZONE_CHIPS
    ]
    chips.append(
        '<button type="button" data-chip="" aria-pressed="true">'
        f"All {len(section.rows)}</button>"
    )
    return '<div class="chips js-only">' + "".join(chips) + "</div>"


def _selects(section: Section) -> str:
    selects = [
        f'<label>{escape(label)} <select data-prefix="{escape(prefix)}">'
        '<option value="">all</option></select></label>'
        for prefix, label in SELECT_FILTERS.get(section.title, ())
    ]
    return f'<div class="chips js-only">{"".join(selects)}</div>' if selects else ""


def _more(section: Section, index: int) -> str:
    """An item's details: beside the list, or opened on a click without scripts."""
    details = section.details_of(index)
    link = section.link_of(index)
    items = "".join(f"<dt>{escape(k)}</dt><dd>{_value(v)}</dd>" for k, v in details)
    raw_link = ""
    if link:
        block, offset, length = link
        raw_link = (
            f'<a class="rawlink" href="#raw-{escape(block)}" data-block="{escape(block)}" '
            f'data-offset="{offset}" data-length="{length}">Open in raw blocks</a>'
        )
    return (
        '<td class="more-cell"><details class="more"><summary>details</summary>'
        f"<dl>{items}</dl>{raw_link}</details></td>"
    )


def _has_more(section: Section) -> bool:
    return any(section.details) or any(section.links)


def _table(section: Section) -> str:
    more = _has_more(section)
    head = "".join(
        f'<th data-sort scope="col">{escape(n)}</th>' for n in section.columns
    )
    if more:
        head += '<th class="more-cell" scope="col"></th>'
    rows = []
    for index, row in enumerate(section.rows):
        tags = " ".join(sorted(section.tags_of(index)))
        attrs = f' data-tags="{escape(tags)}"' if tags else ""
        if more:
            attrs += f' data-title="{escape(" · ".join(c.text for c in row[:2]))}"'
        cells = "".join(f"<td>{_value(c)}</td>" for c in row)
        rows.append(f"<tr{attrs}>{cells}{_more(section, index) if more else ''}</tr>")
    thead = f"<thead><tr>{head}</tr></thead>" if any(section.columns) or more else ""
    return f'<div class="tw"><table>{thead}<tbody>{"".join(rows)}</tbody></table></div>'


def _pane(section: Section, extra: str = "") -> str:
    key = escape(section.key)
    parts = [
        f'<section class="pane" id="sec-{key}" data-pane="{key}">',
        f"<h2>{escape(section.title)}</h2>",
    ]
    if section.note:
        parts.append(f'<p class="note">{escape(section.note)}</p>')
    parts.append(_chips(section) + _selects(section))
    if section.rows:
        if _has_more(section):
            panel = (
                '<aside class="detail" aria-live="polite">'
                '<p class="hint">Select an item to see its details.</p></aside>'
            )
            parts.append(f'<div class="split">{_table(section)}{panel}</div>')
        else:
            parts.append(f'<div class="split single">{_table(section)}</div>')
    parts.append(extra)
    parts.append("</section>")
    return "".join(parts)


def _block(block: raw.RawBlock) -> str:
    name = escape(block.name)
    parts = [
        f'<div class="block" id="raw-{name}" data-hex="{escape(block.hex_text())}" '
        f'data-start="{block.start}" data-record="{block.record or ""}">',
        f'<h3>{name}</h3><p class="meta">{escape(block.header)}</p>',
    ]
    if block.badge:
        parts.append(
            f'<p><span class="badge st-withheld">{escape(block.badge)}</span></p></div>'
        )
        return "".join(parts)
    options = "".join(
        f'<option value="{f}"{" selected" if f is block.default else ""}>'
        f"{escape(raw.FORMAT_NAMES[f])}</option>"
        for f in block.formats
    )
    parts.append(
        '<div class="ctl">'
        f'<label>Decode as <select class="fmt">{options}</select></label>'
        '<label class="bigsw" hidden><input type="checkbox" class="big"> big-endian'
        "</label>"
        f'<label>Row length <input type="range" class="len" min="{raw.ROW_MIN}" '
        f'max="{raw.ROW_MAX}" value="{block.row_length}"> '
        f"<output>{block.row_length}</output></label></div>"
    )
    rows = []
    for hex_row, decoded in raw.lines(block):
        address, _, cells = hex_row.partition(": ")
        hex_html = " ".join(
            '<span class="m">xx</span>' if c == "xx" else escape(c)
            for c in cells.split()
        )
        rows.append(
            f'<tr><td>{escape(address)}: {hex_html}</td><td class="d">'
            f"{escape(decoded)}</td></tr>"
        )
    parts.append(
        f'<div class="tw"><table class="bytes"><tbody>{"".join(rows)}</tbody></table></div>'
    )
    if block.fields:
        items = "".join(f"<li>{escape(line)}</li>" for line in block.fields)
        parts.append(f'<ol class="fields" hidden>{items}</ol>')
    parts.append("</div>")
    return "".join(parts)


def _raw_pane(blocks: list[raw.RawBlock]) -> str:
    links = []
    for b in blocks:
        badge = (
            f'<span class="badge st-withheld">{escape(b.badge.split(":")[0])}</span>'
            if b.badge
            else ""
        )
        empty = " data-empty" if b.badge else ""
        links.append(
            f'<a href="#raw-{escape(b.name)}" data-block="{escape(b.name)}"{empty}>'
            f"<span>{escape(b.name)}</span>{badge}</a>"
        )
    body = "".join(_block(b) for b in blocks)
    return (
        '<section class="pane" id="sec-raw-blocks" data-pane="raw-blocks">'
        "<h2>Raw blocks</h2>"
        '<p class="note">Masked bytes are shown as xx and never guessed.</p>'
        f'<div class="reader"><nav class="blist" aria-label="Blocks">{"".join(links)}'
        f"</nav><div>{body}</div></div></section>"
    )


def render(page: Page) -> str:
    """The whole page."""
    order = list(MENU)
    sections = sorted(
        (s for s in page.sections if s.title != "Summary"),
        key=lambda s: order.index(s.title) if s.title in order else len(order),
    )
    menu = [
        f'<a href="#sec-{escape(s.key)}" data-pane="{escape(s.key)}">'
        f'{escape(MENU.get(s.title, s.title))}<span class="c">{len(s.rows) or ""}</span></a>'
        for s in sections
    ]
    if page.blocks:
        menu.append(
            '<a href="#sec-raw-blocks" data-pane="raw-blocks">Raw blocks'
            f'<span class="c">{len(page.blocks)}</span></a>'
        )
    numbers = "".join(
        f'<div class="num {escape(tone)}"><div class="l">{escape(label)}</div>'
        f'<div class="v">{escape(value)}</div></div>'
        for label, value, tone in page.headline
    )
    summary = next((s for s in page.sections if s.title == "Summary"), None)
    lead = (
        f'<p class="lead">{escape(summary.note)}</p>'
        if summary and summary.note
        else ""
    )
    warn = " warn" if page.findings else ""
    plural = "" if page.findings == 1 else "s"
    details = ""
    if summary:
        rows = "".join(
            f"<tr><td>{escape(row[0].text)}</td><td>{_value(row[1])}</td></tr>"
            for row in summary.rows
        )
        details = (
            '<h2 style="margin-top:1em">Report details</h2>'
            f'<div class="tw"><table><tbody>{rows}</tbody></table></div>'
        )
    panes = "".join(
        _pane(s, details if s.title == "Findings" else "") for s in sections
    )
    raw_pane = _raw_pane(page.blocks) if page.blocks else ""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{escape(page.title)}</title>\n<style>{_STYLE}</style></head>\n<body>\n"
        f'<header class="top">{_ICON}<div class="t"><h1>{escape(page.title)}</h1>'
        f'<div class="sub">{escape(page.subtitle)}</div></div>'
        f'<a class="fbadge{warn}" href="#sec-findings" data-pane="findings">'
        f"{page.findings} finding{plural}</a></header>\n"
        f'<div class="numbers">{numbers}</div>{lead}\n'
        '<div class="app"><nav class="side" aria-label="Sections">'
        '<input id="q" type="search" placeholder="Search" aria-label="Search" '
        'class="js-only">'
        f"{''.join(menu)}</nav><main>{panes}{raw_pane}</main></div>\n"
        f"<script>{_SCRIPT}</script>\n</body></html>\n"
    )
