"""Que el programa no deje nada vivo ni moleste al ordenador.

- Cada programa que lanza (ffmpeg, ffprobe, ffplay, bmx) entra en un "trabajo" de Windows (Job Object) con
  KILL_ON_JOB_CLOSE: al cerrar el programa, o si se cierra mal (un fallo, el Administrador de tareas), Windows los
  termina todos. Nada se queda trabajando por detrás.
- Esos programas van con prioridad "por debajo de lo normal": usan el procesador que sobra y, si el ordenador se usa
  para otra cosa, ceden. La reproducción de sonido (ventana de Montaje) pide prioridad normal para no cortarse.
- salir() termina sin esperar a tareas de fondo (fotogramas del visor, formas de onda): lo pendiente se descarta.

Se activa con contener() al empezar (diagnostico_avid.main). Fuera de Windows no hace nada.
Lo que tiene que sobrevivir al programa (el .bat de la actualización) se lanza con suelto(), fuera del trabajo.
"""

import logging
import os
import subprocess
import sys

LOG = logging.getLogger("diagnostico")

POPEN_ORIGINAL = subprocess.Popen
PRIORIDAD_NORMAL = 0x00000020
PRIORIDAD_BAJA = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
_CLASES_PRIORIDAD = 0x00000020 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00004000 | 0x00008000
_trabajo = None


def _crear_trabajo():
    """Job Object con KILL_ON_JOB_CLOSE, o None si no se puede."""
    import ctypes
    from ctypes import wintypes

    class BASICA(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDIDA(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASICA), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    trabajo = k32.CreateJobObjectW(None, None)
    if not trabajo:
        raise OSError(ctypes.get_last_error(), "CreateJobObject")
    info = EXTENDIDA()
    info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(trabajo, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
    return k32, trabajo


class PopenContenido(POPEN_ORIGINAL):
    """subprocess.Popen que mete el proceso en el trabajo y le baja la prioridad (salvo que pida otra)."""

    def __init__(self, *args, **kwargs):
        if os.name == "nt":
            flags = kwargs.get("creationflags", 0) or 0
            if not flags & _CLASES_PRIORIDAD:
                kwargs["creationflags"] = flags | PRIORIDAD_BAJA
        super().__init__(*args, **kwargs)
        if _trabajo is not None:
            k32, trabajo = _trabajo
            try:
                if not k32.AssignProcessToJobObject(trabajo, int(self._handle)):
                    LOG.debug("No se ha podido meter el proceso %s en el trabajo", self.pid)
            except Exception as e:  # el proceso sigue igual, solo que no se cerrará solo
                LOG.debug("Trabajo de Windows: %s", e)


def contener():
    """Activa todo lo de arriba. Devuelve True si los procesos quedan contenidos (Windows)."""
    global _trabajo
    if os.name != "nt":
        return False
    if subprocess.Popen is not PopenContenido:
        subprocess.Popen = PopenContenido
    if _trabajo is None:
        try:
            _trabajo = _crear_trabajo()
        except Exception as e:  # sin el trabajo, los procesos siguen yendo con prioridad baja
            LOG.warning("No se ha podido crear el trabajo de Windows para los procesos: %s", e)
            return False
    return True


def suelto(*args, **kwargs):
    """Lanza un proceso que tiene que seguir vivo después de cerrar el programa (el .bat de la actualización)."""
    return POPEN_ORIGINAL(*args, **kwargs)


def terminar_hijos():
    """Termina ya todos los procesos lanzados por el programa (los del trabajo)."""
    if _trabajo is not None:
        k32, trabajo = _trabajo
        try:
            k32.TerminateJobObject(trabajo, 1)
        except Exception as e:
            LOG.debug("Trabajo de Windows: %s", e)


def salir(codigo=0):
    """Fin del programa sin dejar nada: termina los procesos hijos, cierra el log y sale sin esperar a las tareas de
    fondo que quedaran (hilos de fotogramas del visor y formas de onda, que no guardan nada a medias)."""
    terminar_hijos()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    logging.shutdown()
    os._exit(codigo)
