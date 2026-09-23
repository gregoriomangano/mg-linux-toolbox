# M.G Linux Toolbox V2

[English](README.md) | Italiano

M.G Linux Toolbox e una utility desktop Linux open source e cross-distro per
informazioni di sistema, prestazioni, gestione software, gaming, pulizia e
installazione di applicazioni.

## Distribuzioni supportate

- Ubuntu e Debian: pacchetto `.deb`.
- Fedora, Arch Linux e openSUSE: AppImage.

Le release ufficiali supportano amd64. La disponibilita delle funzioni dipende
da kernel, hardware, driver e repository configurati.

## Sviluppo

```bash
npm install
npm run tauri dev
```

## Test

```bash
npm test
```

## Build

```bash
npm run tauri build
```

Gli script di release compilano i binari helper privilegiati da
`src-tauri/src/bin/` prima del packaging; nessun helper generato viene
committato.

## Licenza

Il codice originale di M.G Linux Toolbox e distribuito con licenza
[GPL-3.0-or-later](LICENSE). Consulta
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) per i materiali di terze
parti inclusi e [TRADEMARKS.md](TRADEMARKS.md) per nome e identita ufficiale
del progetto.

## Autore

Sviluppato da **Gregorio Mangano**. Sito del progetto:
<https://www.manganogregorio.it/mg-linux-toolbox.html>.
