.PHONY: help run test check appimage

help:
	@echo "M.G Linux Toolbox"
	@echo "  make run       Avvia dal sorgente"
	@echo "  make test      Esegue i test automatici"
	@echo "  make check     Controlla la sintassi Python e shell"
	@echo "  make appimage  Prepara una AppImage locale"

run:
	PYTHONDONTWRITEBYTECODE=1 python3 main.py

test:
	PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests

check:
	PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q -f backend core ui main.py
	bash -n install.sh uninstall.sh run.sh packaging/appimage/build_appimage.sh

appimage:
	./packaging/appimage/build_appimage.sh
