#!/usr/bin/env python3
"""Delete an entire WhatsApp Web chat via Gate box-chrome CDP.

Usage:
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 delete_chat.py \\
    --contact '+20 10 92600692'
  WA_CDP_HTTP=http://127.0.0.1:9427 python3 delete_chat.py \\
    --contact '+201092600692' --dry-run

Opens chat → header Menu → Delete chat → confirm Delete.
Exact title/phone match by default. Groups refused unless --allow-group.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets

from wa_cdp import (
    call,
    clear_search,
    digits_only,
    escape_ui,
    evaluate,
    mouse,
    open_contact,
    phone_match,
    wa_ws_url,
)

DELETE_CHAT_LABELS = (
    "Delete chat",
    "Delete Chat",
    "مسح المحادثة",
    "حذف المحادثة",
    "حذف الدردشة",
)
CONFIRM_DELETE_LABELS = (
    "Delete",
    "مسح",
    "حذف",
)
CANCEL_LABELS = (
    "Cancel",
    "إلغاء",
)


def die(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


async def detect_group(w, mid):
    info = await evaluate(
        w,
        mid,
        """(() => {
          const header = document.querySelector('#main header');
          if (!header) return {group:false, reason:'no-header'};
          const text = (header.innerText || '');
          const hasGroupIcon = !!(
            header.querySelector('[data-icon="default-group"], [data-testid="default-group"]')
            || document.querySelector('#main [data-icon="default-group"]')
          );
          const subtitle = ((document.querySelector('#main header [data-testid="chat-subtitle"]')||{}).innerText||'');
          const participants = /\\d+\\s*(participants|members|مشارك|أعضاء)/i.test(subtitle + ' ' + text);
          const clickHereContact = /click here for contact info|معلومات جهة الاتصال/i.test(subtitle);
          // 1:1 usually says "click here for contact info"; groups show participant count / group icon
          const group = hasGroupIcon || participants || (
            !clickHereContact && /group|مجموعة/i.test(text + ' ' + subtitle)
          );
          return {group, hasGroupIcon, participants, subtitle: subtitle.slice(0,80), header: text.slice(0,120)};
        })()""",
    )
    return mid + 1, info or {"group": False}


async def open_header_menu(w, mid):
    loc = await evaluate(
        w,
        mid,
        """(() => {
          const el = [...document.querySelectorAll('#main header [aria-label], #main header button')]
            .find(e => {
              const a = (e.getAttribute('aria-label') || '').trim();
              return /^(Menu|More options|القائمة|المزيد)$/i.test(a);
            });
          if (!el) {
            return {
              found: false,
              btns: [...document.querySelectorAll('#main header [aria-label], #main header button')]
                .map(e => e.getAttribute('aria-label')).filter(Boolean).slice(0, 20)
            };
          }
          const r = el.getBoundingClientRect();
          return {found: true, aria: el.getAttribute('aria-label'), x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""",
    )
    mid += 1
    if not loc or not loc.get("found"):
        raise RuntimeError(f"header Menu button missing: {loc}")
    await mouse(w, mid, loc["x"], loc["y"])
    mid += 3
    await asyncio.sleep(0.45)
    return mid, loc


async def find_menu_item(w, mid, labels):
    data = await evaluate(
        w,
        mid,
        f"""(() => {{
          const labels = {json.dumps(list(labels))};
          const items = [...document.querySelectorAll('[role="menuitem"]')].map(e => {{
            const t = ((e.getAttribute('aria-label') || '') + ' ' + (e.innerText || '')).trim();
            const r = e.getBoundingClientRect();
            return {{
              t: (e.innerText || '').trim(),
              aria: e.getAttribute('aria-label'),
              blob: t,
              x: r.x + r.width / 2,
              y: r.y + r.height / 2,
              w: r.width,
              h: r.height
            }};
          }}).filter(i => i.w > 8 && i.h > 8);
          let hit = null;
          for (const want of labels) {{
            hit = items.find(i => (i.aria || '').trim() === want || (i.t || '').trim() === want);
            if (hit) break;
          }}
          if (!hit) {{
            for (const want of labels) {{
              hit = items.find(i => (i.blob || '').includes(want));
              if (hit) break;
            }}
          }}
          return {{found: !!hit, hit, items: items.map(i => i.t || i.aria).slice(0, 40)}};
        }})()""",
    )
    return mid + 1, data


async def find_confirm_delete(w, mid):
    """Find the confirm Delete button in the modal (not Cancel, not menu leftovers)."""
    data = await evaluate(
        w,
        mid,
        f"""(() => {{
          const deleteLabels = {json.dumps(list(CONFIRM_DELETE_LABELS))};
          const cancelLabels = {json.dumps(list(CANCEL_LABELS))};
          // Prefer role=dialog / modal popup buttons
          const roots = [
            ...document.querySelectorAll('[role="dialog"], [data-animate-modal-popup], [data-testid*="popup"], [data-testid*="modal"]'),
            document.body
          ];
          const seen = new Set();
          const cands = [];
          for (const root of roots) {{
            if (!root) continue;
            for (const e of root.querySelectorAll('button, [role="button"], div[tabindex]')) {{
              if (seen.has(e)) continue;
              seen.add(e);
              const t = ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')).trim();
              const norm = t.replace(/\\s+/g, ' ');
              const r = e.getBoundingClientRect();
              if (r.width < 20 || r.height < 16 || r.y < 40) continue;
              const isCancel = cancelLabels.some(l => norm === l || norm.startsWith(l));
              const isDelete = deleteLabels.some(l => norm === l);
              // Exact "Delete" / Arabic equivalents — not "Delete chat" (already past that)
              if (!isDelete && !isCancel) continue;
              if (/Delete chat|مسح المحادثة|حذف المحادثة|حذف الدردشة/i.test(norm) && norm !== 'Delete') continue;
              cands.push({{
                t: (e.innerText || '').trim() || (e.getAttribute('aria-label') || '').trim(),
                isDelete, isCancel,
                x: r.x + r.width / 2, y: r.y + r.height / 2,
                w: r.width, h: r.height
              }});
            }}
          }}
          // Also text-node walk for "Delete" in confirm sheet (WA sometimes uses spans)
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let node;
          while ((node = walker.nextNode())) {{
            const v = (node.nodeValue || '').trim();
            if (!deleteLabels.includes(v) && !cancelLabels.includes(v)) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            const r = range.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) continue;
            const isCancel = cancelLabels.includes(v);
            cands.push({{
              t: v, isDelete: !isCancel, isCancel,
              x: r.x + r.width / 2, y: r.y + r.height / 2,
              w: r.width, h: r.height, via: 'text'
            }});
          }}
          const del = cands.find(c => c.isDelete && !c.isCancel);
          const cancel = cands.find(c => c.isCancel);
          const prompt = (() => {{
            const walker2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let n2, hits = [];
            while ((n2 = walker2.nextNode())) {{
              const v = (n2.nodeValue || '').trim();
              if (/Delete chat with|مسح المحادثة|حذف المحادثة|حذف الدردشة/i.test(v)) hits.push(v.slice(0, 120));
            }}
            return hits[0] || null;
          }})();
          return {{found: !!del, del, cancel, prompt, cands: cands.slice(0, 12)}};
        }})()""",
    )
    return mid + 1, data


async def verify_gone(w, mid, contact: str, title: str):
    data = await evaluate(
        w,
        mid,
        f"""(() => {{
          const want = {json.dumps(contact)};
          const title = {json.dumps(title)};
          const phoneDigits = {json.dumps(digits_only(contact) or digits_only(title))};
          const mainOpen = !!document.querySelector('#main footer div[contenteditable="true"]');
          const headerText = ((document.querySelector('#main header')||{{}}).innerText||'');
          const headerDig = headerText.replace(/\\D/g,'');
          const headerStill = !!(
            (title && headerText.includes(title))
            || (want && headerText.includes(want))
            || (phoneDigits.length >= 8 && headerDig.length >= 8 && (
              headerDig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(headerDig.slice(-9))
            ))
          );
          const spans = [...document.querySelectorAll('#pane-side span[title]')];
          const titles = spans.map(s => s.getAttribute('title') || '').filter(Boolean);
          const stillListed = titles.some(t => {{
            if (t === title || t === want) return true;
            const dig = t.replace(/\\D/g,'');
            if (phoneDigits.length >= 8 && dig.length >= 8) {{
              return dig.endsWith(phoneDigits.slice(-9)) || phoneDigits.endsWith(dig.slice(-9))
                || dig.includes(phoneDigits) || phoneDigits.includes(dig);
            }}
            return false;
          }});
          return {{
            mainOpen, headerStill, stillListed,
            header: headerText.slice(0, 100),
            sampleTitles: titles.slice(0, 15)
          }};
        }})()""",
    )
    mid += 1
    gone = bool(data) and not data.get("stillListed") and not (
        data.get("mainOpen") and data.get("headerStill")
    )
    return mid, {"gone": gone, **(data or {})}


async def main(contact: str, allow_substring: bool, allow_group: bool, dry_run: bool):
    try:
        ws_url = wa_ws_url()
    except Exception as e:
        die(str(e))

    async with websockets.connect(ws_url, max_size=None) as w:
        await call(w, 1, "Runtime.enable")
        mid = 10
        title = None
        try:
            mid, title = await open_contact(
                w, mid, contact, allow_substring=allow_substring
            )

            mid, ginfo = await detect_group(w, mid)
            if ginfo.get("group") and not allow_group:
                raise RuntimeError(
                    f"refusing group chat without --allow-group: {ginfo}"
                )

            mid, menu = await open_header_menu(w, mid)
            mid, item = await find_menu_item(w, mid, DELETE_CHAT_LABELS)
            if not item or not item.get("found"):
                raise RuntimeError(f"Delete chat menu item missing: {item}")

            hit = item["hit"]
            await mouse(w, mid, hit["x"], hit["y"])
            mid += 3

            confirm = None
            for _ in range(20):
                await asyncio.sleep(0.3)
                mid, confirm = await find_confirm_delete(w, mid)
                if confirm and confirm.get("found"):
                    break
            else:
                raise RuntimeError(f"Delete confirm never appeared: last={confirm}")

            if dry_run:
                mid = await escape_ui(w, mid, 4)
                mid = await clear_search(w, mid)
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "dry_run": True,
                            "contact": contact,
                            "title": title,
                            "menu": "Delete chat",
                            "confirm": {
                                "prompt": confirm.get("prompt"),
                                "delete": confirm.get("del"),
                                "cancel": confirm.get("cancel"),
                            },
                            "group": ginfo,
                            "verify": None,
                        },
                        ensure_ascii=False,
                    )
                )
                return

            del_btn = confirm["del"]
            await mouse(w, mid, del_btn["x"], del_btn["y"])
            mid += 3
            await asyncio.sleep(1.0)

            # Leave UI healthy, then verify list
            mid = await escape_ui(w, mid, 2)
            mid = await clear_search(w, mid)
            await asyncio.sleep(0.4)

            mid, verify = await verify_gone(w, mid, contact, title)
            if not verify.get("gone"):
                # one more clear + recheck
                mid = await clear_search(w, mid)
                await asyncio.sleep(0.5)
                mid, verify = await verify_gone(w, mid, contact, title)
            if not verify.get("gone"):
                raise RuntimeError(f"chat still present after delete: {verify}")

            print(
                json.dumps(
                    {
                        "ok": True,
                        "contact": contact,
                        "title": title,
                        "verify": verify,
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            try:
                mid = await escape_ui(w, mid, 4)
                mid = await clear_search(w, mid)
            except Exception:
                pass
            die(str(e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Delete entire WhatsApp Web chat via CDP")
    ap.add_argument("--contact", required=True, help="exact title or phone digits")
    ap.add_argument("--allow-substring", action="store_true")
    ap.add_argument(
        "--allow-group",
        action="store_true",
        help="permit deleting group chats (default: refuse)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="open menu + show confirm target but do not confirm",
    )
    args = ap.parse_args()
    asyncio.run(
        main(args.contact, args.allow_substring, args.allow_group, args.dry_run)
    )
