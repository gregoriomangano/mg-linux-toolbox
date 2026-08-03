# Changelog

Le modifiche pubbliche di M.G Linux Toolbox saranno registrate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e il progetto usa [Semantic Versioning](https://semver.org/lang/it/) quando viene assegnata una versione pubblica.

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
