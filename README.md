# M.G Linux Toolbox V2

M.G Linux Toolbox rende piu semplici funzioni e impostazioni Linux che normalmente richiederebbero il terminale.

## Screenshot

| Panoramica | Prestazioni |
|---|---|
| [![Panoramica](docs/images/screenshots/panoramica.png)](docs/images/screenshots/panoramica.png) | [![Prestazioni](docs/images/screenshots/prestazioni.png)](docs/images/screenshots/prestazioni.png) |

### Chi sono

[![Chi sono](docs/images/screenshots/chi-sono.png)](docs/images/screenshots/chi-sono.png)

## Cosa permette di fare

- mostrare in modo chiaro computer, dischi, rete e utilizzo AI;
- gestire profili e controlli della pagina Prestazioni;
- gestire sorgenti software, autostart e pulizia;
- installare componenti Gaming, DNS, WinBoat e GeForce NOW quando supportati;
- installare Upscayl, Gradia, Ferdium e Curtail;
- ripristinare modifiche e gestire le impostazioni.

Le funzioni vengono mostrate in base a cio che il sistema supporta realmente.

## Installazione

### Ubuntu e Debian

Scarica il pacchetto `.deb` dalla [Release v1.0.0](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v1.0.0) e installalo con il gestore software della distribuzione.

### Installazione automatica

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

Ubuntu e Debian usano il `.deb` ufficiale. Fedora, Arch e openSUSE usano l'AppImage ufficiale; l'installer installa anche i componenti necessari e crea la voce nel menu.

## Compatibilita

Release amd64 per distribuzioni Linux moderne basate su Debian/Ubuntu, Fedora, Arch e openSUSE. La disponibilita delle singole funzioni dipende da kernel, hardware, driver e repository configurati.

## Aggiornamento

Per installazioni automatiche riesegui lo stesso comando. L'installer scarica l'ultima release stabile, verifica il checksum prima di installare e aggiorna anche i componenti privilegiati.

## Autore

M.G Linux Toolbox V2 e sviluppato da **Gregorio Mangano**.

## Licenza

M.G Linux Toolbox V2 e distribuito secondo la [M.G Linux Toolbox Proprietary License](LICENSE). Il sorgente V2 non e pubblico. Le versioni storiche pubblicate sotto GPL restano soggette ai termini GPL applicabili alla loro release.

Per problemi di sicurezza consulta [SECURITY.md](SECURITY.md).
