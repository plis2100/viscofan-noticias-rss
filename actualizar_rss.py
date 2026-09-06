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


URL_PRINCIPAL = "https://www.viscofan.com/es/noticias/noticias"
URL_ALTERNATIVA = "https://www.viscofan.com/es/noticias"
DOMINIO = "https://www.viscofan.com"
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
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
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
}


def ejecucion_permitida():
    """
    Las ejecuciones manuales siempre funcionan.

    Las programadas solo generan el RSS de lunes a viernes
    a las 07:00, 13:00 y 19:00 de España.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("Ejecución manual: se ignora el horario.")
        return True

    ahora = datetime.now(ZoneInfo("Europe/Madrid"))

    print(
        "Hora peninsular española:",
        ahora.strftime("%d/%m/%Y %H:%M:%S %Z"),
    )

    if ahora.weekday() >= 5:
        print("Es sábado o domingo. No se actualiza.")
        return False

    if ahora.hour not in {7, 13, 19}:
        print(
            "No corresponde ejecutar ahora. "
            "Horarios: 07:00, 13:00 y 19:00."
        )
        return False

    return True


def limpiar_texto(texto):
    if not texto:
        return ""

    return re.sub(
        r"\s+",
        " ",
        html.unescape(str(texto)),
    ).strip()


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


def descargar(session, url):
    ultimo_error = None

    for intento in range(1, 5):
        try:
            respuesta = session.get(
                url,
                timeout=45,
                allow_redirects=True,
            )
            respuesta.raise_for_status()

            respuesta.encoding = (
                respuesta.apparent_encoding or "utf-8"
            )

            print(
                f"Descargada: {url} "
                f"({len(respuesta.content)} bytes)"
            )

            return respuesta.text

        except requests.RequestException as error:
            ultimo_error = error

            print(
                f"Intento {intento}/4 fallido para "
                f"{url}: {error}",
                file=sys.stderr,
            )

            if intento < 4:
                time.sleep(intento * 3)

    raise RuntimeError(
        f"No se pudo descargar {url}: {ultimo_error}"
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

    coincidencia = re.search(
        r"\b([0-3]?\d)\s*(?:/|\s+de\s+|\s+)"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|setiembre|octubre|noviembre|diciembre)"
        r"\s*(?:/|\s+de\s+|\s+)"
        r"((?:19|20)\d{2})\b",
        texto,
    )

    if coincidencia:
        return convertir_fecha(
            coincidencia.group(1),
            MESES[coincidencia.group(2)],
            coincidencia.group(3),
        )

    return None


def obtener_urls_listados():
    anio_actual = datetime.now(
        ZoneInfo("Europe/Madrid")
    ).year

    urls = [
        URL_PRINCIPAL,
        URL_ALTERNATIVA,
    ]

    # Viscofan organiza el archivo mediante direcciones anuales:
    # /es/noticias/noticias/2026
    for anio in range(anio_actual, PRIMER_ANIO - 1, -1):
        urls.append(
            f"{URL_PRINCIPAL}/{anio}"
        )

    return urls


def es_enlace_noticia(url):
    partes = urlsplit(url)

    if partes.netloc not in {
        "www.viscofan.com",
        "viscofan.com",
    }:
        return False

    return bool(
        re.search(
            r"/es/noticias/noticia/\d+$",
            partes.path.rstrip("/"),
            flags=re.IGNORECASE,
        )
    )


def localizar_noticias(session):
    encontradas = {}
    paginas_descargadas = 0

    for url_listado in obtener_urls_listados():
        try:
            contenido = descargar(
                session,
                url_listado,
            )
            paginas_descargadas += 1

        except Exception as error:
            print(
                f"AVISO: no se pudo descargar el listado "
                f"{url_listado}: {error}",
                file=sys.stderr,
            )
            continue

        soup = BeautifulSoup(
            contenido,
            "html.parser",
        )

        contador = 0

        for enlace in soup.find_all("a", href=True):
            href = enlace.get("href", "").strip()

            if not href:
                continue

            url = limpiar_url(
                urljoin(url_listado, href)
            )

            if not es_enlace_noticia(url):
                continue

            titulo = limpiar_texto(
                enlace.get_text(" ", strip=True)
            )

            if len(titulo) < 8:
                titulo = limpiar_texto(
                    enlace.get("title")
                    or enlace.get("aria-label")
                    or ""
                )

            if url not in encontradas:
                encontradas[url] = titulo
                contador += 1

            elif len(titulo) > len(encontradas[url]):
                encontradas[url] = titulo

        print(
            f"Listado {url_listado}: "
            f"{contador} enlaces nuevos."
        )

        time.sleep(0.25)

    print(
        f"Listados descargados correctamente: "
        f"{paginas_descargadas}"
    )

    return encontradas


def buscar_fecha_detalle(soup):
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

    for selector in [
        "main time",
        "article time",
        ".fecha",
        ".date",
        ".news-date",
        ".noticia-fecha",
    ]:
        for elemento in soup.select(selector):
            fecha = extraer_fecha(
                elemento.get_text(" ", strip=True)
            )

            if fecha:
                return fecha

    return extraer_fecha(
        soup.get_text(" ", strip=True)
    )


def extraer_titulo(soup, titulo_listado, url):
    titulo = ""

    elemento = soup.select_one(
        "main h1, article h1, .noticia h1, h1"
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
        identificador = (
            urlsplit(url).path.rstrip("/").split("/")[-1]
        )
        titulo = f"Noticia de Viscofan {identificador}"

    titulo = re.sub(
        r"\s*[|–-]\s*Viscofan.*$",
        "",
        titulo,
        flags=re.IGNORECASE,
    ).strip()

    return titulo


def obtener_contenedor(soup):
    selectores = [
        "main article",
        "article",
        ".news-detail",
        ".noticia",
        ".detalle-noticia",
        ".contenido-noticia",
        ".content",
        "main",
    ]

    for selector in selectores:
        contenedor = soup.select_one(selector)

        if contenedor:
            return contenedor

    return soup.body or soup


def extraer_descripcion(soup):
    contenedor = obtener_contenedor(soup)

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

        minusculas = texto.lower()

        exclusiones = (
            "política de privacidad",
            "política de cookies",
            "todos los derechos reservados",
            "seleccione un año",
            "aviso legal",
        )

        if any(
            excluido in minusculas
            for excluido in exclusiones
        ):
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

    if len(descripcion) > 3000:
        descripcion = (
            descripcion[:2997].rsplit(" ", 1)[0]
            + "..."
        )

    return descripcion or "Noticia publicada por Viscofan."


def extraer_imagen(soup, url):
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

    contenedor = obtener_contenedor(soup)
    imagen = contenedor.find("img", src=True)

    if imagen:
        return urljoin(
            url,
            imagen.get("src", ""),
        )

    return ""


def extraer_documentos(soup, url):
    documentos = []
    vistos = set()

    for enlace in soup.find_all("a", href=True):
        absoluta = urljoin(
            url,
            enlace.get("href", "").strip(),
        )
        texto = limpiar_texto(
            enlace.get_text(" ", strip=True)
        )

        es_documento = re.search(
            r"\.(pdf|doc|docx|xls|xlsx|zip)(?:$|\?)",
            absoluta,
            flags=re.IGNORECASE,
        )

        es_descarga = (
            "descargar" in texto.lower()
            or "download" in texto.lower()
        )

        if not es_documento and not es_descarga:
            continue

        if absoluta in vistos:
            continue

        vistos.add(absoluta)

        if not texto:
            texto = "Descargar documento"

        documentos.append(
            f'<a href="{html.escape(absoluta, quote=True)}">'
            f"{html.escape(texto)}</a>"
        )

    return documentos


def procesar_noticia(session, url, titulo_listado):
    try:
        contenido = descargar(session, url)
        soup = BeautifulSoup(
            contenido,
            "html.parser",
        )

        titulo = extraer_titulo(
            soup,
            titulo_listado,
            url,
        )
        fecha = buscar_fecha_detalle(soup)
        descripcion = extraer_descripcion(soup)
        imagen = extraer_imagen(soup, url)
        documentos = extraer_documentos(soup, url)

        if not fecha:
            print(
                f"AVISO: no se encontró la fecha: {url}",
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
            "url": limpiar_url(url),
            "fecha": fecha,
            "descripcion": descripcion_html,
            "imagen": imagen,
        }

    except Exception as error:
        print(
            f"AVISO: no se pudo procesar {url}: {error}",
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
                item.findtext("link", "").strip()
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

    except (ET.ParseError, OSError) as error:
        print(
            f"AVISO: no se pudo leer el RSS anterior: {error}",
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
    ET.SubElement(canal, "link").text = (
        URL_PRINCIPAL
    )
    ET.SubElement(canal, "description").text = (
        "Noticias, resultados financieros y comunicaciones "
        "corporativas del Grupo Viscofan."
    )
    ET.SubElement(canal, "language").text = "es-ES"
    ET.SubElement(canal, "lastBuildDate").text = (
        format_datetime(datetime.now(timezone.utc))
    )
    ET.SubElement(canal, "ttl").text = "360"

    atom = ET.SubElement(
        canal,
        "{http://www.w3.org/2005/Atom}link",
    )
    atom.set(
        "href",
        "https://raw.githubusercontent.com/"
        "plis2100/viscofan-noticias-rss/main/rss.xml",
    )
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

        time.sleep(0.25)

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
        f"Total de noticias en el RSS: "
        f"{len(ordenadas)}"
    )

    if not ordenadas:
        raise RuntimeError(
            "Viscofan no devolvió ninguna noticia y "
            "tampoco existe un RSS anterior."
        )

    escribir_rss(ordenadas)

    print("RSS de Viscofan generado correctamente.")


if __name__ == "__main__":
    main()
