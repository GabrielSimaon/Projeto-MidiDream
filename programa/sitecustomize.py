"""Mantém os subprocessos internos de DSP sem janelas de console no Windows.

Ativo somente no ambiente privado iniciado pelo aplicativo desktop.
"""

import os
import subprocess
import sys

if sys.platform == "win32" and os.environ.get("MIDI_STUDIO_HIDE_CONSOLE") == "1":
    _Popen = subprocess.Popen

    class _HiddenPopen(_Popen):
        def __init__(self, *args, **kwargs):
            # creationflags é o argumento posicional 14 de Popen (após self).
            if len(args) >= 14:
                positional = list(args)
                positional[13] |= subprocess.CREATE_NO_WINDOW
                args = tuple(positional)
            else:
                kwargs["creationflags"] = (
                    kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
                )
            super().__init__(*args, **kwargs)

    subprocess.Popen = _HiddenPopen
