import json, os, re, time, requests
from datetime import datetime, timezone

TOKEN = os.environ["TELEGRAM_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
TVM = "https://api.tvmaze.com"
STATE_FILE = "state.json"
FAV_BTN = "⭐ Избранное"
cache = {}


def load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "users": {}}


def save(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def tg(method, **params):
    try:
        return requests.post(f"{API}/{method}", json=params, timeout=40).json()
    except Exception as e:
        print("tg error", e)
        return {}


def say(chat, text, buttons=None, menu=False):
    p = {"chat_id": chat, "text": text[:4000]}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": buttons}
    elif menu:
        p["reply_markup"] = {"keyboard": [[{"text": FAV_BTN}]], "resize_keyboard": True}
    tg("sendMessage", **p)


def get(path, **params):
    r = requests.get(f"{TVM}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def episodes(show_id):
    if show_id not in cache:
        cache[show_id] = get(f"/shows/{show_id}/episodes")
    return cache[show_id]


def air_time(ep):
    if not ep.get("airstamp"):
        return None
    return datetime.fromisoformat(ep["airstamp"])


def fmt_date(ep):
    t = air_time(ep)
    if t:
        return t.strftime("%d.%m.%Y")
    return ep.get("airdate") or "дата неизвестна"


def ep_label(ep):
    return f"S{ep['season']:02d}E{ep['number'] or 0:02d} «{ep['name']}»"


def get_user(state, chat):
    return state["users"].setdefault(chat, {"shows": {}})


def search(chat, text):
    r = get("/search/shows", q=text)
    if not r:
        say(chat, "Ничего не нашёл. Попробуй написать название по-английски.")
        return
    buttons = []
    for item in r[:8]:
        s = item["show"]
        year = (s.get("premiered") or "")[:4]
        buttons.append([{"text": f"{s['name']} ({year})", "callback_data": f"show:{s['id']}"}])
    say(chat, "Каталог — выбери сериал:", buttons)


def show_card(state, chat, sid):
    info = get(f"/shows/{sid}")
    fav = str(sid) in get_user(state, chat)["shows"]
    year = (info.get("premiered") or "")[:4]
    status = {"Running": "идёт", "Ended": "завершён", "To Be Determined": "под вопросом",
              "In Development": "в разработке"}.get(info.get("status"), info.get("status") or "?")
    rating = (info.get("rating") or {}).get("average")
    net = (info.get("network") or info.get("webChannel") or {}).get("name")
    summary = re.sub("<[^>]+>", "", info.get("summary") or "")[:400]
    lines = [f"📺 {info['name']} ({year})", f"Статус: {status}"]
    if net:
        lines.append(f"Канал: {net}")
    if rating:
        lines.append(f"Рейтинг: {rating}")
    now = datetime.now(timezone.utc)
    up = [e for e in episodes(sid) if air_time(e) and air_time(e) > now]
    if up:
        e = min(up, key=air_time)
        lines.append(f"Следующая серия: {ep_label(e)} — {fmt_date(e)}")
    if summary:
        lines += ["", summary]
    fav_btn = ({"text": "✖ Убрать из избранного", "callback_data": f"unfav:{sid}"} if fav
               else {"text": "⭐ В избранное (уведомлять)", "callback_data": f"fav:{sid}"})
    say(chat, "\n".join(lines), [[fav_btn], [{"text": "📋 Список серий", "callback_data": f"eps:{sid}"}]])


def seasons_menu(chat, sid):
    eps = episodes(sid)
    seasons = sorted({e["season"] for e in eps})
    if not seasons:
        say(chat, "Серий пока нет в базе.")
        return
    row, rows = [], []
    for n in seasons:
        row.append({"text": f"Сезон {n}", "callback_data": f"ssn:{sid}:{n}"})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    say(chat, "Выбери сезон:", rows)


def season_list(chat, sid, n):
    now = datetime.now(timezone.utc)
    lines = [f"Сезон {n}", "✅ вышла   🗓 запланирована", ""]
    for e in episodes(sid):
        if e["season"] != n:
            continue
        t = air_time(e)
        mark = "✅" if t and t <= now else "🗓"
        lines.append(f"{mark} E{e['number'] or 0:02d} {e['name']} — {fmt_date(e)}")
    say(chat, "\n".join(lines))


def set_fav(state, chat, sid, on):
    user = get_user(state, chat)
    if on:
        info = get(f"/shows/{sid}")
        now = datetime.now(timezone.utc)
        done = [e["id"] for e in episodes(sid) if air_time(e) and air_time(e) <= now]
        user["shows"][str(sid)] = {"name": info["name"], "notified": done}
        say(chat, f"⭐ «{info['name']}» в избранном. Напишу, когда выйдет новая серия.")
    else:
        s = user["shows"].pop(str(sid), None)
        say(chat, f"Убрал «{s['name']}» из избранного." if s else "Его и не было в избранном.")


def show_favs(state, chat):
    shows = get_user(state, chat)["shows"]
    if not shows:
        say(chat, "В избранном пока пусто. Напиши название сериала.")
        return
    buttons = [[{"text": v["name"], "callback_data": f"show:{k}"}] for k, v in shows.items()]
    say(chat, "⭐ Твоё избранное:", buttons)


def handle_updates(state, wait=0):
    res = tg("getUpdates", offset=state["offset"], timeout=wait).get("result", [])
    for u in res:
        state["offset"] = u["update_id"] + 1
        try:
            if "message" in u and u["message"].get("text"):
                chat = str(u["message"]["chat"]["id"])
                text = u["message"]["text"].strip()
                if text.startswith("/start") or text.startswith("/help"):
                    say(chat, "Привет! Напиши название сериала (лучше по-английски) — покажу каталог. "
                              "Дальше можно добавить в избранное и смотреть список серий.\n"
                              "Кнопка «⭐ Избранное» внизу покажет твои сериалы.", menu=True)
                elif text.startswith("/list") or text == FAV_BTN:
                    show_favs(state, chat)
                else:
                    search(chat, text)
            elif "callback_query" in u:
                cq = u["callback_query"]
                chat = str(cq["message"]["chat"]["id"])
                parts = cq["data"].split(":")
                tg("answerCallbackQuery", callback_query_id=cq["id"])
                action, sid = parts[0], int(parts[1])
                if action == "show":
                    show_card(state, chat, sid)
                elif action == "fav":
                    set_fav(state, chat, sid, True)
                elif action == "unfav":
                    set_fav(state, chat, sid, False)
                elif action == "eps":
                    seasons_menu(chat, sid)
                elif action == "ssn":
                    season_list(chat, sid, int(parts[2]))
        except Exception as e:
            print("update error", e)


def check_releases(state):
    now = datetime.now(timezone.utc)
    for chat, user in state["users"].items():
        for sid, show in user["shows"].items():
            try:
                for e in episodes(int(sid)):
                    t = air_time(e)
                    if t and t <= now and e["id"] not in show["notified"]:
                        show["notified"].append(e["id"])
                        say(chat, f"🎬 Вышла новая серия!\n{show['name']} — {ep_label(e)}")
            except Exception as ex:
                print("check error", sid, ex)


if __name__ == "__main__":
    state = load()
    start = time.time()
    last_check = 0
    while time.time() - start < 280:
        cache.clear()
        handle_updates(state, wait=25)
        if time.time() - last_check > 120:
            check_releases(state)
            last_check = time.time()
        save(state)
        
