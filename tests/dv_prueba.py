"""Para las pruebas: escribe en los fotogramas DV de un MXF el subcódigo que graba una cámara (timecode de cinta y
fecha y hora de grabación), que ffmpeg no escribe. Se reescriben los packs en su sitio, sin cambiar el tamaño."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mxf_util  # noqa: E402


def _bcd(n):
    return ((n // 10) << 4) | (n % 10)


def _packs(tc_frames, cuando, fps):
    base = int(round(fps))
    f, s = tc_frames % base, tc_frames // base
    tc = bytes([0x13, _bcd(f), _bcd(s % 60), _bcd((s // 60) % 60), _bcd((s // 3600) % 24)])
    fecha = bytes([0x62, 0xFF, _bcd(cuando.day), _bcd(cuando.month), _bcd(cuando.year % 100)])
    hora = bytes([0x63, 0xFF, _bcd(cuando.second), _bcd(cuando.minute), _bcd(cuando.hour)])
    return tc, fecha, hora


def poner_subcodigo(ruta, tc_inicio, cuando_inicio, fps=30000 / 1001, salto=None):
    """Escribe, fotograma a fotograma, el timecode de cinta (tc_inicio: 'hh:mm:ss:ff', y sigue) y la fecha y hora de
    grabación (cuando_inicio: datetime, avanza con el tiempo real del fotograma) en todos los canales. salto: (fotograma,
    segundos): desde ese fotograma, timecode y hora van segundos por delante, como cuando la cámara para y vuelve a
    grabar con el timecode siguiendo al reloj. Devuelve cuántos fotogramas ha escrito. Solo para la esencia en un bloque
    (lo que escribe el mxf_opatom de ffmpeg)."""
    h, m, s, f = (int(x) for x in tc_inicio.split(":"))
    base = int(round(fps))
    inicio = ((h * 60 + m) * 60 + s) * base + f
    with open(ruta, "r+b") as fh:
        for pos, clave, n in mxf_util._klv(fh):
            if clave[:12] == mxf_util.CLAVE_ESENCIA and clave[12] in mxf_util.TIPOS_DV:
                break
        else:
            raise ValueError("sin esencia DV")
        tam = mxf_util.subcodigo_dv(ruta)["fotograma"]
        for i in range(n // tam):
            extra = salto[1] if salto and i >= salto[0] else 0
            tc, fecha, hora = _packs(inicio + i + extra * base, cuando_inicio + dt.timedelta(seconds=i / fps + extra), fps)
            for s in range(tam // mxf_util.SECUENCIA_DIF):
                o0 = pos + i * tam + s * mxf_util.SECUENCIA_DIF
                for blk in (1, 2):  # subcódigo
                    for j in range(6):
                        fh.seek(o0 + blk * 80 + 3 + j * 8 + 3)
                        fh.write(tc)
                for blk in (3, 4, 5):  # VAUX: fecha y hora en los dos primeros packs
                    fh.seek(o0 + blk * 80 + 3)
                    fh.write(fecha + hora)
    return n // tam
