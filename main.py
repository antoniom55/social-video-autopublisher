# -*- coding: utf-8 -*-
"""
PAZ Y PERSPECTIVA - Generador y publicador automatico de reels diarios.
Formato v2 (2026-07-23): SERIE (rotulo + progreso) + frase-titulo + DESARROLLO narrado
y SUBTITULADO sincronizado + plantilla renovada (viñeta, tipografia doble) + CTA por cluster.
El desarrollo sale del campo `voz` (ya existente). Voz TTS por serie.

Flujo: elegir reflexion (en orden de serie) -> fondo (Pexels/Pixabay) -> reel
       -> subir a hosting -> publicar IG + FB + YT -> limpiar -> registrar.
Uso:
  python main.py          (corrida real: genera y publica)
  python main.py --test   (solo genera y abre el video; NO publica)
"""
import json, os, sys, time, shutil, subprocess, wave, random, re, glob
from datetime import datetime
import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
sys.stdout.reconfigure(encoding="utf-8")

# ----------------- Config -----------------
BASE = os.path.dirname(os.path.abspath(__file__))
TMP  = os.path.join(BASE, "tmp")
os.makedirs(TMP, exist_ok=True)
REFL = os.path.join(BASE, "reflexiones.json")
VIDEOS_USADOS = os.path.join(BASE, "videos_usados.json")
ESTADO = os.path.join(BASE, "estado_publicacion.json")
LOG = os.path.join(BASE, "bitacora.log")

W, H, FPS, SR = 1080, 1920, 24, 44100
# Formato B (sin voz): tiempos por LECTURA. TITULO = cuanto se ve la frase sola antes del desarrollo.
LEAD, TITULO, GAP, PAUSA, TAIL = 0.5, 3.4, 0.6, 0.5, 2.8
MUSICA_DIR = os.path.join(BASE, "musica")   # pistas reales .mp3 (si vacio -> sintesis de respaldo)
BRAND = "Paz y Perspectiva"
HASHTAGS = ("#pazyperspectiva #calma #bienestar #mindfulness #reflexiondeldia "
            "#pazinterior #saludmental #crecimientopersonal #motivacion")

# Fuentes (Windows)
GEO  = r"C:\Windows\Fonts\georgia.ttf";  GEOI = r"C:\Windows\Fonts\georgiai.ttf"
GEOB = r"C:\Windows\Fonts\georgiab.ttf"; ARI  = r"C:\Windows\Fonts\arial.ttf"
SEGO = r"C:\Windows\Fonts\segoeui.ttf";  SEGOSB = r"C:\Windows\Fonts\seguisb.ttf"

# Orden de publicacion de las series (lo que mas rinde primero: calma/descanso/soltar)
ORDEN_SERIES = ["Ansiedad", "Descanso", "Soltar", "Paz", "Amor Propio",
                "Autoestima", "Límites", "Relaciones", "Perdón", "Comparación"]
MAPA_SERIE = {
    "Ansiedad": "SERENAR LA MENTE", "Descanso": "DARTE DESCANSO", "Soltar": "SOLTAR LO QUE PESA",
    "Paz": "ENCONTRAR LA PAZ", "Amor Propio": "AMOR PROPIO", "Autoestima": "HÁBLATE CON CARIÑO",
    "Límites": "PONER LÍMITES", "Relaciones": "VÍNCULOS SANOS", "Perdón": "PERDONAR PARA SOLTAR",
    "Comparación": "DEJA DE COMPARARTE",
}
CTA_PANTALLA = {"comentario": "Comenta", "guardado": "Guárdalo", "suscribete": "Suscríbete"}
CTA_CAPTION_MAP = {
    "comentario": "Comenta un \U0001F64F si hoy decides soltar esa carga.",
    "guardado":   "Guárdalo. Tu 'yo' del futuro te lo agradecerá.",
    "suscribete": "Suscríbete. Mañana te envío otro recordatorio.",
}

DEVNULL = subprocess.DEVNULL
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
K = json.load(open(os.path.join(BASE, "keys.json"), encoding="utf-8"))
PEXELS, PIXABAY = K["pexels"], K["pixabay"]
GV = "v21.0"; G = f"https://graph.facebook.com/{GV}"
TOKEN, IG, PAGE = K["meta_token"], K["meta_ig_user_id"], K["meta_page_id"]

def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def cargar(path, default=None):
    if not os.path.exists(path):
        return default
    return json.load(open(path, encoding="utf-8"))

def guardar(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ----------------- 1) Reflexion del dia (en orden de serie) -----------------
def _renumerar(refs):
    """Reasigna serie/serie_pos/serie_total a las PENDIENTES (temporada limpia por categoria)."""
    from collections import defaultdict
    por_cat = defaultdict(list)
    for r in refs:
        if not r.get("usado"):
            por_cat[r.get("categoria")].append(r)
    for cat, lst in por_cat.items():
        lst.sort(key=lambda r: r["id"]); total = len(lst)
        serie = MAPA_SERIE.get(cat, (cat or "REFLEXIONES").upper())
        for i, r in enumerate(lst, 1):
            r["serie"], r["serie_pos"], r["serie_total"] = serie, i, total

def elegir_reflexion():
    refs = cargar(REFL)
    pend = [r for r in refs if not r["usado"]]
    if not pend:
        log("Banco agotado -> reiniciando ciclo y renumerando series.")
        for r in refs:
            r["usado"] = False
        _renumerar(refs); guardar(REFL, refs)
        pend = refs[:]
    if any("serie" not in r for r in pend):   # red de seguridad (banco viejo)
        _renumerar(refs); guardar(REFL, refs)
        pend = [r for r in refs if not r["usado"]]
    orden = {c: i for i, c in enumerate(ORDEN_SERIES)}
    pend.sort(key=lambda r: (orden.get(r.get("categoria"), 99), r.get("serie_pos", 999), r["id"]))
    return refs, pend[0]

# ----------------- 2) Video de fondo (Pexels + Pixabay) -----------------
def _descargar(url, path):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(path, "wb") as o:
            for ch in r.iter_content(1 << 16):
                o.write(ch)

def conseguir_fondo(tema, usados):
    path = os.path.join(TMP, "bg_src.mp4")
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": PEXELS},
                         params={"query": tema, "orientation": "portrait",
                                 "size": "medium", "per_page": 15}, timeout=30)
        if r.status_code == 200:
            vids = r.json().get("videos", []); random.shuffle(vids)
            for v in vids:
                sid = f"pexels:{v['id']}"
                if sid in usados:
                    continue
                files = [f for f in v["video_files"] if f.get("file_type") == "video/mp4"
                         and (f.get("height") or 0) > (f.get("width") or 0)]
                if not files:
                    continue
                ge = [f for f in files if (f.get("width") or 0) >= 1080]
                f = min(ge, key=lambda x: x["width"]) if ge else max(files, key=lambda x: x["width"])
                _descargar(f["link"], path); return path, sid
    except Exception as e:
        log(f"Aviso Pexels: {e}")
    try:
        r = requests.get("https://pixabay.com/api/videos/",
                         params={"key": PIXABAY, "q": tema, "per_page": 20, "safesearch": "true"}, timeout=30)
        if r.status_code == 200:
            hits = r.json().get("hits", []); random.shuffle(hits)
            for h in hits:
                sid = f"pixabay:{h['id']}"
                if sid in usados:
                    continue
                vs = h["videos"]; file = vs.get("large") or vs.get("medium") or vs.get("small")
                if not file or not file.get("url"):
                    continue
                _descargar(file["url"], path); return path, sid
    except Exception as e:
        log(f"Aviso Pixabay: {e}")
    return None, None

# ----------------- 3) Audio (SIN voz: musica real + timing por LECTURA) -----------------
def segmentar(voz):
    """Divide el desarrollo en 1-3 segmentos (oraciones) para mostrar como texto."""
    parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', (voz or "").strip()) if p.strip()]
    if len(parts) > 3:
        parts = parts[:2] + [" ".join(parts[2:])]
    return parts

def _lectura(txt):
    """Segundos que se muestra un texto segun su longitud (lectura pausada de calma)."""
    return max(2.6, len(txt.split()) * 0.52 + 1.0)

def _cargar_mp3(path):
    wav = os.path.join(TMP, "music_src.wav")
    # loudnorm: nivel consistente y presente (-19 LUFS, calmado) entre pistas de distinta dinamica
    subprocess.run([FFMPEG, "-y", "-i", path, "-af", "loudnorm=I=-19:TP=-2.0:LRA=11",
                    "-ar", str(SR), "-ac", "2", wav], check=True, stdout=DEVNULL, stderr=DEVNULL)
    wf = wave.open(wav, "rb"); raw = wf.readframes(wf.getnframes()); wf.close()
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return a.reshape(-1, 2)

def _sintetizar(total):
    t = np.arange(total) / SR
    chords = [[110.0,164.81,220.0,329.63],[130.81,196.0,261.63,392.0],
              [146.83,220.0,293.66,440.0],[98.0,146.83,196.0,293.66],[123.47,185.0,246.94,369.99]]
    base = random.choice(chords); det = random.uniform(0.003,0.006); amps=[0.5,0.36,0.28,0.18,0.12]
    m = sum(amps[i%len(amps)]*(np.sin(2*np.pi*f*t)+0.5*np.sin(2*np.pi*f*(1+det)*t)) for i,f in enumerate(base))
    m = m*(0.7+0.3*np.sin(2*np.pi*random.uniform(0.05,0.10)*t)); m = m/np.max(np.abs(m))*0.075
    return np.column_stack([m, m])

def pista_musical(total):
    """Musica real de MUSICA_DIR (al azar, loop/recorte a 'total'). Si no hay/falla -> sintesis."""
    files = glob.glob(os.path.join(MUSICA_DIR, "*.mp3")) if os.path.isdir(MUSICA_DIR) else []
    music, nombre = None, "sintesis"
    if files:
        pick = random.choice(files)
        try:
            a = _cargar_mp3(pick)
            if len(a) < total:
                a = np.tile(a, (int(np.ceil(total/len(a))), 1))
            a = a[:total]; peak = np.max(np.abs(a)) or 1.0
            music = a if peak <= 0.97 else a / peak * 0.97   # loudnorm ya fijo el nivel
            nombre = os.path.basename(pick)
        except Exception as e:
            log(f"Aviso musica: {e} -> sintesis"); music = None
    if music is None:
        music = _sintetizar(total)
    env = np.ones(total); fi, fo = int(1.4*SR), int(0.8*SR)
    env[:fi] = np.linspace(0,1,fi); env[-fo:] = np.linspace(1,0,fo)
    music[:,0] *= env; music[:,1] *= env
    return music, nombre

def construir_pista(segmentos):
    """Timing por lectura (frase sola -> desarrollo -> CTA) + musica. Devuelve wav, D, seg, cta_start, nombre."""
    cursor = LEAD + TITULO + GAP; seg = []
    for s in segmentos:
        dur = _lectura(s); seg.append((cursor, cursor + dur)); cursor += dur + PAUSA
    cta_start = (seg[-1][1] if seg else LEAD + TITULO) + 0.4
    D = cta_start + TAIL; total = int(D * SR)
    music, nombre = pista_musical(total)
    mix = np.clip(music, -1, 1)
    wav = os.path.join(TMP, "audio.wav")
    ww = wave.open(wav, "wb"); ww.setnchannels(2); ww.setsampwidth(2); ww.setframerate(SR)
    ww.writeframes((mix*32767).astype(np.int16).tobytes()); ww.close()
    return wav, D, seg, cta_start, nombre

# ----------------- 4) Plantilla (PNG estatico + subtitulos + CTA) -----------------
def _font(cands, size):
    for p in cands:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()

def _wrap(text, font, maxw, probe):
    out, cur = [], ""
    for w in text.split():
        test = (cur+" "+w).strip()
        if probe.textlength(test, font=font) <= maxw: cur = test
        else:
            if cur: out.append(cur)
            cur = w
    if cur: out.append(cur)
    return out

def _spaced(d, cx, y, text, f, fill, sp):
    tot = sum(d.textlength(c, font=f)+sp for c in text)-sp; x = cx-tot/2
    for c in text:
        d.text((x, y), c, font=f, fill=fill); x += d.textlength(c, font=f)+sp

def _scrim(base, cx, cy, w, h, alpha=180, blur=85):
    """Mancha oscura eliptica difusa (sin bordes duros): da legibilidad SIN efecto 'caja'."""
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([cx-w/2, cy-h/2, cx+w/2, cy+h/2], fill=alpha)
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    black = Image.new("RGBA", (W, H), (0, 0, 0, 0)); black.putalpha(mask)
    return Image.alpha_composite(base, black)

def _sombra(txt, blur=7, mul=0.9):
    """Sombra/glow difusa a partir del alfa de la capa de texto."""
    a = txt.split()[3]
    if mul != 1: a = a.point(lambda v: int(v*mul))
    s = Image.new("RGBA", (W, H), (0, 0, 0, 0)); s.putalpha(a)
    return s.filter(ImageFilter.GaussianBlur(blur))

def crear_estatico(ref):
    """Rotulo de serie + barra de progreso + frase-titulo + marca (scrim difuso + sombra)."""
    serie = ref.get("serie", "PAZ Y PERSPECTIVA")
    pos, tot = ref.get("serie_pos", 1), ref.get("serie_total", 1)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    base = Image.new("RGBA", (W, H), (0,0,0,0))
    # medidas de la frase
    maxw = int(W*0.84)
    for size in (78,72,66,60,56,52):
        f_q = _font([GEO, ARI], size); lines = _wrap(ref["frase"], f_q, maxw, probe)
        if len(lines) <= 3: break
    asc, desc = f_q.getmetrics(); lh = int((asc+desc)*1.22); top = int(H*0.40)-lh*len(lines)//2
    tw = max(probe.textlength(l, font=f_q) for l in lines); bloque_h = lh*(len(lines)-1)+asc+desc
    # scrims difusos (mancha, no caja)
    base = _scrim(base, W/2, 200, 780, 250, alpha=115, blur=78)                        # serie
    base = _scrim(base, W/2, top+bloque_h/2, tw+320, bloque_h+250, alpha=170, blur=100)  # frase
    base = _scrim(base, W/2, H-172, 540, 180, alpha=105, blur=62)                      # marca
    # capa de texto
    txt = Image.new("RGBA", (W, H), (0,0,0,0)); d = ImageDraw.Draw(txt)
    _spaced(d, W/2, 168, serie, _font([SEGOSB, ARI], 34), (255,255,255,255), 6)
    bw, bx, by = 300, W/2-150, 262
    d.rounded_rectangle([bx, by, bx+bw, by+7], radius=4, fill=(255,255,255,80))
    d.rounded_rectangle([bx, by, bx+bw*pos/max(tot,1), by+7], radius=4, fill=(255,255,255,240))
    _spaced(d, W/2, 284, f"{pos:02d}  /  {tot:02d}", _font([SEGOSB, ARI], 24), (255,255,255,215), 3)
    y = top
    for l in lines:
        w = d.textlength(l, font=f_q); d.text((W/2-w/2, y), l, font=f_q, fill=(255,255,255,255)); y += lh
    f_b = _font([GEOI, GEO, ARI], 33)
    d.line([(W/2-42, H-196),(W/2+42, H-196)], fill=(255,255,255,175), width=2)
    bwd = d.textlength(BRAND, font=f_b); d.text((W/2-bwd/2, H-176), BRAND, font=f_b, fill=(255,255,255,240))
    # sombra + texto
    base = Image.alpha_composite(base, _sombra(txt, blur=7))
    base = Image.alpha_composite(base, txt)
    p = os.path.join(TMP, "estatico.png"); base.save(p); return p

def crear_sub(i, texto):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    maxw = int(W*0.82); f = _font([SEGOSB, SEGO, ARI], 50); lines = _wrap(texto, f, maxw, probe)
    asc, desc = f.getmetrics(); lh = int((asc+desc)*1.18); top = 1300
    tw = max(probe.textlength(l, font=f) for l in lines); bloque_h = lh*(len(lines)-1)+asc+desc
    base = _scrim(Image.new("RGBA",(W,H),(0,0,0,0)), W/2, top+bloque_h/2, tw+280, bloque_h+190, alpha=165, blur=88)
    txt = Image.new("RGBA", (W, H), (0,0,0,0)); d = ImageDraw.Draw(txt); y = top
    for l in lines:
        w = d.textlength(l, font=f); d.text((W/2-w/2, y), l, font=f, fill=(255,255,255,255)); y += lh
    base = Image.alpha_composite(base, _sombra(txt, blur=6))
    base = Image.alpha_composite(base, txt)
    p = os.path.join(TMP, f"sub{i}.png"); base.save(p); return p

def crear_cta(palabra):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    f = _font([GEOB, GEO, ARI], 62); top = 1300
    tw = probe.textlength(palabra, font=f); asc, desc = f.getmetrics()
    base = _scrim(Image.new("RGBA",(W,H),(0,0,0,0)), W/2, top+(asc+desc)/2, tw+300, asc+desc+190, alpha=160, blur=85)
    txt = Image.new("RGBA", (W, H), (0,0,0,0)); d = ImageDraw.Draw(txt)
    d.text((W/2-tw/2, top), palabra, font=f, fill=(255,255,255,255))
    base = Image.alpha_composite(base, _sombra(txt, blur=7))
    base = Image.alpha_composite(base, txt)
    p = os.path.join(TMP, "cta.png"); base.save(p); return p

# ----------------- 5) Componer reel -----------------
def componer(bg, estatico, subs, seg, cta_png, cta_start, final_wav, D):
    out = os.path.join(TMP, "reel_hoy.mp4")
    inp = ["-stream_loop","-1","-i",bg, "-i",final_wav, "-loop","1","-framerate","24","-i",estatico]
    for s in subs:
        inp += ["-loop","1","-framerate","24","-i",s]
    inp += ["-loop","1","-framerate","24","-i",cta_png]
    cta_idx = 3 + len(subs)
    parts = [("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=24,"
              "eq=brightness=-0.05:saturation=1.03,vignette=angle=PI/5,setsar=1[bg]"),
             "[2:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1[st]",
             "[bg][st]overlay=0:0[b0]"]
    prev = "b0"
    for i,(s,e) in enumerate(seg):
        parts.append(f"[{3+i}:v]format=rgba,fade=t=in:st={s:.2f}:d=0.3:alpha=1,fade=t=out:st={e-0.3:.2f}:d=0.3:alpha=1[sv{i}]")
        parts.append(f"[{prev}][sv{i}]overlay=0:0:enable='between(t,{s:.2f},{e:.2f})'[b{i+1}]")
        prev = f"b{i+1}"
    parts.append(f"[{cta_idx}:v]format=rgba,fade=t=in:st={cta_start:.2f}:d=0.4:alpha=1[ctaov]")
    parts.append(f"[{prev}][ctaov]overlay=0:0:enable='gte(t,{cta_start:.2f})'[v]")
    filt = ";".join(parts)
    subprocess.run([FFMPEG, "-y", *inp, "-filter_complex", filt, "-map", "[v]", "-map", "1:a",
                    "-t", f"{D:.2f}", "-c:v","libx264","-preset","medium","-pix_fmt","yuv420p","-r","24",
                    "-c:a","aac","-b:a","192k", out], check=True, stdout=DEVNULL, stderr=DEVNULL)
    return out

# ----------------- 6) Publicacion (SIN CAMBIOS respecto a la version estable) -----------------
def subir_hosting(video):
    fn = f"paz_reel_{int(time.time())}.mp4"
    # Subida como multipart/form-data (campo "file"), NO como cuerpo crudo video/mp4:
    # desde el 20-ago-2026 ModSecurity (OWASP CRS) esta activo en el VPS y la regla 920420
    # rechaza los POST con Content-Type video/mp4 (asi estuvo roto del 20 al 22-ago).
    # multipart/form-data es un tipo permitido por CRS y WordPress lo acepta igual.
    with open(video, "rb") as f:
        up = requests.post(K["host_upload_url"], files={"file": (fn, f, "video/mp4")},
                           auth=(K["host_user"], K["host_pass"]), timeout=300, allow_redirects=False)
    # allow_redirects=False: un 301/302 aqui convertiria el POST en GET (asi fallo del 15 al 18-ago-2026
    # tras mover WP de /directorio/ a la raiz). Si redirige, avisamos con la URL destino en vez de reventar.
    if up.status_code in (301, 302, 307, 308):
        log(f"ERROR subida hosting: la URL redirige ({up.status_code}) a {up.headers.get('Location')}. "
            f"Actualiza host_upload_url/host_media_base en keys.json."); return None, None
    if up.status_code not in (200, 201):
        log(f"ERROR subida hosting {up.status_code}: {up.text[:200]}"); return None, None
    j = up.json()
    if not isinstance(j, dict) or "id" not in j or "source_url" not in j:
        log(f"ERROR subida hosting: respuesta inesperada: {str(j)[:200]}"); return None, None
    return j["id"], j["source_url"]

def publicar_ig(url, caption):
    c = requests.post(f"{G}/{IG}/media", params={"media_type": "REELS", "video_url": url,
                      "caption": caption, "share_to_feed": "true", "access_token": TOKEN}, timeout=60).json()
    if "id" not in c:
        log(f"ERROR contenedor IG: {c}"); return False
    cid = c["id"]
    for _ in range(60):
        sc = requests.get(f"{G}/{cid}", params={"fields": "status_code", "access_token": TOKEN},
                          timeout=30).json().get("status_code")
        if sc == "FINISHED":
            break
        if sc == "ERROR":
            log("ERROR procesando video en IG"); return False
        time.sleep(6)
    else:
        log("IG: timeout procesando"); return False
    pub = requests.post(f"{G}/{IG}/media_publish",
                        params={"creation_id": cid, "access_token": TOKEN}, timeout=60).json()
    if "id" in pub:
        log(f"IG publicado: {pub['id']}"); return True
    log(f"ERROR publicar IG: {pub}"); return False

def publicar_fb(url, caption):
    st = requests.post(f"{G}/{PAGE}/video_reels",
                       params={"upload_phase": "start", "access_token": TOKEN}, timeout=60).json()
    if "video_id" not in st:
        log(f"ERROR start FB: {st}"); return False
    vid = st["video_id"]
    requests.post(st["upload_url"], headers={"Authorization": f"OAuth {TOKEN}", "file_url": url}, timeout=300)
    fin = requests.post(f"{G}/{PAGE}/video_reels", params={"upload_phase": "finish", "video_id": vid,
                        "video_state": "PUBLISHED", "description": caption, "access_token": TOKEN}, timeout=60).json()
    if fin.get("success"):
        log(f"FB reel enviado: post {fin.get('post_id', vid)}"); return True
    log(f"ERROR finish FB: {fin}"); return False

def borrar_hosting(media_id):
    try:
        requests.delete(K["host_media_base"] + str(media_id), params={"force": "true"},
                        auth=(K["host_user"], K["host_pass"]), timeout=60)
        log(f"Hosting temporal borrado (media {media_id}).")
    except Exception as e:
        log(f"Aviso: no se pudo borrar hosting {media_id}: {e}")

def ultimo_largo_publico(yt):
    """URL del ultimo video LARGO (>=5 min) del canal que ya este PUBLICO.

    Se pregunta a YouTube en vez de leer un archivo local a proposito: los videos largos se
    suben en privado con hora de publicacion (10:00) y el Short sale a las 7:00. Con una lista
    local se enlazaria a un video aun privado y el espectador se toparia con un muro.
    Devuelve None si no hay ninguno -> el Short sale sin enlace, como siempre.
    """
    try:
        ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        items = yt.playlistItems().list(part="contentDetails", playlistId=up,
                                        maxResults=15).execute().get("items", [])
        ids = [i["contentDetails"]["videoId"] for i in items]
        if not ids:
            return None
        for v in yt.videos().list(part="contentDetails,status", id=",".join(ids)).execute().get("items", []):
            if v["status"].get("privacyStatus") != "public":
                continue
            m = re.search(r"PT(?:(\d+)M)?(?:(\d+)S)?", v["contentDetails"].get("duration", ""))
            seg = (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) if m else 0
            if seg >= 300:                      # 5 min o mas = es un video largo
                return f"https://youtu.be/{v['id']}"
    except Exception as e:
        log(f"Aviso: no se pudo buscar el ultimo video largo: {e}")
    return None


def publicar_youtube(video_path, ref):
    rt = K.get("youtube_refresh_token")
    if not rt:
        log("YouTube: sin refresh token, omitido."); return False
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        creds = Credentials(token=None, refresh_token=rt,
                            client_id=K["youtube_client_id"], client_secret=K["youtube_client_secret"],
                            token_uri="https://oauth2.googleapis.com/token",
                            scopes=["https://www.googleapis.com/auth/youtube.force-ssl"])
        yt = build("youtube", "v3", credentials=creds)
        titulo = ref["frase"].rstrip(".")[:90] + " #Shorts"
        # Embudo Short -> video largo: el trafico del canal entra por los Shorts (~840 vistas
        # por Short) y hasta hoy ninguna de esas visitas sabia que el video largo existe.
        # En YouTube el enlace de la descripcion SI es clicable (en IG no lo seria).
        desc = construir_caption(ref)
        largo = ultimo_largo_publico(yt)
        if largo:
            desc += "\n\n\U0001F33F ¿Quieres más calma? Mira el vídeo largo:\n" + largo
            log(f"Enlace al video largo anadido: {largo}")
        desc += "\n\n#Shorts #reels"
        body = {"snippet": {"title": titulo, "description": desc, "categoryId": "22",
                            "tags": ["pazyperspectiva", "calma", "bienestar", "mindfulness", "reflexiones", "shorts"]},
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
        resp = yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        log(f"YouTube publicado: https://youtu.be/{resp['id']}")
        return True
    except Exception as e:
        log(f"ERROR YouTube: {e}"); return False

def construir_caption(ref):
    grupo = ref.get("cta_grupo")
    serie = ref.get("serie")
    cab = f"{serie} · {ref.get('serie_pos','')}/{ref.get('serie_total','')}\n\n" if serie else ""
    if grupo in CTA_CAPTION_MAP:
        return (f"{cab}{ref['frase']} \U0001F33F\n\n{ref['voz']}\n\n"
                f"{CTA_CAPTION_MAP[grupo]}\n\n{HASHTAGS}")
    cap = ref.get("caption")
    if not cap:
        return f"{cab}{ref['frase']} \U0001F33F\n\n{ref['voz']}\n\n{HASHTAGS}"
    return cap

# ----------------- MAIN -----------------
def main(test=False):
    log("===== INICIO" + (" (TEST)" if test else "") + " =====")
    try:
        hoy = time.strftime("%Y-%m-%d")
        if not test and cargar(ESTADO, default={}).get("ultima_fecha_ok") == hoy:
            log(f"Ya se publico con exito hoy ({hoy}); este reintento se omite. Nada que hacer."); return
        refs, ref = elegir_reflexion()
        serie = ref.get("serie", "?"); pos, tot = ref.get("serie_pos", 1), ref.get("serie_total", 1)
        log(f"Reflexion #{ref['id']} | serie {serie} {pos}/{tot} | \"{ref['frase']}\" | tema: {ref['tema']}")
        usados = set(cargar(VIDEOS_USADOS, default=[]))
        bg, sid = conseguir_fondo(ref["tema"], usados)
        if not bg:
            log("ERROR: no se consiguio video de fondo. Abortando."); return
        log(f"Fondo conseguido: {sid}")
        segmentos = segmentar(ref.get("voz", ""))
        final_wav, D, seg, cta_start, musica = construir_pista(segmentos)
        estatico = crear_estatico(ref)
        subs = [crear_sub(i, s) for i, s in enumerate(segmentos)]
        grupo = ref.get("cta_grupo")
        cta_png = crear_cta(CTA_PANTALLA.get(grupo, "Guárdalo"))
        out = componer(bg, estatico, subs, seg, cta_png, cta_start, final_wav, D)
        log(f"Reel generado ({D:.1f}s, sin voz) | {len(subs)} subt | musica: {musica} | cat: {ref.get('categoria','?')}/{grupo or 'fallback'}: {out}")

        if test:
            log("Modo TEST: NO se publica. Abriendo el video para revisar.")
            os.startfile(out); log("===== FIN (TEST) ====="); return

        caption = construir_caption(ref)
        media_id, url = subir_hosting(out)
        if not url:
            log("ERROR: fallo el hosting. Abortando."); return
        log(f"Subido a hosting: {url}")
        ig_ok = publicar_ig(url, caption)
        fb_ok = publicar_fb(url, caption)
        borrar_hosting(media_id)
        yt_ok = publicar_youtube(out, ref)

        if ig_ok or fb_ok or yt_ok:
            for r in refs:
                if r["id"] == ref["id"]:
                    r["usado"] = True
            guardar(REFL, refs)
            usados.add(sid); guardar(VIDEOS_USADOS, sorted(usados))
            guardar(ESTADO, {"ultima_fecha_ok": hoy})
            log(f"OK. Publicado IG={ig_ok} FB={fb_ok} YT={yt_ok}. Reflexion #{ref['id']} marcada como usada.")
        else:
            log("FALLO en las 3 redes: la reflexion NO se marca usada (se reintentara).")
    except Exception as e:
        import traceback
        log("EXCEPCION: " + repr(e)); log(traceback.format_exc())
    finally:
        log("===== FIN =====")

if __name__ == "__main__":
    main(test=("--test" in sys.argv))
