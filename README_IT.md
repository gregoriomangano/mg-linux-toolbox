# M.G Linux Toolbox V2

[English](README.md) | Italiano

M.G Linux Toolbox è pensato per semplificare Linux anche a chi non vuole usare
il terminale.

## Installazione facile

Apri il terminale, copia questo comando, incollalo e premi Invio:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

L'installer riconosce la tua distribuzione Linux, scarica il pacchetto giusto,
verifica il checksum e crea una voce nel menu delle applicazioni. Quando
necessario può chiedere la password amministratore.

## Preferisci scaricarlo manualmente?

- Ubuntu e Debian: scarica il pacchetto `.deb`.
- Fedora, Arch Linux e openSUSE: scarica l'AppImage.

Scarica il pacchetto dalla [latest stable release](https://github.com/gregoriomangano/mg-linux-toolbox/releases/latest).

## Cosa permette di fare?

- Mostrare informazioni sul computer, dischi, rete e strumenti AI.
- Aiutare a gestire le impostazioni delle prestazioni.
- Installare e gestire software e repository.
- Aiutare a pulire file temporanei e dati non più utili.
- Offrire opzioni utili per gaming e DNS.
- Aiutare a installare e gestire Gradia, Upscayl, Curtail, Ferdium e KDE Connect.
- Usare Flatpak, Snap e pacchetti nativi.
- Offrire WinBoat e GeForce NOW dove supportati.

## Distribuzioni supportate

- Ubuntu e Debian usano il pacchetto `.deb`.
- Fedora, Arch Linux e openSUSE usano l'AppImage.

Le release ufficiali supportano amd64. Alcune funzioni dipendono da hardware,
driver, distribuzione Linux e sorgenti software configurate.

## Aggiornamento

Per aggiornare basta eseguire di nuovo lo stesso comando di installazione.
Scaricherà e installerà il pacchetto più recente disponibile per la tua
distribuzione.

## Open source e licenza

M.G Linux Toolbox è open source ed è distribuito con licenza
[GPL-3.0-or-later](LICENSE). Consulta
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) per i materiali di terze
parti inclusi e [TRADEMARKS.md](TRADEMARKS.md) per nome e identità ufficiale
del progetto.

<details>
<summary><strong>Per gli sviluppatori</strong></summary>

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

</details>
