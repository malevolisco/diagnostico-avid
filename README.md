# DiagnosticoAvid

Herramienta para el Archivo: analiza un disco de edición Avid Media Composer completo (proyectos, bins, clips, MXF) y, opcionalmente, exporta un bruto por proyecto: un único MXF con todos los clips encadenados, con shotlist Excel y hoja de contactos.

**Descarga del portable (Windows, sin instalar nada):**
`https://github.com/malevolisco/diagnostico-avid/releases/latest/download/DiagnosticoAvid_portable.zip`

Cada cambio en `main` construye el `.exe` en GitHub Actions, lo prueba contra un disco Avid simulado y publica una nueva release.

- `INSTRUCCIONES.txt`: hoja de uso para quien ejecute el programa.
- `LEEME_tecnico.md`: cómo funciona por dentro, config de exportación, qué queda por verificar con un disco real.
- `tests/`: generador del disco simulado y comprobador del resultado (los usa el flujo de GitHub).
