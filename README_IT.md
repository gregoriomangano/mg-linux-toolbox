# M.G Linux Toolbox V2

[English](README.md) | Italiano

Utility desktop Linux cross-distro per informazioni di sistema, prestazioni, gestione software, gaming, pulizia e installazione di applicazioni.

## Screenshot

| Panoramica | Prestazioni |
|---|---|
| [![Panoramica](docs/images/screenshots/panoramica.png)](docs/images/screenshots/panoramica.png) | [![Prestazioni](docs/images/screenshots/prestazioni.png)](docs/images/screenshots/prestazioni.png) |

## Funzionalita

- Informazioni di sistema per computer, archiviazione, rete e utilizzo AI.
- Profili prestazioni e controlli opzionali, mostrati solo quando il sistema li supporta.
- Sorgenti software, gestione dell'avvio automatico e strumenti di pulizia.
- Preparazione Gaming, gestione DNS, WinBoat e GeForce NOW quando supportati.
- Opzioni di installazione per Gradia, Upscayl, Curtail, Ferdium e KDE Connect.
- Flatpak, Snap e pacchetti nativi quando supportati dalla funzione selezionata e dalla distribuzione.

## Distribuzioni Supportate

| Distribuzione | Formato distribuito |
|---|---|
| Ubuntu / Debian | Pacchetto `.deb` ufficiale |
| Fedora | AppImage ufficiale |
| Arch Linux | AppImage ufficiale |
| openSUSE | AppImage ufficiale |

Sono disponibili release amd64. Le singole funzioni dipendono da kernel, hardware, driver e repository configurati.

## Installazione

Scarica il pacchetto adatto dalla [release stabile piu recente](https://github.com/gregoriomangano/mg-linux-toolbox/releases/latest). Ubuntu e Debian usano il `.deb`; Fedora, Arch Linux e openSUSE usano l'AppImage.

Per l'installazione automatica:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

L'installer seleziona il pacchetto adatto, ne verifica il checksum e crea l'integrazione desktop necessaria.

## Aggiornamento

Esegui di nuovo lo stesso comando di installazione. Scarica la release stabile piu recente, verifica il checksum e aggiorna il pacchetto o l'AppImage installati insieme ai relativi componenti privilegiati.

## Licenza E Disponibilita Del Sorgente

M.G Linux Toolbox V2 e software proprietario distribuito secondo la [M.G Linux Toolbox Proprietary License](LICENSE). Questo repository pubblico contiene solo materiale di distribuzione e documentazione; non contiene il sorgente V2. Le release GPL storiche restano soggette ai termini applicabili a tali release.

## Autore E Sito Web

Sviluppato da **Gregorio Mangano**. Visita il [sito di M.G Linux Toolbox](https://www.manganogregorio.it/mg-linux-toolbox.html).

## Sicurezza

Per i problemi di sicurezza consulta [SECURITY.md](SECURITY.md).
