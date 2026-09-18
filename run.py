"""Ponto de entrada pro executável (PyInstaller). Fica fora do pacote
`voice_recorder` de propósito: se `main.py` fosse rodado direto como
script top-level, os imports relativos dele (`from .db import ...`)
quebrariam, já que só funcionam quando o módulo é importado como parte
do pacote."""

from voice_recorder.main import main

if __name__ == "__main__":
    main()
