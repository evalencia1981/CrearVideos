"""Punto de entrada del .exe: abre la interfaz y ya.

Está fuera del paquete a propósito: PyInstaller arranca mejor con un archivo
suelto, y así el comportamiento del programa instalable queda separado del
`python -m crearvideos` que se usa desde el código.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # Sin esto, cualquier proceso hijo en Windows relanzaría el .exe entero y
    # abriría copias del programa hasta tumbar el equipo.
    multiprocessing.freeze_support()

    from crearvideos.escritorio import main

    sys.exit(main())
