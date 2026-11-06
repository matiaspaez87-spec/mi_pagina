#!/usr/bin/env python3
"""Genera index.html (página diaria personal) y archive/AAAA-MM-DD.json.

Sin API de pago:
- Titulares: RSS (Google News y medios directos), filtrados por fuentes confiables.
- Clima: Open-Meteo (sin clave).
- Boca: marcador público de ESPN (endpoint no oficial, puede cambiar).
- Calendario y tareas: API de Google Calendar y Google Tasks, con los secretos
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET y GOOGLE_REFRESH_TOKEN (ver obtener_token.py).
  Respaldo para el calendario: dirección secreta iCal en CALENDAR_ICS_URL.
"""

import datetime as dt
import email.utils
import html  # noqa: F401  (se usa en limpiar)
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("America/Santiago")
UA = {"User-Agent": "Mozilla/5.0 (pagina-personal)"}
MAX_TITULARES = 5

# Medios considerados confiables. Se compara el dominio de la fuente.
FUENTES_OK = {
    "latercera.com", "emol.com", "elmercurio.com", "biobiochile.cl", "df.cl",
    "cooperativa.cl", "elmostrador.cl",
    # Medios agrícolas
    "redagricola.com", "portalfruticola.com", "mundoagro.cl", "simfruit.cl",
    "odepa.gob.cl", "fedefruta.cl", "frutasdechile.cl", "agrositio.com.ar", "agraria.pe",
    "bbc.com", "dw.com", "france24.com", "reuters.com", "apnews.com", "elpais.com",
    "infobae.com", "lanacion.com.ar", "clarin.com", "ambito.com", "cronista.com",
}

# Coordenadas aproximadas de Vitacura.
# Modelo europeo ECMWF (el de referencia mundial, el mismo que usan yr.no y Windy), vía Open-Meteo.
# Si ese modelo no responde, se usa la mezcla automática de Open-Meteo como respaldo.
CLIMA_URL_RESPALDO = (
    "https://api.open-meteo.com/v1/forecast?latitude=-33.39&longitude=-70.57"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
    "&current=temperature_2m&timezone=America%2FSantiago&forecast_days=1"
)
CLIMA_URL = CLIMA_URL_RESPALDO + "&models=ecmwf_ifs025"


def gn(q, pais):
    """URL de búsqueda RSS de Google News."""
    params = {"q": q, "hl": "es-419", "gl": pais, "ceid": f"{pais}:es-419"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


MEDIOS_AGRO = ("(site:portalfruticola.com OR site:redagricola.com OR site:mundoagro.cl "
               "OR site:simfruit.cl OR site:odepa.gob.cl OR site:fedefruta.cl)")

# Orden = orden en la página. Cada sección:
#   nombre, feeds, máximo de titulares, orden ("fecha" o "relevancia"),
#   palabras requeridas en el título (vacío = sin filtro),
#   antigüedad máxima en horas (más viejo que eso no se muestra).
SECCIONES = [
    ("🌱 Agro y exportaciones", [
        gn("(exportaciones agrícolas OR fruta OR cerezas OR fruticultura OR agricultura OR agroindustria) Chile when:1d", "CL"),
        gn(f"{MEDIOS_AGRO} when:2d", "CL"),
    ], 6, "fecha", [], 36),
    ("🚜 Tecnología para el agro", [
        gn("(agtech OR \"agricultura de precisión\" OR \"riego tecnificado\" OR drones agrícolas "
           "OR \"agricultura digital\" OR biotecnología agrícola OR tecnología agrícola) when:3d", "CL"),
        gn("(agtech OR \"agricultura de precisión\" OR tecnología agrícola) when:3d", "AR"),
    ], 5, "fecha", [], 72),
    ("Chile", [gn("Chile when:1d", "CL")], 5, "fecha", [], 24),
    ("Economía Chile", [
        gn("(dólar OR cobre OR IPSA OR \"Banco Central\" OR inflación) Chile when:1d", "CL"),
    ], 4, "fecha", ["chile", "cobre", "ipsa", "banco central"], 24),
    ("Global", [
        "https://feeds.bbci.co.uk/mundo/rss.xml",
        "https://rss.dw.com/rdf/rss-sp-all",
        "https://www.france24.com/es/rss",
    ], 4, "fecha", [], 24),
    # Portada de Google News Argentina: viene ordenada por relevancia.
    ("Argentina (lo más relevante)", [
        "https://news.google.com/rss?hl=es-419&gl=AR&ceid=AR:es-419",
    ], 3, "relevancia", [], 24),
]


def aviso(msg):
    print(f"aviso: {msg}", file=sys.stderr)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def dominio(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def es_confiable(dom):
    return any(dom == d or dom.endswith("." + d) for d in FUENTES_OK)


def limpiar(texto):
    t = html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))
    return re.sub(r"\s+", " ", t).strip()


def recortar(texto, largo=240):
    if len(texto) <= largo:
        return texto
    corte = texto[:largo].rsplit(" ", 1)[0]
    return corte + "…"


def leer_feed(url):
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:  # noqa: BLE001
        aviso(f"no se pudo leer {url}: {e}")
        return []

    items = []
    for it in root.iter("item"):
        titulo = limpiar(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        src = it.find("source")
        if src is not None and src.get("url"):
            dom = dominio(src.get("url"))
            nombre = (src.text or dom).strip()
            if src.text and titulo.endswith(" - " + src.text):
                titulo = titulo[: -len(src.text) - 3].strip()
        else:
            dom = dominio(link)
            nombre = dom

        if not es_confiable(dom):
            continue

        desc = limpiar(it.findtext("description"))
        if nombre:
            desc = desc.replace(nombre, "")
        if titulo:
            desc = desc.replace(titulo, "")
        desc = desc.strip(" -–·|")
        bajada = recortar(desc) if len(desc) >= 60 else ""

        try:
            fecha = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            fecha = None

        items.append({
            "titulo": titulo, "link": link, "fuente": nombre,
            "bajada": bajada, "fecha": fecha,
        })
    return items


HISTORIAL = os.path.join(HERE, "historial.json")
DIAS_HISTORIAL = 21  # un titular mostrado en las últimas 3 semanas no se vuelve a mostrar


def clave_titular(titulo):
    """Normaliza el titular para reconocerlo aunque cambien tildes, mayúsculas o puntuación."""
    import unicodedata
    t = unicodedata.normalize("NFKD", titulo.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", t)).strip()[:70]


def leer_historial(hoy):
    try:
        with open(HISTORIAL, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return {}
    limite = (hoy - dt.timedelta(days=DIAS_HISTORIAL)).isoformat()
    return {k: v for k, v in datos.items() if v >= limite}


def armar_secciones(hoy):
    """Elige los titulares más recientes que no se hayan mostrado en días anteriores."""
    salida = []
    minimo = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    ahora = dt.datetime.now(dt.timezone.utc)
    hoy_iso = hoy.isoformat()
    historial = leer_historial(hoy)
    # Lo mostrado hoy se puede repetir si la página se regenera el mismo día; lo de días anteriores, no.
    ya_vistos = {k for k, dia in historial.items() if dia < hoy_iso}
    usados = set()  # evita repetir un titular en dos secciones
    for nombre, urls, maximo, orden, requiere, horas in SECCIONES:
        items = []
        for u in urls:
            items += leer_feed(u)
        if orden == "fecha":
            items.sort(key=lambda x: x["fecha"] or minimo, reverse=True)

        sel = []
        for it in items:
            clave = clave_titular(it["titulo"])
            if not clave or clave in usados or clave in ya_vistos:
                continue
            # Solo noticias recientes: fuera lo publicado antes de la ventana de la sección.
            if it["fecha"] is None or ahora - it["fecha"] > dt.timedelta(hours=horas):
                continue
            if requiere and not any(p in it["titulo"].lower() for p in requiere):
                continue
            usados.add(clave)
            historial.setdefault(clave, hoy_iso)
            sel.append({k: v for k, v in it.items() if k != "fecha"})
            if len(sel) == maximo:
                break
        salida.append({"nombre": nombre, "items": sel})

    with open(HISTORIAL, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(historial.items(), key=lambda kv: kv[1])), f, ensure_ascii=False, indent=0)
    return salida


def clima():
    for url in (CLIMA_URL, CLIMA_URL_RESPALDO):
        try:
            d = json.loads(fetch(url))
            return {
                "actual": round(d["current"]["temperature_2m"]),
                "min": round(d["daily"]["temperature_2m_min"][0]),
                "max": round(d["daily"]["temperature_2m_max"][0]),
                "lluvia": round(d["daily"]["precipitation_sum"][0] or 0, 1),
            }
        except Exception as e:  # noqa: BLE001
            aviso(f"clima no disponible en {url}: {e}")
    return None


def partidos_boca(fecha):
    partidos = []
    for liga in ["arg.1", "conmebol.libertadores", "conmebol.sudamericana"]:
        url = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}"
               f"/scoreboard?dates={fecha:%Y%m%d}")
        try:
            d = json.loads(fetch(url))
        except Exception as e:  # noqa: BLE001
            aviso(f"ESPN {liga}: {e}")
            continue
        for ev in d.get("events", []):
            comp = ev["competitions"][0]
            nombres = [c["team"]["displayName"] for c in comp["competitors"]]
            if any("Boca" in n for n in nombres):
                hora = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(TZ)
                partidos.append({
                    "hora": hora.strftime("%H:%M"),
                    "partido": " vs ".join(nombres),
                    "torneo": (ev.get("league") or {}).get("name", liga),
                })
    return partidos


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def google_token():
    """Cambia el refresh token guardado en los secretos por un token de acceso."""
    claves = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    cid, secreto, refresh = (os.environ.get(k) for k in claves)
    if not (cid and secreto and refresh):
        aviso("faltan los secretos de Google: sin calendario ni tareas por API")
        return None
    datos = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secreto,
        "refresh_token": refresh, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=datos, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())["access_token"]


def google_get(url, token, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**UA, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def calendario_google(token, desde, hasta, hoy):
    """Eventos de hoy en todos los calendarios que tienes visibles en Google Calendar."""
    base = "https://www.googleapis.com/calendar/v3"
    calendarios = google_get(f"{base}/users/me/calendarList", token).get("items", [])
    out = []
    for cal in calendarios:
        if not (cal.get("selected") or cal.get("primary")):
            continue
        datos = google_get(
            f"{base}/calendars/{urllib.parse.quote(cal['id'], safe='')}/events", token, {
                "timeMin": desde.isoformat(), "timeMax": hasta.isoformat(),
                "singleEvents": "true", "orderBy": "startTime", "maxResults": "50",
            })
        for ev in datos.get("items", []):
            if ev.get("status") == "cancelled":
                continue
            inicio = ev.get("start", {})
            if "dateTime" in inicio:
                loc = dt.datetime.fromisoformat(inicio["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
                dia, hora = loc.date().isoformat(), loc.strftime("%H:%M")
            else:
                ini = dt.date.fromisoformat(inicio["date"])
                fin = dt.date.fromisoformat(ev.get("end", {}).get("date", inicio["date"]))
                dia = hoy.isoformat() if ini <= hoy < fin else ini.isoformat()
                hora = "Todo el día"
            out.append({
                "dia": dia, "hora": hora,
                "titulo": ev.get("summary", "(sin título)"),
                "lugar": ev.get("location", ""),
            })
    # Los eventos de todo el día primero, después por hora.
    return sorted(out, key=lambda e: (e["dia"], e["hora"] != "Todo el día", e["hora"]))


def tareas_google(token, hoy):
    """Checklist desde Google Tasks: las tareas de ayer y las de hoy, con su estado."""
    base = "https://tasks.googleapis.com/tasks/v1"
    hoy_iso = hoy.isoformat()
    ayer_iso = (hoy - dt.timedelta(days=1)).isoformat()
    listas = google_get(f"{base}/users/@me/lists", token, {"maxResults": "100"}).get("items", [])
    ayer, de_hoy = [], []
    for lista in listas:
        pagina = None
        while True:
            params = {"showCompleted": "true", "showHidden": "true", "maxResults": "100"}
            if pagina:
                params["pageToken"] = pagina
            datos = google_get(
                f"{base}/lists/{urllib.parse.quote(lista['id'], safe='')}/tasks", token, params)
            for t in datos.get("items", []):
                titulo = (t.get("title") or "").strip()
                if not titulo or t.get("deleted"):
                    continue
                vence = (t.get("due") or "")[:10] or None
                hecho = t.get("status") == "completed"
                fin = None
                if t.get("completed"):
                    fin = dt.datetime.fromisoformat(
                        t["completed"].replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
                item = {"id": t["id"], "titulo": titulo, "lista": lista.get("title", ""),
                        "vence": vence, "hecho": hecho}
                # Ayer: lo que vencía ayer o lo que completaste ayer.
                if vence == ayer_iso or fin == ayer_iso:
                    ayer.append(item)
                # Hoy: lo completado hoy, y lo pendiente que vence hoy, está atrasado o no tiene fecha.
                if hecho:
                    if fin == hoy_iso:
                        de_hoy.append(item)
                elif vence is None or vence <= hoy_iso:
                    de_hoy.append({**item, "atrasada": bool(vence and vence < hoy_iso)})
            pagina = datos.get("nextPageToken")
            if not pagina:
                break
    orden = lambda t: (t["hecho"], t["vence"] is None, t["vence"] or "")  # noqa: E731
    return {"ayer": sorted(ayer, key=orden), "hoy": sorted(de_hoy, key=orden)}


def leer_calendario(desde, hasta):
    url = os.environ.get("CALENDAR_ICS_URL")
    if not url:
        aviso("CALENDAR_ICS_URL no definida: sin eventos")
        return []
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(fetch(url))
    out = []
    for ev in recurring_ical_events.of(cal).between(desde, hasta):
        inicio = ev["DTSTART"].dt
        if isinstance(inicio, dt.datetime):
            loc = inicio.astimezone(TZ)
            dia, hora = loc.date().isoformat(), loc.strftime("%H:%M")
        else:
            dia, hora = inicio.isoformat(), "Todo el día"
        out.append({
            "dia": dia,
            "hora": hora,
            "titulo": str(ev.get("SUMMARY", "(sin título)")),
            "lugar": str(ev.get("LOCATION", "") or ""),
        })
    return sorted(out, key=lambda e: (e["dia"], e["hora"]))


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#E9EAE2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Mi día">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="color-scheme" content="light only">
<title>Mi día</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bitter:wght@400;700&family=Caveat:wght@500;700&display=swap" rel="stylesheet">
<style>
/* Cuaderno de campo: papel cuadriculado, tinta azul, lápiz y un solo destacador. */
:root{
  --papel:#E9EAE2; --cuadro:#DCE1E3; --tinta:#12295A; --lapiz:#2B2E33; --fuerte:#0F1114;
  --margen:#C97878; --destacador:#F2DD4E;
  --texto:"Bitter",Georgia,serif; --mano:"Caveat","Segoe Print","Comic Sans MS",cursive;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;color-scheme:light only}
body{margin:0;color:var(--lapiz);font:16px/1.55 var(--texto);background-color:var(--papel);
  background-image:linear-gradient(var(--cuadro) 1px,transparent 1px),linear-gradient(90deg,var(--cuadro) 1px,transparent 1px);
  background-size:24px 24px}
.pagina{max-width:1100px;margin:0 auto;padding:calc(22px + env(safe-area-inset-top)) calc(18px + env(safe-area-inset-right)) calc(48px + env(safe-area-inset-bottom)) 46px;position:relative;display:grid;gap:8px}
.pagina::before{content:"";position:absolute;top:0;bottom:0;left:30px;width:2px;background:var(--margen)}
@media (min-width:900px){
  .pagina{grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:56px;padding:44px 32px 64px 72px}
  .pagina::before{left:48px}
}
.mano{font-family:var(--mano);color:var(--tinta)}
.resaltado{background:linear-gradient(transparent 35%,var(--destacador) 35%,var(--destacador) 88%,transparent 88%);padding:0 3px;-webkit-box-decoration-break:clone;box-decoration-break:clone}

/* Encabezado */
.saludo{font-family:var(--mano);font-size:1.55rem;color:var(--tinta);margin:0;line-height:1.1}
h1{font-family:var(--texto);font-weight:700;color:var(--tinta);font-size:clamp(2rem,7vw,2.6rem);line-height:1.05;margin:4px 0 6px}
.sub{font-size:.85rem;margin:0 0 22px}
h2{font-family:var(--texto);font-weight:700;color:var(--tinta);font-size:1.15rem;margin:28px 0 8px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
h2 .mano{font-size:1.35rem;font-weight:700;color:var(--lapiz)}

/* Clima: regla de -5 a 40 °C con el rango del día marcado con destacador */
.clima{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;align-items:end}
.clima .grande{font-family:var(--mano);font-weight:700;font-size:4.4rem;line-height:.8;color:var(--tinta)}
.clima p{margin:0;font-size:.95rem}
.fuente-clima{font-size:.8rem}
.regla{grid-column:1/-1;position:relative;height:28px;margin-top:10px;border-bottom:2px solid var(--tinta)}
.regla i{position:absolute;bottom:-2px;width:1px;height:8px;background:var(--tinta)}
.regla .rango{position:absolute;bottom:-5px;height:8px;background:var(--destacador);border:1.5px solid var(--tinta);border-radius:4px}
.regla .ahora{position:absolute;bottom:6px;font-family:var(--mano);font-weight:700;font-size:1.2rem;color:var(--tinta);transform:translateX(-50%);white-space:nowrap;transition:left 1s cubic-bezier(.2,.8,.2,1)}
.escala{grid-column:1/-1;display:flex;justify-content:space-between;font-family:var(--mano);font-weight:700;font-size:1.1rem;color:var(--tinta)}

/* Boca */
.boca{margin:22px 0 0;font-size:1rem}
.boca b{color:var(--fuerte)}

/* Listas */
ul{list-style:none;margin:0;padding:0}
.agenda li{display:grid;grid-template-columns:4.2em 1fr;gap:8px;padding:5px 0}
.agenda .hora{font-family:var(--mano);font-weight:700;font-size:1.3rem;color:var(--tinta);line-height:1.2}
.agenda .lugar{display:block;font-family:var(--mano);font-weight:700;font-size:1.15rem;color:var(--lapiz)}
.vacio{font-family:var(--mano);font-weight:700;font-size:1.25rem;color:var(--lapiz);margin:0}

/* Checklist: ayer vs hoy, con casillas dibujadas */
.vs{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.vs h3{font-family:var(--mano);font-weight:700;font-size:1.5rem;color:var(--tinta);margin:0;border-bottom:1.5px solid var(--tinta);line-height:1.2}
.vs li{display:grid;grid-template-columns:22px 1fr;gap:8px;padding:7px 0;font-size:.95rem;line-height:1.3}
.vs label{cursor:pointer}
.casilla,.vs input{width:18px;height:18px;margin:2px 0 0;border:2px solid var(--tinta);border-radius:2px;transform:rotate(-2deg);position:relative;background:transparent}
.vs input{appearance:none;-webkit-appearance:none;cursor:pointer}
.vs input:disabled{cursor:default}
.casilla.ok::after,.vs input:checked::after{content:"✓";position:absolute;left:1px;top:-14px;font-family:var(--mano);font-weight:700;font-size:1.65rem;color:var(--tinta)}
.vs li.hecha label{text-decoration:line-through;text-decoration-color:var(--tinta)}
.vs .meta{display:block;font-family:var(--mano);font-weight:700;font-size:1.12rem;color:var(--lapiz)}
.vs .vacio{padding:7px 0}
.nota{font-family:var(--mano);font-weight:700;font-size:1.15rem;margin:12px 0 0}

/* Titulares */
.noticias li{padding:8px 0}
.noticias a{color:var(--fuerte);text-decoration:none;font-weight:700;font-size:1rem;line-height:1.35}
.noticias a:hover{text-decoration:underline;text-decoration-color:var(--tinta);text-underline-offset:3px}
.noticias p{margin:4px 0 0;font-size:.92rem;line-height:1.45;max-width:62ch}
.noticias .fuente{display:block;font-family:var(--mano);font-weight:700;font-size:1.2rem;color:var(--tinta);line-height:1.2}
.prioridad h2::before{content:"★";color:var(--tinta);margin-left:-27px;width:17px;font-size:1rem}
@media (min-width:900px){.prioridad h2::before{margin-left:-35px;width:25px}}
.titulares > section:first-child h2{margin-top:6px}

:focus-visible{outline:3px solid var(--tinta);outline-offset:3px;border-radius:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<main class="pagina">
  <section class="dia" aria-label="Tu día">
    <header>
      <p class="saludo" id="saludo"></p>
      <h1 id="fecha"></h1>
      <p class="sub" id="generado"></p>
    </header>
    <div id="dia"></div>
  </section>
  <section class="titulares" aria-label="Titulares" id="titulares"></section>
</main>
<script>
const DATA = __DATA__;
const CLIMA_URL = __CLIMA_URL__;
const CLIMA_URL_RESPALDO = __CLIMA_URL_RESPALDO__;
const $ = s => document.querySelector(s);

function el(tag, props, ...hijos){
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  hijos.flat().forEach(h => { if (h != null) e.append(h); });
  return e;
}
const leer = k => { try { return localStorage.getItem(k) === "1"; } catch (e) { return false; } };
const guardar = (k, v) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch (e) {} };
const fmtFecha = iso => {
  const t = new Date(iso + "T12:00:00").toLocaleDateString("es-CL", {weekday: "long", day: "numeric", month: "long"});
  return t.charAt(0).toUpperCase() + t.slice(1);
};
const isoLocal = f => `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-${String(f.getDate()).padStart(2, "0")}`;

// "Hoy" y "ayer" del checklist salen del reloj del dispositivo.
const ahora = new Date();
const ayerF = new Date(ahora); ayerF.setDate(ahora.getDate() - 1);
const HOY = isoLocal(ahora), AYER = isoLocal(ayerF);

function encabezado(){
  const h = ahora.getHours();
  $("#saludo").textContent = (h < 12 ? "Buenos días" : h < 20 ? "Buenas tardes" : "Buenas noches") + ", Matías";
  $("#fecha").textContent = fmtFecha(DATA.fecha);
  const [f, hora] = DATA.generado.split(" ");
  $("#generado").textContent = `Santiago, Chile. Titulares actualizados el ${f} a las ${hora}.`;
}

function bloqueClima(c){
  const pct = v => Math.min(100, Math.max(0, (v + 5) / 45 * 100));
  const marcas = [];
  for (let g = -5; g <= 40; g += 5) marcas.push(el("i", {style: `left:${pct(g)}%`}));
  const flecha = el("span", {class: "ahora", style: `left:${pct(c.min)}%`, text: "ahora ↓"});
  const caja = el("div", {class: "clima"},
    el("span", {class: "grande", text: `${c.actual}°`}),
    el("p", {}, `Mínima ${c.min}°, máxima ${c.max}°.`, el("br"), (c.lluvia >= 0.1 ? `Lluvia esperada: ${String(c.lluvia).replace(".", ",")} mm.` : "Sin lluvia pronosticada."),
      el("br"), el("span", {class: "fuente-clima", text: `Clima consultado a las ${c.hora}.`})),
    el("div", {class: "regla", role: "img", "aria-label": `Temperatura del día entre ${c.min} y ${c.max} grados; ahora ${c.actual}`},
      ...marcas,
      el("span", {class: "rango", style: `left:${pct(c.min)}%;width:${pct(c.max) - pct(c.min)}%`}),
      flecha),
    el("div", {class: "escala"}, ...["−5°", "10°", "25°", "40°"].map(t => el("span", {text: t}))));
  // Único movimiento de la página: la flecha va de la mínima a la temperatura actual.
  requestAnimationFrame(() => requestAnimationFrame(() => { flecha.style.left = `${pct(c.actual)}%`; }));
  return caja;
}

function bloqueBoca(partidos){
  return partidos.map(p => el("p", {class: "boca", role: "status"},
    el("span", {class: "resaltado"}, el("b", {text: `Boca juega hoy a las ${p.hora}`})),
    ` ${p.partido}, ${p.torneo}.`));
}

function bloqueAgenda(eventos){
  const b = el("div", {}, el("h2", {text: "Agenda de hoy"}));
  if (!eventos.length) {
    b.append(el("p", {class: "vacio", text: "Hoy no tienes eventos en el calendario."}));
    return b;
  }
  b.append(el("ul", {class: "agenda"}, ...eventos.map(e => el("li", {},
    el("span", {class: "hora", text: e.hora === "Todo el día" ? "todo el día" : e.hora}),
    el("span", {}, e.titulo, e.lugar ? el("span", {class: "lugar", text: e.lugar}) : null)))));
  return b;
}

function bloqueChecklist(t){
  const resumen = el("span", {class: "mano"});
  const b = el("div", {}, el("h2", {}, "Checklist", resumen));
  if (t == null) {
    b.append(el("p", {class: "vacio", text: "Google Tasks no está conectado. Agrega los secretos de Google en el repositorio para ver tus tareas aquí."}));
    return b;
  }
  // Lo marcado en la página se guarda en este dispositivo; lo completado en Google llega con la próxima actualización.
  const hechaAyer = x => x.hecho || leer(`gt|${AYER}|${x.id}`);
  const claveHoy = x => `gt|${HOY}|${x.id}`;
  const nAyer = t.ayer.filter(hechaAyer).length;
  const contar = () => {
    const nHoy = t.hoy.filter(x => x.hecho || leer(claveHoy(x))).length;
    resumen.textContent = `ayer ${nAyer}/${t.ayer.length}, hoy ${nHoy}/${t.hoy.length}`;
  };

  const colAyer = el("div", {}, el("h3", {text: "Ayer"}),
    t.ayer.length
      ? el("ul", {}, ...t.ayer.map(x => {
          const ok = hechaAyer(x);
          return el("li", {},
            el("span", {class: "casilla" + (ok ? " ok" : ""), role: "img", "aria-label": ok ? "Hecha" : "No hecha"}),
            el("span", {text: x.titulo}));
        }))
      : el("p", {class: "vacio", text: "ayer no tenías tareas"}));

  const colHoy = el("div", {}, el("h3", {text: "Hoy"}),
    t.hoy.length
      ? el("ul", {}, ...t.hoy.map((x, i) => {
          const id = `gt-${i}`;
          const cb = el("input", {type: "checkbox", id});
          cb.checked = x.hecho || leer(claveHoy(x));
          cb.disabled = x.hecho;
          const meta = x.atrasada ? el("span", {class: "meta"}, el("span", {class: "resaltado", text: "atrasada"}))
            : (x.vence ? null : el("span", {class: "meta", text: "sin fecha"}));
          const li = el("li", {class: cb.checked ? "hecha" : ""}, cb, el("label", {for: id}, x.titulo, meta));
          cb.addEventListener("change", () => { guardar(claveHoy(x), cb.checked); li.classList.toggle("hecha", cb.checked); contar(); });
          return li;
        }))
      : el("p", {class: "vacio", text: "no tienes tareas para hoy"}));

  contar();
  b.append(el("div", {class: "vs"}, colAyer, colHoy),
    el("p", {class: "nota", text: "Lo que marcas aquí queda en este dispositivo. Para que quede hecha en Google, complétala en Google Tasks."}));
  return b;
}

function titulares(secciones){
  const cont = $("#titulares");
  const llenas = secciones.filter(s => s.items.length);
  if (!llenas.length) {
    cont.append(el("p", {class: "vacio", text: "No llegaron titulares en esta actualización. Se vuelven a cargar mañana a las 5:30."}));
    return;
  }
  const EMOJI = /^[\u{1F331}\u{1F69C}]️?\s*/u;
  llenas.forEach(s => {
    const prioridad = EMOJI.test(s.nombre);
    cont.append(el("section", {class: "noticias" + (prioridad ? " prioridad" : "")},
      el("h2", {text: s.nombre.replace(EMOJI, "")}),
      el("ul", {}, ...s.items.map(n => el("li", {},
        el("a", {href: n.link, target: "_blank", rel: "noopener", text: n.titulo}),
        n.bajada ? el("p", {text: n.bajada}) : null,
        el("span", {class: "fuente", text: n.fuente}))))));
  });
}

encabezado();
const dia = $("#dia");
// Clima: primero el que se guardó al generar la página; luego se consulta en vivo cada vez que se abre.
const cajaClima = el("div", {});
dia.append(cajaClima);
if (DATA.clima) cajaClima.append(bloqueClima({...DATA.clima, hora: DATA.generado.split(" ")[1]}));
const leerClima = url => fetch(url).then(r => r.json()).then(d => ({
  actual: Math.round(d.current.temperature_2m),
  min: Math.round(d.daily.temperature_2m_min[0]),
  max: Math.round(d.daily.temperature_2m_max[0]),
  lluvia: Math.round((d.daily.precipitation_sum[0] || 0) * 10) / 10,
  hora: new Date().toLocaleTimeString("es-CL", {hour: "2-digit", minute: "2-digit"}),
}));
leerClima(CLIMA_URL).catch(() => leerClima(CLIMA_URL_RESPALDO))
  .then(c => { if (!isNaN(c.actual)) cajaClima.replaceChildren(bloqueClima(c)); }).catch(() => {});
if (DATA.boca && DATA.boca.length) dia.append(...bloqueBoca(DATA.boca));
dia.append(bloqueAgenda(DATA.eventos), bloqueChecklist(DATA.tareas));
titulares(DATA.secciones);

// Abierta como app en el iPhone no hay botón de recargar: al volver después de 10 minutos, se recarga sola.
const CARGADA = Date.now();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - CARGADA > 10 * 60 * 1000) location.reload();
});
</script>
</body>
</html>
"""


def crear_app_iphone():
    """Ícono y manifiesto para abrir la página como app desde la pantalla de inicio."""
    from PIL import Image, ImageDraw

    def icono(tam):
        f = tam / 180
        img = Image.new("RGB", (tam, tam), "#E9EAE2")
        d = ImageDraw.Draw(img)
        paso = round(18 * f)
        for x in range(0, tam, paso):
            d.line([(x, 0), (x, tam)], fill="#D3DAD9", width=max(1, round(f)))
            d.line([(0, x), (tam, x)], fill="#D3DAD9", width=max(1, round(f)))
        d.line([(round(34 * f), 0), (round(34 * f), tam)], fill="#C97878", width=round(4 * f))
        # Casilla con su ✓, como en el checklist.
        caja = [round(58 * f), round(52 * f), round(138 * f), round(132 * f)]
        d.rounded_rectangle(caja, radius=round(8 * f), outline="#12295A", width=round(9 * f))
        d.line([(round(72 * f), round(90 * f)), (round(94 * f), round(114 * f)), (round(150 * f), round(36 * f))],
               fill="#12295A", width=round(15 * f), joint="curve")
        return img

    icono(180).save(os.path.join(HERE, "apple-touch-icon.png"))
    icono(512).save(os.path.join(HERE, "icono-512.png"))
    manifiesto = {
        "name": "Mi día", "short_name": "Mi día", "lang": "es-CL",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#E9EAE2", "theme_color": "#E9EAE2",
        "icons": [
            {"src": "apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
            {"src": "icono-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    with open(os.path.join(HERE, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(manifiesto, f, ensure_ascii=False, indent=2)


def main():
    ahora = dt.datetime.now(TZ)
    hoy = ahora.date()
    hoy_iso = hoy.isoformat()

    secciones = armar_secciones(hoy)
    clima_hoy = clima()
    boca = partidos_boca(hoy)

    desde = dt.datetime.combine(hoy, dt.time(0), tzinfo=TZ)
    hasta = dt.datetime.combine(hoy + dt.timedelta(days=1), dt.time(0), tzinfo=TZ)
    eventos, tareas = [], None  # tareas = None significa "Google Tasks no conectado"

    token = None
    try:
        token = google_token()
    except Exception as e:  # noqa: BLE001
        aviso(f"no se pudo autorizar con Google: {e}")

    if token:
        try:
            eventos = calendario_google(token, desde, hasta, hoy)
        except Exception as e:  # noqa: BLE001
            aviso(f"Google Calendar no disponible: {e}")
        try:
            tareas = tareas_google(token, hoy)
        except Exception as e:  # noqa: BLE001
            aviso(f"Google Tasks no disponible: {e}")
    else:
        # Respaldo: dirección secreta iCal, si está configurada.
        try:
            eventos = leer_calendario(desde, hasta)
        except Exception as e:  # noqa: BLE001
            aviso(f"calendario no disponible: {e}")

    data = {
        "generado": ahora.strftime("%d/%m/%Y %H:%M"),
        "fecha": hoy_iso,
        "clima": clima_hoy,
        "boca": boca,
        "eventos": [e for e in eventos if e["dia"] == hoy_iso],
        "tareas": tareas,
        "secciones": secciones,
    }
    salida = PLANTILLA.replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")).replace(
        "__CLIMA_URL_RESPALDO__", json.dumps(CLIMA_URL_RESPALDO)).replace(
        "__CLIMA_URL__", json.dumps(CLIMA_URL))
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(salida)
    try:
        crear_app_iphone()
    except Exception as e:  # noqa: BLE001
        aviso(f"no se pudo crear el ícono de la app: {e}")
    print(f"listo: {sum(len(s['items']) for s in secciones)} titulares, "
          f"{len(data['eventos'])} eventos hoy, "
          f"{'sin conexión a' if tareas is None else len(tareas['hoy'])} tareas de Google para hoy, "
          f"{len(boca)} partido(s) de Boca")


if __name__ == "__main__":
    main()
