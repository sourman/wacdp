#!/usr/bin/env python3
"""Archive / unarchive a WhatsApp Web chat via box-chrome CDP, using the search bar.
Never opens the chat (so it isn't marked read).

  python3 archive_chat.py --contact '+20 12 33353105'              # archive
  python3 archive_chat.py --contact '+20 12 33353105' --unarchive
  python3 archive_chat.py --contact '+20 12 33353105' --check      # archived or not?

Flow: search box -> type query -> exact title/phone row -> right-click -> the menu
shows "Archive chat" (currently in main list) or "Unarchive chat" (currently archived)
-> click wanted item -> re-search + right-click to verify the flipped label.
Groups refused unless --allow-group.
"""
from __future__ import annotations
import argparse, asyncio, json, sys
import websockets
from wa_cdp import call, evaluate, mouse, key, clear_search, digits_only, find_search_input, wa_ws_url

ARCHIVE = ("Archive chat", "أرشفة الدردشة", "أرشفة المحادثة")
UNARCHIVE = ("Unarchive chat", "إلغاء أرشفة الدردشة", "إلغاء أرشفة المحادثة")
M = [100]


def nid(n=4):
    M[0] += n
    return M[0]


def out(ok, **kw):
    print(json.dumps({"ok": ok, **kw}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


async def ev(w, expr):
    return await evaluate(w, nid(1), expr)


async def esc(w, n=1):
    for _ in range(n):
        await key(w, nid(2), "Escape", "Escape", 27)
        await asyncio.sleep(0.15)


async def search_row(w, query):
    _, s = await find_search_input(w, nid(1))
    if not s.get("found"):
        raise RuntimeError(f"no search input: {s}")
    await mouse(w, nid(), s["x"], s["y"])
    await asyncio.sleep(0.2)
    await ev(w, """(()=>{const el=document.activeElement;if(!el)return 0;
      if(el.tagName==='INPUT'){el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));}
      else{document.execCommand('selectAll');document.execCommand('delete');}return 1})()""")
    await call(w, nid(1), "Input.insertText", {"text": query})
    dig = digits_only(query)
    last = None
    for _ in range(40):  # results can take several seconds
        await asyncio.sleep(0.4)
        last = await ev(w, f"""(()=>{{const want={json.dumps(query)},wd={json.dumps(dig)};
          const rows=[];for(const el of document.querySelectorAll('#pane-side span[title], #side span[title]')){{
            const t=el.getAttribute('title')||'';if(!t||t.length>90)continue;
            const d=t.replace(/\\D/g,'');const hit=t===want||(wd.length>=8&&d===wd);
            const row=el.closest('[role=listitem],[role=row],[data-testid="cell-frame-container"]')||el;
            const r=row.getBoundingClientRect();if(r.height<10||r.y<60||r.y>innerHeight)continue;
            rows.push({{t,hit,x:r.x+r.width/2,y:r.y+r.height/2,
              group:!!row.querySelector('[data-icon="default-group"]')}});}}
          return {{hit:rows.find(r=>r.hit)||null,n:rows.length,titles:rows.map(r=>r.t).slice(0,8)}}}})()""")
        if last and last.get("hit"):
            return last["hit"]
    raise RuntimeError(f"search found no exact match for {query!r}: {last}")


async def menu_items(w):
    for _ in range(12):
        await asyncio.sleep(0.25)
        items = await ev(w, """(()=>[...document.querySelectorAll('[role=menuitem],[role=application] li,[role=menu] li')]
          .map(e=>{const r=e.getBoundingClientRect();return {t:((e.innerText||'').trim()||(e.getAttribute('aria-label')||'').trim()),x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,h:r.height}})
          .filter(i=>i.w>8&&i.h>8&&i.t))()""")
        if items:
            return items
    return []


async def state_menu(w, query):
    """Search, right-click row. Returns (row, items, state) with menu left OPEN."""
    row = await search_row(w, query)
    await mouse(w, nid(), row["x"], row["y"], button="right")
    items = await menu_items(w)
    labels = [i["t"] for i in items]
    if any(l in UNARCHIVE for l in labels):
        st = "archived"
    elif any(l in ARCHIVE for l in labels):
        st = "main"
    else:
        st = None
    return row, items, st


async def reset(w):
    await esc(w, 2)
    await clear_search(w, nid(1))


async def main(a):
    async with websockets.connect(wa_ws_url(), max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        try:
            await reset(w)
            row, items, st = await state_menu(w, a.contact)
            if st is None:
                raise RuntimeError(f"menu had no archive item: {[i['t'] for i in items]}")
            if a.check:
                await reset(w)
                out(True, contact=a.contact, title=row["t"], state=st)
            want = "main" if a.unarchive else "archived"
            if st == want:
                await reset(w)
                out(True, contact=a.contact, title=row["t"], state=st, note="already")
            if row.get("group") and not a.allow_group:
                raise RuntimeError("refusing group chat without --allow-group")
            labels = UNARCHIVE if a.unarchive else ARCHIVE
            it = next(i for i in items if i["t"] in labels)
            await mouse(w, nid(), it["x"], it["y"])
            await asyncio.sleep(1.2)
            await reset(w)
            await asyncio.sleep(0.5)
            _, _, st2 = await state_menu(w, a.contact)
            await reset(w)
            if st2 != want:
                raise RuntimeError(f"verify failed: state={st2}, wanted {want}")
            out(True, contact=a.contact, title=row["t"],
                action="unarchive" if a.unarchive else "archive", state=st2)
        except SystemExit:
            raise
        except Exception as e:
            try:
                await reset(w)
            except Exception:
                pass
            out(False, error=str(e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True)
    ap.add_argument("--unarchive", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--allow-group", action="store_true")
    asyncio.run(main(ap.parse_args()))
