# Problemi comuni

[Italiano](PROBLEMI_COMUNI.md) | [English](TROUBLESHOOTING.md)

## L'AppImage non si apre

- Controlla nelle proprietà che sia consentita l'esecuzione come programma.
- Verifica che il file sia completo confrontando il checksum ufficiale.
- Controlla che l'architettura del file corrisponda al computer.
- Avvia il file da terminale e leggi il primo errore significativo.

Il file ufficiale è `MG-Linux-Toolbox-0.9.0-beta.4-x86_64.AppImage` ed è associato alla [Release v0.9.0-beta.4](https://github.com/gregoriomangano/mg-linux-toolbox/releases/tag/v0.9.0-beta.4).

## Errore FUSE

Installa soltanto il pacchetto FUSE indicato dalla documentazione della tua distribuzione e adatto alla sua versione. Non aggiungere pacchetti con nomi trovati casualmente: le varianti cambiano tra sistemi.

Se usi `install.sh`, lo script mostra prima il pacchetto disponibile e chiede conferma prima di usare `sudo`. Non anteporre mai `sudo` all'intero installer.

## Errore relativo a GTK, Adwaita o `gi`

Il sistema deve fornire GTK4, Libadwaita, PyGObject e i relativi dati di introspezione. Usa il gestore software o la documentazione ufficiale della distribuzione. Se tali componenti non esistono in una versione abbastanza recente, il sistema è troppo vecchio per questa edizione.

## "M.G Linux Toolbox richiede una versione recente di GTK4 e Libadwaita"

Dalla Beta 4 sia `install.sh` sia l'AppImage stessa controllano le versioni minime reali prima di procedere (**Libadwaita 1.4** è il requisito effettivo — GTK4 4.8 e PyGObject 3.42 non sono il vincolo). Il messaggio mostra la versione trovata e quella richiesta, mai un errore tecnico grezzo.

Esempio concreto: Debian 12 offre Libadwaita 1.2.2, sotto la soglia minima — verificato realmente in un container Debian 12 durante lo sviluppo della Beta 4. Per risolvere, aggiorna la distribuzione a una versione più recente o installa Libadwaita da un repository di backport ufficiale della tua distribuzione.

## Una funzione non è disponibile

Non tutte le funzioni esistono su ogni kernel o hardware. La voce dovrebbe mostrare lo stato rilevato e il motivo dell'indisponibilità. Questo comportamento è più sicuro che proporre una modifica non supportata.

## Una modifica non produce il risultato sperato

- Ripristina il valore precedente dalla stessa funzione o dalla cronologia.
- Se la prova era temporanea, riavvia il computer.
- Controlla che non esista un altro servizio che gestisce la stessa impostazione.
- Non rendere permanente una modifica finché non ne hai verificato l'effetto.

## Le modifiche amministrative non funzionano (o compariva un errore "Permission denied" su `/tmp/.mount_...`)

Nella Beta 3, avviando l'app come AppImage, le modifiche con password potevano fallire con un errore tecnico su un percorso `/tmp/.mount_...`. La Beta 4 corregge il problema con un componente amministrativo stabile installato dal metodo automatico (`install.sh`).

Se l'app dice che "Il componente amministrativo non è installato o deve essere aggiornato":

1. esegui (o riesegui) lo script `install.sh` della Release corrente;
2. conferma l'installazione del componente amministrativo quando viene proposta;
3. verifica dalla Panoramica con "Verifica funzionamento".

Un'AppImage portatile avviata da Scaricati o da una chiavetta offre solo le funzioni di lettura: è un comportamento previsto, non un guasto.

## Il programma chiede una password

Le operazioni che cambiano impostazioni di sistema possono richiedere privilegi amministrativi. Leggi l'azione mostrata prima di confermare. Chiudi la richiesta se non riconosci l'operazione.

## L'icona non compare nel menu

Dopo l'installazione automatica, chiudi e riapri il menu delle applicazioni. Se necessario, termina la sessione e accedi di nuovo. La voce deve chiamarsi **M.G Linux Toolbox** e usa i file nella cartella XDG personale; non servono modifiche sotto `/usr`.

## Un aggiornamento non riesce

Non ignorare un errore di checksum. Lo script rifiuta il nuovo file prima di sostituire quello funzionante e conserva una copia temporanea della versione precedente durante l'aggiornamento. Riprova soltanto usando la Release ufficiale.

## I dati dopo la disinstallazione

La disinstallazione normale conserva cronologia, impostazioni e punti di ripristino. Solo `uninstall.sh --purge`, seguito dalla conferma esplicita richiesta, elimina anche questi dati. Le dipendenze condivise non vengono rimosse.

## Chiedere aiuto senza esporre dati personali

Condividi la distribuzione, la sua versione, il kernel e il testo minimo dell'errore. Rimuovi nome utente, hostname, percorsi personali, indirizzi IP, nomi di rete, seriali e cronologia reale. Non allegare l'intero database dell'applicazione.
