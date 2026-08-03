# M.G Linux Toolbox 0.9.0 Beta 4 — Note di rilascio

Data: 3 agosto 2026
Autore: Gregorio Mangano

La Beta 4 è un hotfix concentrato su tre problemi reali della Beta 3.

## Corretto il problema delle modifiche amministrative nell'AppImage

Nella Beta 3, avviando l'app come AppImage, le modifiche che richiedono
la password (ad esempio KSM) fallivano con un errore tecnico del tipo
`can't open file '/tmp/.mount_.../priv_writer.py': Permission denied`.

La causa: il componente privilegiato veniva cercato dentro il montaggio
temporaneo dell'AppImage (`/tmp/.mount_*`), un percorso che il processo
amministrativo non può attraversare.

La soluzione della Beta 4 è strutturale, non un rattoppo:

- un **helper privilegiato stabile e sicuro** viene installato in
  `/usr/libexec/mg-linux-toolbox/mg-privileged-helper`, di proprietà di
  root e non modificabile dagli utenti normali;
- una **azione Polkit dedicata**
  (`it.manganogregorio.mg-linux-toolbox.modify-system`) mostra
  chiaramente il nome del programma e autorizza soltanto quell'helper;
- prima di ogni operazione l'app **verifica proprietario, permessi e
  versione** dell'helper: se qualcosa non torna, la modifica viene
  bloccata con una spiegazione semplice;
- l'helper accetta solo un elenco chiuso di funzioni e azioni, valida
  ogni valore, rilegge il risultato dopo ogni scrittura e registra
  l'operazione, senza mai eseguire comandi arbitrari.

## Installazione gestita e AppImage portatile

- **Installazione gestita** (con `install.sh`): AppImage in
  `~/.local/opt/mg-linux-toolbox/`, voce nel menu, helper e policy
  Polkit, aggiornamenti con un clic e ripristino della versione
  precedente.
- **AppImage portatile** (avviata da Scaricati, Desktop o USB): tutte le
  funzioni di lettura funzionano; le modifiche amministrative sono
  disabilitate con un messaggio chiaro e l'invito a installare la
  versione completa. Nessun componente di sistema viene mai installato
  di nascosto.

## Aggiornamento completo con un clic

Dalla finestra Informazioni, "Controlla aggiornamenti" ora completa
davvero tutto il percorso: mostra versione installata e disponibile,
canale, novità e dimensione; scarica in una cartella temporanea privata;
verifica il nome dell'asset, l'architettura e lo **SHA-256 prima di
rendere eseguibile il file**; crea il backup della versione corrente;
sostituisce l'AppImage in modo atomico; aggiorna se serve il componente
amministrativo; propone "Riavvia adesso" sul percorso stabile. Se un
passaggio fallisce, la versione in uso resta intatta e il backup non
viene eliminato. È disponibile anche "Ripristina versione precedente"
con conferma esplicita.

## Procedura VFIO migliorata

- I dispositivi sono mostrati con nomi comprensibili ("Scheda video…",
  "Controller USB…"); i codici tecnici stanno sotto "Mostra dettagli
  tecnici".
- La selezione avviene **per gruppo IOMMU**: un gruppo si sposta intero
  o non si sposta affatto.
- I dispositivi essenziali (disco di avvio, scheda video del desktop,
  componenti di sistema) sono protetti e non selezionabili, con la
  spiegazione del perché; un gruppo che li contiene è interamente
  bloccato.
- Se non esiste alcun dispositivo adatto, la schermata lo dice
  chiaramente invece di mostrare un elenco che sembra selezionabile.
- Prima della configurazione compare un riepilogo completo (dispositivi,
  gruppo, driver attuale e dopo il riavvio, file creati, comando
  initramfs, procedura di ripristino) con conferma esplicita.
- Tutte le scritture avvengono nell'helper come **transazione unica**
  con backup, verifica e rollback automatico se la rigenerazione
  dell'initramfs fallisce.

## Altre correzioni

- Stato KSM onesto: "Attiva adesso" e "Avvio automatico: configurato /
  non configurato" sono ora informazioni separate e reali.
- Il ripristino acquisisce il valore reale all'inizio di ogni nuova
  prova, lo applica e lo rilegge. Se il valore non corrisponde, mostra
  un errore e non registra più un risultato positivo. La regressione
  KSM è coperta esplicitamente dal ciclo `0 → 1 → ripristino 0`, insieme
  alla copertura delle funzioni sysctl.
- Messaggi più chiari quando il componente amministrativo manca o va
  aggiornato; i dettagli tecnici restano sotto "Mostra dettagli".
- Nuova card "Componente amministrativo" nella Panoramica con il
  pulsante "Verifica funzionamento" (sola diagnostica, non modifica
  nulla).

## Compatibilità e limiti

- Il progetto è ancora in **Beta**: sono possibili difetti residui.
- VFIO resta una funzione avanzata: dipende dall'hardware, dal
  firmware e dai gruppi IOMMU del singolo computer. L'app non abilita
  mai ACS override e non permette il passthrough della GPU principale o
  del controller del disco di sistema.
- Alcune operazioni richiedono la password di amministratore tramite la
  finestra di sistema Polkit; il programma non conserva mai la password.
- **Requisiti minimi reali determinati in questa versione**: Python
  3.11, GTK4 4.8, **Libadwaita 1.4** (il vincolo effettivo), PyGObject
  3.42. Verificato empiricamente in container Debian 12 (Libadwaita
  1.2.2, sotto soglia — rifiutato correttamente da `install.sh` e
  dall'AppImage stessa) e Fedora/Arch/openSUSE (Libadwaita 1.8+, tutti
  conformi).

## Passaggio dalla Beta 3 alla Beta 4

Il meccanismo di aggiornamento con un clic è stato introdotto proprio in
questa versione. Per questo:

**Chi utilizza la Beta 3 deve eseguire una sola volta nuovamente il
comando di installazione (`install.sh`). Dopo il passaggio alla Beta 4,
i successivi aggiornamenti potranno essere installati direttamente
dall'applicazione.**

Non è possibile né promesso un aggiornamento automatico retroattivo
dalla Beta 3: la Beta 3 non conteneva ancora il codice dell'updater
completo qui descritto.

## File e collaudo

- File: `MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage`
- Suite completa: **1355 test superati**.
