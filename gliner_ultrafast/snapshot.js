(() => {
  if (!document.body) return null;
  const cache = window.__glinerFast ||= {ids:new WeakMap(), nodes:new Map(), next:1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e,cache.next++);
    const id=cache.ids.get(e); cache.nodes.set(id,e); return id;
  };
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const safe = e => !['password','file','hidden'].includes(e.type);
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const name = (e,seen=new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced=(e.getAttribute('aria-labelledby')||'').split(/\s+/)
      .map(id=>name(document.getElementById(id),seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels||[])].map(l=>name(l,seen)).filter(Boolean).join(' ') ||
      (['button','submit','reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName==='INPUT' ? '' : [...e.childNodes].map(n=>n.nodeType===3 ? n.textContent :
        n.nodeType===1 && n.getAttribute('aria-hidden')!=='true' ? name(n,seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // Which part of the page a control belongs to. A link named "London" under
  // "Explore destinations" is a different offer from a field inside the search form.
  const LANDMARK={FORM:'search form',NAV:'navigation',HEADER:'page header',FOOTER:'page footer',
    ASIDE:'sidebar',DIALOG:'dialog',MAIN:'main content'};
  const STRONG=['search','navigation','dialog','banner','contentinfo','region','main','form','menu','listbox'];
  const heading = p => p.querySelector(':scope>h1,:scope>h2,:scope>h3,:scope>header>h1,:scope>header>h2');
  // A picker, menu or modal the page has opened over itself. Its name varies
  // ("Enter your destination dialog"); its role does not.
  const inDialog = e => !!e.closest('dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"],[role="listbox"],[role="menu"]');
  const section = e => {
    for (let p=e.parentElement; p && p!==document.body; p=p.parentElement) {
      const labelled=(p.getAttribute('aria-labelledby')||'').split(/\s+/)
        .map(id=>document.getElementById(id)?.textContent?.trim()).filter(Boolean).join(' ');
      const named=p.getAttribute('aria-label')||labelled||heading(p)?.textContent?.trim()||'';
      const role=p.getAttribute('role')||LANDMARK[p.tagName]||'';
      if (named) return (named+' '+(STRONG.includes(role)?role:'')).trim().slice(0,70);
      if (STRONG.includes(role) || LANDMARK[p.tagName]) return (role||LANDMARK[p.tagName]).slice(0,70);
    }
    return '';
  };
  // Web components put their controls in shadow roots, where querySelectorAll
  // does not look. On plenty of sites that is where the only search field lives,
  // so the page reads as having no way to type at all.
  const deep = (root, selector, out=[]) => {
    for (const e of root.querySelectorAll(selector)) out.push(e);
    for (const e of root.querySelectorAll('*')) if (e.shadowRoot) deep(e.shadowRoot, selector, out);
    return out;
  };
  const roles=['button','link','checkbox','radio','switch','tab','menuitem','menuitemradio',
    'option','gridcell','combobox','textbox','searchbox','spinbutton'];
  const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
    roles.map(role=>'[role="'+role+'"]').join(',');
  const role = e => {
    const explicit=e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName==='BUTTON' || e.tagName==='SUMMARY') return 'button';
    if (e.tagName==='A') return 'link';
    if (e.tagName==='SELECT') return 'combobox';
    if (e.tagName==='TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName==='INPUT') {
      if (['checkbox','radio'].includes(e.type)) return e.type;
      if (['button','submit','reset','image'].includes(e.type)) return 'button';
      if (e.type==='search') return 'searchbox';
      if (e.type==='number') return 'spinbutton';
      if (['text','email','url','tel'].includes(e.type)) return 'textbox';
    }
    return null;
  };
  cache.pageKey=()=>[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    // Only fields that are actually on screen. Component sites mount hidden
    // inputs as they hydrate, and counting those makes this key change on its
    // own -- which reads as "the page moved under you" and rejects every action.
    [...deep(document,'input,textarea,select')].filter(e=>safe(e)&&visible(e))
      .map(e=>[identity(e),e.value,e.checked,e.selectedIndex,e.disabled,e.readOnly])];
  cache.guard=e=>{
    if (!e?.isConnected || !visible(e)) return null;
    const scope=e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    return [identity(e),role(e),name(e),e.value??null,e.checked??null,e.selectedIndex??null,
      e.readOnly??null,e.matches(':disabled'),e.getAttribute('aria-disabled'),
      e.getAttribute('aria-expanded'),e.getAttribute('aria-checked'),e.getAttribute('aria-selected'),
      e.getAttribute('href'),scope?.innerText?.slice(0,6000)||''];
  };
  // Associate autocomplete popups using the page's ARIA relationships, not
  // site-specific markup. A grid popup and a listbox are both valid patterns.
  const popups=[];
  for (const input of deep(document,'input,textarea,[contenteditable="true"]')) {
    if (!safe(input) || !visible(input)) continue;
    const combo=input.closest('[role="combobox"]');
    const ids=[input,combo].filter(Boolean).flatMap(e=>
      ((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).trim().split(/\s+/));
    for (const id of new Set(ids)) {
      const popup=document.getElementById(id);
      if (popup && visible(popup)) popups.push({popup,node:identity(input)});
    }
  }
  const actions=[]; let fields=0, links=0;
  for (const e of deep(document, selector)) {
    if (!safe(e) || !visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, rname=role(e);
    if (!rname || r.width<=0 || r.height<=0) continue;
    // Controls the page has rendered but scrolled past are kept, not dropped: a
    // search field below the fold is the whole task on plenty of sites. They are
    // scrolled into view before input, and only named ones are worth offering.
    const off = x<0 || y<0 || x>=innerWidth || y>=innerHeight;
    // A field below the fold is often the whole task and is kept. Links below
    // the fold are not: measured on the task suite they only add distractors
    // that outrank the control actually on screen.
    const typable = ['textbox','searchbox','combobox','spinbutton'].includes(rname);
    if (off && (!typable || fields>=8)) continue;
    if (rname==='gridcell' && e.querySelector('button,[role="button"]')) continue;
    // The inner input owns typing; the outer combobox is not a second field.
    if (rname==='combobox' && e.querySelector('input,textarea,[contenteditable="true"]')) continue;
    if (off) { if (['textbox','searchbox','combobox','spinbutton'].includes(rname)) fields++; else links++; }
    const form=e.form||e.closest('form,[role="search"]');
    const base={node:identity(e),role:rname,label:name(e)||rname,section:section(e),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    base.document_id=performance.timeOrigin;
    const source=popups.find(({popup})=>popup.contains(e));
    if (source && ['option','gridcell','menuitem'].includes(rname)) base.suggestion_for=source.node;
    if (form) base.form=identity(form);
    if (off) base.offscreen=true;
    if (inDialog(e)) base.dialog=true;
    // A link back to the page you are already on advances nothing. Site headers
    // are full of them, and they read exactly like the task that brought you here.
    const here=location.href.replace(/#.*$/,'');
    const current=(e.getAttribute('aria-current')||e.closest('[aria-current]')?.getAttribute('aria-current')||'')
      .toLowerCase();
    if ((e.tagName==='A' && e.href && e.href.replace(/#$/,'')===here) ||
        ['true','page','location','step'].includes(current)) base.self_link=true;
    // A populated form has to be sent before its values mean anything.
    // e.form is only set inside a real <form>, where a bare <button> submits.
    if (e.form && (e.type==='submit' || (e.tagName==='BUTTON' && !['button','reset'].includes(e.type))))
      base.submit=true;
    for (const key of ['checked','selected','expanded']) {
      const value=e.getAttribute('aria-'+key);
      if (value!==null) base[key]=value;
    }
    if (['checkbox','radio'].includes(e.type)) base.checked=String(e.checked);
    if (e.tagName==='SELECT') {
      for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
        actions.push({...base,kind:'select',value:o.value,
          current_value:[...e.selectedOptions].map(o=>o.label).join(', '),label:base.label+' → '+o.label});
    } else {
      const editable=!e.readOnly && e.getAttribute('aria-readonly')!=='true' &&
        (['textbox','searchbox','spinbutton'].includes(rname) ||
          (rname==='combobox' && ['INPUT','TEXTAREA'].includes(e.tagName)));
      const value='value' in e ? String(e.value) :
        e.isContentEditable || rname==='combobox' ? e.innerText.trim() : '';
      actions.push({...base,kind:editable?'fill':'click',value});
      if (editable) actions.push({...base,kind:'click',value,label:'Open '+base.label});
    }
  }
  const words=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  const range=document.createRange(); let node,length=0;
  while ((node=walker.nextNode()) && length<6000) {
    const value=node.textContent.trim(), parent=node.parentElement;
    if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
    range.selectNodeContents(node); const r=range.getBoundingClientRect();
    if (r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth) {
      words.push(value); length+=value.length;
    }
  }
  const text=words.join('\n').slice(0,6000), height=document.documentElement.scrollHeight;
  const page_key=cache.pageKey(), guards={};
  for (const a of actions) if (!(a.node in guards)) guards[a.node]=cache.guard(cache.nodes.get(a.node));
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics=actions.map(({rect,...action})=>action);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,
    document.title,text,semantics,page_key[6]];
  const omitted_actions=Math.max(0,actions.length-250);
  actions.splice(250);
  actions.forEach((a,i)=>a.id='e'+(i+1));
  if (scrollY+innerHeight<height-2) actions.push({id:'scroll_down',kind:'scroll',label:'Scroll down',delta:560});
  if (scrollY>0) actions.push({id:'scroll_up',kind:'scroll',label:'Scroll up',delta:-560});
  // Plenty of search boxes have no button at all and submit on Enter alone.
  actions.push({id:'press_enter',kind:'key',label:'Press Enter to submit the focused field'});
  actions.push({id:'wait',kind:'wait',label:'Wait for the page to update'});
  return {url:location.href,title:document.title,w:innerWidth,h:innerHeight,text,
    scroll:{y:scrollY,height},actions,marker,page_key,guards,omitted_actions};
})()
