# Installazione di M.G Linux Toolbox

[Italiano](INSTALLAZIONE.md) | [English](INSTALLATION.md)

Questa guida è pensata anche per chi non ha mai usato un terminale.

## Stato attuale

La nuova AppImage è associata alla Release ufficiale 0.9.0 Beta 4.

Scaricala soltanto dalla [Release v0.9.0-beta.4](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.4).

Non usare AppImage provenienti da vecchi backup come se fossero la versione finale.

## Dove scaricare l'AppImage

Usa soltanto il collegamento ufficiale pubblicato nel README e nella pagina del progetto. Nella stessa pagina trovi:

- l'AppImage adatta all'architettura supportata;
- il relativo checksum SHA-256;
- le note di rilascio.

Il file per computer x86_64 è `MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage`.

## Rendere eseguibile il file senza terminale

1. Apri la cartella **Scaricati** nel gestore file.
2. Fai clic con il tasto destro sull'AppImage.
3. Apri **Proprietà** e poi **Permessi**.
4. Attiva l'opzione che consente di eseguire il file come programma.
5. Chiudi le proprietà e fai doppio clic sull'AppImage.

Il nome dell'opzione può cambiare leggermente tra Files, Dolphin, Nemo e altri gestori file.

## Alternativa da terminale

Apri un terminale nella cartella che contiene il file scaricato ed esegui:

```bash
chmod +x "MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage"
./"MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage"
```

Confronta sempre il checksum SHA-256 con quello pubblicato nella stessa Release prima dell'avvio.

## Metodo automatico

Lo script `install.sh` supporta sistemi delle famiglie apt, dnf, pacman e zypper. Rileva l'architettura x86_64, controlla Python 3, GTK4, Libadwaita, PyGObject e FUSE, mostra prima i componenti mancanti e chiede conferma. Usa `sudo` soltanto per gli eventuali pacchetti di sistema.

Esegui:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/install.sh | bash
```

Lo script scarica AppImage e checksum dalla Release, rifiuta un file alterato e installa tutto nella cartella personale `~/.local/opt/mg-linux-toolbox`.

Al termine troverai **M.G Linux Toolbox** nel menu delle applicazioni con l'icona MG. Non eseguire lo script come root e non anteporre `sudo` al comando.

## AppImage portatile e installazione gestita

Le due modalità sono diverse ed è importante saperlo prima di scegliere:

- **AppImage portatile** (scaricata a mano e avviata da Scaricati, Desktop o una chiavetta): tutte le funzioni di **lettura** funzionano subito — panoramica, monitoraggio, informazioni di sistema. Le modifiche che richiedono privilegi restano disattivate, con un messaggio chiaro.
- **Installazione gestita** (con `install.sh`): aggiunge il collegamento nel menu, il **componente amministrativo** e gli **aggiornamenti con un clic** dall'app.

### Componente amministrativo

Il componente amministrativo è un piccolo programma di sistema installato in `/usr/libexec/mg-linux-toolbox/`, di proprietà di root, autorizzato da una regola Polkit dedicata. Serve per applicare le modifiche di sistema (KSM, CPU, batteria, VFIO, ecc.) in modo sicuro anche quando l'app gira come AppImage.

- Durante l'installazione lo script mostra **esattamente** quali file di sistema verranno creati e chiede una conferma.
- Alcune operazioni chiederanno la **password di amministratore** tramite la finestra di sistema: è normale.
- **Il programma non conserva mai la password**: la richiesta è gestita da Polkit, lo stesso meccanismo usato dalle impostazioni del sistema.
- Puoi controllare lo stato del componente dalla Panoramica ("Componente amministrativo" → "Verifica funzionamento").

## Componenti necessari

### FUSE

FUSE permette a molte AppImage di montare il proprio contenuto durante l'avvio. Se compare un errore che nomina FUSE, apri il gestore software della tua distribuzione e cerca il pacchetto FUSE compatibile con la sua versione. I nomi dei pacchetti cambiano tra distribuzioni e non vanno indovinati.

### GTK4

GTK4 disegna finestre, pulsanti e pannelli. Un sistema che non offre una versione compatibile di GTK4 è troppo vecchio per questa edizione.

### Libadwaita

Libadwaita fornisce i componenti grafici moderni usati dall'interfaccia. Deve essere disponibile insieme ai dati di introspezione previsti dalla distribuzione.

### PyGObject

PyGObject collega Python alle librerie GTK4 e Libadwaita. In alcune distribuzioni il pacchetto contiene nel nome `python3-gi`, in altre `python-gobject` o `python3-gobject`.

Il metodo automatico controlla i pacchetti realmente disponibili prima di proporre l'installazione. Se procedi manualmente, usa la documentazione della tua distribuzione o il suo gestore software.

### Versioni minime reali (dalla Beta 4)

Determinate analizzando le funzioni dell'interfaccia realmente usate (non assunte), verificate anche in container Debian/Ubuntu, Fedora, Arch e openSUSE:

| Componente | Versione minima |
|---|---|
| Python | 3.11 |
| GTK4 | 4.8 |
| **Libadwaita** | **1.4** |
| PyGObject | 3.42 |

Il vincolo reale è **Libadwaita 1.4**: alcune funzioni dell'interfaccia (righe espandibili con azioni, adattamento della finestra, la barra degli strumenti moderna) sono state introdotte proprio in quella versione. Debian 12, ad esempio, offre solo Libadwaita 1.2.2 e non soddisfa il requisito. GTK4 e PyGObject non sono il fattore limitante: una distribuzione con Libadwaita 1.4 o superiore porta sempre con sé una versione di GTK4 abbastanza recente.

`install.sh` verifica questi requisiti prima di dichiarare l'installazione riuscita, e l'AppImage stessa li verifica di nuovo a ogni avvio (anche se lanciata manualmente, senza passare da `install.sh`). Se il sistema non li soddisfa, compare:

> "M.G Linux Toolbox richiede una versione recente di GTK4 e Libadwaita. La versione presente su questo sistema è troppo vecchia."

con la versione trovata e quella richiesta — mai un errore tecnico grezzo.

## Distribuzioni: come leggere lo stato

- **Testata:** Pop!_OS 24.04 LTS nell'ambiente usato per il collaudo locale.
- **Compatibilità prevista:** distribuzioni moderne delle famiglie Ubuntu, Fedora, Arch Linux e openSUSE con dipendenze adeguate.
- **Non verificata:** qualunque altra distribuzione o versione non sottoposta a collaudo dedicato.
- **Sistema troppo vecchio:** sistema che non offre GTK4, Libadwaita o PyGObject compatibili.

La compatibilità prevista non è una promessa di funzionamento su ogni derivata.

## Se l'app non si apre

1. Controlla che il file sia eseguibile.
2. Verifica che l'architettura del file corrisponda al computer.
3. Cerca nel messaggio di errore i nomi FUSE, GTK, Adwaita o `gi`.
4. Prova ad avviare il file da un terminale per vedere il messaggio completo.
5. Consulta [PROBLEMI_COMUNI.md](PROBLEMI_COMUNI.md).

## Raccogliere un errore senza condividere dati personali

Per l'avvio dal sorgente puoi salvare soltanto gli errori con:

```bash
python3 main.py 2>errore-mg-toolbox.txt
```

Prima di inviare il file:

- aprilo con un editor di testo;
- rimuovi nome utente, nome del computer e percorsi della cartella personale;
- rimuovi indirizzi IP, nomi di rete, modelli o seriali dei dischi se non servono;
- non inviare database, cronologia, screenshot completi del desktop o file di configurazione personali.

Per l'AppImage, il comando esatto verrà indicato nella Release ufficiale.

## Aggiornare

Per un'installazione gestita, riesegui lo stesso comando automatico. Lo script confronta la versione disponibile, verifica il nuovo checksum e conserva temporaneamente l'AppImage precedente. Se il download o la verifica falliscono, la versione installata non viene sostituita. Cronologia, impostazioni e punti di ripristino restano invariati.

Per un'AppImage usata manualmente, scarica il nuovo file dalla Release, verifica il checksum, chiudi la versione precedente e avvia quella nuova. Elimina la vecchia AppImage soltanto quando hai verificato il nuovo avvio.

## Disinstallare

### AppImage usata manualmente

Chiudi il programma e sposta nel cestino soltanto l'AppImage scaricata. Non è necessario eliminare librerie condivise come GTK4 o FUSE.

### Installazione gestita

Per la disinstallazione normale esegui:

```bash
curl -fsSL https://raw.githubusercontent.com/gregoriomangano/mg-linux-toolbox/main/uninstall.sh | bash
```

Lo script rimuove soltanto AppImage installata, comando di avvio, voce del menu, icona e backup dell'AppImage precedente. Non rimuove Python, GTK4, Libadwaita, PyGObject o FUSE e conserva i dati personali.

Per eliminare anche cronologia, impostazioni e punti di ripristino, scarica `uninstall.sh`, rendilo eseguibile, avvialo con `./uninstall.sh --purge` e scrivi la conferma esatta mostrata dallo script.

## Dove restano cronologia e dati

Percorsi predefiniti:

- `~/.local/share/mg-linux-toolbox`: database della cronologia e punti di ripristino;
- `~/.local/state/mg-linux-toolbox`: stato e registri dell'applicazione.

Se il sistema usa `XDG_DATA_HOME` o `XDG_STATE_HOME`, vengono usate le directory equivalenti definite da tali variabili.
