# Dipendenze e licenze esterne

Il codice di M.G Linux Toolbox è distribuito con licenza GPL-3.0-or-later. Le dipendenze installate dal sistema non vengono incorporate automaticamente nel repository e mantengono le rispettive licenze.

## Runtime

| Componente | Uso | Licenza rilevata nell'ambiente di collaudo |
|---|---|---|
| Python 3 | interprete | licenza Python Software Foundation e licenze collegate alla distribuzione |
| PyGObject | collegamento Python/GObject | LGPL-2.1-or-later; alcune parti Expat |
| GTK4 | interfaccia grafica | principalmente LGPL-2.1-or-later, con componenti sotto licenze compatibili indicate dal pacchetto |
| Libadwaita | componenti grafici | principalmente LGPL-2.1-or-later, con risorse sotto licenze indicate dal pacchetto |
| FUSE | avvio AppImage | licenze GPL/LGPL secondo la variante fornita dal sistema |

## Build AppImage

- `rsync` copia il payload nella directory di build;
- `sha256sum` crea il checksum;
- `appimagetool.AppImage` deve essere ottenuto separatamente dalla fonte ufficiale e collocato in `packaging/appimage/tools/`.

Lo strumento AppImage non è incluso in questa esportazione. Prima della build ufficiale devono essere verificati provenienza, versione, checksum e licenza dello strumento scelto.

## Verifica prima di distribuire

Le licenze effettive dipendono dalle versioni dei pacchetti e dai componenti eventualmente incorporati nella build. Prima di distribuire una AppImage finale occorre produrre l'elenco dei componenti inclusi e conservare gli avvisi di licenza richiesti.
