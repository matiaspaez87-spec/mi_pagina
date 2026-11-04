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
CLIMA_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=-33.39&longitude=-70.57"
    "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
    "&current=temperature_2m&timezone=America%2FSantiago&forecast_days=1"
)


def gn(q, pais):
    """URL de búsqueda RSS de Google News."""
    params = {"q": q, "hl": "es-419", "gl": pais, "ceid": f"{pais}:es-419"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


MEDIOS_AGRO = ("(site:portalfruticola.com OR site:redagricola.com OR site:mundoagro.cl "
               "OR site:simfruit.cl OR site:odepa.gob.cl OR site:fedefruta.cl)")

# Orden = orden en la página. Cada sección:
#   nombre, feeds, máximo de titulares, orden ("fecha" o "relevancia"),
#   palabras requeridas en el título (vacío = sin filtro).
SECCIONES = [
    ("🌱 Agro y exportaciones", [
        gn("(exportaciones agrícolas OR fruta OR cerezas OR fruticultura OR agricultura OR agroindustria) Chile when:2d", "CL"),
        gn(f"{MEDIOS_AGRO} when:3d", "CL"),
    ], 6, "fecha", []),
    ("🚜 Tecnología para el agro", [
        gn("(agtech OR \"agricultura de precisión\" OR \"riego tecnificado\" OR drones agrícolas "
           "OR \"agricultura digital\" OR biotecnología agrícola OR tecnología agrícola) when:7d", "CL"),
        gn("(agtech OR \"agricultura de precisión\" OR tecnología agrícola) when:7d", "AR"),
    ], 5, "fecha", []),
    ("Chile", [gn("Chile when:1d", "CL")], 5, "fecha", []),
    ("Economía Chile", [
        gn("(dólar OR cobre OR IPSA OR \"Banco Central\" OR inflación) Chile when:1d", "CL"),
    ], 4, "fecha", ["chile", "cobre", "ipsa", "banco central"]),
    ("Global", [
        "https://feeds.bbci.co.uk/mundo/rss.xml",
        "https://rss.dw.com/rdf/rss-sp-all",
        "https://www.france24.com/es/rss",
    ], 4, "fecha", []),
    # Portada de Google News Argentina: viene ordenada por relevancia.
    ("Argentina (lo más relevante)", [
        "https://news.google.com/rss?hl=es-419&gl=AR&ceid=AR:es-419",
    ], 3, "relevancia", []),
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


def armar_secciones():
    salida = []
    minimo = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    vistos = set()  # evita repetir un titular en dos secciones
    for nombre, urls, maximo, orden, requiere in SECCIONES:
        items = []
        for u in urls:
            items += leer_feed(u)
        if orden == "fecha":
            items.sort(key=lambda x: x["fecha"] or minimo, reverse=True)

        sel = []
        for it in items:
            clave = it["titulo"].lower()[:60]
            if not clave or clave in vistos:
                continue
            if requiere and not any(p in it["titulo"].lower() for p in requiere):
                continue
            vistos.add(clave)
            sel.append({k: v for k, v in it.items() if k != "fecha"})
            if len(sel) == maximo:
                break
        salida.append({"nombre": nombre, "items": sel})
    return salida


def clima():
    try:
        d = json.loads(fetch(CLIMA_URL))
        return {
            "actual": round(d["current"]["temperature_2m"]),
            "min": round(d["daily"]["temperature_2m_min"][0]),
            "max": round(d["daily"]["temperature_2m_max"][0]),
            "lluvia": d["daily"]["precipitation_probability_max"][0],
        }
    except Exception as e:  # noqa: BLE001
        aviso(f"clima no disponible: {e}")
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
DIAS_TAREAS = 7  # tareas que vencen en los próximos N días (más las atrasadas y sin fecha)


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
    """Tareas pendientes de todas tus listas de Google Tasks."""
    base = "https://tasks.googleapis.com/tasks/v1"
    limite = (hoy + dt.timedelta(days=DIAS_TAREAS)).isoformat()
    listas = google_get(f"{base}/users/@me/lists", token, {"maxResults": "100"}).get("items", [])
    out = []
    for lista in listas:
        pagina = None
        while True:
            params = {"showCompleted": "false", "maxResults": "100"}
            if pagina:
                params["pageToken"] = pagina
            datos = google_get(
                f"{base}/lists/{urllib.parse.quote(lista['id'], safe='')}/tasks", token, params)
            for t in datos.get("items", []):
                titulo = (t.get("title") or "").strip()
                if not titulo or t.get("status") != "needsAction":
                    continue
                vence = (t.get("due") or "")[:10] or None
                if vence and vence > limite:
                    continue
                out.append({
                    "titulo": titulo,
                    "lista": lista.get("title", ""),
                    "vence": vence,
                    "atrasada": bool(vence and vence < hoy.isoformat()),
                })
            pagina = datos.get("nextPageToken")
            if not pagina:
                break
    # Atrasadas y de hoy primero, después las próximas, al final las sin fecha.
    return sorted(out, key=lambda t: (t["vence"] is None, t["vence"] or ""))


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
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#EEF1E7">
<title>Mi día</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,800&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap" rel="stylesheet">
<style>
:root{
  --neblina:#EEF1E7; --papel:#FAFBF6; --tinta:#1E2620; --hoja:#2F5D3A;
  --cereza:#A3172E; --tierra:#6E5A47; --cielo:#4F86A0; --linea:#D3DACB;
  --display:"Bricolage Grotesque",system-ui,sans-serif;
  --lectura:"Newsreader",Georgia,serif;
}
@media (prefers-color-scheme:dark){:root{
  --neblina:#141A16; --papel:#1B231E; --tinta:#E4EADF; --hoja:#8CC59A;
  --cereza:#E35A70; --tierra:#B7A28C; --cielo:#79AFC6; --linea:#2C3830;
}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--neblina);color:var(--tinta);font:17px/1.55 var(--lectura)}
.pagina{max-width:1120px;margin:0 auto;padding:28px 20px 64px;display:grid;gap:44px}
@media (min-width:900px){
  .pagina{grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:64px;padding-top:52px}
  .dia{align-self:start}
}
h1,h2{font-family:var(--display)}

/* Encabezado */
.saludo{font-family:var(--display);font-size:1rem;color:var(--tierra);margin:0}
h1{font-size:clamp(2.4rem,8vw,3.5rem);line-height:1;letter-spacing:-.025em;font-weight:800;margin:.12em 0 .3em}
.actualizado{font-family:var(--display);font-size:.8rem;color:var(--tierra);margin:0 0 30px}

/* Clima: rango del día sobre una escala fija de -5 a 40 °C */
.clima{margin:0 0 34px}
.temps{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;font-family:var(--display)}
.temps .ahora{font-size:3.4rem;font-weight:800;line-height:1;letter-spacing:-.04em}
.temps .rango{font-size:.95rem;color:var(--tierra)}
.escala{position:relative;height:10px;border-radius:5px;background:var(--linea);margin:16px 0 6px}
.escala .tramo{position:absolute;top:0;bottom:0;border-radius:5px;background:linear-gradient(90deg,#6FA3B8,#D9A23A 55%,var(--cereza))}
.escala .marca{position:absolute;top:-6px;width:4px;height:22px;border-radius:2px;background:var(--tinta);transform:translateX(-50%);transition:left 1s cubic-bezier(.2,.8,.2,1)}
.leyenda{display:flex;justify-content:space-between;font-family:var(--display);font-size:.72rem;color:var(--tierra)}
.lluvia{display:flex;align-items:center;gap:12px;margin-top:16px;font-family:var(--display);font-size:.9rem}
.lluvia .barra{flex:1;height:6px;border-radius:3px;background:var(--linea);overflow:hidden}
.lluvia .barra span{display:block;height:100%;background:var(--cielo)}
.lluvia b{font-variant-numeric:tabular-nums;min-width:3ch;text-align:right}

/* Boca */
.boca{display:grid;grid-template-columns:auto 1fr;column-gap:16px;align-items:center;background:#0E3A73;color:#fff;border-radius:6px;padding:12px 16px 14px;margin:0 0 30px;font-family:var(--display);border-bottom:6px solid #F2B705}
.boca strong{grid-row:span 2;font-size:1.7rem;font-weight:800;color:#F2B705;letter-spacing:-.02em}
.boca .cuando{font-weight:600}
.boca .detalle{font-size:.85rem;opacity:.85}

.bloque{margin:0 0 34px}
.bloque h2{font-size:1.2rem;font-weight:600;margin:0 0 10px;letter-spacing:-.01em}

/* Agenda */
.agenda{list-style:none;margin:0;padding:0}
.agenda li{display:grid;grid-template-columns:5.6em 1fr;gap:12px;padding:9px 0;border-top:1px solid var(--linea)}
.agenda .hora{font-family:var(--display);font-weight:600;color:var(--hoja);font-variant-numeric:tabular-nums}
.agenda .lugar{display:block;font-size:.88rem;color:var(--tierra)}
.vacio{color:var(--tierra);font-style:italic;margin:0}

/* Google Tasks */
.gtareas{list-style:none;margin:0;padding:0}
.gtareas li{display:grid;grid-template-columns:5.6em 1fr;gap:12px;padding:9px 0;border-top:1px solid var(--linea)}
.gtareas .vence{font-family:var(--display);font-weight:600;font-size:.9rem;color:var(--tierra)}
.gtareas .vence.atrasada{color:var(--cereza)}
.gtareas .vence.hoy{color:var(--hoja)}
.gtareas .lista{display:block;font-size:.85rem;color:var(--tierra)}

/* Checklist: ayer vs hoy */
.conteo{display:flex;gap:28px;font-family:var(--display);margin:0 0 12px}
.conteo div{font-size:.85rem;color:var(--tierra)}
.conteo b{display:block;font-size:1.7rem;line-height:1.1;font-weight:800;color:var(--tinta);font-variant-numeric:tabular-nums}
.conteo .hoy b{color:var(--cereza)}
.tareas{list-style:none;margin:0;padding:0}
.tareas li{display:grid;grid-template-columns:1fr 2.6em 2.8em;align-items:center;gap:8px;padding:10px 0;border-top:1px solid var(--linea)}
.tareas li.cabecera{font-family:var(--display);font-size:.75rem;color:var(--tierra);border-top:0;padding:0 0 4px}
.tareas li.cabecera span+span{text-align:center}
.tareas label{cursor:pointer}
.tareas .ayer{justify-self:center;width:12px;height:12px;border-radius:50%;border:2px solid var(--tierra)}
.tareas .ayer.ok{background:var(--hoja);border-color:var(--hoja)}
.tareas input{appearance:none;-webkit-appearance:none;justify-self:center;width:28px;height:28px;margin:0;border-radius:50%;border:2px solid var(--cereza);background:transparent;cursor:pointer;display:grid;place-content:center}
.tareas input:checked{background:var(--cereza)}
.tareas input:checked::after{content:"";width:7px;height:12px;border:solid var(--neblina);border-width:0 3px 3px 0;transform:translateY(-2px) rotate(45deg)}
.tareas li.hecha label{color:var(--tierra);text-decoration:line-through;text-decoration-color:var(--cereza)}

/* Titulares */
.seccion{margin:0 0 40px}
.seccion h2{font-size:1.3rem;font-weight:600;letter-spacing:-.01em;margin:0;padding-bottom:8px;border-bottom:2px solid var(--tinta)}
.seccion.destacada{background:var(--papel);border-left:6px solid var(--hoja);border-radius:6px;padding:20px 22px 6px}
.seccion.destacada h2{font-size:1.6rem;font-weight:800;color:var(--hoja);border-bottom-color:var(--hoja)}
.noticias{list-style:none;margin:0;padding:0}
.noticias li{padding:14px 0;border-bottom:1px solid var(--linea)}
.noticias li:last-child{border-bottom:0}
.noticias a{color:var(--tinta);text-decoration:none;font-size:1.13rem;line-height:1.3;font-weight:500}
.noticias a:hover{text-decoration:underline;text-decoration-color:var(--cereza);text-underline-offset:3px}
.noticias p{margin:6px 0 0;color:var(--tierra);font-size:.95rem;line-height:1.5;max-width:62ch}
.noticias .fuente{display:block;margin-top:6px;font-family:var(--display);font-size:.78rem;font-weight:600;color:var(--hoja)}

:focus-visible{outline:3px solid var(--hoja);outline-offset:3px;border-radius:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<main class="pagina">
  <section class="dia" aria-label="Tu día">
    <p class="saludo" id="saludo"></p>
    <h1 id="fecha"></h1>
    <p class="actualizado" id="generado"></p>
    <div id="dia"></div>
  </section>
  <section class="titulares" aria-label="Titulares" id="titulares"></section>
</main>
<script>
const DATA = __DATA__;
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
  $("#generado").textContent = `Titulares actualizados el ${f} a las ${hora}`;
}

function bloqueClima(c){
  const pct = v => Math.min(100, Math.max(0, (v + 5) / 45 * 100));
  const marca = el("span", {class: "marca", style: `left:${pct(c.min)}%`});
  const caja = el("div", {class: "clima"},
    el("div", {class: "temps"},
      el("span", {class: "ahora", text: `${c.actual}°`}),
      el("span", {class: "rango", text: `Ahora en Santiago. Mínima ${c.min}°, máxima ${c.max}°.`})),
    el("div", {class: "escala", role: "img", "aria-label": `Temperatura del día entre ${c.min} y ${c.max} grados`},
      el("span", {class: "tramo", style: `left:${pct(c.min)}%;width:${pct(c.max) - pct(c.min)}%`}),
      marca),
    el("div", {class: "leyenda"}, el("span", {text: "−5°"}), el("span", {text: "40°"})),
    el("div", {class: "lluvia"},
      el("span", {text: "Probabilidad de lluvia"}),
      el("span", {class: "barra"}, el("span", {style: `width:${c.lluvia}%`})),
      el("b", {text: `${c.lluvia}%`})));
  // Único movimiento de la página: la marca viaja de la mínima a la temperatura actual.
  requestAnimationFrame(() => requestAnimationFrame(() => { marca.style.left = `${pct(c.actual)}%`; }));
  return caja;
}

function bloqueBoca(partidos){
  return partidos.map(p => el("div", {class: "boca", role: "status"},
    el("strong", {text: "Boca"}),
    el("span", {class: "cuando", text: `Juega hoy a las ${p.hora}`}),
    el("span", {class: "detalle", text: `${p.partido}, ${p.torneo}`})));
}

function bloqueAgenda(eventos){
  const b = el("div", {class: "bloque"}, el("h2", {text: "Agenda de hoy"}));
  if (!eventos.length) {
    b.append(el("p", {class: "vacio", text: "No tienes eventos en el calendario hoy."}));
    return b;
  }
  b.append(el("ul", {class: "agenda"}, ...eventos.map(e => el("li", {},
    el("span", {class: "hora", text: e.hora}),
    el("span", {}, e.titulo, e.lugar ? el("span", {class: "lugar", text: e.lugar}) : null)))));
  return b;
}

function bloqueTareas(tareas){
  const b = el("div", {class: "bloque"}, el("h2", {text: "Tareas de Google"}));
  if (tareas == null) {
    b.append(el("p", {class: "vacio", text: "Google Tasks no está conectado. Agrega los secretos de Google en el repositorio para ver tus tareas aquí."}));
    return b;
  }
  if (!tareas.length) {
    b.append(el("p", {class: "vacio", text: "No tienes tareas pendientes para esta semana."}));
    return b;
  }
  const etiqueta = t => {
    if (!t.vence) return ["Sin fecha", ""];
    if (t.atrasada) return ["Atrasada", " atrasada"];
    if (t.vence === HOY) return ["Hoy", " hoy"];
    const txt = new Date(t.vence + "T12:00:00").toLocaleDateString("es-CL", {weekday: "short", day: "numeric"});
    return [txt.charAt(0).toUpperCase() + txt.slice(1), ""];
  };
  b.append(el("ul", {class: "gtareas"}, ...tareas.map(t => {
    const [txt, clase] = etiqueta(t);
    return el("li", {},
      el("span", {class: "vence" + clase, text: txt}),
      el("span", {}, t.titulo, t.lista ? el("span", {class: "lista", text: t.lista}) : null));
  })));
  return b;
}

function bloqueChecklist(lista){
  const total = lista.length;
  const nAyer = lista.filter(t => leer(`chk|${AYER}|${t}`)).length;
  const bHoy = el("b", {});
  const contarHoy = () => { bHoy.textContent = `${lista.filter(t => leer(`chk|${HOY}|${t}`)).length}/${total}`; };

  const filas = lista.map((t, i) => {
    const id = `tarea-${i}`;
    const clave = `chk|${HOY}|${t}`;
    const ok = leer(`chk|${AYER}|${t}`);
    const cb = el("input", {type: "checkbox", id});
    cb.checked = leer(clave);
    const li = el("li", {class: cb.checked ? "hecha" : ""},
      el("label", {for: id, text: t}),
      el("span", {class: "ayer" + (ok ? " ok" : ""), role: "img", "aria-label": ok ? "Hecha ayer" : "No hecha ayer"}),
      cb);
    cb.addEventListener("change", () => { guardar(clave, cb.checked); li.classList.toggle("hecha", cb.checked); contarHoy(); });
    return li;
  });
  contarHoy();
  return el("div", {class: "bloque"},
    el("h2", {text: "Checklist"}),
    el("div", {class: "conteo"},
      el("div", {}, el("b", {text: `${nAyer}/${total}`}), "Ayer"),
      el("div", {class: "hoy"}, bHoy, "Hoy")),
    el("ul", {class: "tareas"},
      el("li", {class: "cabecera", "aria-hidden": "true"}, el("span", {text: "Tarea"}), el("span", {text: "Ayer"}), el("span", {text: "Hoy"})),
      ...filas));
}

function titulares(secciones){
  const cont = $("#titulares");
  const llenas = secciones.filter(s => s.items.length);
  if (!llenas.length) {
    cont.append(el("p", {class: "vacio", text: "No llegaron titulares en esta actualización. Se vuelven a cargar mañana a las 6:00."}));
    return;
  }
  llenas.forEach(s => {
    const destacada = /^[\u{1F331}\u{1F69C}]/u.test(s.nombre);
    cont.append(el("section", {class: "seccion" + (destacada ? " destacada" : "")},
      el("h2", {text: s.nombre}),
      el("ul", {class: "noticias"}, ...s.items.map(n => el("li", {},
        el("a", {href: n.link, target: "_blank", rel: "noopener", text: n.titulo}),
        n.bajada ? el("p", {text: n.bajada}) : null,
        el("span", {class: "fuente", text: n.fuente}))))));
  });
}

encabezado();
const dia = $("#dia");
if (DATA.boca && DATA.boca.length) dia.append(...bloqueBoca(DATA.boca));
if (DATA.clima) dia.append(bloqueClima(DATA.clima));
dia.append(bloqueAgenda(DATA.eventos), bloqueTareas(DATA.tareas), bloqueChecklist(DATA.checklist));
titulares(DATA.secciones);
</script>
</body>
</html>
"""


def main():
    ahora = dt.datetime.now(TZ)
    hoy = ahora.date()
    hoy_iso = hoy.isoformat()

    secciones = armar_secciones()
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

    with open(os.path.join(HERE, "checklist.json"), encoding="utf-8") as f:
        checklist = json.load(f)

    data = {
        "generado": ahora.strftime("%d/%m/%Y %H:%M"),
        "fecha": hoy_iso,
        "checklist": checklist,
        "clima": clima_hoy,
        "boca": boca,
        "eventos": [e for e in eventos if e["dia"] == hoy_iso],
        "tareas": tareas,
        "secciones": secciones,
    }
    salida = PLANTILLA.replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(salida)
    print(f"listo: {sum(len(s['items']) for s in secciones)} titulares, "
          f"{len(data['eventos'])} eventos hoy, "
          f"{'sin conexión a' if tareas is None else len(tareas)} tareas de Google, "
          f"{len(boca)} partido(s) de Boca")


if __name__ == "__main__":
    main()
