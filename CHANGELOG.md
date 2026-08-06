# Changelog

Le modifiche pubbliche di M.G Linux Toolbox saranno registrate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e il progetto usa [Semantic Versioning](https://semver.org/lang/it/) quando viene assegnata una versione pubblica.

## [Non rilasciato]

- Gaming Pack: verifica reale della disponibilità nei repository configurati, installazione in una sola transazione, verifica post-installazione e rimozione limitata ai pacchetti registrati dal Toolbox.
- Gaming Pack: supporto verificato per Debian 13, Fedora 44, Arch Linux e openSUSE Tumbleweed, inclusi i pacchetti 32 bit e l'ICD Vulkan AMD corretto.
- Repository e Virtualizzazione: riallineati i controlli pubblici per Packman, Flatpak, Docker, Podman e Distrobox alle implementazioni validate.
- Test: la suite protegge anche la discovery standard da `sudo`, `pkexec`, `su` e `doas`, senza modificare il comportamento runtime dell'applicazione.

## [0.9.0-beta.5] - 2026-08-06

### Corretto

- L'aggiornamento gestito conserva una copia pendente, attende la conferma atomica dell'avvio della nuova AppImage tramite helper esterno e ripristina la versione precedente se la conferma manca o il processo termina durante la stabilizzazione.
- La conversione da AppImage portatile a installazione gestita verifica copia, permessi, dimensione e SHA-256 e ripristina la destinazione se la scrittura del launcher fallisce.

## [0.9.0-beta.4] - 2026-08-04

### Corretto

- **Modifiche amministrative dall'AppImage**: le operazioni che richiedono la password (KSM, CPU, batteria, audio, sicurezza kernel, ecc.) fallivano quando l'app era avviata come AppImage, perché il componente privilegiato veniva cercato dentro il montaggio temporaneo `/tmp/.mount_*`, non attraversabile da root. Ora un helper stabile viene installato in `/usr/libexec/mg-linux-toolbox/` (proprietà root:root) e l'app verifica proprietario, permessi e versione prima di ogni uso.
- La configurazione VFIO e IOMMU non scrive più file in `/etc` dal processo grafico non privilegiato: l'intera operazione (validazione, backup, scrittura, verifica, initramfs, rollback) avviene come transazione unica nell'helper.
- Le modifiche a `/etc/systemd/resolved.conf` (DNS over TLS), `/etc/ssh/sshd_config` (accesso root SSH) e alla virtualizzazione annidata non usano più script generati al volo o `sh -c` come root.
- Lo stato KSM distingue ora onestamente "Attiva adesso" (stato corrente) da "Avvio automatico configurato" (persistenza reale su file).
- Il ripristino salva il valore reale all'inizio di ogni nuova prova e verifica la rilettura finale: se il valore richiesto non è stato applicato, l'operazione restituisce un errore e non viene registrata come riuscita.
- Le transazioni dei repository bloccano l'applicazione quando il backup obbligatorio fallisce e dichiarano un rollback riuscito soltanto dopo un ripristino materiale.
- Le installazioni composte, comprese le dipendenze per gaming, container, manutenzione disco e aggiornamenti automatici, usano un tempo massimo adeguato invece del limite breve destinato ai comandi di lettura.
- Su openSUSE la rimozione automatica dei pacchetti orfani viene indicata come non supportata e non richiede privilegi né dichiara un successo inesistente.
- Il rilevamento di IPv6 e ZRAM usa direttamente `/proc`, senza dipendere dalla presenza di `sysctl` o `swapon` nel `PATH`.

### Aggiunto

- **Aggiornamento completo con un clic**: controllo della versione, dettagli della release, download con avanzamento, verifica SHA-256 prima di rendere eseguibile il file, backup della versione precedente, sostituzione atomica, riavvio sul percorso stabile e "Ripristina versione precedente" con conferma.
- Azione Polkit dedicata `it.manganogregorio.mg-linux-toolbox.modify-system` che autorizza esclusivamente l'helper ufficiale installato.
- Card "Componente amministrativo" nella Panoramica con stato reale (installato, da installare, da aggiornare, danneggiato, modalità portatile) e pulsante "Verifica funzionamento" di sola diagnostica.
- Procedura guidata VFIO riscritta: nomi comprensibili dei dispositivi, selezione per gruppo IOMMU, dispositivi protetti disabilitati con spiegazione, riepilogo completo (driver attuale e futuro, file creati, comando initramfs, procedura di ripristino) e conferma esplicita ad alto rischio.
- In assenza di dispositivi adatti la schermata VFIO lo dice chiaramente, senza elenchi di codici né messaggi fuorvianti.
- `install.sh` installa e verifica il componente amministrativo (helper + policy Polkit) mostrando prima l'elenco esatto dei file e chiedendo una sola conferma; `uninstall.sh` li rimuove in sicurezza.
- Aggiornamento sicuro dell'helper: il candidato viene estratto da un'AppImage già verificata e installato da un'azione chiusa con controllo checksum, controllo versione, backup e rollback.
- Pagina **Software e repository** con rilevamento della distribuzione, stato Flatpak/Flathub, inventario delle sorgenti software e controlli sulla salute dei pacchetti.

### Sicurezza e limiti

- Nessun processo privilegiato esegue mai file da `/tmp/.mount_*` o da percorsi modificabili dall'utente.
- In modalità portatile (AppImage avviata da Scaricati, Desktop, USB) le funzioni in sola lettura restano complete; le modifiche amministrative sono disabilitate con una spiegazione e l'invito a installare la versione gestita.
- VFIO resta una funzione avanzata: dipende dall'hardware e dal gruppo IOMMU, non abilita mai ACS override e non permette il passthrough della GPU principale o del controller del disco di sistema.
- La suite protegge i test da avvii accidentali di `sudo`, `pkexec`, `su` e `doas`; il collaudo finale comprende 1580 test superati senza processi privilegiati reali.

## [0.9.0-beta.3] - 2026-08-03

### Aggiunto

- Pagina **Attività del disco** con velocità per dispositivo e programmi più attivi, basata esclusivamente su dati reali di `/proc` e `/sys`.
- Gaming Pack V1 in modalità di sola analisi e anteprima per distribuzione, GPU e disponibilità dei pacchetti.
- Test specifici per attività del disco, aggiornamento PSI, navigazione e Gaming Pack.

### Modificato

- PSI del disco descritto come tempo di attesa dei programmi, non come percentuale di utilizzo o diagnosi di guasto.
- `avg10` è il segnale corrente principale, `avg60` conferma lo stato e `avg300` resta un dato storico/tecnico.
- Transizioni PSI con due campioni coerenti e aggiornamento live, inclusa la corretta uscita dallo stato critico.
- Campionamento dei processi della pagina disco eseguito fuori dal thread GTK e soltanto mentre la pagina è attiva.

### Sicurezza e limiti

- Il Gaming Pack non installa o rimuove pacchetti, non abilita repository, non modifica driver o kernel e non esegue aggiornamenti completi del sistema.
- La mappatura Debian è stata verificata su Pop!_OS 24.04; Fedora, famiglia Arch e openSUSE richiedono ancora prove su macchine reali.

## [0.9.0-beta.2] - 2026-08-02

### Aggiunto

- Esportazione iniziale del sorgente, dei test, degli asset e della documentazione destinati alla pubblicazione.
- Guide di installazione e risoluzione dei problemi in italiano e inglese.
- Informazioni su sicurezza, licenza GPL-3.0-or-later e identità del progetto.
