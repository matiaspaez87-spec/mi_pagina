#!/usr/bin/env python3
"""Genera index.html (página diaria personal) y archive/AAAA-MM-DD.json.

Sin API de pago:
- Titulares: RSS (Google News y medios directos), filtrados por fuentes confiables.
- Clima: Open-Meteo (sin clave).
- Boca: marcador público de ESPN (endpoint no oficial, puede cambiar).
- Calendario: dirección secreta iCal de Google Calendar, en la variable CALENDAR_ICS_URL.
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
    "cooperativa.cl", "elmostrador.cl", "redagricola.com",
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


SECCIONES = [
    ("Chile", [gn("Chile when:1d", "CL")]),
    ("Argentina", [gn("Argentina when:1d", "AR")]),
    ("Global", [
        "https://feeds.bbci.co.uk/mundo/rss.xml",
        "https://rss.dw.com/rdf/rss-sp-all",
        "https://www.france24.com/es/rss",
    ]),
    ("Exportaciones agrícolas chilenas", [
        gn("exportaciones agrícolas Chile OR fruta chilena exportación OR cerezas exportación when:2d", "CL"),
    ]),
    ("Tecnología para el agro", [
        gn("tecnología agricultura OR agtech OR riego inteligente OR agricultura digital when:3d", "CL"),
    ]),
    ("Economía", [
        gn("dólar Chile OR cobre OR IPSA OR Banco Central when:1d", "CL"),
    ]),
    ("Salud y fitness", [
        gn("salud OR ejercicio OR fitness OR nutrición when:2d", "CL"),
    ]),
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
    for nombre, urls in SECCIONES:
        items = []
        for u in urls:
            items += leer_feed(u)
        items.sort(key=lambda x: x["fecha"] or minimo, reverse=True)

        vistos, sel = set(), []
        for it in items:
            clave = it["titulo"].lower()[:60]
            if not clave or clave in vistos:
                continue
            vistos.add(clave)
            sel.append({k: v for k, v in it.items() if k != "fecha"})
            if len(sel) == MAX_TITULARES:
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
<title>Mi día</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--acc:#2f6b4f;--line:#e2e0da}
@media (prefers-color-scheme: dark){:root{--bg:#161614;--card:#222220;--ink:#eceae4;--mute:#9a9891;--acc:#7fc4a0;--line:#34332f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:1.4rem;margin:.2rem 0 0}
h2{font-size:1.05rem;margin:0 0 .5rem;color:var(--acc)}
.muted{color:var(--mute);font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
.alerta{background:#fff3cd;color:#333;border-radius:12px;padding:12px;margin:12px 0}
@media (prefers-color-scheme: dark){.alerta{background:#4a3f10;color:#f3ecd0}}
ul{margin:0;padding-left:18px}
li{margin:.6rem 0}
li a{color:var(--ink);font-weight:600;text-decoration:none}
li a:hover{text-decoration:underline}
.bajada{color:var(--mute);font-size:.92rem;margin:.15rem 0 0}
.fuente{color:var(--mute);font-size:.8rem}
.clima{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;text-align:center}
.clima b{display:block;font-size:1.3rem}
.vs{width:100%;border-collapse:collapse}
.vs th{font-size:.8rem;color:var(--mute);font-weight:600;text-align:center;padding:4px 6px;border-bottom:1px solid var(--line)}
.vs th:first-child{text-align:left}
.vs td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:middle}
.vs td.c{text-align:center;width:60px}
.vs td.ok{color:var(--acc);font-weight:700}
.vs td.no{color:var(--mute)}
.vs input{width:20px;height:20px;cursor:pointer}
.vs tr.hecho td:first-child{text-decoration:line-through;color:var(--mute)}
.resumen{display:flex;gap:16px;margin:0 0 8px;font-size:.9rem}
.resumen b{color:var(--acc)}
</style>
</head>
<body>
<main>
<h1 id="fecha"></h1>
<p class="muted" id="generado"></p>
<div id="contenido"></div>
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
const fmtFecha = iso => { const t = new Date(iso + "T12:00:00").toLocaleDateString("es-CL", {weekday:"long", day:"numeric", month:"long"}); return t.charAt(0).toUpperCase() + t.slice(1); };
const celda = (valor, etiqueta) => el("div", {}, el("b", {text: valor}), el("span", {class: "muted", text: etiqueta}));

const leer = k => { try { return localStorage.getItem(k) === "1"; } catch (e) { return false; } };
const guardar = (k, v) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch (e) {} };

function render(d){
  const c = $("#contenido");
  c.replaceChildren();
  $("#fecha").textContent = fmtFecha(d.fecha);

  if (d.boca && d.boca.length) {
    c.append(el("div", {class: "alerta"},
      el("b", {text: "⚽ Partido de Boca"}),
      ...d.boca.map(p => el("div", {text: `${p.hora} · ${p.partido} · ${p.torneo}`}))));
  }

  if (d.clima) {
    c.append(el("div", {class: "card"},
      el("h2", {text: "Clima en Santiago"}),
      el("div", {class: "clima"},
        celda(`${d.clima.actual}°`, "Ahora"),
        celda(`${d.clima.min}° / ${d.clima.max}°`, "Mín / Máx"),
        celda(`${d.clima.lluvia}%`, "Prob. lluvia"))));
  }

  const cal = el("div", {class: "card"}, el("h2", {text: "Calendario de hoy"}));
  if (d.eventos.length) {
    cal.append(el("ul", {}, ...d.eventos.map(e =>
      el("li", {text: `${e.hora} · ${e.titulo}${e.lugar ? " · " + e.lugar : ""}`}))));
  } else {
    cal.append(el("p", {class: "muted", text: "Sin eventos en el calendario."}));
  }
  c.append(cal);

  // Checklist: ayer (solo lectura) vs hoy (marcable). Se guarda por texto de la tarea.
  const total = DATA.checklist.length;
  const nAyer = DATA.checklist.filter(t => leer(`chk|${d.ayer}|${t}`)).length;
  const bHoy = el("b", {});
  const resumen = el("div", {class: "resumen"},
    el("span", {}, "Ayer ", el("b", {text: `${nAyer}/${total}`})),
    el("span", {}, "Hoy ", bHoy));
  const contarHoy = () => { bHoy.textContent = `${DATA.checklist.filter(t => leer(`chk|${d.hoyChk}|${t}`)).length}/${total}`; };

  const filas = DATA.checklist.map(t => {
    const ayerOk = leer(`chk|${d.ayer}|${t}`);
    const claveHoy = `chk|${d.hoyChk}|${t}`;
    const cb = el("input", {type: "checkbox", "aria-label": t});
    cb.checked = leer(claveHoy);
    const tr = el("tr", {class: cb.checked ? "hecho" : ""},
      el("td", {text: t}),
      el("td", {class: "c " + (ayerOk ? "ok" : "no"), text: ayerOk ? "✓" : "✗"}),
      el("td", {class: "c"}, cb));
    cb.addEventListener("change", () => {
      guardar(claveHoy, cb.checked);
      tr.classList.toggle("hecho", cb.checked);
      contarHoy();
    });
    return tr;
  });
  contarHoy();
  c.append(el("div", {class: "card"},
    el("h2", {text: "Checklist: ayer vs hoy"}),
    resumen,
    el("table", {class: "vs"},
      el("thead", {}, el("tr", {}, el("th", {text: "Tarea"}), el("th", {text: "Ayer"}), el("th", {text: "Hoy"}))),
      el("tbody", {}, ...filas))));

  const hayNoticias = d.secciones.some(s => s.items.length);
  if (hayNoticias) c.append(el("h2", {text: "Titulares", style: "margin:18px 0 0"}));
  d.secciones.forEach(s => {
    if (!s.items.length) return;
    c.append(el("div", {class: "card"},
      el("h2", {text: s.nombre}),
      el("ul", {}, ...s.items.map(n => el("li", {},
        el("a", {href: n.link, target: "_blank", rel: "noopener", text: n.titulo}),
        n.bajada ? el("p", {class: "bajada", text: n.bajada}) : null,
        el("div", {class: "fuente", text: n.fuente}))))));
  });
  if (!hayNoticias) {
    c.append(el("div", {class: "card muted", text: "No se pudieron cargar titulares en esta actualización."}));
  }
}

// El checklist usa la fecha real del dispositivo, no la de la última actualización,
// para que "hoy" y "ayer" sean correctos aunque la página no se haya regenerado.
const isoLocal = f => `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-${String(f.getDate()).padStart(2, "0")}`;
const ahora = new Date();
const ayerF = new Date(ahora); ayerF.setDate(ahora.getDate() - 1);
DATA.hoyChk = isoLocal(ahora);
DATA.ayer = isoLocal(ayerF);
$("#generado").textContent = "Actualizado " + DATA.generado;
render(DATA);
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

    try:
        eventos = leer_calendario(
            dt.datetime.combine(hoy, dt.time(0), tzinfo=TZ),
            dt.datetime.combine(hoy + dt.timedelta(days=1), dt.time(0), tzinfo=TZ),
        )
    except Exception as e:  # noqa: BLE001
        aviso(f"calendario no disponible: {e}")
        eventos = []

    with open(os.path.join(HERE, "checklist.json"), encoding="utf-8") as f:
        checklist = json.load(f)

    data = {
        "generado": ahora.strftime("%d/%m/%Y %H:%M"),
        "fecha": hoy_iso,
        "checklist": checklist,
        "clima": clima_hoy,
        "boca": boca,
        "eventos": [e for e in eventos if e["dia"] == hoy_iso],
        "secciones": secciones,
    }
    salida = PLANTILLA.replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
        f.write(salida)
    print(f"listo: {sum(len(s['items']) for s in secciones)} titulares, "
          f"{len(data['eventos'])} eventos hoy, {len(boca)} partido(s) de Boca")


if __name__ == "__main__":
    main()
