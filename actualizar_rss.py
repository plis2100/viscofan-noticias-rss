import html
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


URL_ES = "https://www.viscofan.com/es/noticias/noticias"
URL_EN = "https://www.viscofan.com/news/news"
DOMINIO = "https://www.viscofan.com"

URL_RSS = (
    "https://raw.githubusercontent.com/"
    "plis2100/viscofan-noticias-rss/main/rss.xml"
)

ARCHIVO_RSS = Path("rss.xml")
PRIMER_ANIO = 2014
MAXIMO_NOTICIAS = 3000

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
}

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def ejecucion_permitida():
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("Ejecución manual: se ignora el horario.")
        return True

    ahora = datetime.now(ZoneInfo("Europe/Madrid"))

    print(
        "Hora de España:",
        ahora.strftime("%d/%m/%Y %H:%M:%S %Z"),
    )

    if ahora.weekday() >= 5:
        print("Es sábado o domingo. No se actualiza.")
        return False

    if ahora.hour not in {7, 13, 19}:
        print("Solo se ejecuta a las 07:00, 13:00 y 19:00.")
        return False

    return True


def limpiar_texto(texto):
    if not texto:
        return ""

    texto = html.unescape(str(texto))
    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def limpiar_url(url):
    partes = urlsplit(url)

    return urlunsplit(
        (
            partes.scheme.lower(),
            partes.netloc.lower(),
            partes.path.rstrip("/"),
            "",
            "",
        )
    )


def url_jina(url):
    """
    Jina Reader permite leer la página cuando el servidor
    de Viscofan rechaza directamente la IP de GitHub.
    """
    if url.startswith("https://"):
        destino = "http://" + url[len("https://"):]
    elif url.startswith("http://"):
        destino = url
    else:
        destino = "http://" + url

    return "https://r.jina.ai/" + destino


def descargar_directamente(session, url):
    ultimo_error = None

    for intento in range(1, 3):
        try:
            respuesta = session.get(
                url,
                timeout=35,
                allow_redirects=True,
            )
            respuesta.raise_for_status()

            respuesta.encoding = (
                respuesta.apparent_encoding or "utf-8"
            )

            if len(respuesta.text) < 300:
                raise RuntimeError(
                    "La respuesta recibida es demasiado corta."
                )

            print(
                f"Descarga directa correcta: {url} "
                f"({len(respuesta.content)} bytes)"
            )

            return {
                "contenido": respuesta.text,
                "formato": "html",
                "url_origen": url,
            }

        except Exception as error:
            ultimo_error = error

            print(
                f"Acceso directo {intento}/2 fallido: "
                f"{url}: {error}",
                file=sys.stderr,
            )

            time.sleep(intento)

    raise RuntimeError(str(ultimo_error))


def descargar_con_lector(session, url):
    lector = url_jina(url)
    ultimo_error = None

    for intento in range(1, 4):
        try:
            respuesta = session.get(
                lector,
                timeout=60,
                allow_redirects=True,
                headers={
                    "User-Agent": CABECERAS["User-Agent"],
                    "Accept": "text/plain,text/markdown,*/*",
                },
            )
            respuesta.raise_for_status()
            respuesta.encoding = "utf-8"

            contenido = respuesta.text.strip()

            if len(contenido) < 200:
                raise RuntimeError(
                    "El lector devolvió una respuesta vacía."
                )

            print(
                f"Acceso alternativo correcto: {url} "
                f"({len(contenido)} caracteres)"
            )

            return {
                "contenido": contenido,
                "formato": "markdown",
                "url_origen": url,
            }

        except Exception as error:
            ultimo_error = error

            print(
                f"Acceso alternativo {intento}/3 fallido: "
                f"{url}: {error}",
                file=sys.stderr,
            )

            if intento < 3:
                time.sleep(intento * 2)

    raise RuntimeError(str(ultimo_error))


def descargar(session, url):
    try:
        return descargar_directamente(session, url)

    except Exception as error_directo:
        print(
            f"Viscofan bloqueó el acceso directo a {url}: "
            f"{error_directo}",
            file=sys.stderr,
        )

    try:
        return descargar_con_lector(session, url)

    except Exception as error_alternativo:
        raise RuntimeError(
            f"Fallaron el acceso directo y el alternativo: "
            f"{error_alternativo}"
        )


def convertir_fecha(dia, mes, anio):
    try:
        fecha = datetime(
            int(anio),
            int(mes),
            int(dia),
            12,
            0,
            tzinfo=ZoneInfo("Europe/Madrid"),
        )

        return fecha.astimezone(timezone.utc)

    except (ValueError, TypeError):
        return None


def extraer_fecha(texto):
    texto = limpiar_texto(texto).lower()

    coincidencia = re.search(
        r"\b([0-3]?\d)[/\-.]([01]?\d)[/\-.]((?:19|20)\d{2})\b",
        texto,
    )

    if coincidencia:
        return convertir_fecha(
            coincidencia.group(1),
            coincidencia.group(2),
            coincidencia.group(3),
        )

    nombres_meses = "|".join(
        sorted(MESES.keys(), key=len, reverse=True)
    )

    coincidencia = re.search(
        rf"\b([0-3]?\d)\s*(?:/|\s+de\s+|\s+)"
        rf"({nombres_meses})"
        rf"\s*(?:/|\s+de\s+|\s+)"
        rf"((?:19|20)\d{{2}})\b",
        texto,
        flags=re.IGNORECASE,
    )

    if coincidencia:
        return convertir_fecha(
            coincidencia.group(1),
            MESES[coincidencia.group(2).lower()],
            coincidencia.group(3),
        )

    coincidencia = re.search(
        rf"\b({nombres_meses})\s+([0-3]?\d),?\s+"
        rf"((?:19|20)\d{{2}})\b",
        texto,
        flags=re.IGNORECASE,
    )

    if coincidencia:
        return convertir_fecha(
            coincidencia.group(2),
            MESES[coincidencia.group(1).lower()],
            coincidencia.group(3),
        )

    return None


def es_url_noticia(url):
    ruta = urlsplit(url).path.rstrip("/")

    patrones = [
        r"^/es/noticias/noticia/\d+$",
        r"^/news/new/\d+$",
    ]

    return any(
        re.match(patron, ruta, flags=re.IGNORECASE)
        for patron in patrones
    )


def convertir_a_url_espanola(url):
    """
    Si la noticia fue descubierta en la web inglesa,
    construye su dirección equivalente en español.
    """
    coincidencia = re.search(
        r"/(?:es/noticias/noticia|news/new)/(\d+)",
        url,
        flags=re.IGNORECASE,
    )

    if not coincidencia:
        return limpiar_url(url)

    identificador = coincidencia.group(1)

    return (
        f"{DOMINIO}/es/noticias/noticia/"
        f"{identificador}"
    )


def enlaces_desde_html(contenido, url_base):
    soup = BeautifulSoup(contenido, "html.parser")
    encontrados = {}

    for enlace in soup.find_all("a", href=True):
        url = limpiar_url(
            urljoin(url_base, enlace.get("href", ""))
        )

        if not es_url_noticia(url):
            continue

        url_es = convertir_a_url_espanola(url)

        titulo = limpiar_texto(
            enlace.get_text(" ", strip=True)
        )

        if len(titulo) < 8:
            titulo = limpiar_texto(
                enlace.get("title")
                or enlace.get("aria-label")
                or ""
            )

        encontrados[url_es] = titulo

    return encontrados


def enlaces_desde_markdown(contenido, url_base):
    encontrados = {}

    patron_enlace = re.compile(
        r"\[([^\]]*)\]\(([^)\s]+)\)"
    )

    for titulo, href in patron_enlace.findall(contenido):
        href = html.unescape(href).strip()
        url = limpiar_url(urljoin(url_base, href))

        if not es_url_noticia(url):
            continue

        url_es = convertir_a_url_espanola(url)
        titulo = limpiar_texto(titulo)

        encontrados[url_es] = titulo

    # También detecta direcciones mostradas sin sintaxis Markdown.
    patron_url = re.compile(
        r"https?://(?:www\.)?viscofan\.com/"
        r"(?:es/noticias/noticia|news/new)/\d+",
        flags=re.IGNORECASE,
    )

    for url in patron_url.findall(contenido):
        url_es = convertir_a_url_espanola(url)

        if url_es not in encontrados:
            encontrados[url_es] = ""

    # Direcciones relativas que pueda entregar el lector.
    patron_relativo = re.compile(
        r"/(?:es/noticias/noticia|news/new)/\d+",
        flags=re.IGNORECASE,
    )

    for ruta in patron_relativo.findall(contenido):
        url = urljoin(url_base, ruta)
        url_es = convertir_a_url_espanola(url)

        if url_es not in encontrados:
            encontrados[url_es] = ""

    return encontrados


def obtener_listados():
    anio_actual = datetime.now(
        ZoneInfo("Europe/Madrid")
    ).year

    listados = [
        URL_ES,
        "https://www.viscofan.com/es/noticias",
        URL_EN,
        "https://www.viscofan.com/news",
    ]

    for anio in range(anio_actual, PRIMER_ANIO - 1, -1):
        listados.extend(
            [
                f"{URL_ES}/{anio}",
                f"{URL_EN}/{anio}",
            ]
        )

    return listados


def localizar_noticias(session):
    encontradas = {}
    listados_correctos = 0
    fallos_consecutivos_es = 0
    fallos_consecutivos_en = 0

    for url_listado in obtener_listados():
        try:
            resultado = descargar(
                session,
                url_listado,
            )
            listados_correctos += 1

            if "/es/" in url_listado:
                fallos_consecutivos_es = 0
            else:
                fallos_consecutivos_en = 0

        except Exception as error:
            print(
                f"AVISO: listado no disponible: "
                f"{url_listado}: {error}",
                file=sys.stderr,
            )

            if "/es/" in url_listado:
                fallos_consecutivos_es += 1
            else:
                fallos_consecutivos_en += 1

            continue

        if resultado["formato"] == "html":
            enlaces = enlaces_desde_html(
                resultado["contenido"],
                url_listado,
            )
        else:
            enlaces = enlaces_desde_markdown(
                resultado["contenido"],
                url_listado,
            )

        nuevos = 0

        for url, titulo in enlaces.items():
            if url not in encontradas:
                encontradas[url] = titulo
                nuevos += 1

            elif len(titulo) > len(encontradas[url]):
                encontradas[url] = titulo

        print(
            f"Listado procesado: {url_listado}. "
            f"Nuevos enlaces: {nuevos}"
        )

        time.sleep(0.2)

    print(
        f"Listados descargados correctamente: "
        f"{listados_correctos}"
    )

    return encontradas


def titulo_desde_html(soup, titulo_listado, url):
    titulo = ""

    elemento = soup.select_one(
        "main h1, article h1, "
        ".news-detail h1, .noticia h1, h1"
    )

    if elemento:
        titulo = limpiar_texto(
            elemento.get_text(" ", strip=True)
        )

    if not titulo:
        meta = soup.select_one(
            "meta[property='og:title']"
        )

        if meta:
            titulo = limpiar_texto(
                meta.get("content", "")
            )

    if not titulo:
        titulo = limpiar_texto(titulo_listado)

    if not titulo:
        numero = url.rstrip("/").split("/")[-1]
        titulo = f"Noticia de Viscofan {numero}"

    titulo = re.sub(
        r"\s*[|–-]\s*Viscofan.*$",
        "",
        titulo,
        flags=re.IGNORECASE,
    )

    return titulo.strip()


def titulo_desde_markdown(contenido, titulo_listado, url):
    patrones = [
        r"(?m)^#\s+(.+)$",
        r"(?m)^Title:\s*(.+)$",
    ]

    for patron in patrones:
        coincidencia = re.search(patron, contenido)

        if coincidencia:
            titulo = limpiar_texto(
                coincidencia.group(1)
            )

            titulo = re.sub(
                r"\s*[|–-]\s*Viscofan.*$",
                "",
                titulo,
                flags=re.IGNORECASE,
            ).strip()

            if len(titulo) >= 8:
                return titulo

    if titulo_listado:
        return limpiar_texto(titulo_listado)

    numero = url.rstrip("/").split("/")[-1]

    return f"Noticia de Viscofan {numero}"


def fecha_desde_html(soup):
    selectores = [
        "meta[property='article:published_time']",
        "meta[name='date']",
        "meta[name='publication_date']",
        "meta[itemprop='datePublished']",
        "time[datetime]",
    ]

    for selector in selectores:
        elemento = soup.select_one(selector)

        if not elemento:
            continue

        valor = (
            elemento.get("content")
            or elemento.get("datetime")
            or elemento.get_text(" ", strip=True)
        )

        if not valor:
            continue

        try:
            fecha = datetime.fromisoformat(
                valor.strip().replace("Z", "+00:00")
            )

            if fecha.tzinfo is None:
                fecha = fecha.replace(
                    tzinfo=ZoneInfo("Europe/Madrid")
                )

            return fecha.astimezone(timezone.utc)

        except (ValueError, TypeError):
            fecha = extraer_fecha(valor)

            if fecha:
                return fecha

    return extraer_fecha(
        soup.get_text(" ", strip=True)
    )


def descripcion_desde_html(soup):
    contenedor = (
        soup.select_one(
            "main article, article, .news-detail, "
            ".noticia, .detalle-noticia, main"
        )
        or soup.body
        or soup
    )

    for elemento in contenedor.select(
        "script, style, nav, form, button, "
        "footer, aside, noscript"
    ):
        elemento.decompose()

    fragmentos = []
    longitud = 0

    for elemento in contenedor.find_all(
        ["p", "li", "h2", "h3"]
    ):
        texto = limpiar_texto(
            elemento.get_text(" ", strip=True)
        )

        if len(texto) < 25:
            continue

        if texto in fragmentos:
            continue

        fragmentos.append(texto)
        longitud += len(texto)

        if longitud >= 2500:
            break

    descripcion = " ".join(fragmentos)

    if len(descripcion) < 50:
        meta = soup.select_one(
            "meta[property='og:description'], "
            "meta[name='description']"
        )

        if meta:
            descripcion = limpiar_texto(
                meta.get("content", "")
            )

    return limitar_descripcion(descripcion)


def descripcion_desde_markdown(contenido, titulo):
    texto = contenido

    texto = re.sub(
        r"(?m)^(Title|URL Source|Published Time|Markdown Content):.*$",
        "",
        texto,
    )
    texto = re.sub(r"!\[[^\]]*]\([^)]+\)", "", texto)
    texto = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", texto)
    texto = re.sub(r"(?m)^#{1,6}\s*", "", texto)
    texto = re.sub(r"[*_`>|]+", " ", texto)
    texto = limpiar_texto(texto)

    if titulo and texto.startswith(titulo):
        texto = texto[len(titulo):].strip(" :-")

    return limitar_descripcion(texto)


def limitar_descripcion(descripcion):
    descripcion = limpiar_texto(descripcion)

    if len(descripcion) > 3000:
        descripcion = (
            descripcion[:2997].rsplit(" ", 1)[0]
            + "..."
        )

    return descripcion or "Noticia publicada por Viscofan."


def imagen_desde_html(soup, url):
    for selector in [
        "meta[property='og:image']",
        "meta[name='twitter:image']",
    ]:
        elemento = soup.select_one(selector)

        if elemento and elemento.get("content"):
            imagen = urljoin(
                url,
                elemento["content"].strip(),
            )

            if imagen.startswith("http"):
                return imagen

    return ""


def documentos_desde_html(soup, url):
    documentos = []
    vistos = set()

    for enlace in soup.find_all("a", href=True):
        absoluta = urljoin(
            url,
            enlace.get("href", "").strip(),
        )

        if not re.search(
            r"\.(pdf|doc|docx|xls|xlsx|zip)(?:$|\?)",
            absoluta,
            flags=re.IGNORECASE,
        ):
            continue

        if absoluta in vistos:
            continue

        vistos.add(absoluta)

        texto = limpiar_texto(
            enlace.get_text(" ", strip=True)
        ) or "Descargar documento"

        documentos.append(
            f'<a href="{html.escape(absoluta, quote=True)}">'
            f"{html.escape(texto)}</a>"
        )

    return documentos


def intentar_detalle(session, url_es):
    identificador = url_es.rstrip("/").split("/")[-1]

    candidatos = [
        url_es,
        f"{DOMINIO}/news/new/{identificador}",
    ]

    errores = []

    for candidato in candidatos:
        try:
            return descargar(session, candidato)

        except Exception as error:
            errores.append(
                f"{candidato}: {error}"
            )

    raise RuntimeError(" | ".join(errores))


def procesar_noticia(session, url, titulo_listado):
    try:
        resultado = intentar_detalle(session, url)
        contenido = resultado["contenido"]

        if resultado["formato"] == "html":
            soup = BeautifulSoup(
                contenido,
                "html.parser",
            )

            titulo = titulo_desde_html(
                soup,
                titulo_listado,
                url,
            )
            fecha = fecha_desde_html(soup)
            descripcion = descripcion_desde_html(soup)
            imagen = imagen_desde_html(soup, url)
            documentos = documentos_desde_html(
                soup,
                url,
            )

        else:
            titulo = titulo_desde_markdown(
                contenido,
                titulo_listado,
                url,
            )
            fecha = extraer_fecha(contenido)
            descripcion = descripcion_desde_markdown(
                contenido,
                titulo,
            )
            imagen = ""
            documentos = []

        if not fecha:
            print(
                f"AVISO: noticia sin fecha: {url}",
                file=sys.stderr,
            )
            return None

        descripcion_html = (
            f"<p>{html.escape(descripcion)}</p>"
        )

        if documentos:
            descripcion_html += (
                "<p><strong>Documentos:</strong><br>"
                + "<br>".join(documentos)
                + "</p>"
            )

        if imagen:
            descripcion_html = (
                f'<p><img src="'
                f'{html.escape(imagen, quote=True)}" '
                f'alt="{html.escape(titulo, quote=True)}">'
                f"</p>"
                + descripcion_html
            )

        return {
            "titulo": titulo,
            "url": url,
            "fecha": fecha,
            "descripcion": descripcion_html,
            "imagen": imagen,
        }

    except Exception as error:
        print(
            f"AVISO: no se procesó {url}: {error}",
            file=sys.stderr,
        )
        return None


def leer_rss_anterior():
    anteriores = {}

    if not ARCHIVO_RSS.exists():
        return anteriores

    try:
        raiz = ET.parse(ARCHIVO_RSS).getroot()
        canal = raiz.find("channel")

        if canal is None:
            return anteriores

        for item in canal.findall("item"):
            titulo = limpiar_texto(
                item.findtext("title", "")
            )
            url = limpiar_url(
                item.findtext("link", "")
            )
            descripcion = item.findtext(
                "description",
                "",
            )
            fecha_texto = item.findtext(
                "pubDate",
                "",
            )

            if not titulo or not url:
                continue

            try:
                fecha = parsedate_to_datetime(
                    fecha_texto
                )

                if fecha.tzinfo is None:
                    fecha = fecha.replace(
                        tzinfo=timezone.utc
                    )

                fecha = fecha.astimezone(timezone.utc)

            except (ValueError, TypeError):
                fecha = datetime(
                    1970,
                    1,
                    1,
                    tzinfo=timezone.utc,
                )

            enclosure = item.find("enclosure")
            imagen = ""

            if enclosure is not None:
                imagen = enclosure.get("url", "")

            anteriores[url] = {
                "titulo": titulo,
                "url": url,
                "fecha": fecha,
                "descripcion": descripcion,
                "imagen": imagen,
            }

    except Exception as error:
        print(
            f"AVISO: RSS anterior no válido: {error}",
            file=sys.stderr,
        )

    return anteriores


def escribir_rss(noticias):
    ET.register_namespace(
        "atom",
        "http://www.w3.org/2005/Atom",
    )

    rss = ET.Element(
        "rss",
        {"version": "2.0"},
    )
    canal = ET.SubElement(rss, "channel")

    ET.SubElement(canal, "title").text = (
        "Noticias de Viscofan"
    )
    ET.SubElement(canal, "link").text = URL_ES
    ET.SubElement(canal, "description").text = (
        "Noticias, resultados y comunicaciones "
        "corporativas de Viscofan."
    )
    ET.SubElement(canal, "language").text = "es-ES"
    ET.SubElement(canal, "ttl").text = "360"
    ET.SubElement(canal, "lastBuildDate").text = (
        format_datetime(datetime.now(timezone.utc))
    )

    atom = ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom.set("href", URL_RSS)
    atom.set("rel", "self")
    atom.set("type", "application/rss+xml")

    for noticia in noticias[:MAXIMO_NOTICIAS]:
        item = ET.SubElement(canal, "item")

        ET.SubElement(item, "title").text = (
            noticia["titulo"]
        )
        ET.SubElement(item, "link").text = (
            noticia["url"]
        )
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true"},
        ).text = noticia["url"]

        ET.SubElement(item, "pubDate").text = (
            format_datetime(
                noticia["fecha"].astimezone(
                    timezone.utc
                )
            )
        )
        ET.SubElement(item, "description").text = (
            noticia["descripcion"]
        )

        if noticia.get("imagen"):
            ET.SubElement(
                item,
                "enclosure",
                {
                    "url": noticia["imagen"],
                    "type": "image/jpeg",
                },
            )

    ET.indent(rss, space="  ")

    ET.ElementTree(rss).write(
        ARCHIVO_RSS,
        encoding="utf-8",
        xml_declaration=True,
    )


def main():
    if not ejecucion_permitida():
        return

    session = requests.Session()
    session.headers.update(CABECERAS)

    enlaces = localizar_noticias(session)

    print(
        f"Enlaces únicos encontrados: {len(enlaces)}"
    )

    nuevas = {}

    for numero, (url, titulo) in enumerate(
        enlaces.items(),
        start=1,
    ):
        print(
            f"Procesando {numero}/{len(enlaces)}: {url}"
        )

        noticia = procesar_noticia(
            session,
            url,
            titulo,
        )

        if noticia:
            nuevas[noticia["url"]] = noticia

        time.sleep(0.2)

    anteriores = leer_rss_anterior()

    todas = dict(anteriores)
    todas.update(nuevas)

    ordenadas = sorted(
        todas.values(),
        key=lambda noticia: noticia["fecha"],
        reverse=True,
    )

    print(f"Noticias recuperadas ahora: {len(nuevas)}")
    print(
        f"Noticias conservadas del RSS anterior: "
        f"{len(anteriores)}"
    )
    print(
        f"Total de noticias en el RSS: {len(ordenadas)}"
    )

    if not ordenadas:
        raise RuntimeError(
            "No fue posible recuperar noticias de Viscofan "
            "por acceso directo ni alternativo."
        )

    escribir_rss(ordenadas)

    print("RSS de Viscofan generado correctamente.")


if __name__ == "__main__":
    main()
